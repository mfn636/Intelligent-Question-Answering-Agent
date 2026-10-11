"""
tool_functions/product_search.py

直接查询业务商品数据：按类别、预算、品牌和关键词筛选。
"""

import json
from typing import Optional

from contract.data import EcommerceData


def search_products(
    category: Optional[str] = None,
    budget: Optional[int] = None,
    brand: Optional[str] = None,
    keyword: Optional[str] = None,
    query: Optional[str] = None,
    *,
    data: EcommerceData,
) -> str:
    """
    所有条件同时满足才返回；关键词对名称、类别和描述做不区分大小写的包含匹配。
    keyword 优先于 query；均省略时只按其他条件筛选。保持业务数据顺序，返回 JSON 数组。
    """
    text_query = keyword or query
    results = []
    for product in data.products:
        if category and category not in product["category"]:
            continue
        if budget is not None and product["price"] > budget:
            continue
        if brand and brand.lower() not in product["brand"].lower():
            continue
        if text_query:
            text = product["name"] + product["category"] + product["description"]
            if text_query.lower() not in text.lower():
                continue
        results.append(product)
    return json.dumps(results, ensure_ascii=False, allow_nan=False)


def get_product_detail(
    product_id: Optional[str] = None,
    *,
    keyword: Optional[str] = None,
    data: EcommerceData,
) -> str:
    """按 ID 查询详情，可用关键词补充筛选名称、类别和描述；无匹配返回 "[]"。"""
    product_map = {product["id"]: product for product in data.products}
    product = product_map.get(product_id) if product_id else None
    if product and keyword:
        text = product["name"] + product["category"] + product["description"]
        if keyword.lower() not in text.lower():
            product = None
    return json.dumps([product] if product else [], ensure_ascii=False, allow_nan=False)
