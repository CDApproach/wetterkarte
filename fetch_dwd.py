"""
Lädt stündliche Lufttemperaturen aller DWD-Stationen (Open Data, "recent")
und schreibt sie kompakt nach data/temperatures.json für die Webkarte.

Aufruf:  python3 fetch_dwd.py --days 7
Nur Standardbibliothek nötig.
"""
import argparse
import io
import json
import re
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = ("https://opendata.dwd.de/climate_environment/CDC/"
        "observations_germany/climate/hourly/air_temperature/recent/")
STATION_LIST = "TU_Stundenwerte_Beschreibung_Stationen.txt"
OUT = Path(__file__).parent / "data" / "temperatures.json"


def get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def load_stations():
    """Stationsliste parsen: id, Höhe, Lat, Lon, Name, Bundesland."""
    text = get(BASE + STATION_LIST).decode("latin-1")
    stations = {}
    for line in text.splitlines()[2:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        sid, _von, bis, elev, lat, lon = parts[:6]
        stations[sid] = {
            "id": sid,
            "name": " ".join(parts[6:-2]),
            "state": parts[-2],
            "elev": float(elev),
            "lat": float(lat),
            "lon": float(lon),
            "bis": bis,
        }
    return stations


def list_zip_files():
    html = get(BASE).decode("latin-1")
    return sorted(set(re.findall(r"stundenwerte_TU_(\d{5})_akt\.zip", html)))


def load_station_data(sid, cutoff):
    """Gibt {zeitstempel 'YYYYMMDDHH': temperatur} für eine Station zurück."""
    try:
        raw = get(f"{BASE}stundenwerte_TU_{sid}_akt.zip")
    except Exception as e:  # Station kurzzeitig nicht erreichbar -> überspringen
        print(f"  {sid}: Download fehlgeschlagen ({e})")
        return sid, {}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = next(n for n in z.namelist() if n.startswith("produkt_"))
        text = z.read(name).decode("latin-1")
    values = {}
    for line in text.splitlines()[1:]:
        cols = [c.strip() for c in line.split(";")]
        if len(cols) < 4 or cols[1] < cutoff:
            continue
        t = float(cols[3])
        if t > -999:  # -999 = Fehlwert
            values[cols[1]] = round(t, 1)
    return sid, values


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="Zeitraum in Tagen (Standard 7)")
    ap.add_argument("--min-coverage", type=float, default=0.5,
                    help="Mindestanteil vorhandener Werte je Station (0..1)")
    args = ap.parse_args()

    print("Lade Stationsliste ...")
    stations = load_stations()
    ids = [s for s in list_zip_files() if s in stations]
    print(f"{len(ids)} Stationen mit aktuellen Daten")

    # Der "recent"-Datensatz endet meist gestern 23 UTC; Zeitraum ab dort rückwärts
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cutoff_dt = now - timedelta(days=args.days + 2)
    cutoff = cutoff_dt.strftime("%Y%m%d%H")

    print("Lade Messwerte (parallel) ...")
    data = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        for i, (sid, vals) in enumerate(pool.map(lambda s: load_station_data(s, cutoff), ids), 1):
            if vals:
                data[sid] = vals
            if i % 50 == 0:
                print(f"  {i}/{len(ids)}")

    # Gemeinsame Zeitachse: die letzten N Tage bis zum letzten vorhandenen Zeitstempel
    last = max(max(v) for v in data.values())
    end = datetime.strptime(last, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    start = end - timedelta(days=args.days) + timedelta(hours=1)
    times = []
    t = start
    while t <= end:
        times.append(t)
        t += timedelta(hours=1)
    keys = [t.strftime("%Y%m%d%H") for t in times]

    out_stations, columns = [], []
    for sid in sorted(data):
        col = [data[sid].get(k) for k in keys]
        coverage = sum(v is not None for v in col) / len(col)
        if coverage < args.min_coverage:
            continue
        s = stations[sid]
        out_stations.append({k: s[k] for k in ("id", "name", "state", "elev", "lat", "lon")})
        columns.append(col)

    # temps[zeitindex][stationsindex]
    temps = [list(row) for row in zip(*columns)]
    flat = [v for row in temps for v in row if v is not None]

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "Deutscher Wetterdienst (DWD), opendata.dwd.de",
        "times": [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in times],
        "stations": out_stations,
        "temps": temps,
        "min": min(flat),
        "max": max(flat),
    }, separators=(",", ":")), encoding="utf-8")
    print(f"Fertig: {len(out_stations)} Stationen x {len(times)} Stunden -> {OUT}")


if __name__ == "__main__":
    main()
