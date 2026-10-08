"""Entfernt Störfarben aus dem DWD-Radarbild über die PNG-Palette.

Der WMS liefert neben dem Niederschlag eine graue "kein Echo"-Fläche und eine
magentafarbene Abdeckungslinie. Das Plasmoid filtert beides mit einem Shader
(radar_cleaner.frag). Kodi hat keinen Shader-Zugriff, und Pillow ist nicht auf
jeder Plattform verfügbar. Weil png8 ein Palettenbild ist, reicht es, die
betroffenen Paletteneinträge im tRNS-Chunk transparent zu setzen. Die
Pixeldaten bleiben unangetastet.
"""
import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
COLOR_TYPE_PALETTE = 3


def is_noise(r, g, b, a):
    """Gleiche Regeln wie radar_cleaner.frag, auf 0..1 normiert."""
    if a == 0:
        return False
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    is_gray = abs(r - g) <= 0.03 and abs(r - b) <= 0.03 and abs(g - b) <= 0.03
    min_rb = min(r, b)
    # Regenfarbe (204,0,152) hat |R-B| = 0.204, 0.19 ist also sicher.
    is_pink = abs(r - b) <= 0.19 and min_rb > 0.01 and g < min_rb - 0.02
    # Starkregen-Violett hat G = 0, Mischpixel am Rand nicht.
    is_blend = min_rb > 0.3 and g > 0.05
    return is_gray or is_pink or is_blend


def _chunks(data):
    pos = len(PNG_SIGNATURE)
    while pos + 8 <= len(data):
        length, = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        yield ctype, body
        pos += 12 + length
        if ctype == b"IEND":
            break


def _chunk(ctype, body):
    crc = zlib.crc32(ctype + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", crc)


def clean_png(data):
    """Gibt das bereinigte PNG zurück. Andere Bildformate bleiben unverändert."""
    if not data.startswith(PNG_SIGNATURE):
        return data
    chunks = list(_chunks(data))
    if not chunks or chunks[0][0] != b"IHDR" or chunks[0][1][9] != COLOR_TYPE_PALETTE:
        return data

    plte = next((body for ctype, body in chunks if ctype == b"PLTE"), None)
    if plte is None:
        return data
    trns = next((body for ctype, body in chunks if ctype == b"tRNS"), b"")

    count = len(plte) // 3
    alphas = list(trns[:count]) + [255] * (count - len(trns))
    for i in range(count):
        r, g, b = plte[3 * i], plte[3 * i + 1], plte[3 * i + 2]
        if is_noise(r, g, b, alphas[i]):
            alphas[i] = 0

    out = [PNG_SIGNATURE]
    for ctype, body in chunks:
        if ctype == b"tRNS":
            continue
        out.append(_chunk(ctype, body))
        # tRNS muss direkt nach PLTE und vor IDAT stehen.
        if ctype == b"PLTE":
            out.append(_chunk(b"tRNS", bytes(alphas)))
    return b"".join(out)
