import os
import struct
import time
import zlib

import pytest

import frames
import json
import math
import location
import geo
import palette
import store
import tileserver
import wms


# ── geo ──

def test_merc_roundtrip():
    lat, lon = geo.to_latlon(*geo.to_merc(52.52, 13.40))
    assert lat == pytest.approx(52.52)
    assert lon == pytest.approx(13.40)


def test_radar_bbox_matches_plasmoid():
    # Die Plasmoid-BBOX in EPSG:3857 entspricht den Abdeckungsecken.
    x0, y0 = geo.to_merc(geo.RADAR_LAT_MIN, geo.RADAR_LON_MIN)
    x1, y1 = geo.to_merc(geo.RADAR_LAT_MAX, geo.RADAR_LON_MAX)
    assert (x0, y0, x1, y1) == pytest.approx(
        (222638.98, 5621521.49, 2115070.32, 7673967.65), abs=1)


def test_zoom0_covers_whole_radar_height():
    minx, miny, maxx, maxy = geo.view_bbox(*geo.GERMANY_CENTER, zoom=0, aspect=16 / 9.0)
    assert maxy - miny >= 7673967.65 - 5621521.49


def test_center_is_in_middle_of_image():
    bbox = geo.view_bbox(50.0, 8.0, 2, 16 / 9.0)
    px, py = geo.to_pixel(50.0, 8.0, bbox, 1920, 1080)
    assert (px, py) == pytest.approx((960, 540))


def test_pan_north_increases_lat_and_clamps():
    lat, lon = geo.pan(51.0, 10.0, 2, 16 / 9.0, 0, 1)
    assert lat > 51.0 and lon == pytest.approx(10.0)
    lat, lon = geo.pan(56.5, 18.9, 0, 16 / 9.0, 1, 1)
    assert lat == geo.RADAR_LAT_MAX and lon == geo.RADAR_LON_MAX


# ── tileserver ──

def test_static_url_matches_view():
    bbox = geo.view_bbox(52.52, 13.40, 0, 16 / 9.0)
    url = tileserver.static_url(bbox, 1280, 720)
    head, coords, size = url.rsplit("/", 2)
    assert head == "http://127.0.0.1:8585/styles/liberty/static"
    assert size == "1280x720@2x.png"
    lon, lat, zoom = (float(v) for v in coords.split(","))
    assert (lat, lon) == pytest.approx((52.52, 13.40), abs=1e-5)
    # Bei Zoom z ist die Welt 256 * 2^z Punkte breit; 1280 Punkte zeigen den Ausschnitt.
    world = 2 * math.pi * geo.R
    assert 1280 * world / (256 * 2 ** zoom) == pytest.approx(geo.ZOOM0_WIDTH_M, rel=1e-4)
    closer = tileserver.static_url(geo.view_bbox(52.52, 13.40, 1, 16 / 9.0), 1280, 720)
    assert float(closer.rsplit("/", 2)[1].split(",")[2]) == pytest.approx(zoom + 1, abs=1e-3)


class _Docker(object):
    """Nachgebautes docker-Kommando: merkt sich Aufrufe und den Containerzustand."""

    def __init__(self, state=None, run_code=0):
        self.state = state
        self.run_code = run_code
        self.calls = []

    def __call__(self, args, timeout):
        self.calls.append(args[1])
        if args[1] == "inspect":
            if self.state is None:
                return 1, "No such object"
            return 0, "%s %s" % ("true" if self.state[0] else "false", self.state[1])
        if args[1] == "rm":
            self.state = None
        elif args[1] in ("run", "start") and self.run_code == 0:
            self.state = (True, tileserver.IMAGE)
        return self.run_code, "boom" if self.run_code else ""

    def healthy(self):
        return bool(self.state and self.state[0])


def _style_opener(req, timeout):
    return _Resp(b'{"version": 8}')


def _ensure(tmp_path, docker, monkeypatch, found="/usr/bin/docker"):
    monkeypatch.setattr(tileserver, "find_docker", lambda: found)
    tileserver.ensure(str(tmp_path), run=docker, opener=_style_opener,
                      is_healthy=docker.healthy, start_timeout=4, sleep=lambda s: None)


def test_tileserver_creates_container_and_config(tmp_path, monkeypatch):
    docker = _Docker()
    _ensure(tmp_path, docker, monkeypatch)
    assert docker.calls == ["inspect", "run"]
    config = json.load(open(str(tmp_path / "config.json")))
    assert config["styles"]["liberty"]["style"] == "liberty.json"
    assert (tmp_path / "styles" / "liberty.json").read_bytes() == b'{"version": 8}'
    args = tileserver.run_args("docker", "/x")
    assert "127.0.0.1:8585:8080" in args and "/x:/data:ro" in args


def test_tileserver_starts_stopped_and_replaces_old_image(tmp_path, monkeypatch):
    docker = _Docker(state=(False, tileserver.IMAGE))
    _ensure(tmp_path, docker, monkeypatch)
    assert docker.calls == ["inspect", "start"]

    docker = _Docker(state=(False, "maptiler/tileserver-gl:v4.0.0"))
    _ensure(tmp_path, docker, monkeypatch)
    assert docker.calls == ["inspect", "rm", "run"]


def test_tileserver_does_nothing_when_healthy(tmp_path, monkeypatch):
    docker = _Docker(state=(True, tileserver.IMAGE))
    _ensure(tmp_path, docker, monkeypatch, found=None)
    assert docker.calls == []


def test_tileserver_reports_errors(tmp_path, monkeypatch):
    with pytest.raises(tileserver.SetupError, match="Docker nicht gefunden"):
        _ensure(tmp_path, _Docker(), monkeypatch, found=None)
    with pytest.raises(tileserver.SetupError, match="boom"):
        _ensure(tmp_path, _Docker(run_code=125), monkeypatch)


# ── frames ──

def test_base_time_has_safety_margin_and_step():
    now = 1_700_000_123
    base = frames.base_time(now)
    assert base % 300 == 0
    assert now - 900 < base <= now - 600


def test_frame_times_layout():
    base = 1_700_000_100
    times = frames.frame_times(base, 24, True)
    assert len(times) == 24 + 1 + 24
    assert times[24] == base
    assert times[0] == base - 24 * 300
    assert times[-1] == base + 24 * 300
    assert all(b - a == 300 for a, b in zip(times, times[1:]))
    assert frames.frame_times(base, 12, False)[-1] == base


def test_labels():
    assert frames.relative_label(1000, 1000) == "Jetzt"
    assert frames.relative_label(1000 - 2700, 1000) == "-45 min"
    assert frames.relative_label(1000 + 1800, 1000) == "+30 min"
    ts = int(time.mktime((2026, 10, 3, 14, 5, 0, 0, 0, -1)))
    assert frames.local_label(ts) == "Sa, 03.10. 14:05"


# ── wms ──

def test_radar_url():
    url = wms.radar_url(1_700_000_100, (1, 2, 3, 4), 1920, 1080)
    assert "LAYERS=dwd:Niederschlagsradar" in url
    assert "FORMAT=image%2Fpng8" in url or "FORMAT=image/png8" in url
    assert "CRS=EPSG:3857" in url
    assert "BBOX=1.00,2.00,3.00,4.00" in url
    assert "TIME=2023-11-14T22:15:00Z" in url


# ── palette ──

def _chunk(ctype, body):
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)


def _palette_png(colors, alphas=None):
    ihdr = struct.pack(">IIBBBBB", 2, 1, 8, 3, 0, 0, 0)
    plte = b"".join(bytes(c) for c in colors)
    raw = zlib.compress(b"\x00\x00\x01")
    data = palette.PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"PLTE", plte)
    if alphas is not None:
        data += _chunk(b"tRNS", bytes(alphas))
    return data + _chunk(b"IDAT", raw) + _chunk(b"IEND", b"")


def _alphas(png):
    for ctype, body in palette._chunks(png):
        if ctype == b"tRNS":
            return list(body)


@pytest.mark.parametrize("rgb, noise", [
    ((126, 126, 126), True),    # graue "kein Echo"-Fläche
    ((252, 0, 255), True),      # Abdeckungslinie
    ((165, 85, 167), True),     # Mischpixel der Linie
    ((204, 0, 152), False),     # Starkregen
    ((0, 150, 255), False),     # leichter Regen (blau)
    ((255, 255, 0), False),     # gelb
])
def test_noise_rules(rgb, noise):
    assert palette.is_noise(*rgb, a=255) is noise


def test_clean_png_sets_alpha_and_keeps_rain():
    png = _palette_png([(126, 126, 126), (204, 0, 152), (252, 0, 255)], [77, 255])
    cleaned = palette.clean_png(png)
    assert _alphas(cleaned) == [0, 255, 0]
    # tRNS steht zwischen PLTE und IDAT, CRCs stimmen.
    order = [c for c, _ in palette._chunks(cleaned)]
    assert order == [b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND"]


def test_clean_png_without_trns_creates_one():
    cleaned = palette.clean_png(_palette_png([(126, 126, 126), (0, 150, 255)]))
    assert _alphas(cleaned) == [0, 255]


def test_clean_png_passes_other_data_through():
    assert palette.clean_png(b"<xml/>") == b"<xml/>"


# ── store ──

class _Resp(object):
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.data


def test_store_caches_and_prunes(tmp_path):
    calls = []

    def opener(req, timeout):
        calls.append(req.full_url)
        return _Resp(_palette_png([(126, 126, 126), (0, 150, 255)]))

    s = store.FrameStore(str(tmp_path / "c"), opener=opener)
    key = store.view_key((1, 2, 3, 4), 1920, 1080)
    p_obs = s.radar_path(1000, 1000, key)
    p_fc = s.radar_path(1300, 1000, key)
    assert "obs_" in p_obs and "fc_" in p_fc and p_fc != s.radar_path(1300, 1300, key)

    done = {}
    s.ensure_many([(0, "https://x/0", p_obs), (1, "https://x/1", p_fc)], lambda i, p: done.__setitem__(i, p), lambda: False)
    assert done == {0: p_obs, 1: p_fc}
    s.ensure("https://x/0", p_obs)
    assert len(calls) == 2  # zweiter Aufruf aus dem Cache

    s.prune([p_obs])
    assert (tmp_path / "c").joinpath(p_obs.split("/")[-1]).exists()
    assert not (tmp_path / "c").joinpath(p_fc.split("/")[-1]).exists()


def test_fetch_rejects_service_exception():
    def opener(req, timeout):
        return _Resp(b"<?xml version='1.0'?><ServiceExceptionReport/>")

    with pytest.raises(store.FetchError):
        store.fetch("https://x/e", opener)


# ── location ──

def test_location_falls_back_to_next_service():
    answers = {
        "https://a/": b'{"success": false}',
        "https://b/": b'{"status": "success", "lat": 52.5, "lon": 13.4, "city": "Berlin"}',
    }

    def opener(req, timeout):
        return _Resp(answers[req.full_url])

    services = [("https://a/", location._ipwhois), ("https://b/", location._ipapi)]
    assert location.lookup(opener, services) == (52.5, 13.4, "Berlin")


def test_location_returns_none_when_all_fail():
    def opener(req, timeout):
        raise OSError("offline")

    assert location.lookup(opener) is None


def test_location_cache(tmp_path):
    calls = []

    def opener(req, timeout):
        calls.append(req.full_url)
        return _Resp(json.dumps({"latitude": 50.0, "longitude": 8.0, "city": "X"}).encode())

    f = str(tmp_path / "loc.json")
    assert location.cached_lookup(f, opener, now=1000) == (50.0, 8.0, "X")
    assert location.cached_lookup(f, opener, now=2000) == (50.0, 8.0, "X")
    assert len(calls) == 1
    location.cached_lookup(f, opener, now=1000 + location.CACHE_SECONDS + 1)
    assert len(calls) == 2


def test_prune_maps_by_age(tmp_path):
    s = store.FrameStore(str(tmp_path))
    old = s.map_path("alt")
    new = s.map_path("neu")
    for p in (old, new):
        open(p, "wb").write(b"x")
    os.utime(old, (0, 0))
    s.prune_maps(max_age_days=30)
    assert not os.path.exists(old) and os.path.exists(new)


# ── live ──

@pytest.mark.netzwerk
def test_live_dwd_frame():
    base = frames.base_time()
    bbox = geo.view_bbox(*geo.GERMANY_CENTER, zoom=0, aspect=16 / 9.0)
    data = store.fetch(wms.radar_url(base, bbox, 640, 360))
    assert data.startswith(palette.PNG_SIGNATURE)
    assert palette.clean_png(data).startswith(palette.PNG_SIGNATURE)
