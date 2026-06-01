#!/usr/bin/env python3
"""
db_config.py – shared database connection settings.

Imported by both seed_db.py and earthquakes_postgis.py so that
credentials live in exactly one place.
"""

import psycopg
from psycopg.rows import dict_row

# ---------------------------------------------------------------------------
# Change these four values to match your local PostgreSQL installation.
# ---------------------------------------------------------------------------
HOST     = "localhost"
PORT     = 5432
DB_NAME  = "earthquakes_db"
USER     = "postgres"
PASSWORD = input('Postgres Password? ')
# ---------------------------------------------------------------------------

# Connection used by the Flask service (rows returned as dicts)
DB_CONFIG = dict(
    host=HOST, port=PORT, dbname=DB_NAME,
    user=USER, password=PASSWORD,
    row_factory=dict_row,
)

# Connection used only by seed_db.py to CREATE the database
ADMIN_CONFIG = dict(
    host=HOST, port=PORT, dbname="postgres",
    user=USER, password=PASSWORD,
    autocommit=True,
)


def get_conn():
    """Return a new psycopg connection with dict-row results."""
    return psycopg.connect(**DB_CONFIG)
