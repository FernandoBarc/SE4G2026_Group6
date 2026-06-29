"""
ingestion.py
============
Data Ingestion & Preprocessing layer (DD Section 2.2 / 5.2).

Responsibilities
----------------
  • Lazy-loading: check the local DB first; call Meteostat only on cache miss.
  • Automatic chunking: splits requests > 30 days into multiple API calls
    (Meteostat endpoint limit) transparently, without exposing the limit to
    the caller or the dashboard user.
  • Data cleaning: drop corrupt rows, linear-interpolate isolated NaN values.
  • Persist the cleaned rows so future requests are served from the DB (NF2).
  • Enforce UTC storage for all timestamps (NF3).

The public entry point is:

    ensure_observations(station_id, start_date, end_date) -> list[dict]

Returns cleaned hourly rows regardless of whether they came from the DB or
from the remote API. Date range is not limited to 30 days from the caller's
perspective.
"""

import pandas as pd
from datetime import datetime, timedelta

from db_config import get_conn
import meteostat_client as mc


# Maximum window size accepted by the Meteostat /stations/hourly endpoint.
# Handled internally — callers are not affected by this limit.
_METEOSTAT_MAX_DAYS = 30


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_from_db(station_id: str, start: str, end: str) -> pd.DataFrame:
    """Pull existing observations from the local DB for the given window."""
    sql = """
        SELECT record_time, temperature, rel_humidity, precipitation
        FROM   hourly_observations
        WHERE  station_id  = %s
          AND  record_time >= %s
          AND  record_time <= %s
        ORDER  BY record_time
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (station_id, start, end))
            rows = cur.fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["record_time"] = pd.to_datetime(df["record_time"], utc=True)
    return df


def _is_complete(df: pd.DataFrame, start: str, end: str) -> bool:
    """
    Simple completeness check: does the DB already hold at least one row
    for every calendar day in [start, end]?
    (A more rigorous check would count rows per hour, but this is sufficient
    for the lazy-loading strategy described in the DD.)
    """
    if df.empty:
        return False

    start_dt = pd.Timestamp(start, tz="UTC")
    end_dt   = pd.Timestamp(end,   tz="UTC") + pd.Timedelta(days=1)
    expected_days = (end_dt - start_dt).days

    actual_days = df["record_time"].dt.normalize().nunique()
    return actual_days >= expected_days


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """
    Data cleaning as described in the DD (Section 2.2):
      1. Drop rows where ALL three measurement columns are NaN (hardware fault).
      2. Linear-interpolate isolated NaN values in each column (limit=3 hours).
      3. Drop any row that still has NaN after interpolation.
    """
    measure_cols = ["temperature", "rel_humidity", "precipitation"]

    # Drop rows with no measurements at all
    df = df.dropna(subset=measure_cols, how="all").copy()

    # Interpolate short gaps (≤ 3 consecutive missing hours)
    df[measure_cols] = (
        df[measure_cols]
        .interpolate(method="linear", limit=3, limit_direction="both")
    )

    # Drop any remaining NaN rows
    df = df.dropna(subset=measure_cols, how="any")
    return df


def _save_to_db(station_id: str, df: pd.DataFrame) -> None:
    """Upsert cleaned observations into hourly_observations."""
    if df.empty:
        return

    rows = [
        (
            station_id,
            row["record_time"].to_pydatetime(),
            row.get("temperature"),
            row.get("rel_humidity"),
            row.get("precipitation"),
        )
        for _, row in df.iterrows()
    ]

    sql = """
        INSERT INTO hourly_observations
            (station_id, record_time, temperature, rel_humidity, precipitation)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (station_id, record_time) DO UPDATE SET
            temperature   = EXCLUDED.temperature,
            rel_humidity  = EXCLUDED.rel_humidity,
            precipitation = EXCLUDED.precipitation
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()


def _raw_to_df(raw_rows: list[dict]) -> pd.DataFrame:
    """
    Convert the list of dicts returned by meteostat_client into a
    normalised DataFrame with the same column names used in the DB.
    Timestamps are parsed and set to UTC (NF3).
    """
    if not raw_rows:
        return pd.DataFrame()

    df = pd.DataFrame(raw_rows)

    # Meteostat returns "time" as "YYYY-MM-DD HH:MM:SS"
    df["record_time"] = pd.to_datetime(df["time"], utc=True)

    # Rename to our column conventions
    df = df.rename(columns={
        "temp": "temperature",
        "rhum": "rel_humidity",
        "prcp": "precipitation",
    })

    keep = ["record_time", "temperature", "rel_humidity", "precipitation"]
    return df[[c for c in keep if c in df.columns]]


def _ensure_chunk(station_id: str, start: str, end: str) -> None:
    """
    Ensure a single ≤30-day chunk is present in the DB.
    Fetches from Meteostat only when the local cache is incomplete.
    """
    db_df = _fetch_from_db(station_id, start, end)
    if _is_complete(db_df, start, end):
        return  # Cache hit — nothing to do

    # Cache miss: fetch, clean, persist
    raw  = mc.get_hourly_records(station_id, start, end)
    df   = _raw_to_df(raw)
    df   = _clean(df)
    _save_to_db(station_id, df)


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    """Serialise a DataFrame to a list of plain dicts for JSON output."""
    records = []
    for _, row in df.iterrows():
        records.append({
            "record_time":   row["record_time"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "temperature":   row.get("temperature"),
            "rel_humidity":  row.get("rel_humidity"),
            "precipitation": row.get("precipitation"),
        })
    return records


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ensure_observations(
    station_id: str, start_date: str, end_date: str
) -> list[dict]:
    """
    Lazy-loading entry point (DD Section 5.2).

    Transparently handles date ranges longer than 30 days by splitting them
    into multiple chunks (Meteostat API limit). The caller and the dashboard
    are not aware of this limit.

    Flow per chunk:
      1. Check the DB (cache hit → skip API call).
      2. On cache miss, fetch from Meteostat, clean, persist.

    After all chunks are ensured, a single DB query returns the full range.

    Parameters
    ----------
    station_id : str   Meteostat station ID (e.g. "16080")
    start_date : str   Inclusive start in YYYY-MM-DD format
    end_date   : str   Inclusive end   in YYYY-MM-DD format

    Returns
    -------
    list[dict]  Each dict: {record_time, temperature, rel_humidity, precipitation}
                record_time is an ISO-8601 UTC string.

    Raises
    ------
    meteostat_client.MeteostatError  on remote API failures / quota exhaustion.
    ValueError                       if dates are malformed.
    """
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt   = datetime.strptime(end_date,   "%Y-%m-%d")

    # Split the full range into ≤30-day chunks and ensure each one
    current = start_dt
    while current <= end_dt:
        chunk_end = min(current + timedelta(days=_METEOSTAT_MAX_DAYS - 1), end_dt)
        _ensure_chunk(
            station_id,
            current.strftime("%Y-%m-%d"),
            chunk_end.strftime("%Y-%m-%d"),
        )
        current = chunk_end + timedelta(days=1)

    # Return the full requested range from the DB (now guaranteed to be present)
    full_df = _fetch_from_db(station_id, start_date, end_date)
    return _df_to_records(full_df)
