"""统一加载知识库、检索器和企业库，脚本和界面共用。"""
from typing import Collection, Optional

from cra.config import get_settings
from cra.ingest.chunker import load_markdown_dir
from cra.lookup.matcher import EntityMatcher, load_country_mapping, load_entities
from cra.retrieve.hybrid import HybridRetriever, build_retriever


PUBLIC_PREFIX = "公开资料/"
HISTORY_PREFIX = "历史申报/"


def knowledge_source_type(citation: str) -> str:
    """把引用归类到权限可识别的知识库。"""
    if citation.startswith(PUBLIC_PREFIX):
        return "public_reference"
    if citation.startswith(HISTORY_PREFIX):
        return "case_history"
    return "policy"


def knowledge_chunks():
    """三层知识：公司制度（data/policy）+ 公开资料（data/knowledge，出处前缀「公开资料/」）
    + 历史申报（data/history，一次申报一段，出处前缀「历史申报/」）。"""
    from cra.history import history_chunks

    data = get_settings().data_dir
    return (load_markdown_dir(data / "policy") + load_markdown_dir(data / "knowledge", prefix=PUBLIC_PREFIX)
            + history_chunks())


def policy_retriever(
    use_vector: Optional[bool] = None,
    use_rerank: Optional[bool] = None,
    allowed_libraries: Optional[Collection[str]] = None,
) -> HybridRetriever:
    chunks = knowledge_chunks()
    if allowed_libraries is not None:
        allowed = set(allowed_libraries)
        chunks = [
            chunk
            for chunk in chunks
            if knowledge_source_type(chunk.citation) in allowed
        ]
    return build_retriever(chunks, use_vector=use_vector, use_rerank=use_rerank)


def entity_matcher() -> EntityMatcher:
    data = get_settings().data_dir
    return EntityMatcher(load_entities(data / "entities" / "entities_mock.csv"),
                         load_country_mapping(data / "mapping" / "country_substitution.csv"))
