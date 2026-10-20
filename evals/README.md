# Evals

Measure NeuralWire's prompts and models on frozen inputs, judge what they produce, and keep a
leaderboard per problem: model, prompt, precision, recall, cost and time, plus your comments.

There is no answer key to write up front. Each run's outputs are judged keep or skip, by you on
the review page and by Claude through a report, and the scores follow from those verdicts.

## Workflow

```bash
# 1. Freeze a dataset from production (read-only connection, run once)
python -m evals export events --ticker NVDA --since 2026-03-27 --limit 500

# 2. Run variants on it. Models and parameters can be overridden per run.
python -m evals run events batch_validate --dataset nvda_2026-03-27_500
python -m evals run events monthly_select --dataset nvda_2026-03-27_500 --param cap=2
python -m evals run events monthly_select --dataset nvda_2026-03-27_500 --model select_model=deepseek/deepseek-v3.2

# 3. Judge: the review page for you, a report for Claude
make eval-review                      # http://localhost:8765
python -m evals report events         # writes reports/events_for_claude.md
python -m evals import events FILE    # Claude's verdicts, one {key, verdict, note} per line

# 4. The leaderboard (also rebuilt after every verdict and import)
python -m evals leaderboard           # writes leaderboard.md
```

## Scores

- **Precision (you, Claude)**: kept / judged among a run's outputs, separately per judge, with
  the number judged in brackets. Unjudged outputs don't count either way.
- **Pooled recall**: of every output anyone kept, across all runs of the problem, the share this
  run produced. Your verdict overrides Claude's where you both judged an output. The pool grows
  as more variants are run and judged, so recall is relative to what has been found so far.
- **Agreement**: how often you and Claude agree on outputs you both judged. When it is high,
  Claude's verdicts can stand in for yours on the long tail.
- **Cost** is what the variant costs to run, counting cached calls at their original price.
  **Time/call** is the mean LLM latency.

Outputs are keyed by the problem (for events: ticker and earliest source article), so a verdict
carries over to every run that produces the same output. Only new outputs need judging.

## Review page

Keyboard: `↑` `↓` move, `k` keep, `s` skip, `u` clear, `n` note. Filter by run or see all runs
merged; "only unjudged" hides what you have done. Blind mode hides which runs produced an
output. The Leaderboard tab takes a comment per run.

## Layout

```
evals/
  llm.py               OpenRouter calls with a disk cache (.cache/) and exact cost per call
  store.py             runs, verdicts, comments, scoring, leaderboard.md
  problems/<name>/     a problem: dataset export, variants, rubric.md; data/ is gitignored
  runs/<problem>/      one JSONL per run: the run, then its outputs
  labels/<problem>.jsonl, comments/<problem>.jsonl   appended, the latest line wins
  review/              the local review page (standard library server)
```

The LLM cache makes reruns free: a variant run twice on the same dataset only pays for calls it
hasn't made before. Delete `.cache/` to force fresh calls. Run variants that share calls one
at a time: two runs making the same call at once each get their own answer, and only one is kept.

Evals never write to a database. Importing `evals` points `DATABASE_URL` at an address nothing
listens on; only `export` opens a connection, and that one is read-only.

## Adding a problem

Create `problems/<name>/__init__.py` with `run(variant, dataset, models, params) -> Run`, call
`run.add_output(key, title, meta, body, context)` for each output, write a `rubric.md`, and
register it in `problems/__init__.py`.

## Problems

- **events**: which events from a stream of analysed articles belong on a ticker's long-term
  timeline. `batch_validate` is production's scan and batch validation; `monthly_select` scans
  the same way, then picks at most `cap` events per month.
