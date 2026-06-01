#!/usr/bin/env python3
"""
Lab 7 – REST API + PostGIS
============================
Same earthquake REST service as Lab 6, but the data layer moves from
an Excel file (pandas) to a real PostgreSQL/PostGIS database.

You will replace four TODO blocks with SQL that uses PostGIS spatial
functions instead of Python maths.

What changes compared to Lab 6
---------------------------------
  Lab 6                          Lab 7
  ──────────────────────────────  ──────────────────────────────────────
  load_df() / save_df()           get_conn() + SQL queries
  pandas filtering (df[...])      SQL WHERE clauses
  haversine_km() in Python        ST_DWithin / ST_Distance  (PostGIS)
  manual GeoJSON loop             ST_AsGeoJSON(geom)        (PostGIS)
  df.groupby(...)                 SQL GROUP BY

Prerequisites
-------------
  pip install flask psycopg pyjwt openpyxl
  PostgreSQL >= 14 running on localhost:5432 with PostGIS installed.
  Place  earthquakes_italy.xlsx  in the same directory as this file.
"""

from flask import Flask, jsonify, request, session
import datetime
import json
import jwt  # pip install pyjwt

# Connection settings and get_conn() live in db_config.py.
# Credentials are changed there once and shared across all scripts.
from db_config import get_conn

app = Flask(__name__)
app.secret_key  = "earthquake-postgis-secret-please-change-in-production"
JWT_SECRET      = "earthquake-postgis-jwt-secret-please-change-in-production"
JWT_ALG         = "HS256"

USERS = {
    "alice": {"password": "pwd1",   "role": "seismologist"},
    "guest": {"password": "guest",  "role": "viewer"},
}


# ---------------------------------------------------------------------------
# Auth helpers  (identical to Lab 6 – nothing changes here)
# ---------------------------------------------------------------------------

def cookie_user():
    u = session.get("user")
    return USERS.get(u) and {"user": u, **USERS[u]}


def token_user():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        data = jwt.decode(auth[7:], JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        return None
    name = data.get("sub")
    if name in USERS:
        return {"user": name, **USERS[name],
                "role": data.get("role", USERS[name]["role"])}
    return None


def current_user():
    return cookie_user() or token_user()


def require_role(*roles):
    u = current_user()
    if not u:
        return None, (jsonify({"error": "authentication required"}), 401)
    if roles and u["role"] not in roles:
        return None, (jsonify({"error": f"role {u['role']} not allowed"}), 403)
    return u, None


# ---------------------------------------------------------------------------
# Given endpoints  (study these before attempting the exercises)
# ---------------------------------------------------------------------------

@app.get("/events")
def get_events():
    """List events, optionally filtered by date range / magnitude / zone.

    Lab 6 used pandas boolean masks.
    Here we build a SQL WHERE clause dynamically – same logic, different layer.
    """
    frm     = request.args.get("from")
    to      = request.args.get("to")
    min_mag = request.args.get("min_mag", type=float)
    max_mag = request.args.get("max_mag", type=float)
    zone    = request.args.get("zone")

    where, params = [], []
    if frm:
        where.append("time >= %s");            params.append(frm)
    if to:
        where.append("time <= %s");            params.append(to)
    if min_mag is not None:
        where.append("magnitude >= %s");       params.append(min_mag)
    if max_mag is not None:
        where.append("magnitude <= %s");       params.append(max_mag)
    if zone:
        where.append("LOWER(zone) = LOWER(%s)"); params.append(zone)

    q = ("SELECT id, time, lat, lon, depth_km, magnitude, zone, place "
         "FROM earthquakes")
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY time DESC"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(q, params)
            rows = cur.fetchall()

    for r in rows:
        r["time"] = r["time"].strftime("%Y-%m-%dT%H:%M:%S")
    return jsonify(rows)


@app.get("/events/<int:event_id>")
def get_event(event_id):
    """Return a single event by primary key."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, time, lat, lon, depth_km, magnitude, zone, place "
                "FROM earthquakes WHERE id = %s",
                (event_id,),
            )
            row = cur.fetchone()
    if row is None:
        return jsonify({"error": "event not found"}), 404
    row["time"] = row["time"].strftime("%Y-%m-%dT%H:%M:%S")
    return jsonify(row)


@app.get("/stats/by-zone")
def stats_by_zone():
    """Aggregate event counts, max magnitude and average depth per zone.

    Lab 6 used df.groupby("zone").agg(...).
    Here we use SQL GROUP BY – same result, runs inside the database.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT   zone,
                         COUNT(*)                            AS n_events,
                         MAX(magnitude)                      AS max_magnitude,
                         ROUND(AVG(depth_km)::numeric, 1)   AS avg_depth
                FROM     earthquakes
                GROUP BY zone
                ORDER BY n_events DESC
            """)
            rows = cur.fetchall()
    return jsonify(rows)


# ---------------------------------------------------------------------------
# TODO Exercise 1 – GET /stats/by-year
# ---------------------------------------------------------------------------
# Return yearly event counts and the maximum magnitude for each year.
#
# Hint: extract the year directly in SQL with
#         EXTRACT(YEAR FROM time)::int  AS year
#
# Expected JSON shape:
#   [{"year": 2010, "n_events": 12, "max_magnitude": 5.1}, ...]
#
# Compare with Lab 6: df.groupby(df["time"].dt.year).agg(...)
#
# @app.get("/stats/by-year")
# def stats_by_year():
#     ...


# ---------------------------------------------------------------------------
# TODO Exercise 2 – GET /events/near?lat=<lat>&lon=<lon>&radius_km=<r>
# ---------------------------------------------------------------------------
# Return all events within radius_km of the given point, sorted by distance.
#
# PostGIS functions to use:
#   ST_SetSRID(ST_MakePoint(lon, lat), 4326)          build a geometry point
#   geom::geography                                    cast to geography (metres)
#   ST_DWithin(a::geography, b::geography, metres)     fast radius filter
#   ST_Distance(a::geography, b::geography) / 1000.0   distance in km
#
# IMPORTANT: ST_MakePoint takes (longitude, latitude) – the opposite of
#            the haversine_km(lat1, lon1, ...) convention from Lab 6!
#
# Expected JSON (new field added):
#   [{"id": 7, "time": "...", ..., "distance_km": 12.34}, ...]
#
# @app.get("/events/near")
# def events_near():
#     ...


# ---------------------------------------------------------------------------
# TODO Exercise 3 – GET /events.geojson
# ---------------------------------------------------------------------------
# Return a valid GeoJSON FeatureCollection.
# PostGIS can serialise a geometry directly to GeoJSON text, so you do not
# need to build {"type":"Point","coordinates":[...]} by hand.
#
# PostGIS function to use:
#   ST_AsGeoJSON(geom)   -- returns a JSON *string*; parse it with json.loads()
#
# Optional query param: ?min_mag=<float>
#
# Expected output:
#   {
#     "type": "FeatureCollection",
#     "features": [
#       { "type": "Feature",
#         "geometry": {"type": "Point", "coordinates": [lon, lat]},
#         "properties": {"id": 1, "time": "...", "magnitude": 4.2, ...}
#       }, ...
#     ]
#   }
#
# Compare with Lab 6: manual loop building the geometry dict from r["lat"]/r["lon"]
#
# @app.get("/events.geojson")
# def events_geojson():
#     ...


# ---------------------------------------------------------------------------
# TODO Exercise 4 – POST /events   (seismologist role required)
# ---------------------------------------------------------------------------
# Insert a new earthquake event, including its geometry column.
#
# Required JSON body fields: time, lat, lon, depth_km, magnitude, zone
# Optional field: place  (defaults to zone value)
#
# Hints:
#   • Reuse require_role("seismologist") – same as Lab 6.
#   • Build the geometry inline:  ST_SetSRID(ST_MakePoint(%s, %s), 4326)
#     where the two %s are lon, lat  (mind the order!).
#   • Use  RETURNING id  to get the auto-generated id back in one query.
#
# Return 201 with {"message": "event added", "id": <new_id>,
#                  "added_by": <user>, "total_events": <count>}
#
# @app.post("/events")
# def add_event():
#     ...


# ---------------------------------------------------------------------------
# Auth endpoints  (identical to Lab 6)
# ---------------------------------------------------------------------------

@app.post("/login")
def login_cookie():
    data = request.get_json() or {}
    u = USERS.get(data.get("user"))
    if not u or u["password"] != data.get("password"):
        return jsonify({"error": "invalid credentials"}), 401
    session["user"] = data["user"]
    return jsonify({"message": "logged in",
                    "user": data["user"], "role": u["role"]})


@app.post("/logout")
def logout_cookie():
    session.pop("user", None)
    return jsonify({"message": "logged out"})


@app.post("/token")
def login_token():
    data = request.get_json() or {}
    u = USERS.get(data.get("user"))
    if not u or u["password"] != data.get("password"):
        return jsonify({"error": "invalid credentials"}), 401
    now = datetime.datetime.now(datetime.timezone.utc)
    token = jwt.encode(
        {"sub": data["user"], "role": u["role"],
         "iat": now, "exp": now + datetime.timedelta(hours=1)},
        JWT_SECRET, algorithm=JWT_ALG,
    )
    return jsonify({"token": token, "role": u["role"], "expires_in": 3600})


@app.get("/me")
def me():
    u = current_user()
    if not u:
        return jsonify({"error": "not authenticated"}), 401
    return jsonify({"user": u["user"], "role": u["role"]})


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Make sure you have run seed_db.py before starting the server:
    #   python seed_db.py
    app.run(debug=True, use_reloader=False)
