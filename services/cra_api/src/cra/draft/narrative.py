"""风险意见起草 + 定性评分建议（第 7 步，先搭骨架）。

输入是「案件事实」（已核对的财报数字、模拟 D&B 数据）和打分规则；输出必须逐句标号，数字必须能在材料里找到。
评分只能从规则里的档位中选，代码会校验。
"""
import json
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel

from cra.ingest.chunker import Chunk
from cra.llm import LLMClient, complete_json
from cra.retrieve.hybrid import HybridRetriever
from cra.validate.citations import CitationReport, check_answer

# 定性评分的合法档位（与 data/policy/cra_scoring_rules_mock.md 保持一致）
QUALITATIVE_OPTIONS: Dict[str, List[str]] = {
    "pay_habits": ["Prompt", "1 to 15 days arrear", "15 to 20 arrear", "Over 20 days arrear", "No history"],
    "company_type": ["Public listed", "Private - audited", "Private - unaudited", "Bankruptcy/Chapter 11"],
    "sovereign_rating": ["grade A or above", "grade B", "grade C or Not rated"],
    "customer_distinction": ["Priority - Above Average Margin", "Strategic", "Standard", "Not classified"],
}

SYSTEM = """你是信用分析助手，为分析师起草风险意见初稿。
规则：
1. 只依据编号材料写作；每句话末尾标注来源编号，如 [S1]。
2. 不得出现材料里没有的数字；不得使用「一定」「保证」「不会违约」等绝对化说法。
3. risk_comments 是给审批人看的，结论先行，按下面固定格式，总长不超过 300 字：
   【初步意见】支持 / 有条件支持 / 不支持：一句话理由 [S#]。申请额度 X 美元（上次批准 Y 美元，如有）[S#]；具体额度以 SAP 打分结果为准。
   【主要风险】最多 3 条，每条一句。
   【缓释因素】最多 3 条，每条一句。
   【建议条件】如要求担保、预付、季度报表等；没有就写「无」。
   只挑最关键的 1–2 个数字说明问题（优先写变化，如同比、和上次申报相比），不要逐年罗列财报数字。
   偿债、杠杆、盈利、付款记录、国家风险都要考虑到，材料不足的方面写「材料不足」。
   stance 填初步意见：支持 / 有条件支持 / 不支持（与 risk_comments 第一行一致）。
4. 定性评分 4 项都必须给出（pay_habits、company_type、sovereign_rating、customer_distinction），
   只能从给定选项中原样选择，并给出理由（理由也要标号）。
5. 出处以「公开资料/」开头的是外部公开资料，以「历史申报/」开头的是以往案例；两者只作参考，
   与公司制度冲突时以制度为准，不能把历史案例的做法当成规定。
只输出 JSON：
{"company_description":"...","stance":"有条件支持","risk_comments":"【初步意见】...\n【主要风险】...\n【缓释因素】...\n【建议条件】...","qualitative":{
  "pay_habits":{"choice":"...","reason":"..."},
  "company_type":{"choice":"...","reason":"..."},
  "sovereign_rating":{"choice":"...","reason":"..."},
  "customer_distinction":{"choice":"...","reason":"..."}}}"""


class Choice(BaseModel):
    choice: str
    reason: str


class Qualitative(BaseModel):
    """4 项都是必填：漏项会触发 complete_json 自动重试（坏例子 BC-0002：模型只给了 1 项）。"""
    pay_habits: Choice
    company_type: Choice
    sovereign_rating: Choice
    customer_distinction: Choice


class DraftOutput(BaseModel):
    company_description: str
    risk_comments: str
    stance: Optional[Literal["支持", "有条件支持", "不支持"]] = None  # 初步意见；额度由 SAP 打分后再定
    qualitative: Qualitative


# 信用政策禁止的绝对化表述
ABSOLUTE_WORDS = ["保证", "一定", "绝对", "不会违约", "毫无风险", "没有任何风险", "肯定"]


@dataclass
class DraftResult:
    output: DraftOutput
    sources: Dict[str, str]
    origins: Dict[str, str]
    citation_report: CitationReport
    invalid_choices: List[str]
    absolute_words: List[str]
    retrieval: List[Tuple[str, str]] = field(default_factory=list)  # (搜索词, 查到的出处)，界面展示「AI 查了哪些规定」

    @property
    def ok(self) -> bool:
        return self.citation_report.ok and not self.invalid_choices and not self.absolute_words


# 每个案件都要查的规定
BASE_QUERIES = ["定性分 Qualitative Score 权重 档位", "额度计算方法 Tangible Net Worth Net Worth"]
MAX_RULE_CHUNKS = 8  # 交给模型的规定段落上限，太多会稀释重点


def policy_queries(case, entity=None, values: Optional[Dict[str, float]] = None) -> List[str]:
    """看案件情况决定要查哪些规定（固定规则，不让模型自己决定，结果可预期、可审计）。

    case：casefile.Case；entity：确认的 D&B 公司；values：本年度数字（报表币种）。
    """
    from cra.countries import COUNTRY_ZH

    i = case.inputs
    country = case.customer["country"]
    queries = list(BASE_QUERIES)
    queries.append(f"{COUNTRY_ZH.get(country, '')} {country} 国家风险 Sovereign Rating")
    pay = (i.get("pay_habits_from_ar") or "").lower()
    if "arrear" in pay:
        queries.append("逾期 付款习惯 Pay Habits")
    elif "no history" in pay:
        queries.append("新客户 无付款记录 Pay Habits")
    hint = i.get("company_type_hint") or ""
    if "未审计" in hint or "unaudited" in hint.lower():
        queries.append("财报未审计 财报要求 Company Type")
    rating = entity.rating if entity else None
    queries.append(f"D&B 评级 {rating} 综合信用评估" if rating else "D&B 无评级 企业信用评级")
    if values:
        tnw = (values.get("total_equity") or 0) - (values.get("intangible_assets") or 0) - (values.get("goodwill") or 0)
        if values.get("total_equity") is not None and tnw <= 0:
            queries.append("有形净资产为负 额度方法")
    return queries  # 本客户的历史申报不靠检索，由调用方作为本案材料整份附上（检索会随机挑到旧记录）


def own_history_only(bp: str) -> Callable[[Chunk], bool]:
    """写意见时检索结果里不要别的客户的历史申报（坏例子：搜「墨西哥」曾查到一家智利公司的记录）。"""
    return lambda c: not c.source.startswith("历史申报/") or c.id.startswith(f"history#{bp}-")


def draft(case_facts: Dict[str, str], retriever: HybridRetriever, llm: LLMClient,
          model: Optional[str] = None, queries: Optional[List[str]] = None,
          keep: Optional[Callable[[Chunk], bool]] = None) -> DraftResult:
    """case_facts: {"财报摘要": "...", "D&B 信息": "...", ...}，每项作为一段编号材料。

    写意见需要推理，默认用 REASONER_MODEL（deepseek-reasoner）；传 model 可覆盖。
    """
    if model is None:
        from cra.config import get_settings
        model = get_settings().reasoner_model
    # 每个搜索词先各取第 1 段，有空位再取第 2 段：保证每个风险点至少有一段依据，不会被前面的搜索词挤掉
    per_query = [(q, retriever.search(q, k=2, keep=keep)) for q in (queries or BASE_QUERIES)]
    rule_hits, retrieval, seen = [], [], set()
    for rank in range(2):
        for q, hits in per_query:
            if rank < len(hits) and hits[rank].chunk.id not in seen and len(rule_hits) < MAX_RULE_CHUNKS:
                seen.add(hits[rank].chunk.id)
                rule_hits.append(hits[rank])
                retrieval.append((q, hits[rank].chunk.citation))
    sources: Dict[str, str] = {}
    origins: Dict[str, str] = {}  # 出处：本案材料 / 制度 / 公开资料 / 历史申报，让模型分清哪段是规定
    for name, text in case_facts.items():
        sid = f"S{len(sources) + 1}"
        sources[sid], origins[sid] = f"{name}\n{text}", "本案材料"
    for h in rule_hits:
        sid = f"S{len(sources) + 1}"
        sources[sid], origins[sid] = h.chunk.text, h.chunk.citation

    material = "\n\n".join(f"[{sid}] 出处：{origins[sid]}\n{text}" for sid, text in sources.items())
    options = json.dumps(QUALITATIVE_OPTIONS, ensure_ascii=False)
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"材料：\n{material}\n\n定性评分可选项：{options}"}]
    out = complete_json(llm, messages, DraftOutput, model=model)

    text = out.company_description + "\n" + out.risk_comments
    report = check_answer(text, sources)
    invalid = [k for k, opts in QUALITATIVE_OPTIONS.items() if getattr(out.qualitative, k).choice not in opts]
    absolute = [w for w in ABSOLUTE_WORDS if w in text]
    return DraftResult(out, sources, origins, report, invalid, absolute, retrieval)
