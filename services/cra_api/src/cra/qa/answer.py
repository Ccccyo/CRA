"""制度答疑（RAG）：检索 5 段材料 → 编号 [S1]..[S5] → 模型只依据材料回答并逐句标号 → 程序校验出处。"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from pydantic import BaseModel

from cra.llm import LLMClient, complete_json
from cra.retrieve import Hit
from cra.retrieve.hybrid import HybridRetriever
from cra.validate.citations import CitationReport, check_answer

NOT_FOUND = "制度中未找到相关规定。"

SYSTEM = f"""你是信用分析部门的制度答疑助手。
规则：
1. 只依据下面编号的材料回答，不使用材料以外的知识，不猜测。
2. 每句话末尾标注来源编号，例如 [S2]；一句话用到多段就标多个。
3. 材料里没有答案时，found 为 false，answer 只写：{NOT_FOUND}
4. 出处以「公开资料/」开头的是外部公开资料，以「历史申报/」开头的是以往案例；两者只作参考，与公司制度冲突时以制度为准，不能把历史案例的做法当成规定，并说明两者不同。
5. 回答简洁，用中文。
只输出 JSON：{{"found": true, "answer": "..."}}"""


class QAOutput(BaseModel):
    found: bool
    answer: str


@dataclass
class QAResult:
    question: str
    found: bool
    answer: str
    hits: List[Hit]
    citation_report: Optional[CitationReport] = None
    sources: Dict[str, str] = field(default_factory=dict)


def format_sources(hits: List[Hit]) -> Dict[str, str]:
    return {f"S{i + 1}": h.chunk.text for i, h in enumerate(hits)}


def answer_question(question: str, retriever: HybridRetriever, llm: LLMClient,
                    k: int = 5, model: Optional[str] = None) -> QAResult:
    hits = retriever.search(question, k=k)
    sources = format_sources(hits)
    if not hits:
        return QAResult(question, False, NOT_FOUND, hits, CitationReport(), sources)

    material = "\n\n".join(f"[{sid}] 出处：{h.chunk.citation}\n{text}"
                           for (sid, text), h in zip(sources.items(), hits))
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"材料：\n{material}\n\n问题：{question}"}]
    out = complete_json(llm, messages, QAOutput, model=model)
    report = check_answer(out.answer, sources) if out.found else CitationReport()
    return QAResult(question, out.found, out.answer, hits, report, sources)
