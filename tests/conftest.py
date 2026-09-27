"""
Loaded by pytest before any test module is imported.

The backend reads DATABASE_URL from python_backend/.env, which may point at
production. load_dotenv never overrides a variable that is already set, so
pinning it here to an address nothing listens on guarantees the tests can't
reach a real database: a test that accidentally queries one fails fast.
"""

import os

os.environ["DATABASE_URL"] = "postgresql://tests-must-not-use-a-database@127.0.0.1:1/none"
