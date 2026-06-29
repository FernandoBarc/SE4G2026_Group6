# Parasole - Backend

Flask REST API server for the Parasole urban heat monitoring system.
Covers Milan (Italy) and Mexico City (Mexico), tracking temperature, relative humidity, and precipitation.

---

## Project structure

```
parasole_backend/
├── app.py                # Flask server — all REST endpoints
├── db_config.py          # Database connection and credentials loader
├── seed_db.py            # Creates the database schema (run once)
├── meteostat_client.py   # Wrapper for the 4 Meteostat API calls
├── ingestion.py          # Lazy-loading, data cleaning, and persistence
├── requirements.txt      # Python dependencies
├── .env.example          # Credential template — copy this to .env
└── .env                  # Your real credentials — never commit this file
```

---

## First-time setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Open `.env` in any text editor and fill in your values:

```
DB_HOST=localhost
DB_PORT=5432
DB_NAME=parasole_db
DB_USER=postgres
DB_PASSWORD=your_postgres_password

RAPIDAPI_KEY=your_rapidapi_key
RAPIDAPI_HOST=meteostat.p.rapidapi.com
```

> `.env` is listed in `.gitignore` and will never be pushed to the repository.
> Never paste real credentials into `.env.example` — that file is shared with the team.

### 3. Create the database

```bash
createdb parasole_db
```

### 4. Create the schema

```bash
python seed_db.py
```

This creates the `cities`, `weather_stations`, and `hourly_observations` tables,
enables the PostGIS extension, and seeds Milan and Mexico City. Run it only once.

### 5. Start the server

```bash
python app.py
```

The server starts at `http://localhost:5000`.

---

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/cities` | List of monitored cities |
| GET | `/api/v1/stations?name=Milan` | Weather stations for a city |
| GET | `/api/v1/weather?station_id=…&start_date=…&end_date=…` | Hourly observations (with lazy-load from Meteostat on cache miss) |
| GET | `/api/v1/heat-stress?station_id=…&start_date=…&end_date=…` | Critical heat stress hours (UC4) |
| GET | `/api/v1/stations/nearby?lat=…&lon=…` | Stations within 50 km via Meteostat |
| GET | `/api/v1/stations/<id>/normals` | 30-year climate normals for a station |
| POST | `/api/v1/stations/register` | Register a new weather station |

All endpoints return `application/json`. Dates use ISO 8601 UTC format.

### HTTP status codes

| Code | Meaning |
|------|---------|
| 200 | OK — payload returned |
| 400 | Bad request — missing or malformed parameters |
| 404 | Not found — city, station, or date range does not exist |
| 503 | Service unavailable — Meteostat API quota exhausted or network failure |

---

## How lazy-loading works

When the dashboard requests weather data, `ingestion.py` checks the local
PostgreSQL database first. If the data is already cached, it is returned
immediately (target: under 2 seconds). If data is missing, the backend fetches
it from the Meteostat API, cleans it with Pandas (linear interpolation for gaps
up to 3 consecutive hours), stores it for future requests, and returns it.

Date ranges longer than 30 days are automatically split into chunks internally —
the Meteostat API accepts a maximum of 30 days per call. This is transparent to
the caller.

---

## Quick example (from the Jupyter dashboard)

```python
import requests

BASE = "http://localhost:5000/api/v1"

# Get all stations in Milan
stations = requests.get(f"{BASE}/stations", params={"name": "Milan"}).json()

# Fetch hourly observations — lazy-loads from Meteostat if not cached
data = requests.get(f"{BASE}/weather", params={
    "station_id": stations[0]["station_id"],
    "start_date": "2026-06-01",
    "end_date":   "2026-06-07",
}).json()

# Check heat stress hours
stress = requests.get(f"{BASE}/heat-stress", params={
    "station_id":         stations[0]["station_id"],
    "start_date":         "2026-06-01",
    "end_date":           "2026-06-07",
    "temp_threshold":     35,
    "humidity_threshold": 70,
}).json()

print(f"Critical hours: {stress['critical_hours']}")
```

---
# Parasole Dashboard (Jupyter Notebook)

The dashboard is the presentation layer. It connects to the Flask backend over HTTP and visualizes the data on an interactive map, charts, and a heat-stress alert panel. It never touches the database or Meteostat directly.
Prerequisites
The backend must be running before launching the dashboard. Make sure you have completed the backend setup above and that python app.py is running on http://localhost:5000.
Running the dashboard

Open parasole_dashboard.ipynb in Jupyter Notebook.
Run all cells.
The "Backend check" cell confirms the connection by listing the cities and stations.

## How to use it

Select a city to see the station map (with a temperature heatmap) and the city-wide alert banner.
Select a station to see its temperature, humidity, and precipitation charts, plus its heat-stress alerts.
Adjust the temperature and humidity threshold sliders to change what counts as a critical hour. The charts update automatically.
Click "Export Alert Report" to download a CSV of the critical periods for the selected station.

## Notes for teammates

- The backend must be running before launching the Jupyter dashboard.
- Each team member needs their own `.env` file with their own database credentials.
- The RapidAPI free plan allows **500 requests per month**. The lazy-loading cache
  is designed to minimise API calls — always request data through the backend, never
  call Meteostat directly from the notebook.
- If the server returns 503, the monthly quota has been exceeded. The dashboard
  will fall back to locally cached data only.
-The dashboard reads data only through the backend REST API. The BASE_URL at the top of the notebook must match the running server (default: http://localhost:5000/api/v1).
-The first time you load data for a station, it may take a few seconds while the backend fetches it from Meteostat. After that, it is cached and loads faster.
