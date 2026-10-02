# Wetterkarte

2,5D-Temperaturkarte Deutschlands aus den stündlichen Messwerten aller DWD-Stationen.
Die Stationen bilden ein Dreiecksnetz; Höhe und Farbe entsprechen der Temperatur.

## Lokal starten

```bash
python3 fetch_dwd.py --days 365
python3 -m http.server 8765
```

Dann http://localhost:8765 öffnen.

## Online

Bei jedem Push auf `main` und täglich um 11:00 UTC lädt GitHub Actions neue Daten
und veröffentlicht die Seite auf GitHub Pages (`.github/workflows/deploy.yml`).

Daten: Deutscher Wetterdienst (DWD), opendata.dwd.de
