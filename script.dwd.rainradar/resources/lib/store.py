"""Download und Dateicache der Radarbilder. Kodi-unabhängig."""
import hashlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen

import palette

USER_AGENT = "script.dwd.rainradar (Kodi)"
TIMEOUT = 20
RETRIES = 2
# Leere Radarbilder sind als png8 nur wenige hundert Byte groß, Fehlerantworten
# des GeoServers sind XML. Deshalb wird nur die PNG/JPEG-Signatur geprüft.
SIGNATURES = (palette.PNG_SIGNATURE, b"\xff\xd8\xff")


class FetchError(Exception):
    pass


def fetch(url, opener=urlopen):
    last = None
    for _ in range(RETRIES + 1):
        try:
            with opener(Request(url, headers={"User-Agent": USER_AGENT}), timeout=TIMEOUT) as resp:
                data = resp.read()
            if data.startswith(SIGNATURES):
                return data
            last = FetchError("Keine Bilddaten: %r" % data[:120])
        except Exception as exc:  # Netzfehler: erneut versuchen
            last = exc
    raise FetchError(str(last))


def view_key(bbox, width, height):
    """Kurzer, stabiler Schlüssel für einen Kartenausschnitt."""
    raw = "%s|%d|%d" % (",".join("%.0f" % v for v in bbox), width, height)
    return hashlib.sha1(raw.encode("ascii")).hexdigest()[:10]


class FrameStore(object):
    def __init__(self, cache_dir, opener=urlopen):
        self.cache_dir = cache_dir
        self.opener = opener
        if not os.path.isdir(cache_dir):
            os.makedirs(cache_dir)

    def radar_path(self, ts, base, key):
        # Vorhersagen werden mit jedem neuen Basiszeitpunkt neu gerechnet,
        # Verlaufsbilder ändern sich nicht mehr.
        if ts > base:
            name = "fc_%s_%d_%d.png" % (key, ts, base)
        else:
            name = "obs_%s_%d.png" % (key, ts)
        return os.path.join(self.cache_dir, name)

    def legend_path(self):
        return os.path.join(self.cache_dir, "legend.png")

    def map_path(self, key):
        folder = os.path.join(self.cache_dir, "maps")
        if not os.path.isdir(folder):
            os.makedirs(folder)
        return os.path.join(folder, "%s.png" % key)

    def ensure(self, url, path, clean=False):
        """Lädt url nach path, falls noch nicht vorhanden. Gibt path zurück."""
        if os.path.exists(path):
            return path
        data = fetch(url, self.opener)
        if clean:
            data = palette.clean_png(data)
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        return path

    def ensure_many(self, jobs, on_done, cancelled, workers=6, clean=True):
        """jobs: Liste von (index, url, path). on_done(index, path_or_None).

        Bricht ab, sobald cancelled() True liefert.
        """
        lock = threading.Lock()

        def run(job):
            index, url, path = job
            if cancelled():
                return
            try:
                result = self.ensure(url, path, clean=clean)
            except FetchError:
                result = None
            if not cancelled():
                with lock:
                    on_done(index, result)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(run, jobs))

    def prune(self, keep):
        """Löscht alle Radarbilder und Karten, die nicht in keep (Pfade) stehen."""
        keep = set(os.path.abspath(p) for p in keep)
        for name in os.listdir(self.cache_dir):
            if not name.startswith(("obs_", "fc_", "base_")) and not name.endswith(".part"):
                continue
            path = os.path.abspath(os.path.join(self.cache_dir, name))
            if path not in keep:
                try:
                    os.remove(path)
                except OSError:
                    pass

    def prune_maps(self, max_age_days=30, now=None):
        """Löscht Kartenbilder, die älter als max_age_days sind."""
        now = time.time() if now is None else now
        root = os.path.join(self.cache_dir, "maps")
        for folder, _, names in os.walk(root):
            for name in names:
                path = os.path.join(folder, name)
                try:
                    if now - os.path.getmtime(path) > max_age_days * 86400:
                        os.remove(path)
                except OSError:
                    pass
