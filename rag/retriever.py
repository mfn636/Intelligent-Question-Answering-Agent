"""
rag/retriever.py

检索：支持 向量 / 混合（向量 + BM25，RRF 融合）/ 混合 + rerank。
"""

from rag.sparse import BM25


def _payload_match(payload, query_filter):
    """按 Qdrant Filter（field match / range）判断 payload 是否命中（供 BM25 路过滤）。"""
    if query_filter is None:
        return True
    for cond in (getattr(query_filter, "must", None) or []):
        key = getattr(cond, "key", None)
        if key is None:
            continue
        pv = payload.get(key)
        match = getattr(cond, "match", None)
        if match is not None and pv != getattr(match, "value", None):
            return False
        rng = getattr(cond, "range", None)
        if rng is not None:
            if rng.lte is not None and (pv is None or pv > rng.lte):
                return False
            if rng.gte is not None and (pv is None or pv < rng.gte):
                return False
    return True


def rrf_fuse(result_lists, k=60, top_k=20):
    """Reciprocal Rank Fusion：按名次融合多路检索结果（免分数归一化）。"""
    scores, keep = {}, {}
    for results in result_lists:
        for rank, it in enumerate(results, start=1):
            cid = it["id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            keep.setdefault(cid, it)
    ordered = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    out = []
    for cid, s in ordered[:top_k]:
        it = dict(keep[cid])
        it["score"] = s
        out.append(it)
    return out


class Retriever:
    RECALL_N = 20

    def __init__(self, store, embedder, reranker=None):
        self.store = store
        self.embedder = embedder
        self.reranker = reranker
        self._bm25 = {}

    def set_reranker(self, reranker):
        self.reranker = reranker

    def _get_bm25(self, collection) -> BM25:
        if collection not in self._bm25:
            docs = [(d["id"], d["payload"].get("_text", ""), d["payload"])
                    for d in self.store.scroll_all(collection)]
            self._bm25[collection] = BM25(docs)
        return self._bm25[collection]

    def retrieve(self, collection, query, top_k=5, score_threshold=0.3,
                 query_filter=None, mode="hybrid", rerank=False):
        """
        mode:  "vector" = 纯向量；"hybrid" = 向量 + BM25 经 RRF 融合
        rerank: True 时对候选做精排（需注入 reranker）
        """
        # 向量召回
        dense = self.store.search(collection, self.embedder.embed(query), top_k=self.RECALL_N,
                                  score_threshold=None, query_filter=query_filter)

        if mode == "vector":
            items = [it for it in dense if it["score"] >= score_threshold] or dense
            return items[:top_k]

        # 关键词召回（BM25），并按同一过滤条件后置过滤
        sparse = [it for it in self._get_bm25(collection).search(query, top_k=self.RECALL_N)
                  if _payload_match(it["payload"], query_filter)]

        fused = rrf_fuse([dense, sparse], k=60, top_k=max(top_k, self.RECALL_N))

        if rerank and self.reranker:
            fused = self.reranker.rerank(query, fused, top_k=top_k)

        return fused[:top_k]
