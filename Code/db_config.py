"""
db_config.py
============
Database connection settings for the Parasole backend.
All credentials are loaded from a .env file (NF1: never hardcode secrets).

Usage:
    from db_config import get_conn

Setup:
    1. Copy .env.example to .env
    2. Fill in your credentials in .env
    3. Never commit .env to Git (it's listed in .gitignore)
"""

import os
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

# Load variables from .env file (if present)
load_dotenv()

# ---------------------------------------------------------------------------
# PostgreSQL connection parameters — read from environment
# ---------------------------------------------------------------------------
DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME", "parasole_db"),
    "user":     os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}

# ---------------------------------------------------------------------------
# Meteostat / RapidAPI credentials (NF1: keep out of client-side code)
# ---------------------------------------------------------------------------
RAPIDAPI_KEY  = os.environ.get("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = os.environ.get("RAPIDAPI_HOST", "meteostat.p.rapidapi.com")

if not RAPIDAPI_KEY:
    import warnings
    warnings.warn(
        "RAPIDAPI_KEY is not set. Meteostat calls will fail. "
        "Copy .env.example to .env and fill in your key.",
        stacklevel=2,
    )


def get_conn():
    """
    Return a new psycopg3 connection with dict_row factory so that
    cursor.fetchone() / fetchall() return dicts instead of tuples.
    """
    return psycopg.connect(
        **DB_CONFIG,
        row_factory=dict_row,
    )
