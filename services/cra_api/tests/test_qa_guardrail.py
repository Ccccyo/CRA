import json

from cra.ingest.chunker import Chunk
from cra.llm import FakeLLM
from cra.qa.answer import answer_question
from cra.qa.case_chat import answer_case_question
from cra.retrieve.hybrid import build_retriever


def _retriever():
    return build_retriever(
        [
            Chunk(
                "policy#0",
                "建议额度为 100 万元，必须由高级信用分析师复核。",
                "credit-policy.md",
                meta={"library_id": "policy"},
            )
        ],
        use_vector=False,
        use_rerank=False,
    )


def test_policy_qa_retrieves_again_and_rewrites_invalid_answer():
    llm = FakeLLM(
        [
            json.dumps({"found": True, "answer": "建议额度为 500 万元。"}),
            json.dumps(
                {
                    "found": True,
                    "answer": "建议额度为 100 万元，必须由高级信用分析师复核 [S1]。",
                }
            ),
        ]
    )

    result = answer_question("能不能批额度", _retriever(), llm)

    assert result.found is True
    assert result.blocked is False
    assert result.validation_attempts == 2
    assert result.citation_report.ok
    assert len(llm.calls) == 2
    assert "上一版回答没有通过程序校验" in llm.calls[1][-1]["content"]


def test_policy_qa_blocks_answer_when_second_validation_fails():
    invalid = json.dumps({"found": True, "answer": "建议额度为 500 万元。"})

    result = answer_question("能不能批额度", _retriever(), FakeLLM([invalid, invalid]))

    assert result.found is False
    assert result.blocked is True
    assert result.validation_attempts == 2
    assert "转人工复核" in result.answer
    assert "500" not in result.answer


def test_case_qa_blocks_invalid_answer_after_retry():
    invalid = json.dumps({"found": True, "answer": "客户额度为 500 万元。"})

    result = answer_case_question(
        "这个客户能批多少额度",
        {"案件数据": "客户申请额度 100 万元"},
        _retriever(),
        FakeLLM([invalid, invalid]),
    )

    assert result.blocked is True
    assert result.found is False
    assert result.validation_attempts == 2
    assert "转人工复核" in result.answer
