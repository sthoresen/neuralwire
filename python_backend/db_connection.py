import os
from urllib.parse import urlparse

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Local development database (docker-compose). Port 5433 keeps it clear of a
# native PostgreSQL on 5432.
LOCAL_DSN = "host=localhost port=5433 dbname=stocknews user=stocknews password=stocknews"

_database_url = os.environ.get("DATABASE_URL")
DSN = _database_url or LOCAL_DSN

# Name the host, so a remote target is visible before anything writes to it.
if _database_url:
    print(f"DB: REMOTE -> {urlparse(_database_url).hostname or '?'}")
else:
    print("DB: local -> localhost:5433 (docker-compose)")


def get_conn(dict_cursor: bool = False):
    """
    Return a new psycopg2 connection.
    dict_cursor=True: rows support both key access (row['col']) and index access (row[0]).
    """
    if dict_cursor:
        return psycopg2.connect(DSN, cursor_factory=psycopg2.extras.DictCursor)
    return psycopg2.connect(DSN)
