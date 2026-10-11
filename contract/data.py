"""电商数据容器契约：只校验列表及字典结构，不检查业务记录的内部字段。"""

from dataclasses import dataclass, field


@dataclass
class EcommerceData:
    """聚合业务字典并核验容器类型，未提供的数据集合默认为空列表。"""

    products: list[dict] = field(default_factory=list)
    inventory: list[dict] = field(default_factory=list)
    faq: list[dict] = field(default_factory=list)

    def __post_init__(self):
        validate_ecommerce_data(self)


def validate_ecommerce_data(data: EcommerceData) -> None:
    """只检查已存在的已声明字段类型；失败抛 TypeError，不转换或复制数据。

    缺少字段直接跳过，明确传入 None 仍属类型错误；空列表和空字典合法。
    不检查字段完整性，也不检查字典内部的 id、price 等业务字段。
    只核验调用时的数据，之后对列表或字段的修改不会自动触发校验。
    """
    for field_name in ("products", "inventory", "faq"):
        if not hasattr(data, field_name):
            continue
        records = getattr(data, field_name)

        if not isinstance(records, list):
            raise TypeError(
                f"数据接入失败：{field_name} 必须是 list，实际为 {type(records).__name__}"
            )

        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise TypeError(
                    f"数据接入失败：{field_name}[{index}] 必须是 dict，"
                    f"实际为 {type(record).__name__}"
                )
