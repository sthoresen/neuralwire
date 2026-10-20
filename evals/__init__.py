"""
NeuralWire's evals: measure prompts and models on frozen inputs, judge the outputs, and keep a
leaderboard per problem. See evals/README.md.

Evals never touch a database: importing this package points DATABASE_URL at an address nothing
listens on, as tests/conftest.py does, so NeuralWire code imported here can't reach production.
Datasets are exported once (python -m evals export ...) through their own read-only connection.
"""

import os
import sys
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
REPO_DIR = EVALS_DIR.parent

os.environ["DATABASE_URL"] = "postgresql://evals-must-not-use-a-database@127.0.0.1:1/none"
sys.path.insert(0, str(REPO_DIR / "python_backend"))
