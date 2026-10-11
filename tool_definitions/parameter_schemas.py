"""工具调用参数：同时用于生成 JSON Schema 和校验模型传回的字典。

strict=True 拒绝类型自动转换，extra="forbid" 拒绝未声明的参数。
可选条件允许省略或传 null；文本一旦提供，必须包含非空白字符。
"""

from pydantic import BaseModel, ConfigDict, Field


class SearchProductsArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    category: str | None = Field(
        default=None, min_length=1, pattern=r"\S", description="商品类别，例如手机、笔记本电脑。",
    )
    budget: int | None = Field(
        default=None, gt=0, description="预算上限，单位为元，必须是正整数；省略表示不限预算。",
    )
    brand: str | None = Field(
        default=None, min_length=1, pattern=r"\S", description="品牌名称；省略表示不限品牌。",
    )
    keyword: str | None = Field(
        default=None, min_length=1, pattern=r"\S",
        description="对商品名称、类别和描述做不区分大小写的包含匹配，例如轻薄；请从需求中提取关键词，不要传整句需求。与 query 同时提供时优先使用 keyword。",
    )
    query: str | None = Field(
        default=None, min_length=1, pattern=r"\S",
        description="keyword 的兼容参数，同样进行关键词包含匹配；均省略时仅按其他条件筛选。",
    )


class GetProductDetailArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    product_id: str = Field(
        min_length=1, pattern=r"\S", description="要查询的商品 ID，应取自商品搜索等已有结果。",
    )
    keyword: str | None = Field(
        default=None, min_length=1, pattern=r"\S",
        description="可选关键词，对该商品的名称、类别和描述进行不区分大小写的包含匹配；不匹配时返回空数组。",
    )


class CheckInventoryArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    product_id: str | None = Field(
        default=None, min_length=1, pattern=r"\S", description="按商品 ID 查询库存。",
    )
    category: str | None = Field(
        default=None, min_length=1, pattern=r"\S",
        description="按商品类别筛选库存；可与 product_id 同时使用，均省略时查询全部库存。",
    )


class SearchKnowledgeArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    query: str = Field(
        min_length=1, pattern=r"\S", description="需要检索的自然语言问题或主题。",
    )
    category: str | None = Field(
        default=None, min_length=1, pattern=r"\S", description="可选的知识类别筛选条件。",
    )
    top_k: int = Field(
        default=5, ge=1, le=20, description="最多返回的知识片段数，范围为 1 至 20，默认 5。",
    )
