"""Abhängigkeitsfreie PDF-Erzeugung (eigener Mini-PDF-Writer) inkl. Einbettung
des hochgeladenen Vereinslogos (PNG/JPEG) in Kopfzeilen."""
import struct
import zlib
from io import BytesIO
from pathlib import Path

BRANDING_DIR = Path("/app/uploads/branding")

_PDF_PAGE_WIDTH = 595
_PDF_PAGE_HEIGHT = 842


def pdf_escape(text):
    """Escape text for a PDF string using Windows-1252/WinAnsi.

    Standard PDF Type1 fonts such as Helvetica do not understand UTF-8 directly.
    Encoding the text as cp1252 fixes German umlauts in common PDF viewers.
    """
    raw = str(text or "").encode("cp1252", errors="replace")
    out = bytearray()
    for b in raw:
        if b in (40, 41, 92):  # (, ), \
            out.append(92)
            out.append(b)
        elif b < 32 or b > 126:
            out.extend(f"\\{b:03o}".encode("ascii"))
        else:
            out.append(b)
    return out.decode("ascii")


def _pdf_text_cmd(x, y, text, size=10, font="F1"):
    return f"BT /{font} {size} Tf {x} {y} Td ({pdf_escape(text)}) Tj ET"


def decode_png_image(data):
    """Minimaler, abhängigkeitsfreier PNG-Decoder für 8-Bit RGB/RGBA, nicht interlaced.

    Nutzt nur die Python-Standardbibliothek (zlib/struct). Gibt (width, height,
    rgb_bytes, alpha_bytes_or_none) zurück oder wirft ValueError bei nicht
    unterstützten PNG-Varianten (Palette, 16-Bit, interlaced, ...).
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Keine gültige PNG-Datei.")

    pos = 8
    width = height = bit_depth = color_type = interlace = None
    idat = bytearray()

    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        chunk_type = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 8 + length + 4  # + CRC

        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
        elif chunk_type == b"IDAT":
            idat += chunk
        elif chunk_type == b"IEND":
            break

    if not width or not height:
        raise ValueError("PNG-Kopfdaten konnten nicht gelesen werden.")
    if bit_depth != 8:
        raise ValueError("Nur 8-Bit-PNGs werden für das Logo unterstützt.")
    if interlace != 0:
        raise ValueError("Interlaced PNGs werden für das Logo nicht unterstützt.")
    if color_type not in (2, 6):
        raise ValueError("Nur RGB- oder RGBA-PNGs werden für das Logo unterstützt (keine Palette/Graustufen).")

    channels = 3 if color_type == 2 else 4
    raw = zlib.decompress(bytes(idat))
    stride = width * channels

    out = bytearray(height * stride)
    prev_row = bytearray(stride)
    pos = 0
    for y in range(height):
        filter_type = raw[pos]
        pos += 1
        row = bytearray(raw[pos:pos + stride])
        pos += stride

        for x in range(stride):
            a = row[x - channels] if x >= channels else 0
            b = prev_row[x]
            c = prev_row[x - channels] if x >= channels else 0
            if filter_type == 1:
                row[x] = (row[x] + a) & 0xFF
            elif filter_type == 2:
                row[x] = (row[x] + b) & 0xFF
            elif filter_type == 3:
                row[x] = (row[x] + ((a + b) // 2)) & 0xFF
            elif filter_type == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                row[x] = (row[x] + pred) & 0xFF

        out[y * stride:(y + 1) * stride] = row
        prev_row = row

    if channels == 4:
        rgb = bytearray(width * height * 3)
        alpha = bytearray(width * height)
        for i in range(width * height):
            rgb[i * 3:i * 3 + 3] = out[i * 4:i * 4 + 3]
            alpha[i] = out[i * 4 + 3]
        return width, height, bytes(rgb), bytes(alpha)

    return width, height, bytes(out), None


def jpeg_dimensions_and_components(data):
    """Liest Breite/Höhe/Farbkomponenten (1=Graustufen, 3=RGB, 4=CMYK) aus JPEG-Markern."""
    if data[0:2] != b"\xff\xd8":
        raise ValueError("Keine gültige JPEG-Datei.")
    i = 2
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while i < len(data) - 1:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            i += 2
            continue
        length = (data[i + 2] << 8) + data[i + 3]
        if marker in sof_markers:
            height = (data[i + 5] << 8) + data[i + 6]
            width = (data[i + 7] << 8) + data[i + 8]
            components = data[i + 9]
            return width, height, components
        i += 2 + length
    raise ValueError("JPEG-Abmessungen konnten nicht gelesen werden.")


def build_logo_pdf_image(logo_path):
    """Bereitet die Logo-Datei für die PDF-Einbettung vor.

    Gibt ein Dict mit den PDF-Objektbausteinen zurück, oder None wenn keine
    Logo-Datei vorhanden bzw. lesbar ist.
    """
    if not logo_path or not logo_path.exists():
        return None

    try:
        data = logo_path.read_bytes()
        suffix = logo_path.suffix.lower()

        if suffix == ".png":
            width, height, rgb, alpha = decode_png_image(data)
            return {
                "width": width, "height": height,
                "color_space": "DeviceRGB", "filter": "FlateDecode",
                "stream": zlib.compress(rgb),
                "smask_stream": zlib.compress(alpha) if alpha else None,
            }

        if suffix in (".jpg", ".jpeg"):
            width, height, components = jpeg_dimensions_and_components(data)
            color_space = {1: "DeviceGray", 3: "DeviceRGB", 4: "DeviceCMYK"}.get(components, "DeviceRGB")
            return {
                "width": width, "height": height,
                "color_space": color_space, "filter": "DCTDecode",
                "stream": data,
                "smask_stream": None,
            }
    except Exception:
        return None

    return None


def branding_logo_path():
    """Pfad zur gespeicherten Vereinslogo-Datei, falls vorhanden (sonst None)."""
    if not BRANDING_DIR.exists():
        return None
    for ext in ("png", "jpg", "jpeg"):
        candidate = BRANDING_DIR / f"logo.{ext}"
        if candidate.exists():
            return candidate
    return None


_logo_pdf_cache = {"mtime": None, "image": None}


def get_logo_pdf_image():
    """Für PDFs vorbereitetes Logo-Bild, mit einfachem Cache anhand der Dateizeit."""
    path = branding_logo_path()
    if not path:
        _logo_pdf_cache["mtime"] = None
        _logo_pdf_cache["image"] = None
        return None

    mtime = path.stat().st_mtime
    if _logo_pdf_cache["mtime"] != mtime:
        _logo_pdf_cache["image"] = build_logo_pdf_image(path)
        _logo_pdf_cache["mtime"] = mtime
    return _logo_pdf_cache["image"]


def _pdf_page_header_cmds(page_no, title, now_text, margin=42, page_width=_PDF_PAGE_WIDTH, logo_image=None):
    """Gemeinsamer Seitenkopf (Titelband + Datum + Seitenzahl + optionales Vereinslogo)."""
    cmds = []
    cmds.append("0.94 0.97 0.95 rg")
    cmds.append(f"{margin} 748 {page_width - 2*margin} 54 re f")
    cmds.append("0 g")

    title_x = margin + 14
    if logo_image:
        display_height = 34
        display_width = min(100, display_height * (logo_image["width"] / logo_image["height"]))
        logo_x = page_width - margin - 14 - display_width
        logo_y = 754
        cmds.append("q")
        cmds.append(f"{display_width:.2f} 0 0 {display_height:.2f} {logo_x:.2f} {logo_y:.2f} cm")
        cmds.append("/Logo Do")
        cmds.append("Q")

    cmds.append(_pdf_text_cmd(title_x, 782, title, 16, "F2"))
    # Datum und Seitenzahl bewusst zusammen links, damit die gesamte rechte Seite
    # für ein Logo (egal welcher Breite) frei bleibt und nichts überlappt.
    cmds.append(_pdf_text_cmd(title_x, 762, f"Erstellt am: {now_text}   ·   Seite {page_no}", 9, "F1"))
    cmds.append("0.80 0.80 0.80 RG")
    cmds.append(f"{margin} 735 m {page_width - margin} 735 l S")
    return cmds


def _pdf_footer_cmds(margin=42, page_width=_PDF_PAGE_WIDTH):
    """Gemeinsame Fußzeile für alle PDF-Exporte."""
    cmds = []
    cmds.append("0.85 0.85 0.85 RG")
    cmds.append(f"{margin} 40 m {page_width - margin} 40 l S")
    cmds.append("0.60 0.60 0.60 rg")
    cmds.append(_pdf_text_cmd(margin, 28, "Kegelkasse – Vereinsverwaltung", 8, "F1"))
    cmds.append("0 g")
    return cmds


def _pdf_image_object(object_id, image, smask_id=None):
    smask_ref = f" /SMask {smask_id} 0 R" if smask_id else ""
    header = (
        f"{object_id} 0 obj\n"
        f"<< /Type /XObject /Subtype /Image /Width {image['width']} /Height {image['height']} "
        f"/ColorSpace /{image['color_space']} /BitsPerComponent 8 /Filter /{image['filter']}{smask_ref} "
        f"/Length {len(image['stream'])} >>\nstream\n"
    ).encode("ascii")
    return header + image["stream"] + b"\nendstream\nendobj\n"


def _pdf_smask_object(object_id, image):
    header = (
        f"{object_id} 0 obj\n"
        f"<< /Type /XObject /Subtype /Image /Width {image['width']} /Height {image['height']} "
        f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
        f"/Length {len(image['smask_stream'])} >>\nstream\n"
    ).encode("ascii")
    return header + image["smask_stream"] + b"\nendstream\nendobj\n"


def _pdf_assemble(pages_cmds, page_width=_PDF_PAGE_WIDTH, page_height=_PDF_PAGE_HEIGHT, logo_image=None):
    """Baut aus einer Liste von Befehlslisten (eine pro Seite) die rohen PDF-Bytes.

    Dependency-freie PDF-Erzeugung mit eingebauten Helvetica-Schriften, cp1252-
    Textkodierung (ä/ö/ü/ß) und optional einem eingebetteten Vereinslogo (PNG mit
    Transparenz oder JPEG), das über den Namen /Logo in den Seiteninhalten benutzt wird.
    """
    objects = []
    next_id = 5  # 1=Catalog, 2=Pages, 3=Font F1, 4=Font F2

    logo_id = None
    xobject_resource = ""
    if logo_image:
        smask_id = None
        if logo_image.get("smask_stream"):
            smask_id = next_id
            objects.append(_pdf_smask_object(smask_id, logo_image))
            next_id += 1
        logo_id = next_id
        objects.append(_pdf_image_object(logo_id, logo_image, smask_id))
        next_id += 1
        xobject_resource = f" /XObject << /Logo {logo_id} 0 R >>"

    page_ids = []
    for cmds in pages_cmds:
        stream = "\n".join(cmds).encode("latin-1", errors="replace")
        content_id = next_id
        page_id = next_id + 1
        next_id += 2
        objects.append(f"{content_id} 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream\nendobj\n")
        objects.append(f"{page_id} 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] /Resources << /Font << /F1 3 0 R /F2 4 0 R >>{xobject_resource} >> /Contents {content_id} 0 R >>\nendobj\n".encode("ascii"))
        page_ids.append(page_id)

    pdf_objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        f"2 0 obj\n<< /Type /Pages /Kids [{' '.join(str(i) + ' 0 R' for i in page_ids)}] /Count {len(page_ids)} >>\nendobj\n".encode("ascii"),
        b"3 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>\nendobj\n",
    ] + objects

    buffer = BytesIO()
    buffer.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for obj in pdf_objects:
        offsets.append(buffer.tell())
        buffer.write(obj)
    xref_pos = buffer.tell()
    buffer.write(f"xref\n0 {len(pdf_objects)+1}\n".encode("ascii"))
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buffer.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    buffer.write(f"trailer\n<< /Size {len(pdf_objects)+1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode("ascii"))
    return buffer.getvalue()
