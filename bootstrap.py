"""
bootstrap.py

组合根：显式创建应用资源，再用共享依赖组装各个会话。
导入本模块不会读取配置或数据、创建模型客户端、打开向量库。
create_application()
    ↓
没有传入 llm，则准备延迟创建的客户端，首次模型请求才读取配置并创建
    ↓
没有传入 tools，则加载商品与库存并创建 LocalProvider，知识检索服务延迟创建
    ↓
创建 Application，保存 llm、tools 和 resources
    ↓
调用 app.create_conversation()
    ↓
调用 build_agent()
    ↓
创建 Executor、Summarizer、Router、Workflows、Runner
    ↓
返回 ConversationRunner 实例
"""

from contextlib import ExitStack
import os
from pathlib import Path

from agent.core.loop import ReActExecutor
from agent.core.runner import ConversationRunner
from agent.memory.summary import ConversationSummarizer
from contract.tool import ToolProvider, validate_tool_provider
from llm.client import DEFAULT_MODEL, LLMClient, LazyLLMClient
from prompts.ecommerce import SYSTEM_PROMPT
from storage.json_loader import load_ecommerce_data
from tool_definitions.tool_tables import build_tool_tables
from tool_providers.local import LocalProvider
from routing.intent_router import IntentRouter
from workflows.product_queries import ProductQueryWorkflows


class Application:
    """共享依赖的拥有者；每个会话仍有独立历史和运行状态。"""

    def __init__(self, *, llm, tools: ToolProvider | None, resources: ExitStack):
        if tools is not None:
            validate_tool_provider(tools)
        self.llm = llm
        self.tools = tools
        self._resources = resources
        self._closed = False

    def create_conversation(self, session_id="default", system_prompt=SYSTEM_PROMPT):
        if self._closed:
            raise RuntimeError("应用已关闭，不能创建会话")
        return build_agent(llm=self.llm, tools=self.tools, session_id=session_id,
                           system_prompt=system_prompt)

    def close(self):
        if not self._closed:
            self._closed = True
            self._resources.close()

    def __enter__(self):
        if self._closed:
            raise RuntimeError("应用已关闭")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

def create_application(*, llm=None, tools: ToolProvider | None = None) -> Application:
    """缺省时创建 LLM 和本地工具提供方；只关闭应用自己创建的资源。"""
    resources = ExitStack()
    try:
        if llm is None:
            llm = _build_llm(resources)
        if tools is None:
            tools = _build_local_tools(resources)
        return Application(llm=llm, tools=tools, resources=resources)
    except BaseException:
        resources.close()
        raise
    
def _build_llm(resources: ExitStack):
    def create_client():
        from dotenv import load_dotenv
        from openai import OpenAI

        load_dotenv(Path(__file__).resolve().parent / ".env")
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("未找到 DEEPSEEK_API_KEY")
        client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")
        resources.callback(client.close)
        return LLMClient(client, model=os.getenv("LLM_MODEL") or DEFAULT_MODEL)

    llm = LazyLLMClient(create_client)
    resources.callback(llm.close)
    return llm


def _build_local_tools(resources: ExitStack | None = None) -> LocalProvider:
    """加载本地数据并注册知识检索；Qdrant/Ollama 只在首次知识查询时创建。"""
    from rag.service import LazySearchService, RagSearchAdapter

    if resources is None:
        resources = ExitStack()
    data = load_ecommerce_data()

    def build_search_service():
        from qdrant_client import QdrantClient
        from rag.embedder import Embedder
        from rag.retriever import Retriever
        from rag.store import DEFAULT_PATH, VectorStore

        client = QdrantClient(path=DEFAULT_PATH)
        resources.callback(client.close)
        retriever = Retriever(VectorStore(client), Embedder())
        return RagSearchAdapter(retriever)

    search_service = LazySearchService(build_search_service)
    resources.callback(search_service.close)
    tool_schemas, tool_lookup = build_tool_tables(
        data=data,
        search_service=search_service,
    )
    provider = LocalProvider(tool_schemas=tool_schemas, tool_lookup=tool_lookup)
    return provider


def build_agent(
    *, llm, tools: ToolProvider | None = None, session_id="default",
    system_prompt: str = SYSTEM_PROMPT):
    """装配独立会话；LLM 由调用方提供，工具默认不接入。"""
    executor = ReActExecutor(llm=llm, tools=tools)
    summarizer = ConversationSummarizer(llm=llm)
    router = IntentRouter(llm=llm)
    workflows = ProductQueryWorkflows(tools=tools)
    return ConversationRunner(
        executor=executor,
        llm=llm,
        summarizer=summarizer,
        session_id=session_id,
        system_prompt=system_prompt,
        router=router,
        workflows=workflows,
    )

