"""
agent/core/types.py
一个数据结构定义文件，专门规定 Agent 运行结果长什么样。
不负责执行逻辑、调用模型或调用工具。
一轮对话的用户侧返回结构：最终回复 + 用量/耗时。
"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class RouteDecision:
    """程序确认后的执行路径；默认保留完整请求交给 ReAct。"""

    kind: Literal["direct", "workflow", "agent"] = "agent"
    intent: str = "complex_request"
    params: dict = field(default_factory=dict)
    reply: str = ""
    reason: str = "no_router"
    source: str = "fallback"
    parameter_source: str = "none"


@dataclass
class TurnResult:
    replies: list = field(default_factory=list)      # 对用户可见的最终回复列表
    usage: dict = field(default_factory=dict)        # 模型调用/失败数、tokens、模型与整轮耗时


@dataclass
class RunTrace:
    """一次运行的调试/评测轨迹"""
    tool_events: list = field(default_factory=list)
    route: dict = field(default_factory=dict)
