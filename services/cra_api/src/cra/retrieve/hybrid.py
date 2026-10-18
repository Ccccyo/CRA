"""混合检索：关键词 + 向量两路召回 → RRF 合并 → （可选）重排。

RRF（倒数排名融合）只看名次不看分数，两路分数尺度不同也能直接合并。
"""
from typing import Callable, Dict, List, Optional, Sequence

from cra.ingest.chunker import Chunk

from cra.retrieve import Hit
from cra.retrieve.keyword import KeywordIndex


def rrf(result_lists: Sequence[List[Hit]], k: int = 60) -> List[Hit]:
    scores: Dict[str, float] = {}
    chunks = {}
    for hits in result_lists:
        for rank, hit in enumerate(hits):
            scores[hit.chunk.id] = scores.get(hit.chunk.id, 0.0) + 1.0 / (k + rank + 1)
            chunks.setdefault(hit.chunk.id, hit.chunk)
    ranked = sorted(scores, key=scores.get, reverse=True)
    return [Hit(chunks[cid], scores[cid]) for cid in ranked]


class HybridRetriever:
    def __init__(self, keyword: KeywordIndex, vector=None, reranker=None):
        self.keyword = keyword
        self.vector = vector
        self.reranker = reranker

    def candidates(self, query: str, n: int = 20) -> List[Hit]:
        lists = [self.keyword.search(query, n)]
        if self.vector is not None:
            lists.append(self.vector.search(query, n))
        return rrf(lists)[:n]

    def search(self, query: str, k: int = 5, n_candidates: int = 20,
               keep: Optional[Callable[[Chunk], bool]] = None) -> List[Hit]:
        """keep：只保留符合条件的段落，例如写意见时历史申报只要本客户的。"""
        cands = self.candidates(query, n_candidates)
        if keep is not None:
            cands = [h for h in cands if keep(h.chunk)]
        if self.reranker is not None:
            return self.reranker.rerank(query, cands, k)
        return cands[:k]


def build_retriever(chunks, use_vector: Optional[bool] = None, use_rerank: Optional[bool] = None) -> HybridRetriever:
    """默认：装了 ML 依赖就用向量 + 重排，没装就只用关键词。"""
    from cra.retrieve import vector as vector_mod

    available = vector_mod.is_available()
    use_vector = available if use_vector is None else use_vector
    use_rerank = available if use_rerank is None else use_rerank

    vec = rer = None
    if use_vector:
        vec = vector_mod.VectorIndex()
        if not vec.is_current(chunks):  # 内容变了（不只是段数变了）就重建
            vec.build(chunks)
    if use_rerank:
        from cra.retrieve.rerank import Reranker

        rer = Reranker()
    return HybridRetriever(KeywordIndex(chunks), vec, rer)
