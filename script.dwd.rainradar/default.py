"""Einstiegspunkt: öffnet das Regenradar im Vollbild."""
import os
import sys

import xbmcaddon
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_DIR = xbmcvfs.translatePath(ADDON.getAddonInfo("path"))
sys.path.insert(0, os.path.join(ADDON_DIR, "resources", "lib"))

import frames  # noqa: E402
import location  # noqa: E402
import tileserver  # noqa: E402
from store import FrameStore  # noqa: E402
from window import RadarWindow  # noqa: E402


def load_settings():
    addon = xbmcaddon.Addon()
    return {
        "location_mode": addon.getSettingInt("location_mode"),
        "lat": addon.getSettingNumber("latitude"),
        "lon": addon.getSettingNumber("longitude"),
        "start_zoom": addon.getSettingInt("start_zoom"),
        "opacity": addon.getSettingInt("opacity"),
        "past_frames": addon.getSettingInt("past_hours") * 3600 // frames.STEP_SECONDS,
        "forecast": addon.getSettingBool("forecast"),
        "frame_ms": addon.getSettingInt("frame_ms"),
        "autoplay": addon.getSettingBool("autoplay"),
    }


def open_settings():
    xbmcaddon.Addon().openSettings()
    return load_settings()


def main():
    profile = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
    store = FrameStore(os.path.join(profile, "cache"))
    store.prune_maps()
    media = os.path.join(ADDON_DIR, "resources", "media")

    loc_cache = os.path.join(profile, "location.json")

    win = RadarWindow()
    win.start(load_settings(), store, media, open_settings,
              lambda: location.cached_lookup(loc_cache),
              lambda: tileserver.ensure(os.path.join(profile, "tileserver")))
    try:
        win.doModal()
    finally:
        win.close_window()
        win.ticker.join(2)
        del win


if __name__ == "__main__":
    main()
