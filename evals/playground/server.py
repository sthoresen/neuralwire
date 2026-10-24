"""
The playground: paste a prompt, pick models, load cases, run, and read or label the results in
one local page.

  python -m evals playground      then open http://localhost:8770

Files, all under evals/:
  datasets/<name>.jsonl           the cases, one JSON object per line (gitignored)
  datasets/<name>.labels.json     your YES/NO label per case (gitignored, beside its data)
  runs/playground/<run>.json      every run: the prompt, the models, every reply (gitignored)
  highscores.jsonl                scores you saved, one line each (committed)
  playground/models.json          the models the page offers

Model calls go through evals.llm, so they are cached and costed like every other eval: running
the same prompt on the same case and model again is free.
"""

import csv
import io
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

from evals import EVALS_DIR, store
from evals.llm import complete
from evals.playground import core

HERE = Path(__file__).parent
DATASETS = EVALS_DIR / "datasets"
RUNS = EVALS_DIR / "runs" / "playground"
HIGHSCORES = EVALS_DIR / "highscores.jsonl"
WORKERS = 8
SAVE_EVERY = 20  # replies between saves of a running run, so a reload shows progress

SINGLE = "prompt"  # the one "case" of a playground run
_runs: dict[str, dict] = {}  # runs started by this server, while they run
_lock = threading.Lock()
_prices: dict[str, tuple[float, float]] = {}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text.strip())[:60] or "dataset"


# ── Models and prompts ───────────────────────────────────────────────────────


def models() -> list[dict]:
    """The page's model list, with prices per million tokens where OpenRouter lists them."""
    if not _prices:
        try:
            for m in requests.get("https://openrouter.ai/api/v1/models", timeout=20).json()["data"]:
                p = m.get("pricing") or {}
                _prices[m["id"]] = (
                    float(p.get("prompt") or 0) * 1e6,
                    float(p.get("completion") or 0) * 1e6,
                )
        except (requests.RequestException, ValueError, KeyError):
            pass  # estimates just show as unknown
    out = []
    for m in json.loads((HERE / "models.json").read_text()):
        price_in, price_out = _prices.get(m["id"], (None, None))
        out.append({**m, "price_in": price_in, "price_out": price_out})
    return out


def production_prompts() -> dict[str, str]:
    """NeuralWire's prompts (python_backend/prompts.py), to start from."""
    import prompts

    return {
        k: v
        for k, v in vars(prompts).items()
        if isinstance(v, str) and not k.startswith("_") and not k.endswith("_ID") and "{" in v
    }


# ── Datasets and labels ──────────────────────────────────────────────────────


def parse_rows(text: str) -> list[dict]:
    """JSONL (one object per line) or CSV with a header row."""
    lines = [line for line in text.splitlines() if line.strip()]
    if lines and lines[0].lstrip().startswith("{"):
        return [json.loads(line) for line in lines]
    return [dict(r) for r in csv.DictReader(io.StringIO(text))]


def save_dataset(name: str, rows: list[dict]) -> str:
    name = _name(name)
    DATASETS.mkdir(parents=True, exist_ok=True)
    (DATASETS / f"{name}.jsonl").write_text(
        "".join(json.dumps(r, default=str) + "\n" for r in rows)
    )
    return name


def load_dataset(name: str) -> list[dict]:
    path = DATASETS / f"{_name(name)}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def datasets() -> list[dict]:
    out = []
    for path in sorted(DATASETS.glob("*.jsonl")):
        rows = sum(1 for line in path.read_text().splitlines() if line.strip())
        out.append({"name": path.stem, "rows": rows, "labelled": len(load_labels(path.stem))})
    return out


def load_labels(dataset: str) -> dict[str, str]:
    path = DATASETS / f"{_name(dataset)}.labels.json"
    return json.loads(path.read_text()) if path.exists() else {}


def set_labels(dataset: str, labels: dict[str, str | None]) -> dict[str, str]:
    """Sets or (with None) clears labels; returns them all."""
    with _lock:
        current = load_labels(dataset)
        for case, label in labels.items():
            if label in (core.YES, core.NO):
                current[case] = label
            else:
                current.pop(case, None)
        (DATASETS / f"{_name(dataset)}.labels.json").write_text(json.dumps(current, indent=1))
    return current


def check_prompt(prompt: str, dataset: str | None, extra: dict) -> dict:
    """The prompt's placeholders, which ones the cases don't fill, and the average filled length."""
    rows = load_dataset(dataset) if dataset else []
    names = core.placeholders(prompt)
    columns = set().union(*(r.keys() for r in rows)) if rows else set()
    filled = [len(core.fill(prompt, {**extra, **r})) for r in rows] or [len(prompt)]
    clip = lambda v: v if not isinstance(v, str) or len(v) <= 240 else v[:240] + "…"  # noqa: E731
    return {
        "placeholders": names,
        "missing": [n for n in names if n not in columns],
        "columns": sorted(columns),
        "rows": len(rows),
        "calls_per_model": len(rows) if names else 1,
        "sample": [{k: clip(v) for k, v in r.items()} for r in rows[:2]],
        "avg_chars": sum(filled) / len(filled),
    }


# ── Runs ─────────────────────────────────────────────────────────────────────


def _column(m: dict) -> str:
    return f"{m['id']}|{m.get('reasoning') or 'off'}"


def _save(run: dict) -> None:
    RUNS.mkdir(parents=True, exist_ok=True)
    with _lock:
        body = json.dumps({k: v for k, v in run.items() if not k.startswith("_")})
    (RUNS / f"{run['id']}.json").write_text(body)


def load_run(run_id: str) -> dict:
    if run_id in _runs:
        return _runs[run_id]
    return json.loads((RUNS / f"{_name(run_id)}.json").read_text())


def runs() -> list[dict]:
    """Every run, newest first, with the start of its prompt so playground runs can be told apart."""
    out = []
    for path in sorted(RUNS.glob("*.json"), reverse=True):
        r = _runs.get(path.stem) or json.loads(path.read_text())
        out.append(
            {
                **{k: r[k] for k in ("id", "name", "dataset", "mode", "status", "created")},
                "preview": " ".join(r["prompt"].split())[:80],
            }
        )
    return out


def _call(run: dict, column: str, model: dict, case: str, row: dict) -> None:
    if run["status"] == "cancelled" or run["columns"][column].get("error"):
        return
    if run["mode"] == "playground":
        prompt = run["prompt"]  # sent as written
    else:
        prompt = core.fill(run["prompt"], {**run["extra"], **row})
    effort = model.get("reasoning") or "off"
    reasoning = False if effort == "off" else effort
    max_tokens = run["max_tokens"] if effort == "off" else run["max_tokens"] + 4000
    try:
        c = complete(model["id"], prompt, max_tokens=max_tokens, reasoning=reasoning)
    except RuntimeError as e:  # no credits, a bad key or an unknown model: stop this column
        run["columns"][column]["error"] = str(e)[:300]
        return
    result = {
        "text": c.text,
        "cost_usd": c.cost_usd,
        "latency_s": c.latency_s,
        "tokens_out": c.completion_tokens,
        "reasoning_tokens": c.reasoning_tokens,
        "cached": c.cached,
    }
    if run["mode"] == "classify":
        result["answer"] = core.answer(c.text, run["answer_field"])
    with _lock:
        run["columns"][column]["results"][case] = result
        run["done"] += 1
        save_now = run["done"] % SAVE_EVERY == 0
    if save_now:
        _save(run)


def start_run(spec: dict) -> str:
    """
    A run of one prompt on every chosen model. Without a dataset it is a playground run: the
    prompt is sent exactly as written, once per model. With one, it is a batch: the prompt is
    filled in for every case.
    """
    single = not spec.get("dataset")
    rows = [{"id": SINGLE}] if single else load_dataset(spec["dataset"])
    if not single and not core.placeholders(spec["prompt"]):
        rows = rows[:1]  # the same prompt for every case: once per model is enough
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run = {
        "id": f"{stamp}_{_name(spec.get('name') or 'untitled')}",
        "name": spec.get("name") or "untitled",
        "dataset": "" if single else _name(spec["dataset"]),
        "mode": "playground" if single else spec.get("mode", "writing"),
        "prompt": spec["prompt"],
        "extra": spec.get("extra") or {},
        "answer_field": spec.get("answer_field", ""),
        "max_tokens": int(spec.get("max_tokens") or 1000),
        "models": spec["models"],
        "created": _now(),
        "status": "running",
        "total": len(rows) * len(spec["models"]),
        "done": 0,
        "cases": [core.case_id(r) for r in rows],
        "columns": {
            _column(m): {"model": m["id"], "reasoning": m.get("reasoning") or "off", "results": {}}
            for m in spec["models"]
        },
        "notes": {},
    }
    _runs[run["id"]] = run
    _save(run)

    def work():
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for row in rows:  # row by row, so every model's column fills at the same pace
                for m in run["models"]:
                    pool.submit(_call, run, _column(m), m, core.case_id(row), row)
        if run["status"] == "running":
            run["status"] = "done"
        run["finished"] = _now()
        _save(run)

    threading.Thread(target=work, daemon=True).start()
    return run["id"]


def summary(run: dict) -> dict:
    """Cost, time and, for classify runs, the score per column against the current labels."""
    labels = load_labels(run["dataset"]) if run["mode"] == "classify" else {}
    out = {}
    for key, col in run["columns"].items():
        res = col["results"].values()
        s = {
            "replies": len(res),
            "cost_usd": round(sum(r["cost_usd"] for r in res), 5),
            "latency_s_mean": round(sum(r["latency_s"] for r in res) / len(res), 2)
            if res
            else None,
        }
        if run["mode"] == "classify":
            s.update(core.score({c: r["answer"] for c, r in col["results"].items()}, labels))
        out[key] = s
    return out


def save_highscore(run_id: str, column: str, comment: str) -> dict:
    run = load_run(run_id)
    col = run["columns"][column]
    row = {
        "at": _now(),
        "dataset": run["dataset"],
        "prompt_name": run["name"],
        "prompt_hash": core.case_id({"prompt": run["prompt"]})[:8],
        "model": col["model"],
        "reasoning": col["reasoning"],
        **summary(run)[column],
        "comment": comment,
        "run_id": run_id,
    }
    with _lock, HIGHSCORES.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def highscores() -> list[dict]:
    if not HIGHSCORES.exists():
        return []
    return [json.loads(line) for line in HIGHSCORES.read_text().splitlines() if line.strip()]


# ── HTTP ─────────────────────────────────────────────────────────────────────


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, status=200):
        body = json.dumps(obj, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                body = (HERE / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/setup":
                self._json(
                    {"models": models(), "prompts": production_prompts(), "datasets": datasets()}
                )
            elif path == "/api/runs":
                self._json(runs())
            elif path.startswith("/api/runs/"):
                run = load_run(path.rsplit("/", 1)[1])
                rows = (
                    {core.case_id(r): r for r in load_dataset(run["dataset"])}
                    if run["dataset"]
                    else {}
                )
                self._json(
                    {
                        "run": run,
                        "summary": summary(run),
                        "labels": load_labels(run["dataset"]),
                        "rows": rows,
                    }
                )
            elif path == "/api/highscores":
                self._json({"playground": highscores(), "event_scan": store.scan_scores()})
            else:
                self._json({"error": "not found"}, 404)
        except FileNotFoundError:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or "{}")
        try:
            if self.path == "/api/datasets":
                rows = body.get("rows") or parse_rows(body.get("text", ""))
                self._json({"name": save_dataset(body["name"], rows), "rows": len(rows)})
            elif self.path == "/api/placeholders":
                self._json(
                    check_prompt(
                        body.get("prompt", ""), body.get("dataset"), body.get("extra") or {}
                    )
                )
            elif self.path == "/api/run":
                self._json({"id": start_run(body)})
            elif self.path == "/api/cancel":
                load_run(body["id"])["status"] = "cancelled"
                self._json({"ok": True})
            elif self.path == "/api/labels":
                self._json(set_labels(body["dataset"], body["labels"]))
            elif self.path == "/api/note":
                run = load_run(body["id"])
                run["notes"].setdefault(body["column"], {})[body["case"]] = body.get("note", "")
                _save(run)
                self._json({"ok": True})
            elif self.path == "/api/highscores":
                self._json(save_highscore(body["id"], body["column"], body.get("comment", "")))
            else:
                self._json({"error": "not found"}, 404)
        except (KeyError, ValueError, FileNotFoundError, json.JSONDecodeError) as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 400)

    def log_message(self, *args):  # keep the terminal quiet
        pass


def serve(port: int = 8770) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Playground: http://localhost:{port}   (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
