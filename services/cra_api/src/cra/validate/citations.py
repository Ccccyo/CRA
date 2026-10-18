"""出处校验：DeepSeek 没有内置引用功能，所以自己检查。

1. 回答里的 [S#] 编号必须真实存在；
2. 每句话都应带编号；
3. 回答里出现的数字必须能在它引用的材料里找到；
4. 抽取出的「原文」必须能在声称的那一页里找到。
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Set

from rapidfuzz import fuzz

CITE_RE = re.compile(r"\[S(\d+)\]")
NUM_RE = re.compile(r"\(?-?\d[\d,]*\.?\d*\)?%?")
SENTENCE_RE = re.compile(r"[^。！？!?\n]+[。！？!?]?")


def parse_number(token: str) -> float:
    """'(1,234.5)' → -1234.5；'12%' → 12.0"""
    neg = token.startswith("(") and token.endswith(")") or token.startswith("-")
    digits = re.sub(r"[^\d.]", "", token)
    value = float(digits) if digits and digits != "." else 0.0
    return -value if neg else value


def _looks_like_year(tok: str) -> bool:
    """只有不带千分位的四位整数才可能是年份：「2024」是年份，「1,900」是金额。"""
    return bool(re.fullmatch(r"(19|20)\d\d", tok))


def numbers_in(text: str) -> Set[float]:
    """提取有意义的数字：忽略引用编号、个位数和年份。"""
    text = CITE_RE.sub("", text)
    out = set()
    for tok in NUM_RE.findall(text):
        n = abs(parse_number(tok))
        if n < 10 or _looks_like_year(tok):
            continue
        out.add(round(n, 2))
    return out


@dataclass
class CitationReport:
    unknown_ids: List[str] = field(default_factory=list)
    uncited_sentences: List[str] = field(default_factory=list)
    unsupported_numbers: List[float] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.unknown_ids or self.uncited_sentences or self.unsupported_numbers)


TRAILING_CITE_RE = re.compile(r"([。！？!?])\s*((?:\[S\d+\]\s*)+)")


def normalize_citations(answer: str) -> str:
    """「……。[S2]」→「……[S2]。」：标在句号后面的引用归到前一句（坏例子 BC-0003）。"""
    return TRAILING_CITE_RE.sub(lambda m: m.group(2).strip() + m.group(1), answer)


def check_answer(answer: str, sources: Dict[str, str]) -> CitationReport:
    """sources: {"S1": 段落文字, ...}"""
    answer = normalize_citations(answer)
    report = CitationReport()
    cited = {f"S{n}" for n in CITE_RE.findall(answer)}
    report.unknown_ids = sorted(cited - sources.keys())

    unsupported: Set[float] = set()
    for sent in SENTENCE_RE.findall(answer):
        sent_ids = {f"S{n}" for n in CITE_RE.findall(sent)}
        if len(sent.strip()) >= 8 and not sent_ids:
            report.uncited_sentences.append(sent.strip())
        # 逐句核对：这句话里的数字只能来自这句话引用的材料（不能借用别的句子的出处）
        pool: Set[float] = set()
        for sid in sent_ids & sources.keys():
            pool |= numbers_in(sources[sid])
        unsupported |= numbers_in(sent) - pool
    report.unsupported_numbers = sorted(unsupported)
    return report


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def source_text_on_page(snippet: str, page_text: str, threshold: float = 90.0) -> bool:
    """抽取结果声称的原文是否真在这一页上。

    文字允许轻微差异（OCR、换行），但数字必须一字不差：2,500 和 2,000 只差一个字符，模糊匹配会误判为相同。
    """
    if not snippet:
        return False
    a, b = _squash(snippet), _squash(page_text)
    if a in b:
        return True
    return fuzz.partial_ratio(a, b) >= threshold and numbers_in(snippet) <= numbers_in(page_text)
