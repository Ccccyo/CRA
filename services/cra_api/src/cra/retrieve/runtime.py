"""Process-local retrieval cache and authorization-aware search views."""
from __future__ import annotations

from collections import OrderedDict
from datetime import date
from threading import RLock
from typing import Callable, Collection, Hashable, List, Optional, Sequence

from cra.ingest.chunker import Chunk
from cra.retrieve.hybrid import HybridRetriever, build_retriever


NON_RETRIEVABLE_STATUSES = {
    "staging", "draft", "needs_review", "needs_human_review",
    "withdrawn", "superseded", "archived",
}


def is_retrievable_knowledge(metadata: dict) -> bool:
    """Fail closed for explicit unpublished/withdrawn material.

    Legacy demo documents have no publication metadata; changing their default
    requires a separate, reviewed migration of the existing online catalog.
    """
    return (
        metadata.get("is_published") is not False
        and str(metadata.get("status") or "").lower() not in NON_RETRIEVABLE_STATUSES
    )


class QueryResultCache:
    """Small process cache shared by the short-lived authorization views."""

    def __init__(self, maxsize: int = 512) -> None:
        self.maxsize = maxsize
        self._lock = RLock()
        self._items: OrderedDict[Hashable, tuple] = OrderedDict()

    def get(self, key: Hashable) -> Optional[List]:
        with self._lock:
            value = self._items.get(key)
            if value is None:
                return None
            self._items.move_to_end(key)
            return list(value)

    def put(self, key: Hashable, value: Sequence) -> List:
        with self._lock:
            self._items[key] = tuple(value)
            self._items.move_to_end(key)
            while len(self._items) > self.maxsize:
                self._items.popitem(last=False)
        return list(value)


query_result_cache = QueryResultCache()


class RetrieverCache:
    """Build one shared index per knowledge revision inside an API process."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._key: Optional[Hashable] = None
        self._retriever: Optional[HybridRetriever] = None

    def get(
        self,
        key: Hashable,
        load_chunks: Callable[[], Sequence[Chunk]],
        *,
        use_vector: Optional[bool] = None,
        use_rerank: Optional[bool] = None,
    ) -> HybridRetriever:
        with self._lock:
            if self._key == key and self._retriever is not None:
                return self._retriever
            self._retriever = build_retriever(
                load_chunks(),
                use_vector=use_vector,
                use_rerank=use_rerank,
            )
            self._key = key
            return self._retriever

    def clear(self) -> None:
        with self._lock:
            self._key = None
            self._retriever = None


class ScopedRetriever:
    """A cheap permission/case-filtered view over the shared process index."""

    def __init__(
        self,
        retriever: HybridRetriever,
        allowed_libraries: Collection[str],
        *,
        case_id: str = "",
        case_only: bool = False,
        review_date: str = "",
        credit_segment: str = "",
        region: str = "",
        bp: str = "",
        demo_release_id: str = "",
        demo_case: bool = False,
    ) -> None:
        self._retriever = retriever
        self.allowed_libraries = frozenset(allowed_libraries)
        self.case_id = case_id
        self.case_only = case_only
        self.review_date = review_date
        self.credit_segment = credit_segment
        self.region = region
        self.bp = bp
        self.demo_release_id = demo_release_id
        self.demo_case = demo_case
        # Evaluation and diagnostics inspect the keyword corpus.
        self.keyword = retriever.keyword

    def _allowed(self, chunk: Chunk) -> bool:
        meta = chunk.meta
        if not is_retrievable_knowledge(meta):
            return False
        library_id = str(meta.get("library_id") or "")
        if library_id in {"proposed_rule", "evidence_spec"} and not (
            self.demo_release_id
            and self.demo_case
            and meta.get("demo_release_id") == self.demo_release_id
            and meta.get("demo_only") is True
            and meta.get("is_published") is True
            and meta.get("authority") == "project_proposal_not_approved_policy"
        ):
            return False
        if library_id == "case_evidence":
            return bool(self.case_id) and meta.get("case_id") == self.case_id
        if self.case_only:
            return False
        if library_id not in self.allowed_libraries:
            return False
        if self.demo_release_id:
            if self.demo_case:
                if (
                    meta.get("demo_release_id") != self.demo_release_id
                    or meta.get("demo_only") is not True
                    or meta.get("is_published") is not True
                ):
                    return False
            elif (
                meta.get("is_published") is not True
                or meta.get("is_mock") is not False
                or meta.get("demo_only") is True
            ):
                return False
        if library_id == "case_history":
            if meta.get("source_kind") == "completed_case_archive" and not (
                self.demo_release_id
                and self.demo_case
                and meta.get("demo_release_id") == self.demo_release_id
                and meta.get("demo_only") is True
                and meta.get("is_mock") is True
                and meta.get("is_published") is True
                and meta.get("workflow_status") == "completed"
                and meta.get("authority") == "derived_completed_case_not_approval"
            ):
                return False
            if self.demo_release_id and not meta.get("bp"):
                return False
            if meta.get("bp") and (not self.bp or meta["bp"] != self.bp):
                return False
        if library_id == "policy":
            if not self.review_date:
                return False if self.demo_release_id else True
            try:
                date.fromisoformat(self.review_date)
            except ValueError:
                return False
            for field in ("effective_from", "effective_to"):
                if meta.get(field):
                    try:
                        date.fromisoformat(meta[field])
                    except (TypeError, ValueError):
                        return False
            if meta.get("effective_from") and self.review_date < meta["effective_from"]:
                return False
            if meta.get("effective_to") and self.review_date > meta["effective_to"]:
                return False
            if meta.get("credit_segment") and meta["credit_segment"] != self.credit_segment:
                return False
            if meta.get("region") and meta["region"] not in {self.region, "ALL_DEMO"}:
                return False
        return True

    def _where(self) -> Optional[dict]:
        if self.case_only:
            return {
                "$and": [
                    {"library_id": "case_evidence"},
                    {"case_id": self.case_id},
                ]
            }
        libraries = sorted(self.allowed_libraries)
        if self.demo_release_id:
            shared_filter = {
                "$and": [
                    {"library_id": {"$in": libraries}},
                    {"demo_release_id": self.demo_release_id},
                ]
            } if self.demo_case else {
                "$and": [
                    {"library_id": {"$in": libraries}},
                    {"is_published": True},
                    {"is_mock": False},
                ]
            }
            if not self.case_id:
                return shared_filter
            return {"$or": [shared_filter, {"$and": [
                {"library_id": "case_evidence"}, {"case_id": self.case_id},
            ]}]}
        if self.case_id:
            return {
                "$or": [
                    {"library_id": {"$in": libraries}},
                    {
                        "$and": [
                            {"library_id": "case_evidence"},
                            {"case_id": self.case_id},
                        ]
                    },
                ]
            }
        if not libraries:
            return {"library_id": "__none__"}
        return {"library_id": {"$in": libraries}}

    def _combine(
        self, keep: Optional[Callable[[Chunk], bool]]
    ) -> Callable[[Chunk], bool]:
        return lambda chunk: self._allowed(chunk) and (
            keep(chunk) if keep is not None else True
        )

    def _cache_key(self, operation: str, query: str, *sizes: int) -> tuple:
        return (
            self._retriever,
            operation,
            query,
            sizes,
            tuple(sorted(self.allowed_libraries)),
            self.case_id,
            self.case_only,
            self.review_date,
            self.credit_segment,
            self.region,
            self.bp,
            self.demo_release_id,
            self.demo_case,
        )

    def candidates(
        self,
        query: str,
        n: int = 20,
        keep: Optional[Callable[[Chunk], bool]] = None,
    ):
        key = self._cache_key("candidates", query, n)
        if keep is None:
            cached = query_result_cache.get(key)
            if cached is not None:
                return cached
        candidates = self._retriever.candidates(
            query,
            n,
            keep=self._combine(keep),
            where=self._where(),
        )
        candidates = [
            hit for hit in candidates if self._combine(keep)(hit.chunk)
        ][:n]
        return query_result_cache.put(key, candidates) if keep is None else candidates

    def search(
        self,
        query: str,
        k: int = 5,
        n_candidates: int = 20,
        keep: Optional[Callable[[Chunk], bool]] = None,
    ):
        key = self._cache_key("search", query, k, n_candidates)
        if keep is None:
            cached = query_result_cache.get(key)
            if cached is not None:
                return cached
        hits = self._retriever.search(
            query,
            k,
            n_candidates,
            keep=self._combine(keep),
            where=self._where(),
        )
        return query_result_cache.put(key, hits) if keep is None else hits


shared_retriever_cache = RetrieverCache()
