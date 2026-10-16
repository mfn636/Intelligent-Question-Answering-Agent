"""Assemble the existing Agent with observation wrappers and probe dependencies."""
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
import time

from eval.common import ROOT, BudgetExceeded, Unavailable, append_jsonl, digest, safe_error, write_json
from eval.recorders import (ObservedLLM, ObservedProvider, ObservedSearch, ObservedSummarizer, ObservedWorkflows,
                            Recorder, observe_function)


def open_retriever(resources):
    from qdrant_client import QdrantClient
    from rag.store import DEFAULT_PATH, VectorStore
    from rag.embedder import Embedder
    from rag.retriever import Retriever
    # Opening Qdrant on a missing directory can create it: refuse first.
    if not Path(DEFAULT_PATH).is_dir():
        raise Unavailable("Knowledge index is missing; evaluation never creates/rebuilds it")
    client = QdrantClient(path=DEFAULT_PATH)
    resources.callback(client.close)
    return Retriever(VectorStore(client), Embedder())


def audit_index(retriever):
    from rag.collections import COLLECTION_KNOWLEDGE
    from rag.ingest import build_collection_specs
    client = retriever.store.client
    if not client.collection_exists(COLLECTION_KNOWLEDGE):
        raise Unavailable("knowledge collection is missing")
    actual = {}
    offset = None
    while True:
        points, offset = client.scroll(COLLECTION_KNOWLEDGE, limit=256, offset=offset,
                                       with_payload=True, with_vectors=False)
        for point in points:
            payload = dict(point.payload)
            cid = payload.pop("_cid", None)
            text = payload.pop("_text", None)
            if cid in actual:
                raise Unavailable("Duplicate chunk IDs in index")
            actual[cid] = {"text": text, "payload": payload}
        if offset is None:
            break
    expected = {c["id"]: {"text": c["text"], "payload": c["payload"]}
                for c in build_collection_specs()[0].build_chunks()}
    if actual != expected:
        raise Unavailable(f"Index/corpus mismatch: actual {len(actual)} chunks vs expected {len(expected)}; rebuild deliberately outside evaluation")
    vectors = client.get_collection(COLLECTION_KNOWLEDGE).config.params.vectors
    return {"chunks": len(actual), "payload_sha256": digest(actual), "ids": sorted(actual),
            "vector_config": vectors.model_dump() if hasattr(vectors, "model_dump") else str(vectors),
            "embedding_provenance": "Configured bge-m3; historical vector model provenance is not stored by ingestion"}


def probe_worker(payload, directory):
    import math
    from rag.embedder import EMBED_DIM
    try:
        with ExitStack() as resources:
            retriever = open_retriever(resources)
            audit = audit_index(retriever)
            vector = retriever.embedder.embed("评测依赖检查")
            if len(vector) != EMBED_DIM or any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in vector):
                raise Unavailable("Invalid embedding response")
            write_json(Path(directory) / "status.json", {"status": "completed", "index": audit})
    except Exception as exc:
        write_json(Path(directory) / "status.json", {"status": "unavailable", "error": safe_error(exc)})


def assemble(resources, recorder, *, variant, rag_available=True, llm=None):
    from bootstrap import _build_llm, build_agent
    from agent.memory.summary import ConversationSummarizer
    from rag.service import LazySearchService, RagSearchAdapter
    from storage.json_loader import load_ecommerce_data
    from tool_definitions.tool_tables import build_tool_tables
    from tool_providers.local import LocalProvider

    def build_search():
        if not rag_available:
            raise Unavailable("RAG preflight unavailable; actual query cannot be evaluated")
        return RagSearchAdapter(open_retriever(resources))

    service = LazySearchService(build_search) if rag_available else None
    if service is not None:
        resources.callback(service.close)
    # When the caller explicitly skips an unavailable RAG dependency, do not advertise
    # a knowledge tool that cannot execute. This keeps non-RAG cases measurable.
    observed_search = ObservedSearch(service, recorder) if service is not None else None
    schemas, lookup = build_tool_tables(data=load_ecommerce_data(), search_service=observed_search)
    for name, entry in lookup.items():
        entry["fn"] = observe_function(name, entry["fn"], recorder)
    provider = ObservedProvider(LocalProvider(tool_schemas=schemas, tool_lookup=lookup), recorder)
    llm = ObservedLLM(llm or _build_llm(resources), recorder, lookup)
    runner = build_agent(llm=llm, tools=provider)
    runner.router.llm = llm.for_stage("router")
    runner.workflows = ObservedWorkflows(runner.workflows, recorder)
    runner.summarizer = ObservedSummarizer(ConversationSummarizer(llm=llm.for_stage("summary")), recorder)
    if variant == "react":
        runner.router = None
    return runner, llm, schemas


def agent_worker(payload, directory):
    path = Path(directory)
    recorder = Recorder(path, max_calls=payload["max_calls"], max_tokens=payload["max_tokens"], fault=payload["case"].get("fault"))
    status = {"status": "completed"}
    try:
        with ExitStack() as resources:
            runner, _, schemas = assemble(resources, recorder, variant=payload["variant"], rag_available=payload["rag_available"])
            write_json(path / "tool_schemas.json", schemas)
            for turn_number, turn in enumerate(payload["case"]["turns"], 1):
                recorder.turn = turn_number
                recorder.emit("turn_start", user=turn["user"])
                started = time.perf_counter()
                error, result = None, None
                try:
                    # Only user text enters the production runner; no expected facts or labels.
                    result = runner.run(turn["user"])
                except (Exception, BudgetExceeded) as exc:
                    error = safe_error(exc)
                    status = {"status": "budget_exhausted" if isinstance(exc, BudgetExceeded) else "error", "error": error}
                elapsed = (time.perf_counter() - started) * 1000
                row = {"turn": turn_number, "user": turn["user"], "replies": result.replies if result else [],
                       "error": error, "elapsed_ms": elapsed, "state": asdict(runner.state),
                       "history": {"summary": runner.history.summary, "messages": runner.history.messages}}
                append_jsonl(path / "turns.jsonl", row)
                recorder.emit("turn_end", elapsed_ms=elapsed, error=error)
                if error:
                    break
    except (Exception, BudgetExceeded) as exc:
        status = {"status": "error", "error": safe_error(exc)}
    finally:
        write_json(path / "status.json", status)
