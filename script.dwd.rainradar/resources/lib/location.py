"""Standortbestimmung über die öffentliche IP-Adresse. Kodi-unabhängig.

Die Genauigkeit liegt meist bei Stadt- oder Providerebene. Wer es genauer
braucht, trägt die Koordinaten in den Einstellungen von Hand ein.
"""
import json
import os
import time
from urllib.request import Request, urlopen

USER_AGENT = "script.dwd.rainradar (Kodi)"
TIMEOUT = 8
CACHE_SECONDS = 24 * 3600


def _ipwhois(d):
    if d.get("success") is False:
        return None
    return d.get("latitude"), d.get("longitude"), d.get("city") or ""


def _freeipapi(d):
    return d.get("latitude"), d.get("longitude"), d.get("cityName") or ""


# Reihenfolge nach Erfahrung: freeipapi lag beim Test einmal auf dem
# Provider-Knoten (Frankfurt statt Berlin), daher nur als letzte Wahl.
# Nur HTTPS: Eine unverschlüsselte Antwort könnte unterwegs verändert werden.
SERVICES = [
    ("https://ipwho.is/", _ipwhois),
    ("https://freeipapi.com/api/json", _freeipapi),
]


def _clean(result):
    """Prüft eine Antwort und liefert (lat, lon, stadt) oder None."""
    if not result or result[0] is None or result[1] is None:
        return None
    lat, lon = float(result[0]), float(result[1])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    # Der Ortsname landet in einem Kodi-Label; eckige Klammern wären dort Formatierung.
    city = "".join(c for c in str(result[2]) if c.isprintable() and c not in "[]")
    return lat, lon, city[:40]


def lookup(opener=urlopen, services=SERVICES):
    """Liefert (lat, lon, stadt) oder None, wenn kein Dienst antwortet."""
    for url, parse in services:
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with opener(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            result = _clean(parse(data))
            if result:
                return result
        except Exception:
            continue
    return None


def cached_lookup(cache_file, opener=urlopen, now=None):
    """Wie lookup(), merkt sich das Ergebnis aber für einen Tag."""
    now = time.time() if now is None else now
    try:
        with open(cache_file) as fh:
            c = json.load(fh)
        if now - c["time"] < CACHE_SECONDS:
            return c["lat"], c["lon"], c["city"]
    except (OSError, ValueError, KeyError):
        pass

    result = lookup(opener)
    if result:
        lat, lon, city = result
        try:
            tmp = cache_file + ".part"
            with open(tmp, "w") as fh:
                json.dump({"time": now, "lat": lat, "lon": lon, "city": city}, fh)
            os.replace(tmp, cache_file)
        except OSError:
            pass
    return result
