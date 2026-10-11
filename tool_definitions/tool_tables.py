"""从同一份工具清单生成模型侧说明和本地查找表。

调用方提供已经准备好的业务数据，可选提供检索服务；本模块不加载数据，
不创建客户端，不执行查询。返回的两张表由调用方持有。
"""

from functools import partial

from contract.retrieval import SearchService
from tool_functions.inventory import check_inventory
from tool_functions.product_search import get_product_detail, search_products
from contract.data import EcommerceData, validate_ecommerce_data
from tool_definitions.parameter_schemas import (
    CheckInventoryArgs,
    GetProductDetailArgs,
    SearchKnowledgeArgs,
    SearchProductsArgs,
)


# * 要求按参数名传入；data 和 search_service 用于提前绑定本地函数的依赖，让后续调用只需传模型生成并校验后的业务参数。
# 商品和库存只绑定 data；search_service 仅供知识检索使用。
def build_tool_tables(
    *, data: EcommerceData, search_service: SearchService | None = None,
) -> tuple[list[dict], dict[str, dict]]:
    """默认构建三个本地数据工具；提供检索服务时加入知识检索工具。"""
    # 在绑定依赖前核验接入数据，避免只依靠类型标注或对象曾经通过构造检查。
    validate_ecommerce_data(data)
    tool_definitions = [
        {
            "name": "search_products",
            "description": "直接查询业务商品数据，按类别、预算、品牌及关键词筛选；关键词为文本包含匹配，不做语义检索。返回商品 JSON 数组，无匹配时返回空数组。",
            "args_model": SearchProductsArgs,
            "fn": partial(search_products, data=data),
        },
        {
            "name": "get_product_detail",
            "description": "根据已有商品 ID 获取商品详情和规格，可用 keyword 补充筛选商品名称、类别和描述；返回商品 JSON 数组，无匹配时返回空数组。",
            "args_model": GetProductDetailArgs,
            "fn": partial(get_product_detail, data=data),
        },
        {
            "name": "check_inventory",
            "description": "按商品 ID 或类别查询本地库存快照，返回库存数量和是否有货的 JSON 数组；无匹配时返回空数组。",
            "args_model": CheckInventoryArgs,
            "fn": partial(check_inventory, data=data),
        },
    ]

    if search_service is not None:
        from tool_functions.knowledge_search import search_knowledge

        tool_definitions.append({
            "name": "search_knowledge",
            "description": "检索 FAQ、选购指南、政策和帮助文档，适合需要详细依据的问题；返回包含来源、标题、正文与相关性分数的 JSON 数组，无命中时返回空数组。",
            "args_model": SearchKnowledgeArgs,
            "fn": partial(search_knowledge, search_service=search_service),
        })

    tool_schemas = []
    tool_lookup = {}

    for definition in tool_definitions:
        name = definition["name"]
        args_model = definition["args_model"]
        function = definition["fn"]

        if name in tool_lookup:
            raise ValueError(f"工具名称重复：{name}")

        parameters_schema = args_model.model_json_schema(mode="validation")
        tool_schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": definition["description"],
                "parameters": parameters_schema,
            },
        }
        tool_schemas.append(tool_schema)

        tool_lookup[name] = {
            "args_model": args_model,
            "fn": function,
        }

    return tool_schemas, tool_lookup
