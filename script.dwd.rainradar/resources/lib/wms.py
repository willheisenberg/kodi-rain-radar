"""URLs für den DWD-GeoServer."""
from urllib.parse import urlencode

from frames import iso_utc

WMS_BASE = "https://maps.dwd.de/geoserver/ows"
RADAR_LAYER = "dwd:Niederschlagsradar"

def _bbox_str(bbox):
    return ",".join("%.2f" % v for v in bbox)


def getmap_url(layers, styles, bbox, width, height, fmt, transparent, extra=None):
    params = [
        ("SERVICE", "WMS"),
        ("VERSION", "1.3.0"),
        ("REQUEST", "GetMap"),
        ("LAYERS", ",".join(layers)),
        ("STYLES", ",".join(styles)),
        ("CRS", "EPSG:3857"),
        ("BBOX", _bbox_str(bbox)),
        ("WIDTH", str(width)),
        ("HEIGHT", str(height)),
        ("FORMAT", fmt),
        ("TRANSPARENT", "TRUE" if transparent else "FALSE"),
    ]
    if extra:
        params += extra
    return WMS_BASE + "?" + urlencode(params, safe=":,/")


def radar_url(ts, bbox, width, height):
    # png8 liefert ein Palettenbild; so lassen sich Störfarben über die
    # Palette entfernen, ohne Pixel zu dekodieren (siehe palette.py).
    return getmap_url([RADAR_LAYER], [""], bbox, width, height,
                      "image/png8", True, [("TIME", iso_utc(ts))])


def legend_url():
    params = [
        ("SERVICE", "WMS"),
        ("VERSION", "1.3.0"),
        ("REQUEST", "GetLegendGraphic"),
        ("FORMAT", "image/png"),
        ("WIDTH", "20"),
        ("HEIGHT", "20"),
        ("LAYER", RADAR_LAYER),
    ]
    return WMS_BASE + "?" + urlencode(params, safe=":")
