"""
tool_functions/knowledge_search.py

知识库语义检索（RAG）：FAQ / 指南 / 政策 / 帮助 统一检索。
"""

import json
from typing import Optional

from contract.retrieval import SearchFilter, SearchService

from rag.collections import COLLECTION_KNOWLEDGE


def search_knowledge(
    query: str,
    category: Optional[str] = None,
    top_k: int = 5,
    *,
    search_service: SearchService,
) -> str:
    """
    知识库语义检索工具。

    Args:
        query: 用户问题（自然语言）
        category: 可选，按知识类别过滤（如 退换货、支付）
        top_k: 返回条数上限

    Returns:
        知识片段列表的 JSON 字符串，无命中返回 "[]"。
    """
    query_filter = None
    if category:
        query_filter = SearchFilter(equals={"category": category})

    hits = search_service.search(
        COLLECTION_KNOWLEDGE, query, top_k=top_k,
        filters=query_filter,
    )

    items = []
    for h in hits:
        p = h.metadata
        items.append({
            "source": p.get("source", "knowledge"),
            "title": p.get("question") or p.get("title") or "",
            "content": h.text,
            "score": round(h.score, 4),
        })
    return json.dumps(items, ensure_ascii=False, allow_nan=False)
