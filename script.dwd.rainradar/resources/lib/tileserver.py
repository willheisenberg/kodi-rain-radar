"""Eigener Kartenserver (tileserver-gl) als Docker-Container. Kodi-unabhängig.

Kodi kann keine Vektorkarten zeichnen. Der Container rendert deshalb den Stil
"Liberty" von OpenFreeMap, den auch das Plasmoid nutzt, zu einem Bild je
Kartenausschnitt. Die Vektordaten holt er weiterhin online bei OpenFreeMap.
"""
import json
import math
import os
import shlex
import shutil
import subprocess
import time
from urllib.request import Request, urlopen

import geo

IMAGE = "maptiler/tileserver-gl:v5.6.0"
CONTAINER = "dwd-rainradar-tiles"
PORT = 8585
BASE_URL = "http://127.0.0.1:%d" % PORT
STYLE_ID = "liberty"
STYLE_URL = "https://tiles.openfreemap.org/styles/liberty"
USER_AGENT = "script.dwd.rainradar (Kodi)"
ATTRIBUTION = "Karte: OpenFreeMap © OpenMapTiles, Daten © OpenStreetMap-Mitwirkende"

# LibreELEC hat Docker nur als Add-on, dessen bin-Ordner nicht im PATH von Kodi liegt.
DOCKER_PATHS = ("/storage/.kodi/addons/service.system.docker/bin/docker",)

# Der erste Start lädt das Image (rund 1,2 GB).
PULL_TIMEOUT = 3600
START_TIMEOUT = 90
# Wartezeit, bevor ein fehlender Add-on-Ordner als Deinstallation gilt. Bei
# einem Update fehlt er nur für wenige Sekunden.
CLEANUP_DELAY = 30

CONFIG = {
    "options": {
        "paths": {"root": "/data", "styles": "styles"},
        # Größte Bildkante in Pixeln, Vorgabe wäre 2048.
        "maxSize": 4096,
        # Wenige Renderer: Jeder kostet Arbeitsspeicher, und es fragt nur Kodi an.
        "minRendererPoolSizes": [1, 1, 1],
        "maxRendererPoolSizes": [2, 2, 2],
    },
    "styles": {STYLE_ID: {"style": STYLE_ID + ".json", "serve_rendered": True,
                          "serve_data": False}},
    "data": {},
}


class SetupError(Exception):
    pass


def static_url(bbox, width, height):
    """URL eines fertig gerenderten Kartenbilds für einen Ausschnitt in EPSG:3857.

    Das Bild hat die doppelte Pixelzahl (@2x), Schrift und Linien sind damit
    am TV scharf und groß genug.
    """
    minx, miny, maxx, maxy = bbox
    lat, lon = geo.to_latlon((minx + maxx) / 2, (miny + maxy) / 2)
    # Zoomstufe z: Die Welt ist 256 * 2^z Bildpunkte breit.
    zoom = math.log(width * 2 * math.pi * geo.R / (256.0 * (maxx - minx)), 2)
    return "%s/styles/%s/static/%.6f,%.6f,%.4f/%dx%d@2x.png" % (
        BASE_URL, STYLE_ID, lon, lat, zoom, width, height)


def find_docker():
    found = shutil.which("docker")
    if found:
        return found
    for path in DOCKER_PATHS:
        if os.access(path, os.X_OK):
            return path
    return None


def healthy(opener=urlopen, timeout=2):
    try:
        with opener(Request(BASE_URL + "/health"), timeout=timeout) as resp:
            return resp.getcode() == 200
    except Exception:
        return False


def write_config(data_dir, opener=urlopen):
    """Legt Konfiguration und Kartenstil für den Container ab."""
    styles = os.path.join(data_dir, "styles")
    if not os.path.isdir(styles):
        os.makedirs(styles)
    with open(os.path.join(data_dir, "config.json"), "w") as fh:
        json.dump(CONFIG, fh, indent=1)

    style_path = os.path.join(styles, STYLE_ID + ".json")
    if os.path.exists(style_path):
        return
    try:
        with opener(Request(STYLE_URL, headers={"User-Agent": USER_AGENT}), timeout=20) as resp:
            data = resp.read()
        json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise SetupError("Kartenstil nicht ladbar: %s" % exc)
    tmp = style_path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, style_path)


def _run(args, timeout):
    try:
        proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, proc.stdout.decode("utf-8", "replace").strip()


def container_state(docker, run=_run):
    """Liefert (läuft, image) oder None, wenn es den Container nicht gibt."""
    code, out = run([docker, "inspect", "-f", "{{.State.Running}} {{.Config.Image}}", CONTAINER], 30)
    if code != 0:
        return None
    running, _, image = out.partition(" ")
    return running == "true", image


def run_args(docker, data_dir):
    return [docker, "run", "-d", "--name", CONTAINER, "--restart", "unless-stopped",
            "-p", "127.0.0.1:%d:8080" % PORT, "-v", "%s:/data:ro" % data_dir,
            IMAGE, "--config", "/data/config.json"]


def ensure(data_dir, run=_run, opener=urlopen, is_healthy=healthy,
           start_timeout=START_TIMEOUT, sleep=time.sleep):
    """Sorgt dafür, dass der Kartenserver läuft. Wirft SetupError, wenn nicht."""
    if is_healthy():
        return
    docker = find_docker()
    if not docker:
        raise SetupError("Docker nicht gefunden")
    write_config(data_dir, opener)

    state = container_state(docker, run)
    if state and state[1] != IMAGE:
        # Container einer älteren Add-on-Version.
        run([docker, "rm", "-f", CONTAINER], 60)
        state = None
    if state is None:
        code, out = run(run_args(docker, data_dir), PULL_TIMEOUT)
    elif not state[0]:
        code, out = run([docker, "start", CONTAINER], 60)
    else:
        code, out = 0, ""

    # Auch nach einem Fehler warten: Dienst und Fenster können gleichzeitig
    # starten, dann gewinnt einer und der andere scheitert am Containernamen.
    waited = 0
    while waited < start_timeout:
        if is_healthy():
            return
        sleep(2)
        waited += 2
    if code != 0:
        raise SetupError("Docker: %s" % (out.splitlines()[-1] if out else "Fehler %d" % code))
    raise SetupError("Kartenserver antwortet nicht")


def cleanup_command(docker, addon_dir, delay=CLEANUP_DELAY):
    """Shell-Befehl: entfernt Container und Image, wenn das Add-on danach fehlt."""
    marker = shlex.quote(os.path.join(addon_dir, "addon.xml"))
    docker = shlex.quote(docker)
    return "sleep %d; [ -e %s ] || { %s rm -f %s; %s rmi %s; }" % (
        delay, marker, docker, CONTAINER, docker, IMAGE)


def schedule_cleanup(addon_dir, popen=subprocess.Popen):
    """Räumt nach einer Deinstallation auf.

    Kodi meldet Add-ons ihre Deinstallation nicht, es beendet nur den Dienst,
    genau wie beim Herunterfahren oder Deaktivieren. Deshalb prüft ein von
    Kodi losgelöster Prozess etwas später, ob das Add-on noch da ist.
    """
    docker = find_docker()
    if not docker:
        return False
    devnull = subprocess.DEVNULL
    try:
        popen(["/bin/sh", "-c", cleanup_command(docker, addon_dir)], stdin=devnull,
              stdout=devnull, stderr=devnull, start_new_session=True, close_fds=True)
    except OSError:
        return False
    return True
