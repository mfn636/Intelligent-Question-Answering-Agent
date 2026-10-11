# Intelligent Question Answering Agent

An e-commerce question-answering agent built with ReAct, tool calling, hybrid retrieval, and structured conversation memory.

The system handles product recommendations, product specifications, inventory questions, and customer-support knowledge queries. A lightweight intent router sends simple requests to deterministic replies or fixed workflows, while open-ended requests continue through the ReAct loop.

## Highlights

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
Application
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

The tests cover routing boundaries, fixed workflow validation, tool-provider wiring, lazy resource initialization, conversation isolation, and error handling.

## Evaluation notes

The repository includes the source data and documentation for the retrieval evaluation. Historical reports distinguish the original mixed product/knowledge corpus from the current knowledge-only RAG scope. Reported reranking numbers should be treated as experiment records and checked against the exact candidate count, reranker, labels, and execution logs before being used as a current benchmark.

## Project status

The core Agent, local tool layer, intent routing, fixed workflows, conversation summarization, and default RAG tool wiring are implemented. RAG-side production hardening remains open, including atomic index replacement, strict embedding-response validation, BM25 cache invalidation, pagination for large collections, robust long-paragraph chunking, and retry/circuit-breaker policy for external dependencies.

## License

No license has been selected yet.
