"""
python -m evals export events --ticker NVDA --since 2026-03-27 --limit 500
python -m evals run events monthly_select --dataset nvda_2026-03-27_500 [--param cap=2] [--model select_model=...]
python -m evals leaderboard                 rebuild evals/leaderboard.md
python -m evals playground [--port 8770]    paste a prompt, pick models, run and judge (start here)
python -m evals review [--port 8765]        the review page for the events problem
python -m evals report events               a blinded report of outputs for Claude to judge
python -m evals import events FILE          load Claude's verdicts ({key, verdict, note} per line)
"""

import argparse
import json
import random
import sys

from evals import EVALS_DIR, store
from evals.problems import PROBLEMS


def _pairs(items: list[str]) -> dict:
    out = {}
    for item in items or []:
        k, _, v = item.partition("=")
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def cmd_export(a):
    name = PROBLEMS[a.problem].export(a.ticker, a.since, a.limit)
    print(f"dataset: {name}")


def cmd_run(a):
    r = PROBLEMS[a.problem].run(a.variant, a.dataset, _pairs(a.model), _pairs(a.param))
    m = r.meta
    print(
        f"\n{m['run_id']}: {m['outputs']} outputs, {m['calls']} calls ({m['cached_calls']} cached)"
    )
    print(
        f"cost to run ${m['cost_usd']:.4f}, spent now ${m['spent_usd']:.4f}, {m['latency_s_mean']} s/call"
    )
    print(f"metrics: {m['metrics']}")
    print(f"leaderboard: {store.write_leaderboard()}")


def cmd_leaderboard(a):
    path = store.write_leaderboard()
    print(store.terminal_leaderboard(color=sys.stdout.isatty()))
    print(f"(also written as Markdown to {path.relative_to(EVALS_DIR.parent)})")


def cmd_playground(a):
    from evals.playground.server import serve

    serve(a.port)


def cmd_review(a):
    from evals.review.server import serve

    serve(a.port)


def cmd_report(a):
    """Every output Claude hasn't judged yet, deduplicated by key, shuffled, with no variant names."""
    labels = store.load_labels(a.problem)
    seen, items = set(), []
    for run in store.load_runs(a.problem):
        for o in run["outputs"]:
            if o["key"] not in seen and (o["key"], "claude") not in labels:
                seen.add(o["key"])
                items.append(o)
    random.Random(0).shuffle(items)
    rubric = (EVALS_DIR / "problems" / a.problem / "rubric.md").read_text()
    lines = [
        f"# Outputs to judge: {a.problem}",
        "",
        "Write one JSON line per output to a file: "
        '{"key": "...", "verdict": "keep" | "skip", "note": "..."}, then run '
        f"`python -m evals import {a.problem} FILE`.",
        "",
        rubric,
        "",
        "---",
        "",
    ]
    for o in items:
        lines += [f"### {o['key']}", f"**{o['title']}** ({o['meta']})", "", o["body"], ""]
        lines += [f"- source {c.get('date', '')}: {c['text']}" for c in o["context"]]
        lines.append("")
    out = EVALS_DIR / "reports" / f"{a.problem}_for_claude.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines))
    print(f"{len(items)} outputs to judge: {out}")


def cmd_import(a):
    n = 0
    for line in open(a.file):
        if line.strip():
            row = json.loads(line)
            store.add_label(a.problem, row["key"], a.judge, row["verdict"], row.get("note", ""))
            n += 1
    print(f"imported {n} verdicts as {a.judge}; leaderboard: {store.write_leaderboard()}")


def main():
    p = argparse.ArgumentParser(prog="python -m evals", description=__doc__.splitlines()[1])
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("export")
    e.add_argument("problem", choices=PROBLEMS)
    e.add_argument("--ticker", required=True)
    e.add_argument("--since", required=True)
    e.add_argument("--limit", type=int, default=500)

    r = sub.add_parser("run")
    r.add_argument("problem", choices=PROBLEMS)
    r.add_argument("variant")
    r.add_argument("--dataset", required=True)
    r.add_argument(
        "--model", action="append", metavar="ROLE=MODEL", help="e.g. scan_model=tencent/hy3"
    )
    r.add_argument("--param", action="append", metavar="NAME=VALUE", help="e.g. cap=2")

    sub.add_parser("leaderboard")
    pg = sub.add_parser("playground")
    pg.add_argument("--port", type=int, default=8770)
    v = sub.add_parser("review")
    v.add_argument("--port", type=int, default=8765)

    rp = sub.add_parser("report")
    rp.add_argument("problem", choices=PROBLEMS)

    i = sub.add_parser("import")
    i.add_argument("problem", choices=PROBLEMS)
    i.add_argument("file")
    i.add_argument("--judge", default="claude", choices=store.JUDGES)

    a = p.parse_args()
    {
        "export": cmd_export,
        "run": cmd_run,
        "leaderboard": cmd_leaderboard,
        "playground": cmd_playground,
        "review": cmd_review,
        "report": cmd_report,
        "import": cmd_import,
    }[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
