"""
rag/store.py

Qdrant 本地向量库封装：建集合 / 幂等 upsert / 检索（带 payload 过滤）。
point id 用 chunk id 的确定性 UUID → 重复 ingest 幂等覆盖。
"""

import uuid
from pathlib import Path

from qdrant_client.models import Distance, PointStruct, VectorParams

DEFAULT_PATH = str(Path(__file__).resolve().parent / "qdrant_data")
_UUID_NS = uuid.uuid5(uuid.NAMESPACE_URL, "agent-harness/rag")


def _point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(_UUID_NS, chunk_id))


class VectorStore:
    def __init__(self, client):
        """接收已打开的 Qdrant 客户端；其生命周期由入口管理。"""
        self.client = client

    def ensure_collection(self, name: str, dim: int):
        if not self.client.collection_exists(name):
            self.client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )

    def recreate_collection(self, name: str, dim: int):
        """删除并重建集合（保证 ingest 是干净重建，不留旧块）。

        注意（Qdrant 本地模式）：同一进程内 delete+create 可能复用内存中的旧段，
        表现为旧点残留。因此**改动 chunk id 方案 / 向量维度后**，应先删除
        `rag/qdrant_data` 再 ingest；chunk id 稳定时 upsert 幂等，日常重跑无需担心。
        """
        if self.client.collection_exists(name):
            self.client.delete_collection(name)
        self.client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )

    def upsert(self, name: str, chunks, vectors) -> int:
        points = [
            PointStruct(id=_point_id(c["id"]), vector=v,
                        payload={**c["payload"], "_cid": c["id"], "_text": c["text"]})
            for c, v in zip(chunks, vectors)
        ]
        if points:
            self.client.upsert(collection_name=name, points=points)
        return len(points)

    def search(self, name, vector, top_k=5, score_threshold=None, query_filter=None):
        res = self.client.query_points(
            collection_name=name, query=vector, limit=top_k,
            score_threshold=score_threshold, query_filter=query_filter,
        )
        return [
            {"id": p.payload.get("_cid"), "payload": p.payload, "score": p.score}
            for p in res.points
        ]

    def count(self, name) -> int:
        return self.client.count(collection_name=name, exact=True).count

    def scroll_all(self, name: str, limit: int = 100000):
        """取出集合全部点的 (id, payload)，用于构建 BM25 关键词索引。"""
        points, _ = self.client.scroll(
            collection_name=name, limit=limit, with_payload=True, with_vectors=False,
        )
        return [{"id": p.payload.get("_cid"), "payload": p.payload} for p in points]

    def close(self):
        self.client.close()
