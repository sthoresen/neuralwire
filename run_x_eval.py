"""Dev tool: how well do Jev's scores agree with your keep/skip verdicts from the feed page?

  python run_x_eval.py                    # current question set (x_jev.QSET)
  python run_x_eval.py --qset v2          # another one from x_jev.QSETS
  python run_x_eval.py --qset v2 --label  # first score the judged posts it hasn't seen (Jev, paid)

For each threshold it shows how often "rank >= threshold" matches your verdict, how
many posts you'd keep it drops (the costly error) and how much it keeps that you'd
skip. Then it lists every disagreement at the best threshold.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python_backend"))

import x_jev
import x_store

THRESHOLDS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]


def evaluate(rows: list[dict], score) -> list[dict]:
    out = []
    for t in THRESHOLDS:
        pred = [score(r) >= t for r in rows]
        keep = [r["verdict"] == "keep" for r in rows]
        out.append(
            {
                "t": t,
                "accuracy": sum(p == k for p, k in zip(pred, keep, strict=True)) / len(rows),
                "dropped_keeps": sum(k and not p for p, k in zip(pred, keep, strict=True)),
                "kept_skips": sum(p and not k for p, k in zip(pred, keep, strict=True)),
            }
        )
    return out


def print_table(title: str, results: list[dict]) -> None:
    print(f"\n{title}")
    print(f"  {'threshold':>9}{'accuracy':>10}{'good dropped':>14}{'noise kept':>12}")
    for r in results:
        print(
            f"  {r['t']:>9.1f}{r['accuracy']:>10.0%}{r['dropped_keeps']:>14}{r['kept_skips']:>12}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--qset", default=x_jev.QSET, choices=sorted(x_jev.QSETS))
    parser.add_argument(
        "--label", action="store_true", help="score judged posts that lack this question set"
    )
    args = parser.parse_args()

    if args.label:
        todo = x_store.judged_without_labels(args.qset)
        if todo:
            rec = x_jev.label_posts(todo, None, None, "eval", "eval", qset=args.qset)
            run_id = x_store.record_run(
                rec["meta"], len(rec["labels"]), 0, len(rec["failed"]), False
            )
            x_store.save_labels(rec["labels"], args.qset, run_id)
            cost = rec["meta"]["est_cost_usd"]
            print(f"Scored {len(rec['labels'])} judged posts with {args.qset} (${cost:.4f})")

    rows = [r for r in x_store.judged_posts(args.qset) if r["scores"]]
    if not rows:
        sys.exit(f"No judged posts with {args.qset} scores yet. Judge some on /feed first.")
    keeps = sum(r["verdict"] == "keep" for r in rows)
    print(
        f"=== {len(rows)} judged posts ({keeps} keep, {len(rows) - keeps} skip), qset {args.qset} ==="
    )

    def rank(r):
        return x_jev.rank_score(r["scores"], r["group_name"], args.qset)

    results = evaluate(rows, rank)
    rule = x_jev.QSETS[args.qset]["rank"].__doc__.strip().splitlines()[0]
    print_table(f"rank: {rule}", results)
    for q in x_jev.QSETS[args.qset]["questions"]:
        print_table(f"{q} alone", evaluate(rows, lambda r, q=q: r["scores"][q]))

    best = max(results, key=lambda r: r["accuracy"])
    print(f"\nDisagreements at the best rank threshold, {best['t']}:")
    for r in sorted(rows, key=rank):
        if (rank(r) >= best["t"]) != (r["verdict"] == "keep"):
            s = r["scores"]
            text = (r["text"] or "").replace("\n", " ")[:140]
            shown = "  ".join(f"{k}={v:.2f}" for k, v in s.items())
            print(
                f"  you={r['verdict']:<4} rank={rank(r):.2f}  [{r['group_name']}] @{r['author_handle']}: {text}"
            )
            print(f"      {shown}")


if __name__ == "__main__":
    main()
