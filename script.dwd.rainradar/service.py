"""Dienst: richtet nach der Installation und bei jedem Kodi-Start den Kartenserver ein."""
import os
import sys

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON = xbmcaddon.Addon("script.dwd.rainradar")
ADDON_DIR = xbmcvfs.translatePath(ADDON.getAddonInfo("path"))
sys.path.insert(0, os.path.join(ADDON_DIR, "resources", "lib"))

import tileserver  # noqa: E402

NAME = "DWD Regenradar"
# Beim Systemstart ist der Docker-Dienst oft noch nicht bereit.
ATTEMPTS = 5
RETRY_SECONDS = 30


def log(msg, level=xbmc.LOGINFO):
    xbmc.log("[script.dwd.rainradar] %s" % msg, level)


def notify(text, icon=xbmcgui.NOTIFICATION_INFO):
    xbmcgui.Dialog().notification(NAME, text, icon, 8000)


def main():
    monitor = xbmc.Monitor()
    if tileserver.healthy():
        return
    data_dir = os.path.join(xbmcvfs.translatePath(ADDON.getAddonInfo("profile")), "tileserver")
    docker = tileserver.find_docker()
    first = bool(docker) and tileserver.container_state(docker) is None
    if first:
        notify("Kartenserver wird eingerichtet (einmalig, ca. 1,2 GB Download)")

    error = None
    for attempt in range(ATTEMPTS):
        try:
            tileserver.ensure(data_dir)
        except tileserver.SetupError as exc:
            error = exc
            log("Kartenserver, Versuch %d: %s" % (attempt + 1, exc), xbmc.LOGWARNING)
            if monitor.waitForAbort(RETRY_SECONDS):
                return
            continue
        log("Kartenserver läuft")
        if first:
            notify("Kartenserver ist bereit")
        return
    notify("Kartenserver: %s" % error, xbmcgui.NOTIFICATION_ERROR)


if __name__ == "__main__":
    main()
