"""Smoke-Test des Fensters mit nachgebauten Kodi-Modulen (ohne Netzwerk)."""
import sys
import threading
import time
import types

import pytest

from test_logic import _palette_png, _Resp


class _Control(object):
    def __init__(self, *args, **kwargs):
        self.image = args[4] if len(args) > 4 else ""
        self.label = ""
        self.visible = True
        self.pos = (args[0], args[1]) if len(args) > 1 else (0, 0)

    def setImage(self, path, useCache=True):
        self.image = path

    def setLabel(self, text):
        self.label = text

    def setVisible(self, v):
        self.visible = v

    def setPosition(self, x, y):
        self.pos = (x, y)

    def setWidth(self, w):
        pass

    def setHeight(self, h):
        pass

    def setColorDiffuse(self, c):
        pass


class _Window(object):
    def __init__(self):
        self.closed = False

    def addControls(self, controls):
        pass

    def close(self):
        self.closed = True


class _Monitor(object):
    def abortRequested(self):
        return False


class _Action(object):
    def __init__(self, aid):
        self.aid = aid

    def getId(self):
        return self.aid


@pytest.fixture
def window_module(monkeypatch):
    xbmc = types.ModuleType("xbmc")
    xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR = 1, 2, 3
    xbmc.log = lambda msg, level=1: None
    xbmc.Monitor = _Monitor
    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui.Window = _Window
    xbmcgui.ControlImage = _Control
    xbmcgui.ControlLabel = _Control
    monkeypatch.setitem(sys.modules, "xbmc", xbmc)
    monkeypatch.setitem(sys.modules, "xbmcgui", xbmcgui)
    sys.modules.pop("window", None)
    import window
    yield window
    sys.modules.pop("window", None)


SETTINGS = {
    "location_mode": 0, "lat": 0.0, "lon": 0.0, "start_zoom": 0,
    "opacity": 80, "past_frames": 12, "forecast": True, "frame_ms": 100,
    "autoplay": False,
}


def _wait(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_window_loads_steps_zooms_and_closes(window_module, tmp_path):
    import store

    urls = []
    lock = threading.Lock()

    def opener(req, timeout):
        with lock:
            urls.append(req.full_url)
        return _Resp(_palette_png([(126, 126, 126), (0, 150, 255)]))

    st = store.FrameStore(str(tmp_path), opener=opener)
    win = window_module.RadarWindow()
    win.start(dict(SETTINGS), st, "/media", lambda: dict(SETTINGS),
              lambda: (52.52, 13.40, "Berlin"), lambda: None)
    try:
        n = 12 + 1 + 24
        assert _wait(lambda: win.loaded == n)
        assert win.radar.image.startswith(str(tmp_path))
        assert _wait(lambda: win.location is not None)
        assert win.mode_label.label == "Aktuell     \u2022 Berlin"
        assert win.marker.visible

        win.onAction(_Action(window_module.ACTION_LEFT))
        assert win.index == 11 and "Verlauf" in win.mode_label.label
        win.onAction(_Action(window_module.ACTION_RIGHT))
        win.onAction(_Action(window_module.ACTION_RIGHT))
        assert "Vorhersage  (+5 min)" in win.mode_label.label
        assert win.center == window_module.geo.GERMANY_CENTER

        gen = win.generation
        win.onAction(_Action(window_module.ACTION_UP))
        assert win.zoom == 1 and win.center == (52.52, 13.40)
        assert _wait(lambda: win.generation > gen and win.loaded == n)
        assert win.index == 13  # gleiche Uhrzeit nach dem Neuladen

        win.onAction(_Action(window_module.ACTION_SELECT))
        assert win.playing
        start = win.index
        assert _wait(lambda: win.index != start)
    finally:
        win.onAction(_Action(window_module.ACTION_NAV_BACK))
    assert win.closed
    win.ticker.join(2)
    assert not win.ticker.is_alive()


def _fake_window(window_module, tmp_path, settings, locate):
    import store

    def opener(req, timeout):
        return _Resp(_palette_png([(0, 150, 255)]))

    win = window_module.RadarWindow()
    win.start(dict(SETTINGS, **settings), store.FrameStore(str(tmp_path), opener=opener),
              "/media", lambda: dict(SETTINGS), locate, lambda: None)
    return win


def test_zoom_always_targets_location_until_panned(window_module, tmp_path):
    w = window_module
    win = _fake_window(w, tmp_path, {"location_mode": w.LOCATION_MANUAL, "lat": 48.14, "lon": 11.58},
                       lambda: None)
    try:
        home = (48.14, 11.58)
        win.onAction(_Action(w.ACTION_UP))
        assert win.zoom == 1 and win.center == home
        win.onAction(_Action(w.ACTION_UP))
        assert win.zoom == 2 and win.center == home

        win._menu_pan()
        win.onAction(_Action(w.ACTION_RIGHT))
        win.onAction(_Action(w.ACTION_SELECT))  # Verschieben beenden
        moved = win.center
        assert moved != home and win.manual_center
        win.onAction(_Action(w.ACTION_UP))
        assert win.zoom == 3 and win.center == moved

        for _ in range(3):
            win.onAction(_Action(w.ACTION_DOWN))
        assert win.zoom == 0 and win.center == w.geo.GERMANY_CENTER
        assert not win.manual_center
        win.onAction(_Action(w.ACTION_UP))
        assert win.center == home
    finally:
        win.close_window()
        win.ticker.join(2)


def test_auto_location_recenters_when_started_zoomed(window_module, tmp_path):
    w = window_module
    win = _fake_window(w, tmp_path, {"location_mode": w.LOCATION_AUTO, "start_zoom": 2},
                       lambda: (50.94, 6.96, "Köln"))
    try:
        assert _wait(lambda: win.center == (50.94, 6.96))
        assert "Köln" in win.mode_label.label
    finally:
        win.close_window()
        win.ticker.join(2)


def test_location_off_hides_marker_and_menu_entry(window_module, tmp_path):
    w = window_module
    win = _fake_window(w, tmp_path, {"location_mode": w.LOCATION_OFF}, lambda: (1.0, 2.0, "x"))
    try:
        assert win.location is None and not win.marker.visible
        win.onAction(_Action(w.ACTION_UP))
        assert win.center == w.geo.GERMANY_CENTER
    finally:
        win.close_window()
        win.ticker.join(2)


def test_map_server_is_started_when_tiles_fail(window_module, tmp_path):
    import store
    import tileserver

    up = threading.Event()
    calls = []

    def opener(req, timeout):
        if req.full_url.startswith(tileserver.BASE_URL) and not up.is_set():
            raise IOError("Connection refused")
        return _Resp(_palette_png([(0, 150, 255)]))

    def ensure_map():
        calls.append(1)
        up.set()

    win = window_module.RadarWindow()
    win.start(dict(SETTINGS, location_mode=window_module.LOCATION_OFF),
              store.FrameStore(str(tmp_path), opener=opener),
              "/media", lambda: dict(SETTINGS), lambda: None, ensure_map)
    try:
        assert _wait(lambda: win.basemap.image.endswith(".png"))
        assert calls == [1]
        assert _wait(lambda: win.map_note == "" and not win.map_started)
    finally:
        win.close_window()
        win.ticker.join(2)


def test_map_server_error_is_shown_once(window_module, tmp_path):
    import store
    import tileserver

    def opener(req, timeout):
        if req.full_url.startswith(tileserver.BASE_URL):
            raise IOError("Connection refused")
        return _Resp(_palette_png([(0, 150, 255)]))

    def ensure_map():
        raise tileserver.SetupError("Docker nicht gefunden")

    win = window_module.RadarWindow()
    win.start(dict(SETTINGS, location_mode=window_module.LOCATION_OFF),
              store.FrameStore(str(tmp_path), opener=opener),
              "/media", lambda: dict(SETTINGS), lambda: None, ensure_map)
    try:
        assert _wait(lambda: win.map_note == "Kartenserver: Docker nicht gefunden")
        assert _wait(lambda: win.loaded == 37)
        assert _wait(lambda: win.status_label.label == "Kartenserver: Docker nicht gefunden")
    finally:
        win.close_window()
        win.ticker.join(2)
