# Intelligent Question Answering Agent

An e-commerce question-answering agent built with ReAct, tool calling, hybrid retrieval, and structured conversation memory.

The system handles product recommendations, product specifications, inventory questions, and customer-support knowledge queries. A lightweight intent router sends simple requests to deterministic replies or fixed workflows, while open-ended requests continue through the ReAct loop.

## Workspace preview

![Agent Studio showing a three-turn product conversation, conversation memory, live AgentState, and tool-call results](docs/images/web-workspace.png)

A real session in the FastAPI workspace: an inventory lookup, a follow-up price query, and a model-generated laptop comparison using the bundled sample catalog. The inspector displays the session's actual memory and execution state. In this captured run, the comparison completed in two ReAct steps with two product-detail tool calls.

## Highlights

- **FastAPI conversation workspace**
  - A three-column interface with conversation management, chat, and a memory/state inspector.
  - Live state polling, tool-call inspection, and per-turn token and latency reporting.
  - Display history stays available after older Agent context is summarized.
- **Intent routing and fixed workflows**
  - Direct replies for greetings, acknowledgements, and requests that need clarification.
  - Deterministic workflows for single-product inventory, price, and specification queries.
  - ReAct for recommendations, comparisons, multi-step tasks, and requests that require dynamic tool selection.
- **ReAct execution core**
  - Tool calling, argument parsing, validation feedback, tool-result observation, loop limits, and final-answer fallback.
  - Session management is separated from the execution loop.
- **Unified local tool provider**
  - Tool schemas and local lookup entries are built from one definition table.
  - Pydantic models generate the model-facing JSON Schema and validate tool arguments before execution.
  - Tools return validated JSON strings with explicit error propagation.
- **RAG knowledge retrieval**
  - FAQ, guides, policies, and help documents are indexed in Qdrant.
  - BGE-M3 embeddings and BM25 keyword retrieval are available through a hybrid RRF retriever.
  - The knowledge-search tool is connected to the default Agent through a lazily initialized RAG service.
- **Conversation memory**
  - Recent user/assistant messages are combined with an incremental LLM-generated summary after the conversation grows beyond the configured threshold.
  - Tool traces remain separate from user-visible replies.
- **Dependency and lifecycle management**
  - The composition root owns shared LLM, tool, and retrieval resources.
  - The default LLM and RAG dependencies are initialized lazily when a request actually needs them.

## Architecture

```text
CLI / FastAPI Web Workspace
  └── Application
        └── ConversationRunner
              ├── IntentRouter
              ├── ProductQueryWorkflows
              └── ReActExecutor
                    └── LocalProvider
                          ├── local product/inventory functions
                          └── knowledge-search function

Knowledge search:
LocalProvider → LazySearchService → RagSearchAdapter → Retriever
                                      ├── Qdrant vector search
                                      └── BM25 + RRF hybrid search
```

## Requirements

- Python 3.11+
- A DeepSeek-compatible chat API for normal Agent requests
- Ollama with the `bge-m3` embedding model for RAG queries and ingestion
- `qdrant-client` for the local Qdrant store

Install Python dependencies:

```bash
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set `DEEPSEEK_API_KEY`. `LLM_MODEL` is optional.

## Run the CLI

```bash
python main.py
```

The default application loads local product and inventory data and exposes four tools:

- `search_products`
- `get_product_detail`
- `check_inventory`
- `search_knowledge`

Product and inventory requests use local data. The Qdrant client and embedding service are initialized only when the knowledge-search tool is first used.

## Run the web workspace

The FastAPI workspace provides a three-column interface: a sidebar for conversations and future extensions, a chat panel, and an inspector for conversation memory and `AgentState`.

```bash
python -m uvicorn web.app:app --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The frontend is served by FastAPI and does not require Node.js or a separate build step.

- Create, switch, and delete conversations. Each conversation has its own runner, history, and state; a browser cookie scopes access to its conversations.
- Inspect the actual incremental summary, recent messages, routing decision, ReAct step count, tool arguments/results, model usage, and full state. The page polls during execution; the final answer appears when the turn completes.
- Display history is kept separately from Agent memory, so summarizing older context does not remove chat bubbles.
- The existing composition root supplies the real local tools and lazy RAG service. Greetings and queries such as `NB001 有货吗` work without model requests; open-ended requests require the configured model API.
- Agent turns are queued across conversations because the current core shares lazy dependencies and a model usage log. Blocking execution runs outside the web event loop, keeping state and health endpoints responsive.

This is a local development workspace. Conversations live in process memory and disappear on restart. Run one server worker; multiple workers would have separate session stores. Browser scoping is not account authentication. The existing CLI entry point remains available.

Web code: `web/app.py` handles HTTP and resource lifecycle, `web/sessions.py` manages conversations and execution, and `web/static/` contains the interface. Interactive API documentation is available at `/docs`.

To reproduce the conversation in the screenshot, send these questions in the same session:

| Query | Execution path | What it demonstrates |
| --- | --- | --- |
| `NB002 有货吗` | Fixed workflow | Reads the local inventory record through `check_inventory`. |
| `它的价格` | Fixed workflow | Resolves the product reference from recent conversation history and calls `get_product_detail`. |
| `比较一下 NB001 和 NB002，哪台更适合带去学校写代码？请用三句话回答。` | ReAct Agent | Fetches both product records and uses the configured model to compare their specifications. |

The first two requests do not call the model. The comparison requires `DEEPSEEK_API_KEY`; its wording and usage can vary between runs. These examples use sample product data, not a live retail inventory feed.

## Build the knowledge index

The offline ingestion command loads FAQ and long-form knowledge documents, chunks them, creates BGE-M3 embeddings, and rebuilds the `knowledge` collection:

```bash
python -m rag.ingest
```

Make sure Qdrant local storage and Ollama are available before running ingestion. The generated local database under `rag/qdrant_data/` is intentionally ignored by Git.

## Test

The test suite uses local data and model/service doubles; it does not require live model requests for the normal test run:

```bash
python -m unittest discover -s tests -v
```

The tests cover routing boundaries, fixed workflow validation, tool-provider wiring, lazy resource initialization, conversation isolation, and error handling. Web integration tests also cover browser isolation, live state observation, request deduplication, execution queuing, and preservation of the display transcript after memory compaction.

## Evaluation notes

The repository includes the source data and documentation for the retrieval evaluation. Historical reports distinguish the original mixed product/knowledge corpus from the current knowledge-only RAG scope. Reported reranking numbers should be treated as experiment records and checked against the exact candidate count, reranker, labels, and execution logs before being used as a current benchmark.

## Project status

The core Agent, local tool layer, intent routing, fixed workflows, conversation summarization, default RAG tool wiring, and FastAPI web workspace are implemented. RAG-side production hardening remains open, including atomic index replacement, strict embedding-response validation, BM25 cache invalidation, pagination for large collections, robust long-paragraph chunking, and retry/circuit-breaker policy for external dependencies.

## License

No license has been selected yet.
