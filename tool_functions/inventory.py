import json
from typing import Optional

from contract.data import EcommerceData


def check_inventory(
    product_id: Optional[str] = None,
    category: Optional[str] = None,
    *,
    data: EcommerceData,
) -> str:
    """返回库存查询结果的 JSON 数组字符串，无匹配返回 "[]"。"""
    product_map = {product["id"]: product for product in data.products}
    results = []

    for item in data.inventory:
        pid = item["product_id"]
        product = product_map.get(pid)
        if not product:
            continue

        if product_id and pid != product_id:
            continue

        if category and category not in product["category"]:
            continue

        results.append({
            "product_id": pid,
            "product_name": product["name"],
            "stock": item["stock"],
            "in_stock": item["stock"] > 0,
        })

    return json.dumps(results, ensure_ascii=False, allow_nan=False)
