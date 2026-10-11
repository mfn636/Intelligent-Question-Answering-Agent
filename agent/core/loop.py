"""
ReAct 执行器：基于本轮上下文调用模型和工具，返回最终回答。
执行任务、更新过程状态：读取上下文，调用模型和工具，追加工具往返与轨迹，返回回答
"""


import json

from agent.core.state import AgentState
from contract.tool import ToolProvider, validate_tool_provider


class ReActExecutor:

    # ReAct 循环最大轮数（终止条件①）
    MAX_TURNS = 5

    def __init__(self, *, llm, tools: ToolProvider | None = None):
        if tools is not None:
            validate_tool_provider(tools)
        self.llm = llm
        self.tools = tools

    def execute(self, state: AgentState) -> str:
        """维护模型消息、循环步数和工具轨迹；会话生命周期由调用方管理。"""
        messages = state.messages
        tool_events = state.trace.tool_events
        # 每轮跑的逻辑流程
        while state.step < self.MAX_TURNS:
            state.step += 1

            # ---- ReAct: Thought（模型思考：调用什么工具 / 填什么参数）----
            tool_schemas = None
            if self.tools is not None:
                tool_schemas = self.tools.list_tools()
            response = self.llm.chat(messages=messages, tools=tool_schemas)

            # 如果模型调工具
            if response.tool_calls:
                # 回填工具调用请求，丢弃该响应附带的 content
                self._append_tool_calls(messages, response)

                for tc in response.tool_calls:
                    try:
                        args = self._parse_arguments(tc.function.arguments)
                    except ValueError as e:
                        # 解析失败时跳过执行，将错误回填给模型修正
                        args = None
                        result_json = json.dumps({
                            "error": f"工具参数解析失败：{e}。工具未执行，请修正参数后重新调用。",
                        }, ensure_ascii=False)
                    else:
                        # ---- ReAct: Action（agent 执行工具）----
                        result_json = self._safe_call_tool(tc.function.name, args)
                    tool_events.append(self._build_event(tc, args, result_json, response))
                    # ---- ReAct: Observation（结果回填，供下一轮参考）----
                    messages.append(self._tool_message(tc, result_json))

                continue

            # 答案出口，模型最终发出去的话
            elif response.content and response.content.strip():
                return response.content
            else:
                # 终止条件②：模型未调工具也未输出内容，不再循环
                break

        # 循环退出：超轮数 → 无工具收尾作答；空响应 → 兜底话术
        reply = self._finalize_answer(messages) if state.step >= self.MAX_TURNS else None
        return reply or "抱歉，暂时无法处理您的问题，请稍后再试。"

    # ---------- ReAct 具名状态方法 ----------

    def _append_tool_calls(self, messages, response):
        """将 tool_calls 转成 assistant 消息加入对话，丢弃附带的 content。"""
        tool_call_dicts = []
        for tc in response.tool_calls:
            tool_call_dicts.append({
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments
                }
            })
        msg = {"role": "assistant", "tool_calls": tool_call_dicts}
        # 工具调用请求追加到 messages 列表
        messages.append(msg)

    def _parse_arguments(self, args_str):
        """解析工具参数为字典；JSON 格式错误或非对象时抛 ValueError。"""
        try:
            args = json.loads(args_str)
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(f"参数不是合法的 JSON：{e}") from e
        if not isinstance(args, dict):
            raise ValueError("参数必须是 JSON 对象，无参数时请传 {}")
        return args

    def _safe_call_tool(self, name, args) -> str:
        """透传工具返回的合法 JSON 字符串，调用异常转为错误 JSON。"""
        if self.tools is None:
            return json.dumps({
                "error": "当前未接入工具，无法执行调用。请依据已有信息作答，无法确认的信息应明确说明。",
            }, ensure_ascii=False)
        try:
            return self.tools.call(name, args)
        except Exception as e:
            return json.dumps({"error": f"工具调用失败: {e}"}, ensure_ascii=False)
        
    def _build_event(self, tc, args, result_json, response):
        """组装工具事件；args 为 None 表示参数解析失败、工具未执行。"""
        return {
            "intent": {
                "tool": tc.function.name,
                "args": args,
                "reasoning": getattr(response, "reasoning_content", None) or "",
            },
            "result": result_json,
        }
    
    def _finalize_answer(self, messages):
        """超轮数收尾：不再进入ReAct循环，重起一次调用，LLM基于已有结果作答；失败返回 None 走兜底。"""
        try:
            resp = self.llm.chat(messages=messages, tools=None)
            return (resp.content or "").strip() or None
        except Exception:
            return None

    def _tool_message(self, tc, result_json):
        """组装 role=tool 消息，tool_call_id 与 assistant 的 tool_calls 一一对应。"""
        return {
            "role": "tool",
            "tool_call_id": tc.id,
            "content": result_json,
        }
