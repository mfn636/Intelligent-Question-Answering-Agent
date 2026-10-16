"""Evaluation-only observers. Production routing, prompts and tools stay unchanged."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from functools import wraps
import json
import time

from eval.common import BudgetExceeded, append_jsonl, safe_error


class Recorder:
    def __init__(self, directory, *, max_calls=100, max_tokens=100000, fault=None):
        from pathlib import Path
        self.path = Path(directory) / "events.jsonl"
        self.turn = 0
        self.calls = 0
        self.tokens = 0
        self.max_calls = max_calls
        self.max_tokens = max_tokens
        self.fault = dict(fault or {})
        self.fault_uses = 0
        self.active_tool = None
        self.stage = "executor"
        self.tool_source = "model"

    def emit(self, kind, **fields):
        event = {"kind": kind, "turn": self.turn, "time": time.time(), **fields}
        append_jsonl(self.path, event)
        return event

    def before_llm(self):
        if self.calls >= self.max_calls or self.tokens >= self.max_tokens:
            raise BudgetExceeded("Model call/token budget exhausted before next request")
        self.calls += 1
        return self.calls

    def inject(self, kinds, *, tool=None):
        if self.fault.get("kind") not in kinds or self.fault_uses >= self.fault.get("times", 1):
            return False
        if tool and self.fault.get("tool") != tool:
            return False
        self.fault_uses += 1
        self.emit("fault_injected", fault=self.fault["kind"], tool=tool)
        return True


def argument_check(name, raw, lookup):
    """Inspect using the SAME model classes; do not replace provider validation."""
    if name not in lookup:
        return {"valid": False, "error_stage": "lookup", "args": None}
    try:
        args = json.loads(raw)
        if not isinstance(args, dict):
            raise ValueError("arguments are not an object")
    except (ValueError, TypeError):
        return {"valid": False, "error_stage": "parse", "args": None}
    try:
        lookup[name]["args_model"].model_validate(args)
    except Exception:
        return {"valid": False, "error_stage": "validation", "args": args}
    return {"valid": True, "error_stage": None, "args": args}


class ObservedLLM:
    def __init__(self, llm, recorder, lookup=None, stage="executor"):
        self.llm, self.recorder, self.lookup, self.stage = llm, recorder, lookup or {}, stage

    @property
    def usage_log(self):
        return self.llm.usage_log

    def for_stage(self, stage):
        return ObservedLLM(self.llm, self.recorder, self.lookup, stage)

    def chat(self, **kwargs):
        r = self.recorder
        stage = "finalize" if self.stage == "executor" and kwargs.get("tools") is None else self.stage
        call_id = r.before_llm()
        mark = len(self.usage_log)
        started = time.perf_counter()
        r.stage = stage
        # Labels are never passed here. These are the actual runtime messages only.
        r.emit("llm_start", call_id=call_id, stage=stage, messages=deepcopy(kwargs.get("messages", [])),
               parameters={"temperature": .4, "max_tokens": 1024, "stream": False, "thinking": None,
                           **{k: v for k, v in kwargs.items() if k not in ("messages", "tools")}},
               tool_names=[s["function"]["name"] for s in kwargs.get("tools") or []])
        response = None
        error = None
        injected_failure = False
        try:
            if (stage == "summary" and r.inject({"summary_error"})) or (stage == "executor" and r.inject({"llm_error"})):
                injected_failure = True
                raise TimeoutError("Injected model dependency timeout before HTTP request")
            response = self.llm.chat(**kwargs)
            response = self._inject_arguments(response) if stage == "executor" else response
            for tc in getattr(response, "tool_calls", None) or []:
                injected = tc.id in getattr(self, "_injected_ids", set())
                r.emit("model_tool_request", call_id=call_id, tool_call_id=tc.id, name=tc.function.name,
                       raw_arguments=tc.function.arguments, injected=injected,
                       **argument_check(tc.function.name, tc.function.arguments, self.lookup))
            return response
        except BaseException as exc:
            error = safe_error(exc)
            raise
        finally:
            entries = self.usage_log[mark:]
            usage = entries[-1] if entries and not entries[-1].get("failed") else None
            # A successful response with all-zero counters may mean missing upstream usage.
            if usage and usage.get("total_tokens", 0) <= 0:
                usage = None
            if usage:
                r.tokens += usage["total_tokens"]
            r.emit("llm_end", call_id=call_id, stage=stage, usage=usage,
                   usage_unknown=usage is None and not injected_failure, injected=injected_failure,
                   error=error, elapsed_ms=(time.perf_counter() - started) * 1000,
                   content=getattr(response, "content", None))

    def _inject_arguments(self, response):
        self._injected_ids = set()
        kinds = {"invalid_json", "unknown_tool", "wrong_type", "extra_arg", "missing_arg", "non_object"}
        for tc in getattr(response, "tool_calls", None) or []:
            if not self.recorder.inject(kinds, tool=tc.function.name):
                continue
            # Preserve the unmodified natural output separately for audit.
            self.recorder.emit("original_model_tool_request", name=tc.function.name, raw_arguments=tc.function.arguments)
            response = deepcopy(response)
            changed = next(item for item in response.tool_calls if item.id == tc.id)
            kind = self.recorder.fault["kind"]
            if kind == "unknown_tool":
                changed.function.name = "nonexistent_eval_tool"
            elif kind == "invalid_json":
                changed.function.arguments = '{"product_id":"NB001"'
            elif kind == "non_object":
                changed.function.arguments = "[]"
            else:
                args = {"product_id": "NB001"}
                if kind == "wrong_type":
                    args["product_id"] = 1
                elif kind == "extra_arg":
                    args["unexpected"] = True
                elif kind == "missing_arg":
                    args = {}
                changed.function.arguments = json.dumps(args)
            self._injected_ids.add(tc.id)
            break
        return response


def observe_function(name, fn, recorder):
    @wraps(fn)
    def observed(**kwargs):
        active = recorder.active_tool
        if active is not None:
            active["entered"] = True
        recorder.emit("function_start", name=name, args=kwargs)
        started = time.perf_counter()
        error = None
        try:
            if recorder.inject({"temporary_error", "persistent_error"}, tool=name):
                raise TimeoutError("Injected local dependency failure")
            if recorder.inject({"invalid_result", "non_string"}, tool=name):
                return "{broken" if recorder.fault["kind"] == "invalid_result" else {"unexpected": "dict"}
            return fn(**kwargs)
        except BaseException as exc:
            error = safe_error(exc)
            if active is not None:
                active["body_error"] = True
            raise
        finally:
            recorder.emit("function_end", name=name, error=error, elapsed_ms=(time.perf_counter() - started) * 1000)
    return observed


class ObservedProvider:
    def __init__(self, provider, recorder):
        self.provider, self.recorder = provider, recorder

    def list_tools(self):
        return self.provider.list_tools()

    def call(self, name, args):
        r = self.recorder
        active = {"entered": False, "body_error": False}
        r.active_tool = active
        started = time.perf_counter()
        r.emit("tool_start", name=name, args=args, source=r.tool_source)
        result = None
        error = None
        try:
            result = self.provider.call(name, args)
            return result
        except BaseException as exc:
            error = safe_error(exc)
            raise
        finally:
            stage = None
            if error:
                names = {t["function"]["name"] for t in self.list_tools()}
                stage = "execution" if active["body_error"] else "result_contract" if active["entered"] else "validation" if name in names else "lookup"
            r.emit("tool_end", name=name, args=args, result=result, error=error, error_stage=stage,
                   function_entered=active["entered"], source=r.tool_source, elapsed_ms=(time.perf_counter() - started) * 1000)
            r.active_tool = None


class ObservedSearch:
    def __init__(self, service, recorder):
        self.service, self.recorder = service, recorder

    def search(self, collection, query, filters=None, top_k=5):
        started = time.perf_counter()
        hits, error = [], None
        try:
            if not self.recorder.inject({"empty_search"}):
                hits = self.service.search(collection, query, filters=filters, top_k=top_k)
            return hits
        except BaseException as exc:
            error = safe_error(exc)
            raise
        finally:
            self.recorder.emit("retrieval", collection=collection, query=query,
                               filters=asdict(filters) if filters else None, top_k=top_k,
                               hits=[asdict(hit) for hit in hits], error=error,
                               elapsed_ms=(time.perf_counter() - started) * 1000,
                               mode="hybrid", rerank="disabled")


class ObservedSummarizer:
    def __init__(self, summarizer, recorder):
        self.summarizer, self.recorder = summarizer, recorder

    def summarize(self, previous_summary, messages):
        before = deepcopy(messages)
        try:
            summary = self.summarizer.summarize(previous_summary, messages)
        except Exception as exc:
            self.recorder.emit("summary", success=False, previous=previous_summary, outgoing=before, error=safe_error(exc))
            raise
        self.recorder.emit("summary", success=True, previous=previous_summary, outgoing=before, summary=summary)
        return summary


class ObservedWorkflows:
    def __init__(self, workflows, recorder):
        self.workflows, self.recorder = workflows, recorder

    def supports(self, intent):
        return self.workflows.supports(intent)

    def execute(self, decision, state):
        self.recorder.tool_source = "workflow"
        try:
            return self.workflows.execute(decision, state)
        finally:
            self.recorder.tool_source = "model"
