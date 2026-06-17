import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

DSN = (
    os.environ.get("DATABASE_URL")
    or "host=localhost port=5432 dbname=stocknews user=stocknews password=stocknews"
)

print("DB: connecting via DATABASE_URL" if os.environ.get("DATABASE_URL") else "DB: connecting via local fallback")


def get_conn(dict_cursor: bool = False):
    """
    Return a new psycopg2 connection.
    dict_cursor=True: rows support both key access (row['col']) and index access (row[0]).
    """
    if dict_cursor:
        return psycopg2.connect(DSN, cursor_factory=psycopg2.extras.DictCursor)
    return psycopg2.connect(DSN)
