#!/usr/bin/env python3
"""
seed_db.py  –  PROVIDED (do not modify for the exercises)
==========================================================
Run this script ONCE before starting the Flask server.

What it does, step by step:
  1. Creates the 'earthquakes_db' database (skips if it already exists)
  2. Enables the PostGIS extension inside that database
  3. Creates the 'earthquakes' table, including a GEOMETRY(Point, 4326)
     column that stores each event's location as a 2-D point in WGS-84
  4. Loads the 170 events from earthquakes_italy.xlsx into the table
  5. Creates a GIST spatial index on the geometry column so that the
     proximity queries in Exercise 2 run fast

Usage
-----
  python seed_db.py            # normal run – skips if data already loaded
  python seed_db.py --reset    # drops the table first, then reloads

After this script finishes you can start the service:
  python earthquakes_postgis.py
"""

import sys
import psycopg
import pandas as pd
from db_config import ADMIN_CONFIG, DB_CONFIG, DB_NAME

DATA_FILE = "earthquakes_italy.xlsx"

# ---------------------------------------------------------------------------
# Step 1 – Create the database
# ---------------------------------------------------------------------------

def create_database():
    print(f"[1/5] Checking database '{DB_NAME}' …", end=" ")
    with psycopg.connect(**ADMIN_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,)
            )
            if cur.fetchone():
                print("already exists.")
            else:
                cur.execute(f'CREATE DATABASE "{DB_NAME}"')
                print("created.")


# ---------------------------------------------------------------------------
# Step 2-5 – Schema + data
# ---------------------------------------------------------------------------

def seed(reset: bool = False):
    with psycopg.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:

            # --- PostGIS extension ----------------------------------------
            print("[2/5] Enabling PostGIS extension …", end=" ")
            cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
            print("done.")

            # --- Table -------------------------------------------------------
            if reset:
                print("[3/5] Dropping 'earthquakes' table (--reset) …", end=" ")
                cur.execute("DROP TABLE IF EXISTS earthquakes;")
                print("done.")
            else:
                print("[3/5] Checking 'earthquakes' table …", end=" ")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS earthquakes (
                    id          SERIAL           PRIMARY KEY,
                    time        TIMESTAMPTZ      NOT NULL,
                    lat         DOUBLE PRECISION NOT NULL,
                    lon         DOUBLE PRECISION NOT NULL,
                    depth_km    DOUBLE PRECISION NOT NULL,
                    magnitude   DOUBLE PRECISION NOT NULL,
                    zone        TEXT             NOT NULL,
                    place       TEXT,

                    -- PostGIS geometry column: 2-D point, WGS-84 (EPSG:4326)
                    -- ST_MakePoint(lon, lat) stores coordinates in (x, y) order
                    geom        GEOMETRY(Point, 4326)
                );
            """)
            print("ready.")

            # --- Data --------------------------------------------------------
            print("[4/5] Loading data from Excel …", end=" ")
            cur.execute("SELECT COUNT(*) AS n FROM earthquakes")
            existing = cur.fetchone()["n"]

            if existing > 0 and not reset:
                print(f"skipped ({existing} rows already present).")
                print("      Tip: run  python seed_db.py --reset  to reload.")
            else:
                df = pd.read_excel(DATA_FILE, sheet_name="events")
                df["time"] = pd.to_datetime(df["time"])

                rows_inserted = 0
                for _, row in df.iterrows():
                    cur.execute("""
                        INSERT INTO earthquakes
                               (time, lat, lon, depth_km, magnitude, zone, place, geom)
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s,
                            -- ST_MakePoint expects (longitude, latitude) = (x, y)
                            ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                        )
                    """, (
                        row["time"],
                        float(row["lat"]),
                        float(row["lon"]),
                        float(row["depth_km"]),
                        float(row["magnitude"]),
                        str(row["zone"]),
                        str(row.get("place", row["zone"])),
                        float(row["lon"]),   # MakePoint(lon …
                        float(row["lat"]),   #          … lat)
                    ))
                    rows_inserted += 1

                print(f"{rows_inserted} events inserted.")

            # --- Spatial index -----------------------------------------------
            print("[5/5] Creating GIST spatial index …", end=" ")
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_earthquakes_geom
                ON earthquakes
                USING GIST(geom);
            """)
            print("done.")

        conn.commit()


# ---------------------------------------------------------------------------
# Verification – quick sanity check printed after seeding
# ---------------------------------------------------------------------------

def verify():
    with psycopg.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:

            cur.execute("SELECT COUNT(*) AS n FROM earthquakes")
            total = cur.fetchone()["n"]

            cur.execute("""
                SELECT COUNT(*) AS n
                FROM   earthquakes
                WHERE  geom IS NOT NULL
            """)
            with_geom = cur.fetchone()["n"]

            cur.execute("""
                SELECT MIN(magnitude) AS min_mag,
                       MAX(magnitude) AS max_mag,
                       MIN(time)      AS earliest,
                       MAX(time)      AS latest
                FROM   earthquakes
            """)
            stats = cur.fetchone()

    print()
    print("── Verification ─────────────────────────────────────────")
    print(f"  Total rows      : {total}")
    print(f"  Rows with geom  : {with_geom}")
    print(f"  Magnitude range : {stats['min_mag']} – {stats['max_mag']}")
    print(f"  Time range      : {stats['earliest'].date()} → {stats['latest'].date()}")
    print("─────────────────────────────────────────────────────────")
    print("Database is ready. You can now start the service:")
    print("  python earthquakes_postgis.py")
    print()


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    reset_flag = "--reset" in sys.argv

    if reset_flag:
        confirm = input(
            "reset will DELETE all existing rows. Type 'yes' to continue: "
        )
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            sys.exit(0)

    create_database()
    seed(reset=reset_flag)
    verify()
