"""
Runs, verdicts and comments for each eval problem, and the leaderboard built from them.

  runs/<problem>/<run_id>.jsonl   first line: the run (variant, models, prompt, cost, metrics);
                                  then one line per output, with what the review page shows
  labels/<problem>.jsonl          verdicts, appended: {key, judge, verdict, note, at}
  comments/<problem>.jsonl        your notes on whole runs, appended: {run_id, text, at}

Outputs are identified by a key the problem chooses (for events: ticker and source article),
so a verdict on an output carries over to every run that produces the same output. The latest
line per key and judge wins.
"""

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from evals import EVALS_DIR
from evals.llm import Completion

ROOT = EVALS_DIR
JUDGES = ("you", "claude")
VERDICTS = ("keep", "skip")
_write_lock = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _append(path: Path, row: dict) -> None:
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(row, default=str) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── Runs ─────────────────────────────────────────────────────────────────────


class Run:
    """One variant run over one dataset. Collects LLM calls and outputs; save() writes it."""

    def __init__(
        self, problem: str, variant: str, dataset: str, models: dict, prompt_id: str, params: dict
    ):
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        # runs started in the same second (in parallel) need distinct ids
        setup = hashlib.sha256(json.dumps([models, params], sort_keys=True).encode()).hexdigest()[:6]
        self.meta = {
            "type": "run",
            "run_id": f"{stamp}_{variant}_{setup}",
            "problem": problem,
            "variant": variant,
            "dataset": dataset,
            "models": models,
            "prompt_id": prompt_id,
            "params": params,
            "started_at": _now(),
        }
        self.calls: list[Completion] = []
        self.outputs: list[dict] = []
        self._lock = threading.Lock()

    def record_call(self, c: Completion) -> None:
        with self._lock:
            self.calls.append(c)

    def add_output(
        self, key: str, title: str, meta: str = "", body: str = "", context=(), fields=None
    ):
        """context: list of {"text", "url"?, "date"?} shown under the output on the review page."""
        self.outputs.append(
            {
                "type": "output",
                "key": key,
                "title": title,
                "meta": meta,
                "body": body,
                "context": list(context),
                "fields": fields or {},
            }
        )

    def save(self, metrics: dict | None = None) -> Path:
        fresh = [c for c in self.calls if not c.cached]
        self.meta.update(
            finished_at=_now(),
            calls=len(self.calls),
            cached_calls=len(self.calls) - len(fresh),
            cost_usd=round(sum(c.cost_usd for c in self.calls), 5),  # what the variant costs
            spent_usd=round(sum(c.cost_usd for c in fresh), 5),  # what this run spent
            latency_s_mean=round(sum(c.latency_s for c in self.calls) / len(self.calls), 3)
            if self.calls
            else None,
            completion_tokens_mean=round(
                sum(c.completion_tokens for c in self.calls) / len(self.calls)
            )
            if self.calls
            else None,
            reasoning_tokens_mean=round(
                sum(c.reasoning_tokens for c in self.calls) / len(self.calls)
            )
            if self.calls
            else None,
            outputs=len(self.outputs),
            metrics=metrics or {},
        )
        path = ROOT / "runs" / self.meta["problem"] / f"{self.meta['run_id']}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(r, default=str) for r in [self.meta, *self.outputs]) + "\n"
        )
        return path


def load_runs(problem: str) -> list[dict]:
    """Every run of a problem, oldest first: {**meta, "outputs": [...]}."""
    runs = []
    for path in sorted((ROOT / "runs" / problem).glob("*.jsonl")):
        rows = _read_jsonl(path)
        if rows and rows[0].get("type") == "run":
            runs.append({**rows[0], "outputs": [r for r in rows[1:] if r.get("type") == "output"]})
    return runs


def problems() -> list[str]:
    return sorted(p.name for p in (ROOT / "runs").glob("*") if p.is_dir())


# ── Verdicts and comments ────────────────────────────────────────────────────


def add_label(problem: str, key: str, judge: str, verdict: str | None, note: str = "") -> None:
    """verdict None clears an earlier one."""
    if judge not in JUDGES:
        raise ValueError(f"judge must be one of {JUDGES}")
    if verdict is not None and verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS} or None")
    row = {"key": key, "judge": judge, "verdict": verdict, "note": note, "at": _now()}
    _append(ROOT / "labels" / f"{problem}.jsonl", row)


def load_labels(problem: str) -> dict[tuple[str, str], dict]:
    """(key, judge) -> the latest verdict row, leaving out cleared ones."""
    latest = {}
    for row in _read_jsonl(ROOT / "labels" / f"{problem}.jsonl"):
        latest[(row["key"], row["judge"])] = row
    return {k: v for k, v in latest.items() if v.get("verdict")}


def add_comment(problem: str, run_id: str, text: str) -> None:
    _append(ROOT / "comments" / f"{problem}.jsonl", {"run_id": run_id, "text": text, "at": _now()})


def load_comments(problem: str) -> dict[str, str]:
    return {r["run_id"]: r["text"] for r in _read_jsonl(ROOT / "comments" / f"{problem}.jsonl")}


# ── Leaderboard ──────────────────────────────────────────────────────────────


def score_run(run: dict, labels: dict, pool: set[str]) -> dict:
    """
    Precision per judge: kept / judged among this run's outputs.
    Pooled recall: the share of every output anyone kept, across all runs, that this run found.
    That needs no answer key: the pool grows as more variants are run and judged.
    """
    keys = {o["key"] for o in run["outputs"]}
    out = {"outputs": len(keys)}
    for judge in JUDGES:
        verdicts = [labels[(k, judge)]["verdict"] for k in keys if (k, judge) in labels]
        out[f"judged_{judge}"] = len(verdicts)
        out[f"precision_{judge}"] = verdicts.count("keep") / len(verdicts) if verdicts else None
    out["recall_pooled"] = len(keys & pool) / len(pool) if pool else None
    return out


def kept_pool(labels: dict) -> set[str]:
    """Keys someone kept. Your verdict overrides Claude's where you both judged a key."""
    pool = {
        k for (k, judge), row in labels.items() if judge == "claude" and row["verdict"] == "keep"
    }
    for (k, judge), row in labels.items():
        if judge == "you":
            (pool.add if row["verdict"] == "keep" else pool.discard)(k)
    return pool


def agreement(labels: dict) -> tuple[int, int]:
    """(keys where you and Claude agree, keys you both judged)."""
    both = [k for (k, judge) in labels if judge == "you" and (k, "claude") in labels]
    same = sum(labels[(k, "you")]["verdict"] == labels[(k, "claude")]["verdict"] for k in both)
    return same, len(both)


def leaderboard(problem: str) -> list[dict]:
    """One row per run, best first: precision by you, then by Claude, then pooled recall."""
    labels = load_labels(problem)
    pool = kept_pool(labels)
    comments = load_comments(problem)
    rows = []
    for run in load_runs(problem):
        meta = {k: v for k, v in run.items() if k != "outputs"}
        rows.append(
            {**meta, **score_run(run, labels, pool), "comment": comments.get(run["run_id"], "")}
        )

    def best(r):
        return tuple(
            -1 if r[k] is None else r[k]
            for k in ("precision_you", "precision_claude", "recall_pooled")
        )

    return sorted(rows, key=best, reverse=True)


def _pct(x) -> str:
    return "–" if x is None else f"{x:.0%}"


def write_leaderboard() -> Path:
    """evals/leaderboard.md: one table per problem."""
    lines = [
        "# Eval leaderboard",
        "",
        "Generated by `python -m evals leaderboard`. Best first.",
        "",
    ]
    for problem in problems():
        rows = leaderboard(problem)
        same, both = agreement(load_labels(problem))
        lines += [f"## {problem}", ""]
        if both:
            lines += [f"You and Claude agree on {same} of the {both} outputs you both judged.", ""]
        lines += [
            "| Run | Variant | Models | Prompt | Outputs | Precision (you) | Precision (Claude) "
            "| Pooled recall | Cost | Time/call | Metrics | Comment |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            models = ", ".join(f"{k}: {v}" for k, v in r["models"].items())
            metrics = ", ".join(
                f"{k}: {v}" for k, v in r["metrics"].items() if not isinstance(v, dict | list)
            )
            lines.append(
                f"| {r['run_id']} | {r['variant']} | {models} | {r['prompt_id']} | {r['outputs']} "
                f"| {_pct(r['precision_you'])} ({r['judged_you']}) "
                f"| {_pct(r['precision_claude'])} ({r['judged_claude']}) "
                f"| {_pct(r['recall_pooled'])} | ${r['cost_usd']:.3f} | {r['latency_s_mean'] or 0:.1f} s "
                f"| {metrics} | {r['comment']} |"
            )
        lines.append("")
    path = ROOT / "leaderboard.md"
    path.write_text("\n".join(lines))
    return path
