"""检索契约的 RAG 适配器：隔离 Qdrant 条件与内部 payload 格式。"""

from typing import TYPE_CHECKING, Callable

from contract.retrieval import SearchFilter, SearchHit, SearchService

if TYPE_CHECKING:
    from rag.retriever import Retriever


class RagSearchAdapter:
    def __init__(self, retriever: "Retriever"):
        """只适配已注入的检索器，不负责创建依赖或关闭资源。"""
        self._retriever = retriever

    @staticmethod
    def _build_filter(filters: SearchFilter | None):
        if filters is None or not (filters.equals or filters.less_than_or_equal):
            return None

        from qdrant_client.models import FieldCondition, Filter, MatchValue, Range

        must = [
            FieldCondition(key=key, match=MatchValue(value=value))
            for key, value in filters.equals.items()
        ]
        must.extend(
            FieldCondition(key=key, range=Range(lte=value))
            for key, value in filters.less_than_or_equal.items()
        )
        return Filter(must=must)

    def search(
        self,
        collection: str,
        query: str,
        filters: SearchFilter | None = None,
        top_k: int = 5,
    ) -> list[SearchHit]:
        query_filter = self._build_filter(filters)
        hits = self._retriever.retrieve(
            collection, query, top_k=top_k,
            score_threshold=0.3, query_filter=query_filter,
        )
        return [
            SearchHit(
                id=hit["id"],
                text=hit["payload"].get("_text", ""),
                metadata={key: value for key, value in hit["payload"].items()
                          if key not in ("_text", "_cid")},
                score=float(hit["score"]),
            )
            for hit in hits
        ]


class LazySearchService:
    """首次查询时调用注入的工厂；具体依赖与资源归工厂的创建方管理。"""

    def __init__(self, factory: Callable[[], SearchService]):
        self._factory = factory
        self._service = None
        self._closed = False

    def search(self, collection, query, filters=None, top_k=5) -> list[SearchHit]:
        if self._closed:
            raise RuntimeError("检索服务所属的应用已关闭")
        if self._service is None:
            self._service = self._factory()
        return self._service.search(collection, query, filters=filters, top_k=top_k)

    def close(self):
        # 不关闭借用的服务；这里只阻止关闭后再次延迟创建资源。
        self._closed = True
