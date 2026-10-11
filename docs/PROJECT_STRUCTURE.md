# 项目结构

2026-10-10 更新：会话入口已接入直接回复、固定商品查询与 ReAct 三条路径。默认 LLM 在首次请求时才创建，规则覆盖的直接回复和本地查询不依赖模型配置。实现与边界见 [INTENT_ROUTING_DESIGN.md](INTENT_ROUTING_DESIGN.md)。

2026-10-11 更新：默认应用已通过 `LocalProvider` 接入商品搜索、商品详情、库存查询和知识检索四个工具。知识检索服务延迟创建，首次查询才打开 Qdrant 并使用 Ollama BGE-M3；独立 FAQ 工具仍仅保留本地备用函数。

| 位置 | 职责 |
|---|---|
| `main.py` | 命令行对话入口 |
| `bootstrap.py` | 创建默认 LLM、本地与知识工具提供方，组装会话并关闭自建资源；支持外部依赖注入 |
| `agent/` | 会话管理、ReAct 执行、运行状态和内存历史 |
| `llm/` | 模型请求与用量记录 |
| `prompts/` | 业务系统提示词、摘要规则，以及 `routing.py` 中的分类说明和固定话术 |
| `routing/intent_router.py` | 完整请求规则、一次模型分类、分类结果校验与路径决策 |
| `workflows/product_queries.py` | 单商品库存、价格、通用参数查询；调用 Provider、核验结果并格式化回复 |
| `agent/memory/history.py` | 本会话近期原文与早期摘要，摘要成功后释放已覆盖原文 |
| `agent/memory/summary.py` | 用注入的 LLM 增量生成摘要，不持有会话状态、不读写文件 |
| `tool_functions/` | 商品、库存、知识查询函数，以及未注册的本地 FAQ 备用函数；返回合法 JSON 字符串 |
| `tool_definitions/parameter_schemas.py` | 四个工具的 Pydantic 参数定义，用于生成 JSON Schema 和本地校验 |
| `tool_definitions/tool_tables.py` | 构建商品、库存和知识工具的两张表；知识工具接收注入的检索服务 |
| `tool_providers/local.py` | `LocalProvider` 使用两张表提供说明、校验参数、执行函数并检查 JSON 返回值 |
| `storage/json_loader.py` | 从根目录 `data/` 读取 JSON，直接返回原始字典列表 |
| `contract/data.py` | 定义 `EcommerceData` 数据容器，只核验已存在的声明字段为列表且元素为字典 |
| `data/` | 原始 JSON 数据与检索评测数据 |
| `contract/tool.py` | 定义 `ToolProvider` 接口及 `validate_tool_provider()` 接入核验 |
| `contract/retrieval.py` | 本地检索函数依赖的检索接口、过滤条件和结果结构 |
| `rag/collections.py` | 知识库向量集合名称 `knowledge` |
| `rag/ecommerce_chunks.py` | FAQ、指南、政策、帮助文档转换为检索片段 |
| `rag/chunker.py` | 通用文本分块 |
| `rag/ingest.py` | 离线入库入口，定义和组装集合加载及分块流程 |
| `rag/` 其余模块 | 向量生成、存储、检索、重排及检索服务适配 |

## 在线对话

`main.py` 显式创建一个 `Application`，再由它创建会话。组合根从 `prompts/ecommerce.py`
选择默认提示词，将 LLM 和装配好的 `LocalProvider` 注入会话及 ReAct 执行器；模型请求附带四个工具的说明。

```python
from bootstrap import create_application

with create_application() as app:
    conversation = app.create_conversation()
    result = conversation.run("你好")
```

`create_application(llm=None, tools=None)` 是应用创建入口。`llm=None` 时准备 `LazyLLMClient`，首次 `chat()` 才创建默认模型客户端；`tools=None` 时加载本地商品与库存，并注册延迟的 `LazySearchService`，通过 `_build_local_tools()` 创建 `LocalProvider`。创建应用时不打开 Qdrant、不请求 Ollama；首次调用 `search_knowledge` 才创建 RAG 依赖。
可以直接注入 LLM 或 `LocalProvider` 等工具实现；被注入的资源由调用方管理，应用不会替调用方关闭它们。
显式传入工具 Provider 时直接使用，不读取默认业务数据；如需关闭工具，可传入空表的 `LocalProvider(tool_schemas=[], tool_lookup={})`。旧的 `data`、`search_service` 应用参数和 `build_tools()` 仍未恢复。

同一个应用可以创建多个会话，共享 LLM 和工具 Provider，每个应用的默认商品与库存只加载一次。每个会话有独立历史、摘要、状态和工具轨迹。
当前会话执行与用量日志按顺序调用设计，不支持多个线程同时运行。
默认应用可以查询本地商品、库存和知识库；独立 FAQ 工具不在默认工具说明或查找表中，模型无法直接调用它。

底层组装函数不再创建默认依赖：

```python
conversation = build_agent(llm=llm, system_prompt="自定义提示词")
```

`build_agent()` 要求显式提供 LLM；默认启动使用上面的应用上下文。
工具实现提供符合契约的 `list_tools()` 和 `call(name, args)` 后，就可以通过 `tools=` 注入，无需继承接口或增加转发包装。
`Application` 与 `ReActExecutor` 构造时会对非 `None` 的工具调用 `validate_tool_provider()`；应用接入和独立执行器接入都受检查。创建会话时会创建执行器，因此会再次核验，不在每次 ReAct 循环中重复核验。
核验检查同步方法及可兼容的调用签名，读取一次 `list_tools()` 检查说明结构、工具名唯一性和 JSON 可序列化性；失败抛出带位置的 `TypeError`。空工具列表合法，`tools=None` 跳过检查。不试执行 `call()`，不要求 Provider 暴露内部查找表。
该检查只覆盖接入当时的接口与说明格式，不验证参数 JSON Schema 的内部规则，也不能证明每个工具均有实现、参数校验实际执行或返回结果始终合法；这些行为仍由 Provider 的调用实现保证，`SearchService` 接入核验尚待完成。
工具参数和两张表在 `tool_definitions/` 中构建，`tool_providers/local.py` 中的 `LocalProvider(tool_schemas=..., tool_lookup=...)` 消费这些表。构造时检查表的一致性和绑定函数；`call()` 使用表里的 Pydantic 模型校验参数、执行函数，并检查返回的 JSON 字符串，成功时原样返回，失败抛异常。具体业务工具不由 Provider 创建或加载。
默认工具由 `_build_local_tools()` 组装：`load_ecommerce_data()` → 创建 `LazySearchService` → `build_tool_tables(data=data, search_service=...)` → `LocalProvider`。如需自定义，可自己准备数据和检索服务并创建 Provider 后通过 `tools=` 注入。完整示例见 [TOOL_TABLES.md](TOOL_TABLES.md)。
核心保留 JSON 参数解析、工具往返消息、错误反馈、轨迹与循环收尾。两张表的用法见 [TOOL_TABLES.md](TOOL_TABLES.md)。
`LLMClient(client, model=...)`、`Retriever(store, embedder, reranker=None)`、`RagSearchAdapter(retriever)`、
`VectorStore(client)` 和 `CrossEncoderReranker(model)` 都接收已经准备好的依赖。内部的消息列表、索引和 BM25 缓存仍由组件自己维护。

## 加载与关闭时机

| 时机 | 行为 |
|---|---|
| 导入 `bootstrap`、`main` 或工具模块 | 定义代码，不读取 `.env` 或业务 JSON，不创建客户端 |
| `create_application()` 准备默认 LLM | 创建延迟客户端，不读取模型配置 |
| 首次需要分类、摘要或 ReAct 模型请求 | 读取项目 `.env`，创建 SDK 客户端并注入 `LLMClient`，注册关闭回调 |
| `create_application()` 创建默认工具 | 读取商品与库存 JSON，建表并创建一个共享的 `LocalProvider`；不读取 FAQ/指南，不打开 Qdrant 或 Ollama |
| 首次调用 `search_knowledge` | 延迟创建 Qdrant 本地客户端、BGE-M3 Embedder、Retriever 和 RAG 适配器；资源注册到应用生命周期 |
| 创建会话 | 借用应用依赖，初始化独立历史与状态 |
| 直接使用本地函数 | 调用方提供所需数据；知识查询还需显式提供检索服务 |
| 退出应用上下文或显式 `app.close()` | 关闭自己创建的模型 SDK 客户端；外部注入资源由调用方关闭 |

初始化中途失败时，已创建的资源立即释放。应用关闭后不能再创建会话，也应停止使用原有会话。
`EcommerceData` 是显式加载时的电商数据快照，文件修改不会自动刷新；读取更新后的数据需要调用方重新加载。

## 业务数据

商品、库存、FAQ 和知识检索结果使用普通 `dict` / `list`。原 `data_schema/` 中的业务模型已移除。
`storage/json_loader.py` 只读取并解析 JSON；字段原样保留，不自动转换类型、补默认值或做字段级校验。
本地函数和 RAG 分块直接按字典键读取字段，查询结果使用标准库 `json.dumps()` 生成合法 JSON 字符串。

商品搜索、详情和库存查询只依赖 `EcommerceData`，不调用检索服务。`search_products()` 按类别、预算、品牌和关键词直接筛选 `data.products`；关键词是名称、类别和描述的包含匹配，不再进行商品语义检索。模型应提取筛选条件或关键词，不能把整句自然语言需求当作语义查询。
`search_knowledge()` 使用注入的 `SearchService` 检索 `knowledge` 集合，仍覆盖 FAQ 文档；默认装配通过 `LazySearchService` 延迟接入 RAG。独立 `search_faq()` 仅做本地关键词和类别筛选，不依赖 RAG，已从工具说明表及本地查找表移除；故障时的自动启用流程尚未实现。后续接入真实商品数据只需调整业务数据来源或商品查询实现，无需同步商品向量索引。

`load_ecommerce_data()` 只读取商品和库存，不再顺带加载备用 FAQ。需要手动使用 FAQ 函数时，可显式创建 `EcommerceData(faq=load_faq())`；RAG 入库仍独立加载 FAQ，分块与检索范围不变。

`EcommerceData` 是聚合商品、库存、FAQ 的 dataclass，未提供的数据集合默认为独立空列表，可以只传 `products` 等本次需要的数据。
创建时通过 `validate_ecommerce_data()` 检查已存在的 `products`、`inventory`、`faq` 字段是否为列表、元素是否为字典；缺失字段跳过，不要求接入对象字段齐全。明确传入 `None` 仍是类型错误。`build_tool_tables()` 接收数据时调用同一类型检查。
空列表和空字典合法，字典内部字段不检查、不转换，原对象引用保持不变；例如 `products=[{"price": "abc"}]` 可以通过结构核验。
失败抛出包含字段路径的 `TypeError`，例如 `inventory[1] 必须是 dict，实际为 str`。
商品 ID 索引由商品详情和库存查询函数在调用时建立，搜索直接遍历商品数据；数据容器不访问 `id`，缺少业务字段只会在实际消费时暴露。
结构检查只对调用当时的数据生效，之后的直接修改不会自动触发检查；外部更新数据后可显式再次调用 `validate_ecommerce_data()`。
单独调用 `load_products()`、`load_inventory()`、`load_faq()` 仍只解析文件，结构核验在组装 `EcommerceData` 或进入工具表构建时执行。
`AgentState`、`RunTrace`、检索接口等运行结构继续由各自模块维护。
Pydantic 在 `tool_definitions/parameter_schemas.py` 中定义工具参数，由 `LocalProvider.call()` 实际执行校验；业务数据继续使用普通字典和列表。

## 会话上下文

上下文由系统提示词、较早对话摘要、全部未压缩原文组成。
进入 ReAct 且未压缩原文超过 20 条时，按完整问答批量更新摘要并保留最多 10 条近期原文；尚未触发摘要时也不会丢弃中间消息。直接回复和固定查询延后摘要，原文先保留；连续只走快捷路径时，原文可以超过 20 条。
摘要失败会保留原文继续回答，摘要调用计入本轮用量。摘要仅保存在当前会话内存中。
实现与边界详见 [CONVERSATION_MEMORY.md](CONVERSATION_MEMORY.md)。

## 离线入库

```bash
python -m rag.ingest
```

`rag.ingest.main()` 是独立的离线组合根，创建并关闭向量客户端，选择集合和向量生成器。
`build_collection_specs()` 只组织 `knowledge` 集合，加载 FAQ 与长文档；`ingest()` 使用传入的依赖读取数据、分块、计算向量并重建知识集合。已移除商品分块、商品集合常量和商品入库配置。
需要 Qdrant 客户端依赖及可用的向量服务。该命令会实际重建向量数据，不作为普通的导入检查运行。

代码调用形式为 `ingest(collections, store=store, embedder=embedder)`。这些依赖由调用方创建和关闭。
在线对话不依赖入库配置、评测用例或领域注册表。

旧向量库中的 `products` 集合作为历史数据保留，当前商品工具、默认入库流程和当前 RAG 评测均不读取或更新它；本次调整没有操作数据库文件。

## 检索评测范围

`eval/evaluate_retrieval.py` 当前仅评测知识检索。从原始 270 条黄金查询中排除 80 条商品查询，使用剩余 190 条，并将筛选数量写入 `audit.json` 与 `provenance.json`。默认输出为 `eval/results/knowledge/`，不覆盖历史目录。

```bash
python -m eval.evaluate_retrieval --skip-rerank
```

上述命令需要可用的向量服务；此次重构只做离线测试与索引一致性核验，没有重新运行模型评测。历史 270 条结果和原始黄金集保留，`eval/analyze_retrieval.py` 仍专门分析 2026-09-28 的历史结果，不能用于当前 190 条知识评测。

## 后续工作

入口路由、三个固定工作流与模型延迟初始化已实现；真实分类效果、误路由率和整体成本收益仍需评测。业务数据刷新策略和本地有限重试尚未实现。
`LocalProvider` 已在默认启动入口完成本地商品、库存和知识工具装配；RAG 依赖采用延迟创建。检索接口接入核验、故障重试和 FAQ 备用切换仍待实现。
