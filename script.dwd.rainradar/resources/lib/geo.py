"""Web-Mercator-Rechnung für Kartenausschnitte (EPSG:3857).

Kodi-unabhängig, damit es sich ohne Kodi testen lässt.
"""
import math

R = 6378137.0

# Ausdehnung der DWD-Radarabdeckung, aus dem Plasmoid übernommen.
RADAR_LAT_MIN, RADAR_LAT_MAX = 45.0, 56.576107
RADAR_LON_MIN, RADAR_LON_MAX = 2.0, 19.0

# Breite des Ausschnitts bei Zoomstufe 0 in Metern. Bei 16:9 passt damit
# die gesamte Radarabdeckung (ca. 2050 km hoch) auf den Bildschirm.
ZOOM0_WIDTH_M = 3650000.0
MAX_ZOOM = 4

# Mittelpunkt der Deutschlandansicht (Mitte der Radarabdeckung).
GERMANY_CENTER = (51.2, 10.45)


def to_merc(lat, lon):
    x = math.radians(lon) * R
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R
    return x, y


def to_latlon(x, y):
    lon = math.degrees(x / R)
    lat = math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)
    return lat, lon


def clamp_center(lat, lon):
    """Hält den Mittelpunkt innerhalb der Radarabdeckung."""
    lat = min(max(lat, RADAR_LAT_MIN), RADAR_LAT_MAX)
    lon = min(max(lon, RADAR_LON_MIN), RADAR_LON_MAX)
    return lat, lon


def width_for_zoom(zoom):
    return ZOOM0_WIDTH_M / (2 ** zoom)


def view_bbox(center_lat, center_lon, zoom, aspect):
    """Liefert (minx, miny, maxx, maxy) in EPSG:3857.

    aspect = Breite / Höhe des Zielbilds.
    """
    cx, cy = to_merc(center_lat, center_lon)
    w = width_for_zoom(zoom)
    h = w / aspect
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def to_pixel(lat, lon, bbox, width, height):
    """Position eines Punkts im Bild als (px, py), Ursprung oben links."""
    x, y = to_merc(lat, lon)
    minx, miny, maxx, maxy = bbox
    px = (x - minx) / (maxx - minx) * width
    py = (maxy - y) / (maxy - miny) * height
    return px, py


def pan(center_lat, center_lon, zoom, aspect, dx, dy, fraction=0.25):
    """Verschiebt den Mittelpunkt um einen Bruchteil der Ausschnittgröße.

    dx/dy in {-1, 0, 1}; dy=1 heißt nach Norden.
    """
    cx, cy = to_merc(center_lat, center_lon)
    w = width_for_zoom(zoom)
    h = w / aspect
    cx += dx * w * fraction
    cy += dy * h * fraction
    return clamp_center(*to_latlon(cx, cy))
