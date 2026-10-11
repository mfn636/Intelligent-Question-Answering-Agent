"""
rag/ingest.py

知识库离线入库入口：加载 FAQ 与长文档、分块、计算向量并重建知识集合。
CLI：python -m rag.ingest
"""

from dataclasses import dataclass
from contextlib import ExitStack
from typing import Callable

from rag.collections import COLLECTION_KNOWLEDGE
from rag.ecommerce_chunks import chunk_faq, chunk_guides
from rag.embedder import EMBED_DIM
from storage.json_loader import load_faq, load_guides


@dataclass
class CollectionSpec:
    """一个入库集合：集合名与生成检索片段的函数。"""

    name: str
    build_chunks: Callable[[], list]


def build_collection_specs() -> list[CollectionSpec]:
    """只收录知识数据；调用 build_chunks 时才读取 FAQ 和长文档源文件。"""
    return [
        CollectionSpec(
            COLLECTION_KNOWLEDGE,
            lambda: chunk_faq(load_faq()) + chunk_guides(load_guides()),
        ),
    ]


def ingest(collections: list[CollectionSpec], *, store, embedder) -> dict:
    """使用调用方提供的集合和依赖入库；不创建或关闭外部资源。"""
    result = {}
    for col in collections:
        chunks = col.build_chunks()
        store.recreate_collection(col.name, EMBED_DIM)
        store.upsert(col.name, chunks, embedder.embed([c["text"] for c in chunks]))
        result[col.name] = store.count(col.name)
    return result


def main():
    """离线组合根：选择数据、创建向量依赖，并负责关闭客户端。"""
    from qdrant_client import QdrantClient
    from rag.embedder import Embedder
    from rag.store import DEFAULT_PATH, VectorStore

    with ExitStack() as resources:
        client = QdrantClient(path=DEFAULT_PATH)
        resources.callback(client.close)
        result = ingest(build_collection_specs(), store=VectorStore(client), embedder=Embedder())
        print("ingest ->", result)


if __name__ == "__main__":
    main()
