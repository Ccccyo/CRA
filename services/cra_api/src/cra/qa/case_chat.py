"""案件内问答：在案件工作台右下角，回答「这个案件」的问题 —— 数字、打分怎么算出来的、制度怎么规定。

材料 = 本案材料（工作台当前状态：财报、D&B、打分明细、历史申报）+ 按问题检索的制度条文。
和制度答疑一样：只依据编号材料回答、逐句标出处、程序校验；算式只能用材料里的数字，结果由分析师核对。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from cra.llm import LLMClient, complete_json
from cra.qa.answer import NOT_FOUND, QAOutput
from cra.retrieve.hybrid import HybridRetriever
from cra.validate.citations import CitationReport, check_answer

SYSTEM = f"""你是信用分析助手，帮助分析师核对当前这个 CRA 案件。
规则：
1. 只依据下面编号的材料回答：本案材料（财报数字、D&B 信息、打分明细、历史申报）和制度条文。不用材料以外的知识，不猜测。
2. 每句话末尾标注来源编号，例如 [S2]；一句话用到多段就标多个。
3. 解释计算时，写出算式，只用材料里出现的数字；不要替分析师下结论或改分数。
4. 材料里没有答案时，found 为 false，answer 只写：{NOT_FOUND}
5. 出处以「公开资料/」开头的是外部公开资料，以「历史申报/」开头的是以往案例；两者只作参考，与公司制度冲突时以制度为准，不能把历史案例的做法当成规定。
6. 回答简洁，用中文。
7. 不向用户暴露 blocked、financial_evidence 等内部状态码、JSON 字段名或系统枚举；改用「已阻断」、「财务证据核验」等业务语言。
只输出 JSON：{{"found": true, "answer": "..."}}"""


@dataclass
class ChatAnswer:
    question: str
    found: bool
    answer: str
    sources: Dict[str, str]  # 编号 → 原文
    labels: Dict[str, str]  # 编号 → 出处名称
    citation_report: Optional[CitationReport] = None
    history: List[dict] = field(default_factory=list)


def collect_sources(question: str, case_materials: Dict[str, str], retriever: HybridRetriever,
                    k: int = 4) -> (Dict[str, str], Dict[str, str]):
    """本案材料在前（固定编号），制度检索结果在后。"""
    sources, labels = {}, {}
    for name, text in case_materials.items():
        sid = f"S{len(sources) + 1}"
        sources[sid], labels[sid] = text, f"本案 · {name}"
    for h in retriever.search(question, k=k):
        sid = f"S{len(sources) + 1}"
        tier = h.chunk.source.split("/")[0] if "/" in h.chunk.source else "制度"
        sources[sid], labels[sid] = h.chunk.text, f"{tier} · {h.chunk.citation.split('/', 1)[-1]}"
    return sources, labels


def answer_case_question(question: str, case_materials: Dict[str, str], retriever: HybridRetriever,
                         llm: LLMClient, history: Optional[List[dict]] = None,
                         model: Optional[str] = None) -> ChatAnswer:
    sources, labels = collect_sources(question, case_materials, retriever)
    material = "\n\n".join(f"[{sid}] 出处：{labels[sid]}\n{text}" for sid, text in sources.items())
    messages = [{"role": "system", "content": SYSTEM}]
    for turn in (history or [])[-4:]:  # 只带最近两轮，方便追问
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": f"材料：\n{material}\n\n问题：{question}"})
    out = complete_json(llm, messages, QAOutput, model=model)
    report = check_answer(out.answer, sources) if out.found else CitationReport()
    return ChatAnswer(question, out.found, out.answer, sources, labels, report)
