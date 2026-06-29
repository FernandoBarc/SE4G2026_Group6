"""
seed_db.py
==========
Creates the Parasole schema (cities, weather_stations, hourly_observations)
and pre-populates the cities table with Milan and Mexico City.

Run once before starting the Flask server:
    python seed_db.py
"""

import sys
from db_config import get_conn
import meteostat_client as api

CITIES = [
    {"name": "Milan",       "country": "Italy",  "timezone": "Europe/Rome",
     "lat": 45.4642, "lon":   9.1900},
    {"name": "Mexico City", "country": "Mexico", "timezone": "America/Mexico_City",
     "lat": 19.4326, "lon": -99.1332},
]

DDL = """
-- ── Extension ───────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS postgis;

-- ── Cities ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cities (
    city_id  SERIAL       PRIMARY KEY,
    name     VARCHAR(100) NOT NULL UNIQUE,
    country  VARCHAR(100) NOT NULL,
    timezone VARCHAR(50)  NOT NULL
);

-- ── Weather Stations ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_stations (
    station_id VARCHAR(50)               PRIMARY KEY,
    city_id    INTEGER                   NOT NULL REFERENCES cities(city_id),
    name       VARCHAR(150)              NOT NULL,
    elevation  FLOAT,
    geom       GEOMETRY(Point, 4326)     NOT NULL
);

-- GiST spatial index for fast proximity queries
CREATE INDEX IF NOT EXISTS idx_stations_geom
    ON weather_stations USING GIST (geom);

-- ── Hourly Observations ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hourly_observations (
    observation_id BIGSERIAL    PRIMARY KEY,
    station_id     VARCHAR(50)  NOT NULL REFERENCES weather_stations(station_id),
    record_time    TIMESTAMPTZ  NOT NULL,
    temperature    FLOAT,          -- °C
    rel_humidity   FLOAT,          -- %
    precipitation  FLOAT,          -- mm
    UNIQUE (station_id, record_time)
);

-- Composite B-Tree index for fast date-range queries per station (NF2)
CREATE INDEX IF NOT EXISTS idx_obs_station_time
    ON hourly_observations (station_id, record_time);
"""

SEED_CITIES = """
INSERT INTO cities (name, country, timezone)
VALUES
    (%s, %s, %s),
    (%s, %s, %s)
ON CONFLICT (name) DO NOTHING;
"""

def main(reset=False):
    print("Connecting to database ...")
    with get_conn() as conn:
        with conn.cursor() as cur:
            if reset:
                print("Dropping tables (--reset) ...", end=" ")
                cur.execute("DROP TABLE IF EXISTS hourly_observations CASCADE;")
                cur.execute("DROP TABLE IF EXISTS weather_stations  CASCADE;")
                cur.execute("DROP TABLE IF EXISTS cities            CASCADE;")
                print("done.")
            
            print("Creating schema ...")
            cur.execute(DDL)
            print("Seeding cities ...")
            cur.execute(SEED_CITIES, 
                (CITIES[0]["name"], CITIES[0]["country"], CITIES[0]["timezone"],
                CITIES[1]["name"], CITIES[1]["country"], CITIES[1]["timezone"]))
            
            print("Seeding Stations ...")
            for c in CITIES:
                cur.execute(
                    "SELECT city_id, name, country, timezone "
                    "FROM cities WHERE LOWER(name) = LOWER(%s)",
                    (c["name"],),
                    )
                city = cur.fetchone()
                nearby = api.get_nearby_stations(c["lat"], c["lon"])
                for item in nearby:
                    meta = api.get_station_meta(item["id"])
                    if meta and meta["lat"] is not None and meta["lon"] is not None:
                        cur.execute("""
                            INSERT INTO weather_stations
                                   (station_id, name, elevation, city_id, geom)
                            VALUES (%s, %s, %s, %s,
                                    ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                            ON CONFLICT (station_id) DO NOTHING
                        """, (
                            meta["station_id"], meta["name"], meta["elevation"], city["city_id"],
                            meta["lon"], meta["lat"],     # ST_MakePoint takes (lon, lat)
                        ))
        conn.commit()
    print("Done – database is ready.")


def verify():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM cities")
            n_cities = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM weather_stations")
            n_stations = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM hourly_observations")
            n_obs = cur.fetchone()["n"]

    print()
    print("-- Verification ------------------------------------------")
    print(f"  Cities              : {n_cities}")
    print(f"  Weather stations    : {n_stations}")
    print(f"  Hourly observations : {n_obs}")
    print("----------------------------------------------------------")
    print("Database is ready. You can now start the service:")
    print("  python app.py")
    print()



if __name__ == "__main__":
    
    reset_flag    = "--reset" in sys.argv

    if reset_flag:
        confirm = input(
            "reset will DELETE all stored data. Type 'y' to continue: "
        )
        if confirm.strip().lower() != "y":
            print("Aborted.")
            sys.exit(0)
    main(reset = reset_flag)
    verify()

