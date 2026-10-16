"""Small file, identity and process helpers shared by evaluation commands."""
from __future__ import annotations

import hashlib
import json
import math
import multiprocessing as mp
import platform
import re
import subprocess
import time
import uuid
from importlib.metadata import version, PackageNotFoundError
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "eval" / "results"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def append_jsonl(path, value):
    # Close after each record: progress survives termination of the worker.
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = []
    for index, line in enumerate(lines):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # Only tolerate a torn final write after a worker was killed.
            if index != len(lines) - 1:
                raise
    return rows


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def text_file_hash(path):
    """Fingerprint text fixtures consistently across Git's LF/CRLF conversion."""
    content = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(content).hexdigest()


def snapshot():
    files = sorted({*ROOT.glob("agent/**/*.py"), *ROOT.glob("contract/*.py"),
                    *ROOT.glob("llm/*.py"), *ROOT.glob("prompts/*.py"),
                    *ROOT.glob("routing/*.py"), *ROOT.glob("workflows/*.py"),
                    *ROOT.glob("tool_*/*.py"), *ROOT.glob("rag/*.py"),
                    *ROOT.glob("eval/*.py"), *ROOT.glob("storage/*.py"), ROOT / "bootstrap.py"})
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    packages = {}
    for name in ("openai", "pydantic", "qdrant-client", "requests", "rank-bm25", "jieba"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {
        "commit": commit, "python": platform.python_version(), "platform": platform.platform(),
        "packages": packages,
        "code": {str(p.relative_to(ROOT)).replace("\\", "/"): file_hash(p) for p in files},
        "data": {f"data/{name}.json": file_hash(ROOT / "data" / f"{name}.json")
                 for name in ("products", "inventory", "faq", "guides")},
    }


def new_run(kind, config):
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + kind + "-" + uuid.uuid4().hex[:6]
    directory = RESULTS / run_id
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "config.json", {"run_id": run_id, "created_utc": datetime.now(timezone.utc).isoformat(),
                                          "kind": kind, **config, "snapshot": snapshot()})
    return directory


def safe_error(error):
    # Never persist credentials in an upstream exception message.
    text = str(error)
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)(bearer\s+)[^\s'\"]+", r"\1[REDACTED]", text)
    return {"type": type(error).__name__, "message": text[:1500]}


class BudgetExceeded(BaseException):
    """Bypass production Exception fallbacks; the evaluation worker catches this."""


class Unavailable(Exception):
    """A dependency was unavailable before executing the case."""


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    pos = (len(values) - 1) * q
    lower = math.floor(pos)
    return values[lower] + (values[math.ceil(pos)] - values[lower]) * (pos - lower)


def supervised(target, payload, directory, *, case_timeout, turn_timeout):
    """Windows-safe isolation. Killing a worker prevents background API spending."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    process = mp.get_context("spawn").Process(target=target, args=(payload, str(directory)))
    started = time.monotonic()
    process.start()
    outcome = None
    active = None
    try:
        while process.is_alive():
            process.join(.1)
            if not process.is_alive():
                break
            events = read_jsonl(directory / "events.jsonl")
            opened = [e for e in events if e["kind"] == "turn_start"]
            closed = {e["turn"] for e in events if e["kind"] == "turn_end"}
            active = next((e for e in reversed(opened) if e["turn"] not in closed), None)
            if time.monotonic() - started > case_timeout:
                outcome = "case_timeout"
            elif active and time.time() - active["time"] > turn_timeout:
                outcome = "turn_timeout"
            if outcome:
                break
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join()
    process.join()
    status_file = directory / "status.json"
    status = read_json(status_file) if status_file.exists() else {"status": "worker_error", "exitcode": process.exitcode}
    if outcome:
        status = {"status": "timeout", "reason": outcome}
        if active:
            status["active_turn_elapsed_ms"] = (time.time() - active["time"]) * 1000
    status["worker_elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
    write_json(directory / "status.json", status)
    return status
