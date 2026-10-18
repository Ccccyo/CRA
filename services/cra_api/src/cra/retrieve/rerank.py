"""重排（bge-reranker-v2-m3）：把「问题 + 段落」放在一起判断相关性，从 20 条里挑出最准的几条。"""
from typing import List, Optional

from cra.config import get_settings
from cra.retrieve import Hit


class Reranker:
    def __init__(self, model_name: Optional[str] = None):
        from sentence_transformers import CrossEncoder

        from cra.retrieve.vector import _device

        self._model = CrossEncoder(model_name or get_settings().rerank_model, device=_device())

    def rerank(self, query: str, hits: List[Hit], k: int = 5) -> List[Hit]:
        if not hits:
            return []
        scores = self._model.predict([(query, h.chunk.text) for h in hits])
        ranked = sorted(zip(hits, scores), key=lambda x: x[1], reverse=True)
        return [Hit(h.chunk, float(s)) for h, s in ranked[:k]]
