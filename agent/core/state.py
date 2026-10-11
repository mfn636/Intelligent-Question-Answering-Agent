"""
本轮结构化状态：会话入口管理生命周期，执行器更新步骤、消息和工具轨迹。
定义并承载本轮状态：上下文、步数、运行状态、最终回答和轨迹
"""

from dataclasses import dataclass, field
from typing import Literal

from agent.core.types import RunTrace


@dataclass
class AgentState:
    """每次 run 创建新状态及其工具轨迹；跨轮会话历史由历史容器维护。"""

    user_input: str = ""
    messages: list[dict] = field(default_factory=list)  # 本次运行上下文，含工具往返与最终回复
    step: int = 0                                    # 已执行的 ReAct 循环步数
    # completed 表示正常返回（包括兜底回复）；failed 表示异常退出
    status: Literal["idle", "running", "completed", "failed"] = "idle"
    final_replies: list[str] = field(default_factory=list)
    trace: RunTrace = field(default_factory=RunTrace)  # 本次运行的调试/评测轨迹
