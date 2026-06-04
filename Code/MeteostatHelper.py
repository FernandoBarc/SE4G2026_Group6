import MS_config
import requests
import pprint as pp

HEADERS = MS_config.meteostat_request_header() #to keep apikey private
URL = "https://meteostat.p.rapidapi.com/stations/"

def getWeatherRecords(granularity, start_date, end_date, station_id, tz="UTC" ):
    '''
    "station": "10637", "start": "2020-01-01", "end": "2020-01-01", "tz": "UTC" <- to keep consistent records across cities.
    '''

    querystring = {"station": station_id, "start": start_date, "end": end_date, "tz": tz}
    stationRecords = requests.get(f'{URL}{granularity}', headers=HEADERS, params=querystring)

    return stationRecords.json()["data"]

def getStationMetadata(station_id = "10637"):
    '''
    This request does not have any cost on the Meteostat API basic Plan.

    https://data.meteostat.net/stations/{station}.json
    10637
    '''
    response = requests.get(f'https://data.meteostat.net/stations/{station_id}.json').json()
    print(response)
    return response

def getStationNormals(station_id = "10637"):

    url = "https://meteostat.p.rapidapi.com/stations/normals"
    querystring = {"station": station_id, "start": "1961", "end": "1990"}
    response = requests.get(url, headers=HEADERS, params=querystring)

    pp.pprint(response.json()) # ["data"]
    return response.json() # ["data"]

def getNearbyStations(city="Milan", radius="50000"):
    '''

    '''
    assert str(city)
    assert int(radius)
    cityCoordinates = {
        "Milan" : {"lat": "45.4688", "lon": "9.1816"},
        "CDMX" : {"lat": "19.4055", "lon": "-99.1537"}
    }
    try:
        querystring = {"lat": cityCoordinates[city]["lat"], "lon": cityCoordinates[city]["lon"], "radius": 50000}
    except Exception:
        print("City not not correctly defined")

    resNearby = requests.get(url=f"{URL}nearby", headers=HEADERS, params=querystring)
    return  resNearby.json()["data"]

if __name__ == "__main__":
    # getWeatherRecords(granularity = "hourly", start_date = "2025-05-02", end_date= "2025-05-03", station_id = [10637], tz= "Europe/Berlin")
    # getNearbyStations(city="Milan", radius="50000")
    getStationMetadata("16081")
    getStationNormals("16081")
