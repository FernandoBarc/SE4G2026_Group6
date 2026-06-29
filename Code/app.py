"""
app.py
======
Parasole – Flask REST API Backend
===================================
Implements all endpoints specified in the Design Document (DD Section 4).

Base URL: /api/v1

Endpoints
---------
  GET  /api/v1/stations          – station metadata for a city (UC2)
  GET  /api/v1/weather           – hourly observations for a station (UC1/3)
  GET  /api/v1/cities            – list of available cities
  GET  /api/v1/heat-stress       – hours exceeding configurable thresholds (UC4)
  POST /api/v1/stations/register – register a new station (admin use)

Prerequisites
-------------
  pip install flask flask-cors psycopg pandas requests
  Run seed_db.py once before starting the server.

Start:
  python app.py
"""

from flask import Flask, jsonify, request
from flask_cors import CORS

from db_config import get_conn
import ingestion
import meteostat_client as mc
from meteostat_client import MeteostatError

app = Flask(__name__)
CORS(app)   # Allow cross-origin requests from the Jupyter dashboard (DD §4)

API_PREFIX = "/api/v1"


# ───────────────────────────────────────────────────────────────────────────
# Utility helpers
# ───────────────────────────────────────────────────────────────────────────

def _error(message: str, status: int) -> tuple:
    """Build a standard JSON error response (DD §4.3)."""
    return jsonify({
        "error":   {400: "Bad Request", 404: "Not Found",
                    503: "Service Unavailable"}.get(status, "Error"),
        "message": message,
        "status":  status,
    }), status


def _require_params(*names):
    """
    Check that all named query parameters are present.
    Returns (values_dict, None) on success or (None, error_response) on failure.
    """
    missing = [n for n in names if not request.args.get(n)]
    if missing:
        return None, _error(
            f"Missing mandatory query parameters: {', '.join(missing)} must be provided.",
            400,
        )
    return {n: request.args.get(n) for n in names}, None


# ───────────────────────────────────────────────────────────────────────────
# GET /api/v1/cities
# ───────────────────────────────────────────────────────────────────────────

@app.get(f"{API_PREFIX}/cities")
def get_cities():
    """
    Return the list of monitored cities.

    Response 200:
        [{"city_id": 1, "name": "Milan", "country": "Italy",
          "timezone": "Europe/Rome"}, ...]
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT city_id, name, country, timezone "
                "FROM cities ORDER BY name"
            )
            rows = cur.fetchall()
    return jsonify(rows)


# ───────────────────────────────────────────────────────────────────────────
# GET /api/v1/stations?name=<city_name>
# ───────────────────────────────────────────────────────────────────────────

@app.get(f"{API_PREFIX}/stations")
def get_stations():
    """
    Retrieve metadata and coordinates for all stations in a given city.
    (DD §4.2 – Endpoint 1: Fetch Station Coordinates)

    Query parameters:
        name  (required)  Name of the city, e.g. "Milan"

    Response 200:
        [{"station_id": "16080", "name": "Milano Linate",
          "elevation": 107.0, "latitude": 45.445, "longitude": 9.276}, ...]
    """
    params, err = _require_params("name")
    if err:
        return err

    city_name = params["name"]

    sql = """
        SELECT  ws.station_id,
                ws.name,
                ws.elevation,
                ST_Y(ws.geom)  AS latitude,
                ST_X(ws.geom)  AS longitude
        FROM    weather_stations ws
        JOIN    cities           c  ON c.city_id = ws.city_id
        WHERE   LOWER(c.name) = LOWER(%s)
        ORDER   BY ws.name
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (city_name,))
            rows = cur.fetchall()

    if not rows:
        return _error(
            f"No stations found for city '{city_name}'. "
            "Check the name or register stations first.",
            404,
        )
    return jsonify(rows)


# ───────────────────────────────────────────────────────────────────────────
# GET /api/v1/weather?station_id=…&start_date=…&end_date=…
# ───────────────────────────────────────────────────────────────────────────

@app.get(f"{API_PREFIX}/weather")
def get_weather():
    """
    Extract hourly observations for a station over an explicit date window.
    Implements the lazy-loading cache strategy (DD §4.2 – Endpoint 2 / §5.2).

    Query parameters:
        station_id  (required)  e.g. "16080"
        start_date  (required)  YYYY-MM-DD
        end_date    (required)  YYYY-MM-DD   (max 30-day window)

    Response 200:
        {
          "station_id": "16080",
          "city": "Milan",
          "parameters": ["temp", "rhum", "prcp"],
          "series": [
            {"timestamp": "2026-06-01T12:00:00Z",
             "temp": 28.5, "rhum": 55.0, "prcp": 0.0},
            ...
          ]
        }
    """
    params, err = _require_params("station_id", "start_date", "end_date")
    if err:
        return err

    station_id = params["station_id"]
    start_date = params["start_date"]
    end_date   = params["end_date"]

    # Resolve the city name for the response payload
    city_name = _city_for_station(station_id)

    # Lazy-load (cache hit or Meteostat fetch + clean + persist)
    try:
        records = ingestion.ensure_observations(station_id, start_date, end_date)
    except ValueError as exc:
        return _error(str(exc), 400)
    except MeteostatError as exc:
        return _error(str(exc), exc.status_code)

    # Reshape to match the response schema in the DD
    series = [
        {
            "timestamp": r["record_time"],
            "temp":  r["temperature"],
            "rhum":  r["rel_humidity"],
            "prcp":  r["precipitation"],
        }
        for r in records
    ]

    return jsonify({
        "station_id": station_id,
        "city":       city_name or "Unknown",
        "parameters": ["temp", "rhum", "prcp"],
        "series":     series,
    })


# ───────────────────────────────────────────────────────────────────────────
# GET /api/v1/heat-stress?station_id=…&start_date=…&end_date=…
#                        [&temp_threshold=35][&humidity_threshold=70]
# ───────────────────────────────────────────────────────────────────────────

@app.get(f"{API_PREFIX}/heat-stress")
def get_heat_stress():
    """
    Return hours where BOTH temperature and relative humidity exceed the
    configured thresholds (UC4 – Configure and Monitor Heat Stress Alerts).

    Default thresholds (DD §6.2):
        temp_threshold     = 35 °C
        humidity_threshold = 70 %

    Query parameters:
        station_id         (required)
        start_date         (required)  YYYY-MM-DD
        end_date           (required)  YYYY-MM-DD
        temp_threshold     (optional)  float, default 35.0
        humidity_threshold (optional)  float, default 70.0

    Response 200:
        {
          "station_id": "16080",
          "temp_threshold": 35.0,
          "humidity_threshold": 70.0,
          "critical_hours": 12,
          "critical_periods": [
            {"timestamp": "2026-06-15T14:00:00Z",
             "temp": 37.2, "rhum": 72.5}, ...
          ]
        }
    """
    params, err = _require_params("station_id", "start_date", "end_date")
    if err:
        return err

    station_id  = params["station_id"]
    start_date  = params["start_date"]
    end_date    = params["end_date"]

    # Optional thresholds with defaults
    try:
        temp_thr = float(request.args.get("temp_threshold",     35.0))
        hum_thr  = float(request.args.get("humidity_threshold", 70.0))
    except ValueError:
        return _error("temp_threshold and humidity_threshold must be numbers.", 400)

    if temp_thr < -90 or temp_thr > 60:
        return _error("temp_threshold must be between -90 and 60 °C.", 400)
    if hum_thr < 0 or hum_thr > 100:
        return _error("humidity_threshold must be between 0 and 100 %.", 400)

    try:
        records = ingestion.ensure_observations(station_id, start_date, end_date)
    except ValueError as exc:
        return _error(str(exc), 400)
    except MeteostatError as exc:
        return _error(str(exc), exc.status_code)

    critical = [
        {
            "timestamp": r["record_time"],
            "temp":      r["temperature"],
            "rhum":      r["rel_humidity"],
        }
        for r in records
        if r["temperature"] is not None
        and r["rel_humidity"] is not None
        and r["temperature"]  > temp_thr
        and r["rel_humidity"] > hum_thr
    ]

    return jsonify({
        "station_id":         station_id,
        "temp_threshold":     temp_thr,
        "humidity_threshold": hum_thr,
        "critical_hours":     len(critical),
        "critical_periods":   critical,
    })


# ───────────────────────────────────────────────────────────────────────────
# POST /api/v1/stations/register
# ───────────────────────────────────────────────────────────────────────────

@app.post(f"{API_PREFIX}/stations/register")
def register_station():
    """
    Register a new weather station for a monitored city.
    Can accept either an explicit lat/lon payload OR a Meteostat station_id
    to auto-fetch metadata from the remote API.

    JSON body:
        {
          "station_id": "16080",          ← required
          "city_name":  "Milan",          ← required
          "fetch_from_api": true          ← if true, auto-fill name/lat/lon from Meteostat
                                            otherwise supply name, lat, lon, elevation
        }

    Optional (when fetch_from_api is false or omitted):
        "name", "latitude", "longitude", "elevation"

    Response 201:
        {"message": "Station registered", "station_id": "16080"}
    """
    body = request.get_json() or {}
    station_id  = body.get("station_id")
    city_name   = body.get("city_name")
    fetch_api   = body.get("fetch_from_api", False)

    if not station_id or not city_name:
        return _error("station_id and city_name are required.", 400)

    # Resolve city_id
    city_id = _city_id_for_name(city_name)
    if city_id is None:
        return _error(f"City '{city_name}' not found in the database.", 404)

    if fetch_api:
        try:
            meta = mc.get_station_metadata(station_id)
        except MeteostatError as exc:
            return _error(str(exc), exc.status_code)

        if meta is None:
            return _error(f"Station '{station_id}' not found on Meteostat.", 404)

        name      = meta.get("name", {}).get("en") or meta.get("name", station_id)
        latitude  = meta["location"]["latitude"]
        longitude = meta["location"]["longitude"]
        elevation = meta["location"].get("elevation")
    else:
        name      = body.get("name", station_id)
        latitude  = body.get("latitude")
        longitude = body.get("longitude")
        elevation = body.get("elevation")

        if latitude is None or longitude is None:
            return _error("latitude and longitude are required when fetch_from_api is false.", 400)

    sql = """
        INSERT INTO weather_stations (station_id, city_id, name, elevation, geom)
        VALUES (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
        ON CONFLICT (station_id) DO UPDATE SET
            name      = EXCLUDED.name,
            elevation = EXCLUDED.elevation,
            geom      = EXCLUDED.geom
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (station_id, city_id, name, elevation, longitude, latitude))
        conn.commit()

    return jsonify({"message": "Station registered", "station_id": station_id}), 201


# ───────────────────────────────────────────────────────────────────────────
# GET /api/v1/stations/nearby?lat=…&lon=…[&radius_km=50]
# ───────────────────────────────────────────────────────────────────────────

@app.get(f"{API_PREFIX}/stations/nearby")
def stations_nearby():
    """
    Return Meteostat stations within radius_km of a coordinate pair.
    Wraps meteostat_client.get_nearby_stations() (DD §5.1).

    Query parameters:
        lat        (required)  float
        lon        (required)  float
        radius_km  (optional)  int, default 50
    """
    params, err = _require_params("lat", "lon")
    if err:
        return err

    try:
        lat       = float(params["lat"])
        lon       = float(params["lon"])
        radius_km = int(request.args.get("radius_km", 50))
    except ValueError:
        return _error("lat and lon must be numbers.", 400)

    try:
        stations = mc.get_nearby_stations(lat, lon, radius_km)
    except MeteostatError as exc:
        return _error(str(exc), exc.status_code)

    return jsonify(stations)


# ───────────────────────────────────────────────────────────────────────────
# GET /api/v1/stations/<station_id>/normals
# ───────────────────────────────────────────────────────────────────────────

@app.get(f"{API_PREFIX}/stations/<station_id>/normals")
def station_normals(station_id: str):
    """
    Retrieve the 30-year climate normals for a station (DD §5.1).
    Useful for the dashboard to compare current conditions against history.
    """
    try:
        normals = mc.get_station_normals(station_id)
    except MeteostatError as exc:
        return _error(str(exc), exc.status_code)

    if not normals:
        return _error(f"No climate normals found for station '{station_id}'.", 404)

    return jsonify({"station_id": station_id, "normals": normals})


# ───────────────────────────────────────────────────────────────────────────
# Internal DB helpers
# ───────────────────────────────────────────────────────────────────────────

def _city_for_station(station_id: str) -> str | None:
    """Return the city name associated with a station, or None."""
    sql = """
        SELECT c.name
        FROM   weather_stations ws
        JOIN   cities           c  ON c.city_id = ws.city_id
        WHERE  ws.station_id = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (station_id,))
            row = cur.fetchone()
    return row["name"] if row else None


def _city_id_for_name(city_name: str) -> int | None:
    """Return the city_id for a given city name, or None."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT city_id FROM cities WHERE LOWER(name) = LOWER(%s)",
                (city_name,),
            )
            row = cur.fetchone()
    return row["city_id"] if row else None


# ───────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Make sure seed_db.py has been run before starting:
    #   python seed_db.py
    app.run(debug=True, use_reloader=False, port=5000)
