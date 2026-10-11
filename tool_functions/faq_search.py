"""
tool_functions/faq_search.py

备用 FAQ 查询函数：只查询调用方提供的本地数据，不依赖 RAG。
当前未加入工具表；RAG 故障时的备用工具切换尚未实现。
"""

import json
from typing import Optional

from contract.data import EcommerceData


def search_faq(
    query: Optional[str] = None,
    category: Optional[str] = None,
    *,
    data: EcommerceData,
) -> str:
    """按类别和关键词筛选；不传条件时返回全部 FAQ，无匹配返回 JSON 字符串 "[]"。"""
    keywords = _extract_keywords(query) if query else []
    results = []
    for entry in data.faq:
        if category and category not in entry["category"]:
            continue
        if query:
            text = entry["question"] + entry["answer"]
            if not any(kw in text for kw in keywords):
                continue
        results.append(entry)
    return json.dumps(results, ensure_ascii=False, allow_nan=False)


def _extract_keywords(text: str) -> list[str]:
    result = [text]
    for i in range(len(text) - 1):
        chunk = text[i:i + 2]
        chunk = chunk.strip(" ?？！!，,。.")
        if chunk and len(chunk) == 2:
            result.append(chunk)
    return result
