"""Erzeugt die PWA-App-Icons (Kegel auf grünem Grund) als PNG-Dateien.

Reines Python (nur Standardbibliothek, kein Pillow nötig). Aufruf aus dem
Projektverzeichnis:

    python3 scripts/erzeuge_pwa_icons.py

Schreibt nach app/static/pwa/: icon-192.png, icon-512.png, apple-touch-icon.png
(180x180). Der Hintergrund ist vollflächig, das Motiv liegt in der "Safe Zone"
(innerer Kreis mit 80 % Durchmesser), damit Android das Icon auch als
"maskable" (Kreis/Squircle) zuschneiden kann, ohne den Kegel abzuschneiden.
"""
import os
import struct
import zlib

BG = (0x17, 0x6A, 0x2F)        # --green der App
PIN = (0xFF, 0xFF, 0xFF)
STRIPE = (0xC6, 0x28, 0x28)
SHADE = (0xE3, 0xE8, 0xE4)     # leichter Schatten am Rand des Kegels

SUPERSAMPLE = 3  # 3x3 Unterpixel je Pixel -> glatte Kanten


def _inside_pin(x, y):
    """Kegelsilhouette in Einheitskoordinaten (0..1)."""
    # Kopf
    if (x - 0.5) ** 2 + (y - 0.27) ** 2 <= 0.085 ** 2:
        return True
    # Hals (verjüngt sich nach unten zum Bauch hin leicht)
    if 0.30 <= y <= 0.46 and abs(x - 0.5) <= 0.045 + (y - 0.30) * 0.10:
        return True
    # Bauch (Ellipse)
    if ((x - 0.5) / 0.175) ** 2 + ((y - 0.63) / 0.205) ** 2 <= 1.0:
        return True
    return False


def _pixel(x, y):
    if not _inside_pin(x, y):
        return BG
    # Zwei rote Streifen am Hals
    if 0.355 <= y <= 0.385 or 0.405 <= y <= 0.435:
        return STRIPE
    # Dezenter Schatten auf der rechten Seite des Bauchs
    if y > 0.46 and x > 0.5 + 0.09 + (0.63 - y) ** 2 * 0.5:
        return SHADE
    return PIN


def render(size):
    rows = []
    n = SUPERSAMPLE
    for py in range(size):
        row = bytearray([0])  # PNG-Filtertyp 0
        for px in range(size):
            r = g = b = 0
            for sy in range(n):
                for sx in range(n):
                    x = (px + (sx + 0.5) / n) / size
                    y = (py + (sy + 0.5) / n) / size
                    cr, cg, cb = _pixel(x, y)
                    r += cr
                    g += cg
                    b += cb
            k = n * n
            row += bytes((r // k, g // k, b // k))
        rows.append(bytes(row))
    return rows


def write_png(path, size):
    raw = b"".join(render(size))

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))  # 8 Bit RGB
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)
    print(f"{path} ({size}x{size}, {len(png)} Bytes)")


if __name__ == "__main__":
    target = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static", "pwa")
    os.makedirs(target, exist_ok=True)
    write_png(os.path.join(target, "icon-192.png"), 192)
    write_png(os.path.join(target, "icon-512.png"), 512)
    write_png(os.path.join(target, "apple-touch-icon.png"), 180)
