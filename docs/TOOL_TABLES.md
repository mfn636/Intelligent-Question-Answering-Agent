# 工具说明表与本地查找表

默认启动已通过 `LocalProvider` 接入商品搜索、商品详情、库存查询三个工具。`build_tool_tables()` 默认生成这三项的说明表与查找表，显式传入检索服务时才增加知识检索工具。当前启动流程不导入或创建 RAG 服务；独立 FAQ 仅保留本地备用函数。

## 阅读顺序

1. `tool_definitions/parameter_schemas.py`：用 Pydantic 定义工具参数的字段、默认值、描述和约束。
2. `tool_definitions/tool_tables.py`：列出每个工具的名称、用途、参数模型和已绑定依赖的函数，再一次循环生成两张表。
3. `tool_providers/local.py`：持有两张表，通过 `list_tools()` 提供说明，通过 `call()` 校验参数、执行函数并核验 JSON 返回值。

`build_tool_tables()` 的调用方式：

```python
from tool_definitions.tool_tables import build_tool_tables

# data 由调用方提前准备好；默认只构建三个商品与库存工具。
tool_schemas, tool_lookup = build_tool_tables(data=data)
```

构建过程不读取数据文件、不创建检索客户端，也不执行查询。每次调用得到独立的两张表，其中的函数引用调用方提供的数据和检索服务。
`search_service` 默认是 `None`，此时不生成知识工具的说明或执行入口，也不导入知识检索模块。后续若显式调用 `build_tool_tables(data=data, search_service=search_service)`，两张表才会同时增加 `search_knowledge`；并非注册一个无法执行的空工具。
构建前调用 `validate_ecommerce_data(data)`：仅检查已存在的 `products`、`inventory`、`faq` 字段是否为列表且元素为字典，缺失字段跳过，类型错误报 `TypeError`。不要求字段齐全，也不检查记录里的商品 ID、价格等业务字段；检索服务的接入核验仍待实现。

## 两张表分别是什么

`tool_schemas` 是可以作为 `LLMClient.chat(tools=...)` 参数的列表，每项包含 `type`、工具名称、用途描述和参数 JSON Schema。
其中的 `parameters` 直接通过对应参数模型的 `model_json_schema(mode="validation")` 生成，不手写第二份字段规则。

`tool_lookup` 是按工具名称查找的普通字典，每项只有两个内容：

```python
{
    "get_product_detail": {
        "args_model": GetProductDetailArgs,
        "fn": partial(get_product_detail, data=data),
    },
    # 其他工具同样组织。
}
```

`partial` 提前绑定程序依赖。模型不需要提供 `data` 或 `search_service`，这两个字段也不会出现在模型侧 Schema 中。
商品搜索、详情和库存查询只绑定 `data`，知识查询只绑定 `search_service`。建表不导入或绑定独立的 `search_faq` 函数。
工具名称只在同一份清单中定义；生成两张表时检查重复名称，发现重复立即报错。

## 参数规则

| 工具 | 参数约定 |
|---|---|
| `search_products` | 直接筛选业务商品数据；类别、品牌、预算、keyword、query 均可选，条件按 AND 组合；预算为正整数元。关键词对名称、类别和描述做不区分大小写的包含匹配，keyword 优先于兼容参数 query；不做语义检索 |
| `get_product_detail` | 商品 ID 必填；keyword 可选，对该商品的名称、类别和描述做不区分大小写的包含匹配；不匹配时返回空数组 |
| `check_inventory` | 商品 ID、类别可选，均省略表示查询全部库存 |
| `search_knowledge`（可选，默认不注册） | query 必填，类别可选，top_k 默认 5、范围 1～20 |

所有参数模型设置 `strict=True` 和 `extra="forbid"`。例如预算字符串 `"5000"`、布尔值 `true` 或未声明的 `data` 字段都会被拒绝。
可选条件支持省略或 JSON null；文本一旦提供，必须含有非空白字符。
`strict=True` 指本地 Pydantic 的校验方式，不代表模型一定生成合法参数。

## 本地查找和调用示例

```python
tool_name = "get_product_detail"
args = {"product_id": "NB001"}  # loop 已完成 JSON 字符串到字典的解析。

tool = tool_lookup[tool_name]
args_model = tool["args_model"]
validated_args = args_model.model_validate(args)
call_args = validated_args.model_dump()

function = tool["fn"]
result_json = function(**call_args)
```

直接执行 `fn` 不会自动校验；上面的调用顺序已由 `LocalProvider.call()` 统一落实。
查找失败会抛出 `KeyError`，参数校验失败会抛出 Pydantic `ValidationError`，通过后才执行本地函数。
商品查询直接使用业务数据；可选的知识工具通过显式提供的检索服务查询知识库，包括 FAQ 文档。结果仍是合法 JSON 字符串。

业务数据本身保持普通字典和列表；Pydantic 在这里仅定义和校验工具调用参数。

## 使用 LocalProvider

`main.py` 调用 `create_application()`，后者在 `tools is None` 时通过 `_build_local_tools()` 完成：加载商品与库存 → 生成三项工具的两张表 → 创建 `LocalProvider` → 传给 `Application` → 创建会话时传给执行器。数据每个应用加载一次，多个会话共用同一个 Provider。

也可以手动提供业务数据和 Provider：

```python
from bootstrap import create_application
from tool_providers.local import LocalProvider
from tool_definitions.tool_tables import build_tool_tables

# data 和 llm 由调用方提前准备并管理。
tool_schemas, tool_lookup = build_tool_tables(data=data)
provider = LocalProvider(tool_schemas=tool_schemas, tool_lookup=tool_lookup)

# 可以直接调用，参数必须先由 Pydantic 校验通过。
result_json = provider.call("get_product_detail", {"product_id": "NB001"})

# 也可以交给 Agent，让模型选择工具并生成参数。
with create_application(llm=llm, tools=provider) as app:
    conversation = app.create_conversation()
    result = conversation.run("查询 NB001 的规格")
```

Provider 不加载业务数据或创建检索客户端，也不关闭调用方持有的资源。构造时调用现有的 `validate_tool_provider()` 检查说明，并核对两张表的名称完全一致、参数模型属于 Pydantic、绑定函数可调用、说明中的参数 Schema 与模型生成结果一致；不会试执行工具。

注意 `create_application(tools=None)` 现在表示构建默认本地工具。显式传入 Provider 时直接使用它，不加载默认数据；如需完全不提供工具，可传 `LocalProvider(tool_schemas=[], tool_lookup={})`。底层 `build_agent(llm=llm)` 仍然只装配传入的依赖，不会自动加载工具。

Provider 复制工具说明和查找表项，`list_tools()` 返回说明副本，避免外部修改导致两张表不一致；函数、参数模型和函数绑定的数据及服务仍使用原引用。

`call()` 的顺序是：按名称查找 → 要求 args 为字典 → Pydantic 校验并补默认参数 → 调用函数 → 检查返回值是合法 JSON 字符串 → 原样返回。JSON 检查仅解析，不重新序列化，不压缩或截断结果；空数组 `"[]"` 是合法成功结果。

未知名称抛 `KeyError`，业务参数错误抛 Pydantic `ValidationError`，非字典参数或非字符串返回值抛 `TypeError`，非法 JSON 字符串抛 `ValueError`。本地执行异常直接保留原异常向外抛出；目前不重试、不熔断，也不自动切换 FAQ。Agent 已有的异常处理会将这些异常回填给模型。

## 工具提供方的接入核验

`contract/tool.py` 提供 `validate_tool_provider(tools)`，由 `Application` 和 `ReActExecutor` 在接收非空工具实现时调用。也可以独立调用：

```python
from contract.tool import validate_tool_provider

validate_tool_provider(provider)
```

- `list_tools()` 与 `call(name, args)` 必须可同步调用，签名兼容执行器的调用方式；额外可选参数允许，额外必填参数会被拒绝。
- `list_tools()` 必须返回列表，空列表合法；每项为 `type="function"` 的字典，`function` 包含非空白且唯一的名称、字符串描述和字典形式的参数 Schema。
- 每条说明必须可以序列化为合法 JSON，不允许集合、自定义对象或 NaN 等值；保留原始数据，不转换或删改字段。

核验失败抛出带具体位置的 `TypeError`；读取工具说明时的异常保留为异常原因。每次核验读取一次清单，不执行工具。`LocalProvider` 构造、应用接入与执行器接入各检查一次；循环中正常获取工具说明，不重复运行接入核验。

通用接入核验不测试业务函数：参数 Schema 只检查字典类型和可序列化性，不递归解释 JSON Schema 规则。`LocalProvider` 另外负责构造时核对函数映射和参数模型一致性，并在每次调用时实施参数校验及结果 JSON 检查；其他 Provider 需要自行满足相同行为契约。

离线验证：`python -m unittest discover -s tests -v`。`tests/test_local_tool_bootstrap.py` 覆盖真实本地数据、三个工具的命令行往返、可选知识工具、会话共享、依赖注入、异常清理，以及在禁止 RAG 导入时正常启动。模型与检索服务使用测试替身，不发起真实服务请求。

## 预留的本地 FAQ 函数

`tool_functions/faq_search.py` 只保留本地查询能力，不调用 RAG，不生成工具说明，也不进入本地工具查找表。`SearchFaqArgs` 已移除；RAG 故障时如何启用这个函数留待后续设计。

手动调用需要显式提供 FAQ 数据：

```python
from contract.data import EcommerceData
from storage.json_loader import load_faq
from tool_functions.faq_search import search_faq

faq_data = EcommerceData(faq=load_faq())
result_json = search_faq(query="退款", data=faq_data)
```

query 和 category 均可选；沿用原本的关键词匹配规则，按整句或连续两个字符匹配问题及答案，类别按包含关系筛选。均省略时返回全部 FAQ，无匹配返回 JSON 字符串 `"[]"`。命中只表示找到候选，不能据此直接认定候选能回答用户问题。

`load_ecommerce_data()` 现在只加载商品与库存；RAG 入库仍通过 `load_faq()` 读取 FAQ 文档。数据契约继续保留可选的 `faq` 列表，供独立函数使用。
