"""读取业务 JSON，直接返回字典列表，不创建业务模型或转换字段类型。"""

import json
from pathlib import Path

from contract.data import EcommerceData


# 原始数据统一存放在项目根目录的 data/ 中。
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
PRODUCT_FILE = DATA_DIR / "products.json"
INVENTORY_FILE = DATA_DIR / "inventory.json"
FAQ_FILE = DATA_DIR / "faq.json"
GUIDES_FILE = DATA_DIR / "guides.json"


def load_ecommerce_data() -> EcommerceData:
    """加载当前工具所需的商品和库存；备用 FAQ 数据由调用方另行加载。"""
    return EcommerceData(
        products=load_products(),
        inventory=load_inventory(),
    )


def load_products() -> list[dict]:
    with open(PRODUCT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["products"]

def load_inventory() -> list[dict]:
    with open(INVENTORY_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def load_faq() -> list[dict]:
    """显式读取 FAQ，供知识入库或独立的备用查询使用。"""
    with open(FAQ_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def load_guides() -> list[dict]:
    """长文档（选购指南/政策/帮助）：返回原始 dict 列表（含 id/title/category/source/content）。"""
    with open(GUIDES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)
