"""Vollbildfenster mit Karte, Radaranimation und Fernbedienungssteuerung."""
import os
import threading
import time

import xbmc
import xbmcgui

import frames
import geo
import tileserver
import wms
from store import FetchError, view_key

# Kodi-Koordinatensystem für Python-Fenster (wird auf die echte Auflösung skaliert).
SCREEN_W, SCREEN_H = 1280, 720
# Auflösung der angeforderten Radarbilder.
IMG_W, IMG_H = 1920, 1080
# Größe des Kartenbilds; der Kartenserver liefert die doppelte Pixelzahl.
MAP_W, MAP_H = 1600, 900
SCALE = float(SCREEN_W) / IMG_W
ASPECT = float(IMG_W) / IMG_H

ACTION_LEFT = 1
ACTION_RIGHT = 2
ACTION_UP = 3
ACTION_DOWN = 4
ACTION_SELECT = 7
ACTION_PREVIOUS_MENU = 10
ACTION_PAUSE = 12
ACTION_STOP = 13
ACTION_PLAYER_PLAY = 79
ACTION_NAV_BACK = 92
ACTION_CONTEXT_MENU = 117
ACTION_PLAYER_PLAYPAUSE = 229
ACTION_PAGE_UP = 5
ACTION_PAGE_DOWN = 6

# Werte der Einstellung location_mode.
LOCATION_AUTO, LOCATION_MANUAL, LOCATION_OFF = 0, 1, 2

# Pause am Ende der Schleife, bevor die Animation von vorn beginnt.
LOOP_HOLD_SECONDS = 1.5
# Wartezeit nach Zoom/Verschieben, bevor neu geladen wird.
RELOAD_DEBOUNCE_SECONDS = 0.8

TIMELINE_X, TIMELINE_Y, TIMELINE_W = 40, 696, 1200
COLOR_PAST = "FF8FA3B8"
COLOR_FUTURE = "FFE0A040"


def log(msg, level=xbmc.LOGINFO):
    xbmc.log("[script.dwd.rainradar] %s" % msg, level)


class RadarWindow(xbmcgui.Window):
    # xbmcgui.Window nimmt keine eigenen Konstruktorargumente an, deshalb
    # wird das Fenster parameterlos erzeugt und über start() eingerichtet.
    def start(self, settings, store, media_dir, open_settings, locate, ensure_map):
        """locate() liefert (lat, lon, stadt) per IP oder None; läuft im Hintergrund.

        ensure_map() startet den Kartenserver und wirft tileserver.SetupError.
        """
        self.settings = settings
        self.store = store
        self.media = media_dir
        self.open_settings = open_settings
        self.locate = locate
        self.ensure_map = ensure_map
        self.map_setup = False
        # True nach erfolgreichem Start, bis wieder ein Kartenbild ankommt. Verhindert
        # eine Endlosschleife, wenn der Server läuft, aber keine Karte liefert.
        self.map_started = False
        self.map_note = ""
        self.status_text = ""

        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.generation = 0
        self.reload_timer = None

        self.zoom = 0
        self.center = geo.GERMANY_CENTER
        self.pan_mode = False
        # Standort als (lat, lon, stadt), sobald bekannt.
        self.location = None
        # True, sobald der Ausschnitt von Hand verschoben wurde. Dann zoomt
        # Hoch/Runter um die aktuelle Mitte statt um den Standort.
        self.manual_center = False
        self.playing = settings["autoplay"]
        self.base = 0
        self.times = []
        self.paths = []
        self.index = 0
        self.loaded = 0
        self.bbox = None

        self._build_controls()
        self._apply_start_view()
        self._resolve_location()
        self.reload()

        self.ticker = threading.Thread(target=self._tick_loop, name="dwdradar-ticker")
        self.ticker.daemon = True
        self.ticker.start()

    # ── Aufbau ──

    def _tex(self, name):
        return os.path.join(self.media, name)

    def _build_controls(self):
        white = self._tex("white.png")
        opacity = "%02X" % int(round(self.settings["opacity"] / 100.0 * 255))

        self.basemap = xbmcgui.ControlImage(0, 0, SCREEN_W, SCREEN_H, "")
        self.radar = xbmcgui.ControlImage(0, 0, SCREEN_W, SCREEN_H, "",
                                          colorDiffuse=opacity + "FFFFFF")
        self.marker = xbmcgui.ControlImage(0, 0, 22, 22, self._tex("marker.png"))

        self.info_bg = xbmcgui.ControlImage(20, 20, 400, 78, white, colorDiffuse="B0000000")
        self.time_label = xbmcgui.ControlLabel(36, 26, 380, 36, "", font="font30_title",
                                               textColor="FFFFFFFF")
        self.mode_label = xbmcgui.ControlLabel(36, 62, 380, 30, "", font="font13",
                                               textColor="FFDDDDDD")

        self.status_bg = xbmcgui.ControlImage(860, 20, 400, 40, white, colorDiffuse="B0000000")
        self.status_label = xbmcgui.ControlLabel(870, 26, 380, 30, "", font="font13",
                                                 textColor="FFFFFFFF", alignment=1)

        # Legende: Originalgröße 100x390 Pixel.
        self.legend = xbmcgui.ControlImage(1196, 330, 64, 250, "")

        # Quellenangabe, von den Kartenanbietern verlangt.
        self.attr_bg = xbmcgui.ControlImage(0, 616, 560, 24, white, colorDiffuse="90000000")
        self.attribution = xbmcgui.ControlLabel(10, 616, 545, 24, "", font="font10",
                                                textColor="FFDDDDDD")

        self.hint_bg = xbmcgui.ControlImage(0, 640, SCREEN_W, 80, white, colorDiffuse="A0000000")
        self.hint_label = xbmcgui.ControlLabel(40, 650, 1200, 30, "", font="font13",
                                               textColor="FFCCCCCC", alignment=2)
        self.track_past = xbmcgui.ControlImage(TIMELINE_X, TIMELINE_Y, 1, 6, white,
                                               colorDiffuse=COLOR_PAST)
        self.track_future = xbmcgui.ControlImage(TIMELINE_X, TIMELINE_Y, 1, 6, white,
                                                 colorDiffuse=COLOR_FUTURE)
        self.now_tick = xbmcgui.ControlImage(TIMELINE_X, TIMELINE_Y - 6, 2, 18, white,
                                             colorDiffuse="FFFFFFFF")
        self.knob = xbmcgui.ControlImage(TIMELINE_X, TIMELINE_Y - 6, 18, 18,
                                         self._tex("marker.png"))

        self.addControls([self.basemap,
            self.radar, self.marker, self.attr_bg, self.attribution,
            self.info_bg, self.time_label, self.mode_label,
            self.status_bg, self.status_label, self.legend,
            self.hint_bg, self.hint_label,
            self.track_past, self.track_future, self.now_tick, self.knob,
        ])
        self.marker.setVisible(False)
        self._update_hint()

    def _apply_start_view(self):
        self.zoom = self.settings["start_zoom"]
        self.center = geo.GERMANY_CENTER
        self.manual_center = False

    # ── Standort ──

    def _resolve_location(self):
        mode = self.settings["location_mode"]
        if mode == LOCATION_MANUAL:
            self._set_location((self.settings["lat"], self.settings["lon"], ""))
        elif mode == LOCATION_AUTO:
            worker = threading.Thread(target=self._locate_worker, name="dwdradar-locate")
            worker.daemon = True
            worker.start()
        else:
            self._set_location(None)

    def _locate_worker(self):
        result = self.locate()
        if self.stop_event.is_set():
            return
        if result is None:
            log("Standort per IP nicht ermittelbar", xbmc.LOGWARNING)
        else:
            # Ohne Koordinaten: Kodi-Logs werden oft öffentlich geteilt.
            log("Standort per IP ermittelt")
        self._set_location(result)

    def _set_location(self, location):
        self.location = location
        self._place_marker()
        self._show_frame()
        if location and self.zoom > 0 and not self.manual_center:
            self.center = self._home_center()
            # Vor dem ersten reload() (manueller Standort) reicht der neue Mittelpunkt.
            if self.bbox is not None:
                self.reload(keep_time=True)

    def _home_center(self):
        return geo.clamp_center(self.location[0], self.location[1])

    # ── Laden ──

    def reload(self, keep_time=False):
        """Baut Zeitliste und Ausschnitt neu auf und startet den Download."""
        with self.lock:
            self.generation += 1
            gen = self.generation
            old_ts = self.times[self.index] if keep_time and self.times else None
            bbox = geo.view_bbox(self.center[0], self.center[1], self.zoom, ASPECT)
            view_changed = bbox != self.bbox
            self.bbox = bbox
            self.bbox_zoom = self.zoom
            self.base = frames.base_time()
            self.times = frames.frame_times(self.base, self.settings["past_frames"],
                                            self.settings["forecast"])
            self.paths = [None] * len(self.times)
            self.loaded = 0
            now_index = self.settings["past_frames"]
            if old_ts in self.times:
                self.index = self.times.index(old_ts)
            else:
                self.index = now_index

        if view_changed:
            self.basemap.setImage("", False)
            self.radar.setImage("", False)
        self._place_marker()
        self._layout_timeline()
        self._show_frame()
        self._set_status("Lade Karte …")

        worker = threading.Thread(target=self._load, args=(gen,), name="dwdradar-loader")
        worker.daemon = True
        worker.start()

    def _cancelled(self, gen):
        return self.stop_event.is_set() or gen != self.generation

    def _load(self, gen):
        bbox, key = self.bbox, self._view_key()
        self._load_basemap(gen, bbox, key)

        try:
            self.legend.setImage(self.store.ensure(wms.legend_url(), self.store.legend_path()))
        except FetchError:
            pass

        with self.lock:
            if self._cancelled(gen):
                return
            times, base, start = list(self.times), self.base, self.index
        jobs = [(i, wms.radar_url(ts, bbox, IMG_W, IMG_H), self.store.radar_path(ts, base, key))
                for i, ts in enumerate(times)]
        # Das gerade sichtbare Bild zuerst, dann nach außen.
        jobs.sort(key=lambda job: abs(job[0] - start))

        self._set_status("Lade Radarbilder 0/%d" % len(jobs))
        self.store.ensure_many(jobs, lambda i, p: self._frame_done(gen, i, p),
                               lambda: self._cancelled(gen))
        if self._cancelled(gen):
            return

        with self.lock:
            missing = self.paths.count(None)
            keep = [p for p in self.paths if p]
        self.store.prune(keep)
        if missing == len(jobs):
            self._set_status("Keine Radardaten erhalten")
        elif missing:
            self._set_status("%d Bilder fehlen" % missing)
        else:
            self._set_status("")

    def _load_basemap(self, gen, bbox, key):
        self.attribution.setLabel(tileserver.ATTRIBUTION)
        try:
            path = self.store.ensure(tileserver.static_url(bbox, MAP_W, MAP_H),
                                     self.store.map_path(key))
        except FetchError as exc:
            if self._cancelled(gen):
                return
            log("Hintergrundkarte nicht ladbar: %s" % exc, xbmc.LOGWARNING)
            if self.map_started:
                self._set_map_note("Hintergrundkarte nicht ladbar")
            else:
                self._start_map_setup()
            return
        if self._cancelled(gen):
            return
        self.basemap.setImage(path)
        self.map_started = False
        self._set_map_note("")

    def _start_map_setup(self):
        with self.lock:
            if self.map_setup:
                return
            self.map_setup = True
        self._set_map_note("Kartenserver wird gestartet …")
        worker = threading.Thread(target=self._map_setup_worker, name="dwdradar-mapsetup")
        worker.daemon = True
        worker.start()

    def _map_setup_worker(self):
        try:
            self.ensure_map()
        except tileserver.SetupError as exc:
            log("Kartenserver: %s" % exc, xbmc.LOGERROR)
            self.map_setup = False
            if not self.stop_event.is_set():
                self._set_map_note("Kartenserver: %s" % exc)
            return
        self.map_started = True
        self.map_setup = False
        if not self.stop_event.is_set():
            self._set_map_note("")
            self.reload(keep_time=True)

    def _frame_done(self, gen, index, path):
        with self.lock:
            if gen != self.generation:
                return
            self.paths[index] = path
            self.loaded += 1
            total = len(self.paths)
            current = index == self.index
        if self.loaded < total:
            self._set_status("Lade Radarbilder %d/%d" % (self.loaded, total))
        if current:
            self._show_frame()

    def _view_key(self):
        return view_key(self.bbox, IMG_W, IMG_H)

    def _schedule_reload(self):
        if self.reload_timer:
            self.reload_timer.cancel()
        self._set_status("Ausschnitt wird geladen …")
        self.reload_timer = threading.Timer(RELOAD_DEBOUNCE_SECONDS, self.reload, kwargs={"keep_time": True})
        self.reload_timer.daemon = True
        self.reload_timer.start()

    # ── Anzeige ──

    def _set_status(self, text):
        # Der Hinweis zum Kartenserver bleibt stehen, solange sonst nichts ansteht.
        self.status_text = text
        text = text or self.map_note
        self.status_label.setLabel(text)
        self.status_bg.setVisible(bool(text))

    def _set_map_note(self, note):
        self.map_note = note
        self._set_status(self.status_text)

    def _place_marker(self):
        if not self.location or not self.bbox:
            self.marker.setVisible(False)
            return
        px, py = geo.to_pixel(self.location[0], self.location[1], self.bbox, IMG_W, IMG_H)
        x, y = int(px * SCALE) - 11, int(py * SCALE) - 11
        inside = 0 <= x <= SCREEN_W - 22 and 0 <= y <= SCREEN_H - 22
        self.marker.setPosition(x, y)
        self.marker.setVisible(inside)

    def _layout_timeline(self):
        n = len(self.times)
        now_index = self.settings["past_frames"]
        past_w = int(TIMELINE_W * now_index / float(max(n - 1, 1)))
        self.track_past.setWidth(max(past_w, 1))
        self.track_future.setPosition(TIMELINE_X + past_w, TIMELINE_Y)
        self.track_future.setWidth(max(TIMELINE_W - past_w, 1))
        self.track_future.setVisible(self.settings["forecast"])
        self.now_tick.setPosition(TIMELINE_X + past_w - 1, TIMELINE_Y - 6)

    def _show_frame(self):
        with self.lock:
            if not self.times:
                return
            i = self.index
            ts, path, base, n = self.times[i], self.paths[i], self.base, len(self.times)

        self.radar.setImage(path or "", True)
        self.time_label.setLabel(frames.local_label(ts))
        if ts < base:
            kind = "Verlauf"
        elif ts == base:
            kind = "Aktuell"
        else:
            kind = "Vorhersage"
        rel = frames.relative_label(ts, base)
        mode = kind if rel == "Jetzt" else "%s  (%s)" % (kind, rel)
        if path is None and self.loaded >= n:
            mode += "  –  Bild fehlt"
        if self.location and self.location[2]:
            mode += "     \u2022 " + self.location[2]
        self.mode_label.setLabel(mode)

        x = TIMELINE_X + int(TIMELINE_W * i / float(max(n - 1, 1))) - 9
        self.knob.setPosition(x, TIMELINE_Y - 6)

    def _update_hint(self):
        if self.pan_mode:
            text = "Pfeiltasten: Ausschnitt verschieben     OK / Zurück: fertig"
        else:
            # Pfeilsymbole fehlen in den Skin-Schriften, daher Klartext.
            text = ("Links/Rechts: Bild     OK: Start/Stopp     Hoch/Runter: Zoom     "
                    "Menü: Optionen     Zurück: Beenden")
        self.hint_label.setLabel(text)

    # ── Animation ──

    def _step(self, delta, only_loaded=False):
        with self.lock:
            n = len(self.times)
            if not n:
                return False
            i = self.index
            for _ in range(n):
                i = (i + delta) % n
                if not only_loaded or self.paths[i]:
                    break
            wrapped = (delta > 0 and i < self.index) or (delta < 0 and i > self.index)
            self.index = i
        self._show_frame()
        return wrapped

    def _tick_loop(self):
        monitor = xbmc.Monitor()
        while not self.stop_event.is_set() and not monitor.abortRequested():
            delay = self.settings["frame_ms"] / 1000.0
            if self.playing and self.loaded:
                self._step(1, only_loaded=True)
                with self.lock:
                    if self.index == len(self.times) - 1:
                        delay = max(delay, LOOP_HOLD_SECONDS)
            if frames.base_time() != self.base:
                log("Neuer Basiszeitpunkt, aktualisiere")
                self.reload(keep_time=True)
            if self.stop_event.wait(delay):
                break
        self.close_window()

    # ── Eingabe ──

    def onAction(self, action):
        aid = action.getId()
        if self.pan_mode:
            self._pan_action(aid)
            return

        if aid in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK, ACTION_STOP):
            self.close_window()
        elif aid == ACTION_LEFT:
            self.playing = False
            self._step(-1)
        elif aid == ACTION_RIGHT:
            self.playing = False
            self._step(1)
        elif aid in (ACTION_SELECT, ACTION_PAUSE, ACTION_PLAYER_PLAY, ACTION_PLAYER_PLAYPAUSE):
            self.playing = not self.playing
        elif aid in (ACTION_UP, ACTION_PAGE_UP):
            self._zoom(1)
        elif aid in (ACTION_DOWN, ACTION_PAGE_DOWN):
            self._zoom(-1)
        elif aid == ACTION_CONTEXT_MENU:
            self._menu()

    def _pan_action(self, aid):
        moves = {ACTION_LEFT: (-1, 0), ACTION_RIGHT: (1, 0), ACTION_UP: (0, 1), ACTION_DOWN: (0, -1)}
        if aid in moves:
            dx, dy = moves[aid]
            self.center = geo.pan(self.center[0], self.center[1], self.zoom, ASPECT, dx, dy)
            self.manual_center = True
            self._schedule_reload()
        elif aid in (ACTION_SELECT, ACTION_PREVIOUS_MENU, ACTION_NAV_BACK, ACTION_CONTEXT_MENU):
            self.pan_mode = False
            self._update_hint()

    def _zoom(self, delta):
        zoom = min(max(self.zoom + delta, 0), geo.MAX_ZOOM)
        if zoom == self.zoom:
            return
        if zoom == 0:
            self.center = geo.GERMANY_CENTER
            self.manual_center = False
        elif self.location and not self.manual_center:
            # Gezoomt wird immer auf den eigenen Standort.
            self.center = self._home_center()
        self.zoom = zoom
        self._schedule_reload()

    def _menu(self):
        entries = []
        if self.location:
            entries.append(("Auf Standort zentrieren", self._menu_home))
        entries += [
            ("Ganz Deutschland", self._menu_germany),
            ("Ausschnitt verschieben", self._menu_pan),
            ("Jetzt neu laden", lambda: self.reload(keep_time=True)),
            ("Einstellungen", self._menu_settings),
        ]
        choice = xbmcgui.Dialog().select("DWD Regenradar", [e[0] for e in entries])
        if choice >= 0:
            entries[choice][1]()

    def _menu_home(self):
        self.center = self._home_center()
        self.manual_center = False
        self.zoom = max(self.zoom, 2)
        self.reload(keep_time=True)

    def _menu_germany(self):
        self.center = geo.GERMANY_CENTER
        self.zoom = 0
        self.manual_center = False
        self.reload(keep_time=True)

    def _menu_pan(self):
        if self.zoom == 0:
            self.zoom = 1
            self._schedule_reload()
        self.pan_mode = True
        self._update_hint()

    def _menu_settings(self):
        self.settings = self.open_settings()
        opacity = "%02X" % int(round(self.settings["opacity"] / 100.0 * 255))
        self.radar.setColorDiffuse(opacity + "FFFFFF")
        # bbox zurücksetzen, damit ein manueller Standort nicht doppelt lädt.
        self.bbox = None
        self._resolve_location()
        self.reload()

    def close_window(self):
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        if self.reload_timer:
            self.reload_timer.cancel()
        self.close()
