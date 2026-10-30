"""重排（bge-reranker-v2-m3）：把「问题 + 段落」放在一起判断相关性，从 20 条里挑出最准的几条。"""
from functools import lru_cache
from typing import List, Optional

from cra.config import get_settings
from cra.retrieve import Hit


@lru_cache(maxsize=4)
def reranker_model(model_name: str, device: Optional[str]):
    """Load each cross-encoder once per API process."""
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model_name, device=device)


class Reranker:
    def __init__(self, model_name: Optional[str] = None, model=None):
        from cra.retrieve.vector import _device

        resolved_model = model_name or get_settings().rerank_model
        self._model = model or reranker_model(resolved_model, _device())

    def rerank(self, query: str, hits: List[Hit], k: int = 5) -> List[Hit]:
        if not hits:
            return []
        scores = self._model.predict([(query, h.chunk.text) for h in hits])
        ranked = sorted(zip(hits, scores), key=lambda x: x[1], reverse=True)
        return [Hit(h.chunk, float(s)) for h, s in ranked[:k]]
