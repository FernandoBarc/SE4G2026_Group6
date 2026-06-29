#!/usr/bin/env python3
"""
meteostat_client.py - thin wrapper around the Meteostat RapidAPI service.

This module ONLY talks to the remote Meteostat API. It does not know about
the database or about caching - that job belongs to data_layer.py.  Keeping
it separate means the "when do we call the API?" logic lives in exactly one
place (the data layer) and is easy to reason about.

Design Document section 5 lists four methods we need:

  1. get_nearby_stations(lat, lon)   -> stations within ~50 km of a point
  2. station_meta(station_id)    -> name / location / elevation / timezone
  3. hourly(station_id, s, e)    -> hourly temp / rhum / prcp  (<= 30 days)
  4. station_normals(station_id) -> 30-year climate normals (optional extra)

Only `hourly` and `get_nearby_stations` count against the 500-request monthly
quota in a meaningful way, so the data layer calls them as little as
possible.

Errors are turned into two custom exceptions so the Flask layer can map them
to the right HTTP status code:

  MeteostatQuotaError  -> 503  (monthly RapidAPI limit reached)
  MeteostatError       -> 503  (any other network / API failure)
"""

import requests

from db_config import RAPIDAPI_KEY, RAPIDAPI_HOST
METEOSTAT_BASE_URL = "https://meteostat.p.rapidapi.com"

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class MeteostatError(Exception):
    """Raised on any network failure or unexpected Meteostat response."""


class MeteostatQuotaError(MeteostatError):
    """Raised when the RapidAPI monthly quota (500 requests) is exhausted."""


# ---------------------------------------------------------------------------
# Internal helper - every call goes through here
# ---------------------------------------------------------------------------

def _get(path, params):
    """Perform a GET request against the Meteostat RapidAPI gateway.

    Returns the parsed `data` field of the JSON response.
    Raises MeteostatQuotaError / MeteostatError on failure.
    """
    if not RAPIDAPI_KEY:
        raise MeteostatError(
            "No Meteostat API key configured. Set the environment variable "
            "METEOSTAT_RAPIDAPI_KEY before starting the server."
        )

    headers = {
        "X-RapidAPI-Key":  RAPIDAPI_KEY,
        "X-RapidAPI-Host": RAPIDAPI_HOST,
    }

    try:
        resp = requests.get(
            f"{METEOSTAT_BASE_URL}{path}",
            headers=headers, params=params, timeout=20,
        )
    except requests.RequestException as exc:
        raise MeteostatError(f"network error contacting Meteostat: {exc}")

    # RapidAPI returns 429 (too many requests) when the quota is gone;
    # some plans answer 403 instead. Treat both as "quota exhausted".
    if resp.status_code in (429, 403):
        raise MeteostatQuotaError(
            "Meteostat API monthly rate quota exceeded."
        )
    if resp.status_code != 200:
        raise MeteostatError(
            f"Meteostat returned HTTP {resp.status_code}: {resp.text[:200]}"
        )

    try:
        payload = resp.json()
    except ValueError:
        raise MeteostatError("Meteostat response was not valid JSON")

    return payload.get("data", [])


# ---------------------------------------------------------------------------
# 1. Nearby stations  (~50 km around a city centre)
# ---------------------------------------------------------------------------

def get_nearby_stations(lat, lon, limit=8, radius_m=50000):
    """Return a list of station ids near the given coordinate.

    Each item looks like {"id": "16080", "distance": 1234.5}.
    """
    data = _get("/stations/nearby",
                {"lat": lat, "lon": lon, "limit": limit, "radius": radius_m})
    return data


# ---------------------------------------------------------------------------
# 2. Station metadata
# ---------------------------------------------------------------------------

def get_station_meta(station_id):
    """Return metadata for a single station, normalised to a flat dict:

        {"station_id", "name", "country", "elevation", "lat", "lon", "timezone"}
    """
    data = _get("/stations/meta", {"id": station_id})
    if not data:
        return None

    # The "name" field is a dict of translations, e.g. {"en": "Milano Linate"}.
    name = data.get("name")
    if isinstance(name, dict):
        name = name.get("en") or next(iter(name.values()), station_id)

    loc = data.get("location", {}) or {}
    return {
        "station_id": str(data.get("id", station_id)),
        "name":       name or station_id,
        "country":    data.get("country"),
        "elevation":  loc.get("elevation"),
        "lat":        loc.get("latitude"),
        "lon":        loc.get("longitude"),
        "timezone":   data.get("timezone"),
    }


# ---------------------------------------------------------------------------
# 3. Hourly observations  (Meteostat limits a single call to <= 30 days)
# ---------------------------------------------------------------------------

def get_hourly_records(station_id, start_date, end_date):
    """Return raw hourly rows for [start_date, end_date] (inclusive).

    `start_date` / `end_date` are strings formatted YYYY-MM-DD.
    Times come back in UTC (we do not pass a `tz` parameter).
    Each row contains at least: time, temp, rhum, prcp.
    """
    data = _get("/stations/hourly", {
        "station": station_id,
        "start":   start_date,
        "end":     end_date,
        "units":   "metric",
        # no "tz" -> Meteostat returns timestamps in UTC, which is what we store
    })
    return data


# ---------------------------------------------------------------------------
# 4. Station normals  (30-year reference statistics - optional analysis aid)
# ---------------------------------------------------------------------------

def get_station_normals(station_id):
    """Return the 30-year monthly climate normals for a station."""
    data = _get("/stations/normals", {"station": station_id})
    return data
