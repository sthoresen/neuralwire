import os

import psycopg2
import psycopg2.extras

print("DEBUG DATABASE_URL_PRIVATE:", os.environ.get("DATABASE_URL_PRIVATE"))
print("DEBUG DATABASE_URL:", os.environ.get("DATABASE_URL"))

DSN = (
    os.environ.get("DATABASE_URL_PRIVATE")
    or os.environ.get("DATABASE_URL")
    or "host=localhost port=5432 dbname=stocknews user=stocknews password=stocknews"
)


def get_conn(dict_cursor: bool = False):
    """
    Return a new psycopg2 connection.
    dict_cursor=True: rows support both key access (row['col']) and index access (row[0]).
    """
    if dict_cursor:
        return psycopg2.connect(DSN, cursor_factory=psycopg2.extras.DictCursor)
    return psycopg2.connect(DSN)
