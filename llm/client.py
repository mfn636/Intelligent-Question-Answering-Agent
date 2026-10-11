'''
可考虑进行宏观限定client为单一领域
'''

import time

# 默认模型；应用入口可用环境变量 LLM_MODEL 覆盖
DEFAULT_MODEL = "deepseek-v4-flash"


def _cached_tokens(usage):
    """
    兼容不同厂商的"缓存命中 token"字段：
    - DeepSeek: usage.prompt_cache_hit_tokens
    - OpenAI / 智谱: usage.prompt_tokens_details.cached_tokens
    """
    if usage is None:
        return 0
    v = getattr(usage, "prompt_cache_hit_tokens", None)
    if v is not None:
        return int(v)
    details = getattr(usage, "prompt_tokens_details", None)
    v = getattr(details, "cached_tokens", None) if details is not None else None
    return int(v) if v is not None else 0


class LLMClient:
    def __init__(self, client, model: str = DEFAULT_MODEL):
        """接收已创建的 SDK 客户端；配置加载与关闭由创建方负责。"""
        self.model = model
        self.client = client
        # 每次调用的用量/耗时日志（供指标统计，不影响返回）
        self.usage_log = []

    def chat(
        self,
        messages,           # 对话记录列表，格式：[{role, content}, ...]
        tools=None,         # 工具定义列表
        temperature=0.4,    # 随机程度，0=死板，1=放飞
        max_tokens=1024,    # 回答最长多少字
        stream=False,       # 是否流式输出
        response_format=None,
        thinking=None,      # None=厂商默认；False=关闭思考（省 token、提速）；True=开启
    ):
        """发送调用方组装好的 messages；系统提示词统一放在 system 消息中。"""

        params = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        if response_format:
            params["response_format"] = response_format

        if thinking is not None:
            params["extra_body"] = {"thinking": {"type": "enabled" if thinking else "disabled"}}

        t0 = time.perf_counter()
        # 此处为实际发送
        try:
            response = self.client.chat.completions.create(**params)
        except Exception:
            # 失败的请求也消耗时间；未取得服务端 usage 的 token 无法统计。
            self.usage_log.append({
                "model": self.model, "prompt_tokens": 0, "completion_tokens": 0,
                "total_tokens": 0, "cached_tokens": 0, "failed": True,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            })
            raise
        latency_ms = (time.perf_counter() - t0) * 1000

        if stream:
            return response

        usage = getattr(response, "usage", None)
        self.usage_log.append({
            "model": self.model,
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            "cached_tokens": _cached_tokens(usage),
            "latency_ms": round(latency_ms, 1),
        })

        return response.choices[0].message


class LazyLLMClient:
    """首次 chat 才创建客户端；只读用量不会触发配置加载。资源仍由组合根关闭。"""

    def __init__(self, factory):
        self._factory = factory
        self._llm = None
        self._closed = False

    @property
    def usage_log(self):
        return self._llm.usage_log if self._llm is not None else []

    def chat(self, **kwargs):
        if self._closed:
            raise RuntimeError("应用已关闭，不能请求模型")
        if self._llm is None:
            self._llm = self._factory()
        return self._llm.chat(**kwargs)

    def close(self):
        # SDK 的 close 已注册在应用 ExitStack 上，这里阻止关闭后的延迟创建。
        self._closed = True
