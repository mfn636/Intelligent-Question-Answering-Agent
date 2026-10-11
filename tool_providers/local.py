"""本地工具提供方：持有两张表，查找工具、校验参数并执行函数。"""

from copy import deepcopy
import json

from pydantic import BaseModel

from contract.tool import validate_tool_provider


def _reject_json_constant(value):
    """标准 JSON 不允许 NaN、Infinity 等特殊浮点值。"""
    raise ValueError(f"非法 JSON 常量：{value}")


class LocalProvider:
    def __init__(self, *, tool_schemas: list[dict], tool_lookup: dict[str, dict]):
        """接收已构建的两张表；不读取文件、创建服务或执行工具。"""
        if not isinstance(tool_lookup, dict):
            raise TypeError("本地工具接入失败：tool_lookup 必须是 dict")

        self._tool_schemas = deepcopy(tool_schemas)
        self._tool_lookup = {}
        validate_tool_provider(self)

        schema_names = {schema["function"]["name"] for schema in self._tool_schemas}
        if schema_names != set(tool_lookup):
            raise ValueError("本地工具接入失败：工具说明表与本地查找表的名称必须一一对应")

        for schema in self._tool_schemas:
            name = schema["function"]["name"]
            entry = tool_lookup[name]
            if not isinstance(entry, dict):
                raise TypeError(f"本地工具接入失败：{name} 的查找表项必须是 dict")

            args_model = entry.get("args_model")
            function = entry.get("fn")
            if not isinstance(args_model, type) or not issubclass(args_model, BaseModel):
                raise TypeError(f"本地工具接入失败：{name}.args_model 必须是 Pydantic 模型类")
            if not callable(function):
                raise TypeError(f"本地工具接入失败：{name}.fn 必须可调用")

            parameters_schema = args_model.model_json_schema(mode="validation")
            if schema["function"]["parameters"] != parameters_schema:
                raise ValueError(f"本地工具接入失败：{name} 的参数说明与 Pydantic 模型不一致")

            # 复制表项，但保留函数及已绑定数据、服务的引用。
            self._tool_lookup[name] = {"args_model": args_model, "fn": function}

    def list_tools(self) -> list[dict]:
        """返回模型工具说明的副本，防止调用方修改内部说明。"""
        return deepcopy(self._tool_schemas)

    def call(self, name: str, args: dict) -> str:
        """查找工具 → Pydantic 校验参数 → 执行函数 → 检查 JSON → 原样返回
           失败直接抛异常。"""
        if name not in self._tool_lookup:
            raise KeyError(f"未知工具：{name}")
        if not isinstance(args, dict):
            raise TypeError(f"工具 {name} 的参数必须是 dict")

        tool = self._tool_lookup[name]
        args_model = tool["args_model"]
        # 校验参数，成功后得到模型实例
        validated_args = args_model.model_validate(args)
        # 把模型实例转换成字典，供函数调用
        call_args = validated_args.model_dump()

        function = tool["fn"]
        # 也就是说执行到这里result_json = function(**call_args)才知道结果对不对是吧
        result_json = function(**call_args)

        if not isinstance(result_json, str):
            raise TypeError(f"工具 {name} 必须返回合法 JSON 字符串，实际为 {type(result_json).__name__}")
        try:
            json.loads(result_json, parse_constant=_reject_json_constant)
        except ValueError as error:
            raise ValueError(f"工具 {name} 返回的字符串不是合法 JSON：{error}") from error
        return result_json
