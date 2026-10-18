"""读财报填表：定位报表页 → 大模型按格式抽取 → 原文核对 → 规则检查。

大模型只负责「抄」，所有计算和检查都由代码完成。
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from cra.extract.schema import KEY_FIELDS, ExtractedValue, ExtractionResult
from cra.ingest.pdf_reader import Page, load_statement_pages, ocr_backend
from cra.llm import LLMClient, complete_json
from cra.validate import Flag
from cra.validate.citations import numbers_in, source_text_on_page
from cra.validate.financial_checks import Values, run_all

SYSTEM = """你是财报录入助手，从给定的财务报表页面中抄录指定科目。
规则：
1. 只抄报表上的数字，不计算、不推断、不凭记忆；找不到就 value 填 null，并在 note 说明。
2. 优先使用合并报表（consolidated），不要用母公司报表。
3. 注意表头的单位（元、千元、万元、百万、thousands、millions），填入 unit_multiplier。
4. 括号表示负数，例如 (1,234) 填 -1234；但 interest_expense（利息费用）和 capex（资本性支出）一律填正数，
   报表上写成 (79) 也填 79。
5. 每个数字都要给出 page（页码）和 source_text（报表上包含该数字的那一行原文）。
6. 每个科目、每个期间各输出一条。
只输出 JSON，格式：{"company":"...","statement_scope":"consolidated","values":[{"field":"...","period_end":"YYYY-MM-DD","value":0,"unit_multiplier":1,"currency":"USD","page":1,"source_text":"...","confidence":"high","note":null}]}"""


@dataclass
class ExtractionReport:
    result: ExtractionResult
    pages: List[Page]  # 交给模型的报表页（界面预览、人工核对用）
    verify_flags: List[Flag] = field(default_factory=list)  # 针对 AI 输出：原文对不上、低置信度、未找到
    rule_flags: List[Flag] = field(default_factory=list)  # 会计规则：平衡、小计、正负号、数量级

    @property
    def flags(self) -> List[Flag]:
        return self.verify_flags + self.rule_flags

    @property
    def pages_used(self) -> List[int]:
        return [p.number for p in self.pages]

    def values_by_period(self) -> Values:
        out: Values = {}
        for v in self.result.values:
            if v.normalized is not None:
                out.setdefault(v.period_end, {})[v.field] = v.normalized
        return out


# 这两个科目在 CRA 里按正数录入；报表上常写在括号里，模型可能照抄成负数，由代码统一纠正
POSITIVE_FIELDS = {"interest_expense", "capex"}


def _positive(v: ExtractedValue) -> ExtractedValue:
    if v.field in POSITIVE_FIELDS and v.value is not None and v.value < 0:
        note = "报表上为括号，按正数录入" + (f"；{v.note}" if v.note else "")
        return v.model_copy(update={"value": -v.value, "note": note})
    return v


DERIVED_NOTE = "程序推算"


def _derive_total_liabilities(values: List[ExtractedValue]) -> List[ExtractedValue]:
    """很多美国公司不单列负债合计：有资产总计和所有者权益时，由程序算出（不是模型抄的，标明推算）。"""
    by = {(v.period_end, v.field): v for v in values}
    out = list(values)
    for period in {v.period_end for v in values}:
        tl, ta, te = (by.get((period, f)) for f in ("total_liabilities", "total_assets", "total_equity"))
        if (tl is None or tl.value is None) and ta and te and ta.normalized is not None and te.normalized is not None:
            out = [v for v in out if not (v.period_end == period and v.field == "total_liabilities")]
            out.append(ExtractedValue(field="total_liabilities", period_end=period, value=ta.normalized - te.normalized,
                                      unit_multiplier=1.0, currency=ta.currency, page=ta.page, confidence="medium",
                                      note=f"{DERIVED_NOTE}：报表未单列负债合计，按 资产总计 − 所有者权益合计 计算"))
    return out


def is_derived(v: Optional[ExtractedValue]) -> bool:
    return bool(v and v.note and v.note.startswith(DERIVED_NOTE))


def build_prompt(pages: Sequence[Page], fields: Dict[str, str]) -> str:
    field_list = "\n".join(f"- {code}: {desc}" for code, desc in fields.items())
    body = "\n\n".join(f"=== 第 {p.number} 页 ===\n{p.text}" for p in pages)
    return f"需要抄录的科目：\n{field_list}\n\n报表页面：\n{body}"


def verify(result: ExtractionResult, pages: Sequence[Page]) -> List[Flag]:
    """逐条核对：原文是否真在那一页、科目是否在清单里、低置信度提示人工复核。"""
    page_text = {p.number: p.text for p in pages}
    flags = []
    for v in result.values:
        if v.field not in KEY_FIELDS:
            flags.append(Flag("warn", v.field, "不在科目清单中，已忽略"))
            continue
        if v.value is None:
            flags.append(Flag("warn", v.field, f"{v.period_end}：未找到（{v.note or '无说明'}）"))
            continue
        if is_derived(v):
            continue
        if v.page not in page_text or not source_text_on_page(v.source_text or "", page_text[v.page]):
            flags.append(Flag("error", v.field, f"{v.period_end}：原文在第 {v.page} 页找不到，可能是编造或抄错页"))
        elif abs(v.value) >= 10 and round(abs(v.value), 2) not in numbers_in(v.source_text or ""):
            flags.append(Flag("error", v.field, f"{v.period_end}：数值 {v.value:,} 与原文「{v.source_text}」不一致"))
        if v.confidence == "low":
            flags.append(Flag("warn", v.field, f"{v.period_end}：模型自评置信度低，请人工核对"))
    if result.statement_scope != "consolidated":
        flags.append(Flag("warn", "statement_scope", f"报表口径为 {result.statement_scope}，确认是否为合并报表"))
    return flags


def extract_from_pages(pages: Sequence[Page], llm: LLMClient, fields: Optional[Dict[str, str]] = None,
                       model: Optional[str] = None, reference: Optional[Values] = None) -> ExtractionReport:
    fields = fields or KEY_FIELDS
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": build_prompt(pages, fields)}]
    result = complete_json(llm, messages, ExtractionResult, model=model)
    result.values = _derive_total_liabilities([_positive(v) for v in result.values if v.field in fields])
    report = ExtractionReport(result, list(pages))
    report.verify_flags = verify(result, pages)
    report.rule_flags = run_all(report.values_by_period(), reference)
    return report


def extract_from_pdf(path: Path, llm: LLMClient, use_ocr: Optional[bool] = None, **kwargs) -> ExtractionReport:
    """PDF 或照片。use_ocr=None 时自动：有文字层直接读；扫描件才 OCR（只读报表所在的几页给模型）。"""
    statement_pages = load_statement_pages(path, use_ocr=use_ocr)
    if not statement_pages:
        hint = "扫描件需要 OCR：安装 PaddleOCR 或配置 GLM_API_KEY" if ocr_backend() == "none" else "确认文件里有三大报表"
        raise ValueError(f"{Path(path).name}：没有找到资产负债表/利润表所在页（{hint}）")
    return extract_from_pages(statement_pages, llm, **kwargs)


def check_level(v: Optional[ExtractedValue], pages: Sequence[Page], field_flags: Sequence[Flag]) -> str:
    """程序核对出的可信度（代替模型自评，模型几乎总说自己 high）：
    高 = 原文在那一页找得到、数字和原文一致、没有规则提示；中 = 有提示；低 = 有错误；— = 未找到。
    """
    if v is None or v.value is None:
        return "—"
    if is_derived(v):
        return "推算"
    if any(f.level == "error" for f in field_flags):
        return "低"
    page_text = {p.number: p.text for p in pages}
    found = v.page in page_text and source_text_on_page(v.source_text or "", page_text[v.page])
    matches = abs(v.value) < 10 or round(abs(v.value), 2) in numbers_in(v.source_text or "")
    if not (found and matches):
        return "低"
    return "中" if field_flags else "高"


# 公司名里没有区分度的词：查「文件里有没有这家公司」时不算
GENERIC_NAME_WORDS = {"chemical", "chemicals", "international", "intl", "trading", "holdings", "holding", "group",
                      "company", "co", "corporation", "corp", "inc", "llc", "ltd", "limited", "sa", "de", "cv", "the",
                      "and", "&", "industries", "industrial", "materials", "plc", "gmbh", "ag", "sas", "sarl", "nv"}


def company_in_text(text: str, names: Sequence[str]) -> Optional[str]:
    """文件文字里能不能找到客户名称（任一个名字的核心词都出现就算找到），返回找到的名字。"""
    import re

    from cra.lookup.normalize import CN_SUFFIXES, _strip_accents, has_cjk

    plain = _strip_accents(text).lower()
    words = set(re.findall(r"[a-z0-9]+", plain))
    for name in [n for n in names if n]:
        if has_cjk(name):
            core = name
            for suffix in CN_SUFFIXES:
                core = core.replace(suffix, "")
            if core and core in text:
                return name
            continue
        core = [w for w in re.findall(r"[a-z0-9]+", _strip_accents(name).lower()) if w not in GENERIC_NAME_WORDS]
        if core and all(w in words for w in core):
            return name
    return None


def precheck_company(path: Path, names: Sequence[str]) -> Tuple[Optional[bool], str]:
    """调用大模型之前，免费检查文件里有没有这家公司。返回 (True 找到 / False 没找到 / None 没法查, 说明)。

    只读有文字层的页（封面、报表页），不做 OCR；扫描件和照片没法预查，抽取后再核对公司名。
    """
    from cra.ingest.pdf_reader import find_statement_pages, read_pages

    if Path(path).suffix.lower() != ".pdf":
        return None, "照片无法预查公司名，抽取后再核对"
    pages = [p for p in read_pages(path) if not p.needs_ocr]
    if not pages:
        return None, "扫描件无法预查公司名，抽取后再核对"
    text = "\n".join(p.text for p in pages[:10] + find_statement_pages(pages))
    hit = company_in_text(text, names)
    if hit:
        return True, f"文件里找到了「{hit}」"
    return False, f"文件的封面和报表页里找不到「{'」「'.join(n for n in names if n)}」"


COMPANY_MATCH_SCORE = 60  # 报表上的公司名与案件客户名的相似度下限


def check_company(report_company: Optional[str], expected_names: Sequence[str]) -> List[Flag]:
    """报表上的公司名和案件客户对不上时提醒（坏例子：把 Huntsman 的 10-K 传进了 Harbor Polymer 的案件）。

    只提醒不拦截：用母公司合并报表做授信也很常见，由分析师判断。
    """
    from rapidfuzz import fuzz

    from cra.lookup.normalize import normalize_name

    names = [n for n in expected_names if n]
    if not report_company or not names:
        return []
    got = normalize_name(report_company)
    score = max(max(fuzz.token_set_ratio(got, normalize_name(n)), fuzz.partial_ratio(got, normalize_name(n)))
                for n in names)
    if score >= COMPANY_MATCH_SCORE:
        return []
    return [Flag("warn", "公司名称", f"报表上的公司是「{report_company}」，和案件客户「{names[0]}」对不上（相似度 {score:.0f}）。"
                                   f"请确认是否传错了文件；如果是母公司合并报表，请在意见里说明")]


def to_json(report: ExtractionReport) -> str:
    return json.dumps(
        {"pages_used": report.pages_used, "result": report.result.model_dump(),
         "flags": [f.__dict__ for f in report.flags]},
        ensure_ascii=False, indent=2,
    )
