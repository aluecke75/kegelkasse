"""Generische Export-Helfer: Dateiname, CSV/Excel/PDF-Erzeugung, Format-Dispatch
sowie die Spaltenüberschriften je Export-Typ."""
import csv
from datetime import datetime
from io import StringIO

from flask import Response, flash, redirect, url_for
from werkzeug.utils import secure_filename

from services.pdf import (
    _PDF_PAGE_WIDTH,
    _pdf_text_cmd,
    _pdf_page_header_cmds,
    _pdf_footer_cmds,
    _pdf_assemble,
    get_logo_pdf_image,
)


def export_filename(prefix, extension):
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    safe_prefix = secure_filename(prefix) or "export"
    return f"{safe_prefix}_{stamp}.{extension}"


def export_rows_to_csv(rows, headers, filename):
    output = StringIO()
    output.write("﻿")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([row.get(header, "") for header in headers])
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def export_rows_to_excel(rows, headers, filename, title="Kegelkasse Export"):
    # Ohne zusätzliche Python-Abhängigkeit: Excel-kompatibles HTML mit .xls-Endung.
    def esc(value):
        return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    html = [
        '<html><head><meta charset="utf-8"></head><body>',
        f'<h2>{esc(title)}</h2>',
        '<table border="1" cellspacing="0" cellpadding="4">',
        '<tr>' + ''.join(f'<th>{esc(header)}</th>' for header in headers) + '</tr>',
    ]
    for row in rows:
        html.append('<tr>' + ''.join(f'<td>{esc(row.get(header, ""))}</td>' for header in headers) + '</tr>')
    html.append('</table></body></html>')
    return Response(
        "﻿" + "\n".join(html),
        mimetype="application/vnd.ms-excel; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def export_rows_to_pdf(rows, headers, filename, title="Kegelkasse Export"):
    """Dependency-free, more structured PDF export.

    The PDF uses built-in Helvetica fonts and cp1252 text encoding so ä/ö/ü/ß
    are displayed correctly in normal PDF readers. Jede Zeile wird als Karte mit
    zweispaltigem Feld/Wert-Layout dargestellt (Feldname grau, Wert fett), mit
    gemeinsamem Seitenkopf/-fuß und optional dem hinterlegten Vereinslogo.
    """
    margin = 42
    line_height = 15
    page_bottom = 66
    now_text = datetime.now().strftime("%d.%m.%Y %H:%M")
    logo_image = get_logo_pdf_image()

    rows = rows or []
    pages_cmds = []

    def new_page(page_no):
        return _pdf_page_header_cmds(page_no, title, now_text, margin, logo_image=logo_image), 718

    def close_page(cmds):
        cmds.extend(_pdf_footer_cmds(margin))
        pages_cmds.append(cmds)

    page_no = 1
    cmds, y = new_page(page_no)

    if not rows:
        cmds.append(_pdf_text_cmd(margin, y, "Keine Daten vorhanden.", 11, "F1"))
    else:
        for idx, row in enumerate(rows, 1):
            needed = line_height * (len(headers) + 2) + 14
            if y - needed < page_bottom:
                close_page(cmds)
                page_no += 1
                cmds, y = new_page(page_no)

            # Record card background
            card_height = line_height * (len(headers) + 1) + 12
            cmds.append("0.98 0.98 0.98 rg")
            cmds.append(f"{margin} {y - card_height + 8} {_PDF_PAGE_WIDTH - 2*margin} {card_height} re f")
            cmds.append("0.82 0.82 0.82 RG")
            cmds.append(f"{margin} {y - card_height + 8} {_PDF_PAGE_WIDTH - 2*margin} {card_height} re S")
            cmds.append("0 g")
            cmds.append(_pdf_text_cmd(margin + 10, y, f"Eintrag {idx}", 11, "F2"))
            y -= line_height + 2

            for header in headers:
                value = str(row.get(header, ""))
                cmds.append("0.42 0.42 0.42 rg")
                cmds.append(_pdf_text_cmd(margin + 16, y, header, 9, "F1"))
                cmds.append("0 g")
                max_value_len = 58
                if len(value) <= max_value_len:
                    cmds.append(_pdf_text_cmd(margin + 190, y, value, 9, "F2"))
                    y -= line_height
                else:
                    y -= line_height
                    wrap_len = 100
                    while value:
                        part = value[:wrap_len]
                        value = value[wrap_len:]
                        cmds.append(_pdf_text_cmd(margin + 24, y, part, 9, "F2"))
                        y -= line_height
            y -= 10

    close_page(cmds)

    return Response(
        _pdf_assemble(pages_cmds, logo_image=logo_image),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def export_response(rows, headers, export_type, fmt, title):
    if fmt == "csv":
        return export_rows_to_csv(rows, headers, export_filename(export_type, "csv"))
    if fmt == "excel":
        return export_rows_to_excel(rows, headers, export_filename(export_type, "xls"), title=title)
    if fmt == "pdf":
        return export_rows_to_pdf(rows, headers, export_filename(export_type, "pdf"), title=title)
    flash("Unbekanntes Exportformat.", "danger")
    return redirect(url_for("exports_page"))


def export_headers(export_type):
    return {
        "cashbook": ["Datum", "Art", "Konto/Umbuchung", "Betrag", "Kategorie", "Empfänger/Einzahler", "Grund", "Erfasst von", "Notiz"],
        "members": ["Name", "Vorname", "Nachname", "Spitzname", "E-Mail", "Benutzername", "Rolle", "Aktiv", "Dauerauftragstag", "Offene Strafen", "Guthaben"],
        "penalty_balances": ["Mitglied", "Offene Strafen", "Guthaben", "Saldo"],
        "annual_closing": ["Jahr", "Abschlussdatum", "Barkasse", "Bank", "Gesamtbestand", "Offene Strafen", "Guthaben Mitglieder", "Einnahmen im Jahr", "Ausgaben im Jahr", "Kegelabende abgeschlossen", "Kegelabende ausgefallen", "Kegelabende offen", "Letzte Kassenprüfung", "Notiz"],
        "statistics": ["Mitglied", "Anwesend", "Fehlt entschuldigt", "Fehlt unentschuldigt", "Strafen gesamt", "Höchste Einzelstrafe", "Eingezahlt", "Offen", "Guthaben"],
        "interest": ["Buchungsdatum", "Zeitraum", "Zinssatz", "Bankbestand Grundlage", "Brutto-Zinsen", "Kapitalertragsteuer", "Solidaritätszuschlag", "Kirchensteuer", "Netto-Zinsen", "Notiz"],
        "cash_audits": ["Prüfdatum", "Barkasse laut System", "Barkasse gezählt", "Barkasse Differenz", "Bank laut System", "Bank laut Auszug", "Bank Differenz", "Erfasst von", "Status", "Bestätigt von", "Bestätigt am", "Notiz", "Prüfernotiz"],
    }.get(export_type, [])
