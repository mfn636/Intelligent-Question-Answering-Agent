"""单商品库存、价格和通用参数查询；每次至多调用一个工具。"""

import json
import math
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from contract.tool import ToolProvider


class SingleProductArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    product_id: str = Field(pattern=r"^[A-Za-z]{1,8}[0-9]{3,12}$")


WORKFLOW_TOOLS = {
    "inventory_lookup": "check_inventory",
    "price_lookup": "get_product_detail",
    "product_detail": "get_product_detail",
}
SPEC_LABELS = {
    "brand": "品牌", "category": "类别", "cpu": "处理器", "memory": "内存",
    "storage": "存储", "display": "屏幕", "weight": "重量", "battery": "电池",
    "os": "系统", "gpu": "显卡", "camera": "相机", "chipset": "芯片",
    "connectivity": "连接方式", "waterproof": "防水", "driver": "驱动单元",
    "noise_cancellation": "降噪", "battery_life": "续航",
}


def reject_json_constant(value):
    raise ValueError(f"非法 JSON 常量：{value}")


class ProductQueryWorkflows:
    def __init__(self, *, tools: ToolProvider | None):
        self.tools = tools

    def supports(self, intent):
        """分派前核对可用工具；未接入对应工具时仍交给原执行器处理。"""
        if self.tools is None or intent not in WORKFLOW_TOOLS:
            return False
        names = {item["function"]["name"] for item in self.tools.list_tools()}
        return WORKFLOW_TOOLS[intent] in names

    def execute(self, decision, state) -> str:
        tool_name = WORKFLOW_TOOLS[decision.intent]
        # 固定库存流程必须有单个商品编号，不能用工具的可选参数触发全库查询。
        validated_args = SingleProductArgs.model_validate(decision.params)
        args = validated_args.model_dump()
        call_id = "workflow_" + uuid4().hex
        state.messages.append({
            "role": "assistant",
            "tool_calls": [{
                "id": call_id, "type": "function",
                "function": {"name": tool_name, "arguments": json.dumps(args, ensure_ascii=False)},
            }],
        })

        result_json = None
        error = None
        try:
            result_json = self.tools.call(tool_name, args)
            if not isinstance(result_json, str):
                raise TypeError("工具结果必须为 JSON 字符串")
            records = json.loads(result_json, parse_constant=reject_json_constant)
            reply = self._format(decision.intent, args["product_id"], records)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            reply = "亲～本次查询失败，暂时无法确认该商品的信息，请稍后重试。"

        # 原始结果保存在轨迹；错误与合法空结果分开，失败后不再交给 Agent 重复查。
        if not isinstance(result_json, str):
            result_json = json.dumps({"error": error or "工具未返回 JSON"}, ensure_ascii=False)
        event = {"intent": {"tool": tool_name, "args": args, "reasoning": ""}, "result": result_json}
        if error is not None:
            event["error"] = error
        state.trace.tool_events.append(event)
        state.messages.append({"role": "tool", "tool_call_id": call_id, "content": result_json})
        return reply

    def _format(self, intent, product_id, records):
        if not isinstance(records, list):
            raise TypeError("单商品查询必须返回数组")
        if not records:
            return f"亲～未查到商品 {product_id} 的相关记录，请核对商品编号。"
        if len(records) != 1 or not isinstance(records[0], dict):
            raise ValueError("单商品查询必须至多返回一条对象记录")
        record = records[0]
        id_field = "product_id" if intent == "inventory_lookup" else "id"
        name_field = "product_name" if intent == "inventory_lookup" else "name"
        if record.get(id_field) != product_id:
            raise ValueError("返回商品编号与请求不一致")
        name = record.get(name_field)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("缺少有效商品名称")

        if intent == "inventory_lookup":
            stock = record.get("stock")
            in_stock = record.get("in_stock")
            if type(stock) is not int or stock < 0 or type(in_stock) is not bool:
                raise ValueError("库存字段类型或范围错误")
            if in_stock != (stock > 0):
                raise ValueError("库存数量与是否有货不一致")
            return f"亲～{name}（{product_id}）的本地库存记录为 {stock} 件。"
        if intent == "price_lookup":
            price = record.get("price")
            if type(price) not in (int, float) or not math.isfinite(price) or price < 0:
                raise ValueError("缺少有效价格")
            return f"亲～{name}（{product_id}）的本地记录价格为 {price} 元。"

        specs = []
        for key, label in SPEC_LABELS.items():
            if key not in record or record[key] is None:
                continue
            value = record[key]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"规格 {key} 必须是非空文本")
            specs.append(f"{label}：{value}")
        detail = "；".join(specs) if specs else "暂无已提供的规格信息"
        return f"亲～{name}（{product_id}）：{detail}。未列出的规格暂无数据。"
