"""关键词检索（BM25）：负责「字面一样」的匹配，比如 EAPI030、ACCOUNT_DUNS_NBR、YT。

分词规则：英文、数字、下划线、& 连在一起当一个词（错误码、字段名、D&B 不能被拆开）；
中文用 jieba 搜索模式切词，并加载信用领域词典 data/mapping/credit_terms.txt：
「利息保障倍数」既作为整词索引，也保留「利息」「倍数」等子词，问整词和问局部都能命中。

IDF 用 Lucene/Elasticsearch 的平滑公式 log(1 + (N - n + 0.5) / (n + 0.5))，永远为正。
经典 Okapi 公式在「一个词出现在一半文档里」时权重为 0，小知识库里很常见，会导致明明命中却搜不到。
"""
import math
import re
from collections import Counter
from typing import List, Sequence

import jieba

from cra.ingest.chunker import Chunk
from cra.retrieve import Hit

from cra.config import get_settings

jieba.setLogLevel(60)  # 关掉 jieba 启动日志
_DICT = get_settings().data_dir / "mapping" / "credit_terms.txt"
if _DICT.exists():
    jieba.load_userdict(str(_DICT))

_TOKEN_RE = re.compile(r"[A-Za-z0-9_\.\-&]+|[一-鿿]+")
_STOPWORDS = {"的", "了", "是", "在", "和", "与", "或", "吗", "什么", "怎么", "如何", "为什么", "the", "a", "an", "of", "is", "to"}


def tokenize(text: str) -> List[str]:
    tokens: List[str] = []
    for piece in _TOKEN_RE.findall(text):
        if piece[0].isascii():
            tokens.append(piece.lower().strip(".-&"))
        else:
            tokens.extend(jieba.lcut_for_search(piece))
    return [t for t in tokens if t and t not in _STOPWORDS]


class KeywordIndex:
    K1 = 1.5
    B = 0.75

    def __init__(self, chunks: Sequence[Chunk]):
        self.chunks = list(chunks)
        self._docs = [Counter(tokenize(c.text)) for c in self.chunks]
        self._lens = [sum(d.values()) for d in self._docs]
        self._avg_len = sum(self._lens) / max(len(self._lens), 1)
        df = Counter(t for d in self._docs for t in d)
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def _score(self, query_tokens: List[str], i: int) -> float:
        doc, length, score = self._docs[i], self._lens[i], 0.0
        for t in query_tokens:
            tf = doc.get(t, 0)
            if tf:
                norm = tf + self.K1 * (1 - self.B + self.B * length / self._avg_len)
                score += self._idf[t] * tf * (self.K1 + 1) / norm
        return score

    def search(self, query: str, k: int = 20) -> List[Hit]:
        q = tokenize(query)
        scored = [(self._score(q, i), i) for i in range(len(self.chunks))]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [Hit(self.chunks[i], s) for s, i in scored[:k] if s > 0]
