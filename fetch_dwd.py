"""
Lädt stündliche Messwerte aller DWD-Stationen (Open Data, "recent")
und schreibt sie kompakt nach data/<messgröße>.json für die Webkarte.

Messgrößen:
  temperature    Lufttemperatur 2 m in °C
  precipitation  Niederschlagshöhe in mm pro Stunde

Aufruf:  python3 fetch_dwd.py --days 365                  (beide Messgrößen)
         python3 fetch_dwd.py --days 7 --param temperature
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

ROOT = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/hourly/"

# Je Messgröße: DWD-Verzeichnis, Kürzel im Dateinamen, Spalte des Messwerts
PARAMS = {
    "temperature":   {"dir": "air_temperature", "code": "TU", "column": 3, "unit": "°C"},
    "precipitation": {"dir": "precipitation",   "code": "RR", "column": 3, "unit": "mm/h"},
}
OUT_DIR = Path(__file__).parent / "data"


def get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def load_stations(base, code):
    """Stationsliste parsen: id, Höhe, Lat, Lon, Name, Bundesland."""
    text = get(f"{base}{code}_Stundenwerte_Beschreibung_Stationen.txt").decode("latin-1")
    stations = {}
    for line in text.splitlines()[2:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        sid, _von, _bis, elev, lat, lon = parts[:6]
        stations[sid] = {
            "id": sid,
            "name": " ".join(parts[6:-2]),
            "state": parts[-2],
            "elev": float(elev),
            "lat": float(lat),
            "lon": float(lon),
        }
    return stations


def list_zip_files(base, code):
    html = get(base).decode("latin-1")
    return sorted(set(re.findall(rf"stundenwerte_{code}_(\d{{5}})_akt\.zip", html)))


def load_station_data(base, code, column, sid, cutoff):
    """Gibt {zeitstempel 'YYYYMMDDHH': messwert} für eine Station zurück."""
    try:
        raw = get(f"{base}stundenwerte_{code}_{sid}_akt.zip")
    except Exception as e:  # Station kurzzeitig nicht erreichbar -> überspringen
        print(f"  {sid}: Download fehlgeschlagen ({e})")
        return sid, {}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = next(n for n in z.namelist() if n.startswith("produkt_"))
        text = z.read(name).decode("latin-1")
    values = {}
    for line in text.splitlines()[1:]:
        cols = [c.strip() for c in line.split(";")]
        if len(cols) <= column or cols[1] < cutoff:
            continue
        v = float(cols[column])
        if v > -999:  # -999 = Fehlwert
            values[cols[1]] = v
    return sid, values


def fetch(param, days, min_coverage):
    cfg = PARAMS[param]
    base = f"{ROOT}{cfg['dir']}/recent/"
    print(f"[{param}] Lade Stationsliste ...")
    stations = load_stations(base, cfg["code"])
    ids = [s for s in list_zip_files(base, cfg["code"]) if s in stations]
    print(f"[{param}] {len(ids)} Stationen mit aktuellen Daten")

    # Der "recent"-Datensatz endet meist gestern 23 UTC; Zeitraum ab dort rückwärts
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cutoff = (now - timedelta(days=days + 2)).strftime("%Y%m%d%H")

    data = {}
    load = lambda s: load_station_data(base, cfg["code"], cfg["column"], s, cutoff)
    with ThreadPoolExecutor(max_workers=16) as pool:
        for i, (sid, vals) in enumerate(pool.map(load, ids), 1):
            if vals:
                data[sid] = vals
            if i % 200 == 0:
                print(f"  {i}/{len(ids)}")

    # Gemeinsame Zeitachse: die letzten N Tage bis zum letzten vorhandenen Zeitstempel
    last = max(max(v) for v in data.values())
    end = datetime.strptime(last, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    hours = days * 24
    start = end - timedelta(hours=hours - 1)
    keys = [(start + timedelta(hours=h)).strftime("%Y%m%d%H") for h in range(hours)]

    out_stations, columns = [], []
    for sid in sorted(data):
        # Werte als ganze Zehntel speichern (15.3 °C -> 153): kleinere Datei, schneller im Browser
        col = [round(data[sid][k] * 10) if k in data[sid] else None for k in keys]
        if sum(v is not None for v in col) / hours < min_coverage:
            continue
        s = stations[sid]
        out_stations.append(s)
        columns.append(col)

    values = [list(row) for row in zip(*columns)]   # values[stunde][station]
    flat = sorted(v for row in values for v in row if v is not None)
    nonzero = [v for v in flat if v > 0]
    p99 = nonzero[int(len(nonzero) * 0.99)] if nonzero else flat[-1]

    out = OUT_DIR / f"{param}.json"
    out.write_text(json.dumps({
        "param": param,
        "unit": cfg["unit"],
        "source": "Deutscher Wetterdienst (DWD), opendata.dwd.de",
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),   # stündliche Werte ab hier
        "hours": hours,
        "divisor": 10,                                    # gespeicherte Werte / 10 = echte Werte
        "stations": out_stations,
        "values": values,
        "min": flat[0] / 10,
        "max": flat[-1] / 10,
        "p99": p99 / 10,                                  # 99 % aller Werte > 0 liegen darunter
    }, separators=(",", ":")), encoding="utf-8")
    print(f"[{param}] Fertig: {len(out_stations)} Stationen x {hours} Stunden -> {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365, help="Zeitraum in Tagen (Standard 365)")
    ap.add_argument("--param", choices=[*PARAMS, "all"], default="all", help="Messgröße (Standard: alle)")
    ap.add_argument("--min-coverage", type=float, default=0.5,
                    help="Mindestanteil vorhandener Werte je Station (0..1)")
    args = ap.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    for param in (PARAMS if args.param == "all" else [args.param]):
        fetch(param, args.days, args.min_coverage)


if __name__ == "__main__":
    main()
