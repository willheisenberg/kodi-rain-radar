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


def _ipapi(d):
    if d.get("status") != "success":
        return None
    return d.get("lat"), d.get("lon"), d.get("city") or ""


def _freeipapi(d):
    return d.get("latitude"), d.get("longitude"), d.get("cityName") or ""


# Reihenfolge nach Erfahrung: freeipapi lag beim Test einmal auf dem
# Provider-Knoten (Frankfurt statt Berlin), daher nur als letzte Wahl.
SERVICES = [
    ("https://ipwho.is/", _ipwhois),
    ("http://ip-api.com/json/", _ipapi),
    ("https://freeipapi.com/api/json", _freeipapi),
]


def lookup(opener=urlopen, services=SERVICES):
    """Liefert (lat, lon, stadt) oder None, wenn kein Dienst antwortet."""
    for url, parse in services:
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with opener(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            result = parse(data)
            if result and result[0] is not None and result[1] is not None:
                return float(result[0]), float(result[1]), result[2]
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
