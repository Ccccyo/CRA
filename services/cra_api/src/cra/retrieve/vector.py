"""向量检索（bge-m3 + Chroma）：负责「意思相近」的匹配。

需要 requirements-ml.txt；没装时 is_available() 返回 False，系统自动退化为只用关键词检索。
"""
import hashlib
from pathlib import Path
from typing import List, Optional, Sequence

from cra.config import get_settings
from cra.ingest.chunker import Chunk
from cra.retrieve import Hit

COLLECTION = "cra_knowledge"


def fingerprint(chunks: Sequence[Chunk]) -> str:
    """段落内容的指纹：任何一段的编号或文字变了，指纹就变，索引需要重建。"""
    h = hashlib.sha256()
    for c in chunks:
        h.update(c.id.encode("utf-8") + b"\0" + c.text.encode("utf-8") + b"\0")
    return h.hexdigest()


def is_available() -> bool:
    try:
        import chromadb  # noqa: F401
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


def _device() -> Optional[str]:
    """Apple 芯片用 GPU（MPS），否则交给 sentence-transformers 自己选。"""
    try:
        import torch
        return "mps" if torch.backends.mps.is_available() else None
    except ImportError:
        return None


class VectorIndex:
    def __init__(self, index_dir: Optional[Path] = None, model_name: Optional[str] = None,
                 collection: str = COLLECTION, model=None):
        import chromadb
        from sentence_transformers import SentenceTransformer

        settings = get_settings()
        self._model = model or SentenceTransformer(model_name or settings.embed_model, device=_device())
        self._dir = Path(index_dir or settings.index_dir / "chroma")
        client = chromadb.PersistentClient(path=str(self._dir))
        self._col = client.get_or_create_collection(collection, metadata={"hnsw:space": "cosine"})
        self._fp_file = self._dir / f"fingerprint_{collection}.txt"

    def count(self) -> int:
        return self._col.count()

    def is_current(self, chunks: Sequence[Chunk]) -> bool:
        return self._fp_file.exists() and self._fp_file.read_text() == fingerprint(chunks)

    def _embed(self, texts: Sequence[str]) -> List[List[float]]:
        return self._model.encode(list(texts), normalize_embeddings=True, batch_size=16).tolist()

    def build(self, chunks: Sequence[Chunk], batch: int = 256) -> None:
        """全量重建：先清空再写入，避免旧段落残留。标签（meta）一起存，检索时可以按公司、年份过滤。"""
        existing = self._col.get()["ids"]
        if existing:
            self._col.delete(ids=existing)
        for i in range(0, len(chunks), batch):  # 分批写入，Chroma 单次写入有上限
            part = chunks[i:i + batch]
            self._col.add(
                ids=[c.id for c in part],
                embeddings=self._embed([c.text for c in part]),
                documents=[c.text for c in part],
                metadatas=[{"source": c.source, "section": c.section, "page": c.page or 0, **c.meta} for c in part],
            )
        self._fp_file.write_text(fingerprint(chunks))

    def search(self, query: str, k: int = 20, where: Optional[dict] = None) -> List[Hit]:
        """where：按标签过滤，如 {"ticker": "DOW"} 只查这家公司。"""
        res = self._col.query(query_embeddings=self._embed([query]), n_results=k, where=where)
        hits = []
        base = {"source", "section", "page"}
        for cid, doc, meta, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]):
            chunk = Chunk(id=cid, text=doc, source=meta["source"], section=meta["section"],
                          page=meta["page"] or None, meta={k2: v for k2, v in meta.items() if k2 not in base})
            hits.append(Hit(chunk, 1.0 - float(dist)))
        return hits
