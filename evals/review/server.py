"""
The local review page: browse every output of every run, judge them keep or skip, and comment on
runs. Verdicts go straight into evals/labels/, and the leaderboard is rebuilt after each change.

  python -m evals review        then open http://localhost:8765

Standard library only; it serves on localhost and is meant for one person at a time.
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from evals import EVALS_DIR, store

PAGE = Path(__file__).parent / "index.html"


def _problem_data(problem: str) -> dict:
    labels = store.load_labels(problem)
    runs = store.load_runs(problem)
    board = {r["run_id"]: r for r in store.leaderboard(problem)}
    items: dict[str, dict] = {}
    for run in runs:
        for o in run["outputs"]:
            item = items.setdefault(o["key"], {**o, "runs": []})
            item["runs"].append(run["run_id"])
    for key, item in items.items():
        item["labels"] = {
            j: {"verdict": labels[(key, j)]["verdict"], "note": labels[(key, j)].get("note", "")}
            for j in store.JUDGES
            if (key, j) in labels
        }
    rubric = EVALS_DIR / "problems" / problem / "rubric.md"
    same, both = store.agreement(labels)
    return {
        "rubric": rubric.read_text() if rubric.exists() else "",
        "runs": [board[r["run_id"]] for r in runs],
        "items": list(items.values()),
        "agreement": {"same": same, "both": both},
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200):
        self._send(json.dumps(obj, default=str).encode(), "application/json", status)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/data":
            self._json({"problems": {p: _problem_data(p) for p in store.problems()}})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or "{}")
        try:
            if self.path == "/api/label":
                store.add_label(
                    body["problem"], body["key"], "you", body.get("verdict"), body.get("note", "")
                )
            elif self.path == "/api/comment":
                store.add_comment(body["problem"], body["run_id"], body.get("text", ""))
            else:
                return self._json({"error": "not found"}, 404)
        except (KeyError, ValueError) as e:
            return self._json({"error": str(e)}, 400)
        store.write_leaderboard()
        self._json({"ok": True})

    def log_message(self, *args):  # keep the terminal quiet
        pass


def serve(port: int = 8765) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Review page: http://localhost:{port}   (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
