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
- **Docker packaging**
  - A non-root CLI image and Docker Compose configuration with an optional Ollama service.
  - Persistent volumes for model files and the knowledge index, with API credentials supplied at runtime.

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

For local Python runs:

- Python 3.11+
- A DeepSeek-compatible chat API for normal Agent requests
- Ollama with the `bge-m3` embedding model for RAG queries and ingestion
- `qdrant-client` for the local Qdrant store

Install Python dependencies:

```bash
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set `DEEPSEEK_API_KEY`. `LLM_MODEL` is optional.

For containerized runs, follow [Run with Docker](#run-with-docker). A host Python
installation is not required.

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

## Run with Docker

Install Docker with the Compose v2 plugin (Docker Desktop on Windows/macOS),
copy `.env.example` to `.env`, and set `DEEPSEEK_API_KEY` for model-backed requests.
The container runs the same interactive CLI; it does not expose an HTTP API.

Build the image and open a conversation:

```bash
docker compose build agent
docker compose run --rm agent
```

Enter `exit` to close the conversation. Rule-matched greetings and fixed product
queries do not require an API key. Knowledge retrieval additionally requires
the RAG setup below.

### Enable knowledge retrieval

Start the optional Ollama service, download BGE-M3, and build the knowledge index:

```bash
docker compose --profile rag up -d --wait ollama
docker compose exec ollama ollama pull bge-m3
docker compose run --rm agent python -m rag.ingest
docker compose run --rm agent
```

The first model download and index build can take several minutes. The bundled
Ollama service uses CPU by default and is reachable only on the Compose network;
no host ports are published. Models are stored in the `ollama_models` volume and
the local Qdrant database in `qdrant_data`. Subsequent conversations reuse both.
Conversation history remains in memory and ends with the CLI process.

Ingestion rebuilds the knowledge collection. Exit any Agent process using the
index before running ingestion: Qdrant local storage supports only one process
accessing the same database directory. Multiple Agent containers cannot share
this volume concurrently.

To reuse an existing Ollama server instead of starting the bundled service, set
`OLLAMA_URL` in `.env` to an address reachable from the container. On Docker
Desktop, `http://host.docker.internal:11434` addresses the host machine; Ollama
must accept connections from Docker. Linux users can use a reachable server
address or configure a host-gateway mapping. For local Python runs, export
`OLLAMA_URL` in the process environment; its default is
`http://127.0.0.1:11434`.

The image includes source code and sample business data. `.env`, Git history,
local indexes, and caches are excluded from the build context. API credentials
are supplied at runtime by Compose. The container starts with a fresh database
volume; it does not copy your existing host index.

Stop the optional background service while preserving its volumes:

```bash
docker compose --profile rag down
```

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
