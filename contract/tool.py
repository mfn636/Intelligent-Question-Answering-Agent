"""Agent 与工具提供方之间的接口及行为契约。

LocalProvider 按此契约实现本地工具接入。此文件定义约定和接入核验，
工具说明生成、名称查找、参数校验和函数执行由具体实现负责。
Protocol 本身不做运行时检查，接入时需显式调用 validate_tool_provider()。
"""

import inspect
import json
from typing import Protocol


class ToolProvider(Protocol):
    """核心只通过 list_tools() 和 call() 使用工具。

    工具名称必须唯一，工具说明中的名称与 call() 使用的名称一致。
    对于 LocalProvider，每个声明的工具都必须绑定对应的本地函数。

    对外的参数说明必须与本地校验规则一致。LocalProvider 使用同一个
    Pydantic 参数定义生成 JSON Schema 并校验参数，不分别维护两套规则。
    """

    def list_tools(self) -> list[dict]:
        """返回可直接作为 LLM 请求 tools 参数的工具说明列表。

        每个元素的结构为：
        {
            "type": "function",
            "function": {
                "name": 工具名称,
                "description": 工具用途说明,
                "parameters": 参数的 JSON Schema,
            },
        }

        列表中每个工具都必须能通过 call() 按名称找到对应实现。
        没有工具时返回空列表；获取说明不会执行本地业务函数。
        """
        ...

    def call(self, name: str, args: dict) -> str:
        """按名称查找工具，校验参数，通过后执行并返回合法 JSON 字符串。

        name 是工具说明中的名称。args 是核心已经完成 JSON 解析的字典，
        本方法负责检查具体工具的字段、类型和约束。

        执行顺序：查找工具 → 校验参数 → 使用校验后的参数调用本地函数。
        查找或参数校验失败时，不能执行本地函数。

        本地函数负责返回合法 JSON 字符串，本方法成功时原样返回，
        不重复序列化。合法空结果与执行失败应明确区分。

        未知工具、参数不合法或执行失败时抛出带具体原因的异常，
        不将失败伪装成成功或空结果；错误反馈由 Agent 核心处理。
        """
        ...


def validate_tool_provider(tools: ToolProvider) -> None:
    """核验同步工具接口和工具说明，失败抛出带位置的 TypeError。

    读取一次 list_tools()，不执行 call()，不要求继承 ToolProvider 或暴露内部查找表。
    只检查参数 Schema 是可序列化的字典，不解析其内部 JSON Schema 规则。
    名称与实现的对应关系、参数校验及执行结果由 Provider 在调用阶段保证。
    核验仅对接入当时有效；None 表示未接入工具，由调用方跳过本函数。
    """
    if inspect.isclass(tools):
        raise TypeError("工具接入失败：请传入工具提供方实例，不能传入类")

    for method_name, call_args in (("list_tools", ()), ("call", ("tool_name", {}))):
        method = getattr(tools, method_name, None)
        if not callable(method):
            raise TypeError(f"工具接入失败：{method_name} 必须存在且可调用")

        # 当前执行器同步调用两个方法，不会 await 或遍历生成器。
        implementations = (method, getattr(method, "__call__", None))
        if any(
            inspect.iscoroutinefunction(fn) or inspect.isgeneratorfunction(fn)
            or inspect.isasyncgenfunction(fn)
            for fn in implementations
        ):
            raise TypeError(f"工具接入失败：{method_name} 必须是同步调用并直接返回结果的方法")

        try:
            method_signature = inspect.signature(method)
            method_signature.bind(*call_args)
        except (TypeError, ValueError) as error:
            expected = "list_tools()" if method_name == "list_tools" else "call(name, args)"
            raise TypeError(f"工具接入失败：{method_name} 的签名必须支持 {expected}：{error}") from error

    try:
        tool_schemas = tools.list_tools()
    except Exception as error:
        raise TypeError(f"工具接入失败：读取 list_tools() 失败：{error}") from error
    if not isinstance(tool_schemas, list):
        raise TypeError("工具接入失败：list_tools() 必须返回 list")

    tool_names = set()
    for index, schema in enumerate(tool_schemas):
        location = f"list_tools()[{index}]"
        if not isinstance(schema, dict):
            raise TypeError(f"工具接入失败：{location} 必须是 dict")
        if schema.get("type") != "function":
            raise TypeError(f"工具接入失败：{location}.type 必须是 'function'")
        function = schema.get("function")
        if not isinstance(function, dict):
            raise TypeError(f"工具接入失败：{location}.function 必须是 dict")
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            raise TypeError(f"工具接入失败：{location}.function.name 必须是非空白字符串")
        if name in tool_names:
            raise TypeError(f"工具接入失败：{location}.function.name 工具名称重复：{name}")
        tool_names.add(name)
        if not isinstance(function.get("description"), str):
            raise TypeError(f"工具接入失败：{location}.function.description 必须是 str")
        if not isinstance(function.get("parameters"), dict):
            raise TypeError(f"工具接入失败：{location}.function.parameters 必须是 dict")
        try:
            json.dumps(schema, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, OverflowError) as error:
            raise TypeError(f"工具接入失败：{location} 必须可序列化为合法 JSON：{error}") from error
