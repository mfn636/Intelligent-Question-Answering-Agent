"""
rag/rerank.py

Rerank（精排）：对候选片段按与 query 的相关性重排。
生产可用 cross-encoder（如 bge-reranker）；此处复用现有 LLM 做"query+片段"交叉打分，零额外模型依赖。
失败时降级为保持原序，不阻塞检索。
"""

import json

_SYSTEM = "你是检索重排器，判断候选片段与用户查询的相关性，只输出 JSON。"

_PROMPT = """请对下面每个候选片段与用户查询的相关性打分（0~1，越高越相关）。

只输出 JSON：{{"scores":[{{"id":"候选id","score":0.0}}]}}

用户查询：{query}

候选片段：
{cands}
"""


class LLMReranker:
    def __init__(self, llm, max_cands: int = 10, snippet: int = 180):
        self.llm = llm
        self.max_cands = max_cands
        self.snippet = snippet

    def rerank(self, query, items, top_k: int = 5):
        if not items:
            return items
        cand = items[:self.max_cands]
        lines = [f'[{it["id"]}] {it["payload"].get("_text", "")[:self.snippet]}' for it in cand]
        try:
            resp = self.llm.chat(
                messages=[{"role": "system", "content": _SYSTEM},
                          {"role": "user", "content": _PROMPT.format(query=query, cands="\n".join(lines))}],
                temperature=0.0, max_tokens=800, thinking=False,
                response_format={"type": "json_object"},
            )
            scores = {s.get("id"): float(s.get("score", 0))
                      for s in json.loads(resp.content).get("scores", [])}
            cand.sort(key=lambda it: scores.get(it["id"], 0.0), reverse=True)
        except Exception:  # noqa: BLE001
            pass
        return cand[:top_k]


class CrossEncoderReranker:
    """使用调用方已加载的 cross-encoder 模型精排。"""

    def __init__(self, model, max_cands: int = 20):
        self.model = model
        self.max_cands = max_cands

    def rerank(self, query, items, top_k: int = 5):
        if not items:
            return items
        cand = items[:self.max_cands]
        pairs = [(query, it["payload"].get("_text", "")) for it in cand]
        try:
            scores = self.model.predict(pairs)
        except Exception:  # noqa: BLE001
            return cand[:top_k]
        for it, s in zip(cand, scores):
            it["rerank_score"] = float(s)
        cand.sort(key=lambda it: it.get("rerank_score", 0.0), reverse=True)
        return cand[:top_k]
