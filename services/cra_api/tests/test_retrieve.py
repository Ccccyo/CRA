from cra.ingest.chunker import Chunk, chunk_markdown
from cra.resources import policy_retriever
from cra.retrieve import Hit
from cra.retrieve.hybrid import rrf
from cra.retrieve.keyword import KeywordIndex, tokenize


def test_tokenize_keeps_codes_intact():
    tokens = tokenize("接口报错 EAPI030，字段 ACCOUNT_DUNS_NBR")
    assert "eapi030" in tokens
    assert "account_duns_nbr" in tokens


def test_chunk_markdown_keeps_heading_path():
    chunks = chunk_markdown("# A\n## B\n内容一\n\n## C\n内容二", source="x.md")
    assert [c.section for c in chunks] == ["A > B", "A > C"]
    assert chunks[0].text.startswith("A > B")


def test_chunk_markdown_splits_a_single_oversized_paragraph():
    chunks = chunk_markdown("长" * 2500, source="long.md", max_chars=1200)

    assert len(chunks) == 3
    assert all(len(chunk.text) <= 1200 for chunk in chunks)


def test_keyword_exact_code_beats_similar_code():
    chunks = [Chunk("1", "EAPI030 凭证错误", "a.md"), Chunk("2", "EAPI135 内容为空", "a.md")]
    hits = KeywordIndex(chunks).search("EAPI030 怎么办")
    assert hits[0].chunk.id == "1"


def test_rrf_rewards_agreement():
    a, b, c = (Chunk(i, i, "s") for i in "abc")
    fused = rrf([[Hit(a, 9), Hit(b, 8)], [Hit(b, 0.9), Hit(c, 0.8)]])
    assert fused[0].chunk.id == "b"


def test_policy_retriever_keyword_only_finds_merge_rule():
    retriever = policy_retriever(use_vector=False, use_rerank=False)
    hits = retriever.search("Merge 返回 200 OK 是不是成功", k=5)
    assert any(h.chunk.source == "dnb_api_guide_mock.md" for h in hits)


def test_history_is_retrievable_and_fingerprint_tracks_content():
    from cra.ingest.chunker import Chunk
    from cra.retrieve.vector import fingerprint

    retriever = policy_retriever(use_vector=False, use_rerank=False)
    hits = retriever.search("有没有主体未确认、不授信改预付款的历史案例？", k=5)
    assert any(h.chunk.source.startswith("历史申报/") and "Corvel" in h.chunk.text for h in hits)
    a = [Chunk("x#0", "额度 100", "x.md")]
    assert fingerprint(a) != fingerprint([Chunk("x#0", "额度 200", "x.md")])  # 段数不变、内容变了也要重建


def test_tokenize_keeps_dnb_and_domain_terms():
    from cra.retrieve.keyword import tokenize

    assert tokenize("D&B 评级 3A2") == ["d&b", "评级", "3a2"]  # 回归：D&B 曾被切成 d、b
    toks = tokenize("利息保障倍数下降")
    assert "利息保障倍数" in toks and "倍数" in toks  # 整词和子词都在，问整词、问局部都能命中
