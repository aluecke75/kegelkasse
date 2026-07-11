"""Dokumentenverwaltung: Hochladen, Kategorien, Download/Vorschau, Archivierung."""
import json
import secrets
from datetime import datetime

from flask import request, flash, redirect, url_for, render_template, send_file
from flask_login import login_required, current_user
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from extensions import app
from auth import role_required
from config import get_document_dir, get_active_database_profile
from models import db, Document, BowlingEvent, CashbookEntry
from services.settings import setting_value, set_setting_value
from services.audit import audit_log, audit_value

ALLOWED_DOCUMENT_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "gif", "webp", "doc", "docx", "xls", "xlsx", "txt", "csv", "json", "html", "htm", "rtf"}
DEFAULT_DOCUMENT_CATEGORIES = ["Bahnrechnungen", "Kassenprüfung", "Jahresabschluss", "Kegeltour", "Historische Importdaten", "Vereinsunterlagen", "Sonstiges"]
DOCUMENT_DIR = get_document_dir(get_active_database_profile())


def document_categories():
    raw = setting_value("document_custom_categories", "[]")
    try:
        custom = json.loads(raw or "[]")
    except Exception:
        custom = []
    categories = []
    for category in DEFAULT_DOCUMENT_CATEGORIES + custom:
        category = (category or "").strip()
        if category and category not in categories:
            categories.append(category)
    return categories or ["Sonstiges"]


def save_custom_document_category(name):
    name = (name or "").strip()
    if not name:
        return None
    if len(name) > 80:
        name = name[:80].strip()
    categories = document_categories()
    if name in categories:
        return name
    custom = [c for c in categories if c not in DEFAULT_DOCUMENT_CATEGORIES]
    custom.append(name)
    set_setting_value("document_custom_categories", json.dumps(custom, ensure_ascii=False))
    return name


def document_allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_DOCUMENT_EXTENSIONS


def document_date_from_form(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def document_summary(doc):
    links = []
    if doc.event:
        links.append(f"Kegelabend {doc.event.event_date.strftime('%d.%m.%Y')}")
    if doc.cashbook_entry:
        links.append(f"Kassenbuch #{doc.cashbook_entry.id}: {doc.cashbook_entry.reason}")
    return (
        f"Titel: {audit_value(doc.title)}\n"
        f"Kategorie: {audit_value(doc.category)}\n"
        f"Datum: {doc.document_date.strftime('%d.%m.%Y') if doc.document_date else '-'}\n"
        f"Datei: {audit_value(doc.original_filename)}\n"
        f"Größe: {doc.size_label()}\n"
        f"Zuordnung: {audit_value('; '.join(links) if links else '-') }"
    )


def document_extension(doc):
    filename = (doc.original_filename or doc.stored_filename or "").lower()
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[1]


def document_preview_type(doc):
    ext = document_extension(doc)
    if ext in {"jpg", "jpeg", "png", "gif", "webp"}:
        return "image"
    if ext == "pdf":
        return "pdf"
    if ext in {"txt", "csv", "json", "html", "htm", "rtf"}:
        return "text"
    return "download_only"


@app.route("/documents", methods=["GET", "POST"])
@login_required
@role_required("admin", "cashier", "auditor")
def documents_page():
    if current_user.role == "auditor" and request.method == "POST":
        flash("Kassenprüfer/-innen haben nur Leserechte und können keine Änderungen speichern.", "warning")
        return redirect(request.referrer or url_for("dashboard"))

    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)

    if request.method == "POST":
        form_action = request.form.get("form_action", "upload")

        if form_action == "upload":
            title = (request.form.get("title") or "").strip()
            category = request.form.get("category") or "Sonstiges"
            new_category = (request.form.get("new_category") or "").strip()
            if category == "__new__":
                category = save_custom_document_category(new_category) or "Sonstiges"
            description = (request.form.get("description") or "").strip()
            document_date = document_date_from_form(request.form.get("document_date"))
            event_id = request.form.get("event_id") or None
            cashbook_entry_id = request.form.get("cashbook_entry_id") or None
            file = request.files.get("document_file")

            if not title:
                flash("Bitte einen Titel angeben.", "error")
                return redirect(url_for("documents_page"))
            if not file or not file.filename:
                flash("Bitte eine Datei auswählen.", "error")
                return redirect(url_for("documents_page"))
            if not document_allowed(file.filename):
                flash("Dieser Dateityp ist nicht erlaubt. Erlaubt sind PDF, Bilder, Text, CSV, JSON, HTML, RTF, DOCX und XLSX.", "error")
                return redirect(url_for("documents_page"))

            original_filename = secure_filename(file.filename)
            suffix = original_filename.rsplit(".", 1)[1].lower() if "." in original_filename else "bin"
            stored_filename = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(8)}.{suffix}"
            target = DOCUMENT_DIR / stored_filename
            file.save(target)
            size = target.stat().st_size if target.exists() else 0

            document = Document(
                title=title,
                category=category if category in document_categories() else "Sonstiges",
                document_date=document_date,
                description=description or None,
                original_filename=original_filename,
                stored_filename=stored_filename,
                mime_type=file.mimetype,
                file_size=size,
                event_id=int(event_id) if event_id else None,
                cashbook_entry_id=int(cashbook_entry_id) if cashbook_entry_id else None,
                uploaded_by_user_id=current_user.id,
            )
            db.session.add(document)
            db.session.commit()

            audit_log(
                "Dokumente",
                "document_uploaded",
                f"Dokument hochgeladen: {document.title}",
                details=document_summary(document),
                object_type="document",
                object_id=document.id,
                new_value=document_summary(document),
            )
            flash("Dokument wurde hochgeladen.", "success")
            return redirect(url_for("documents_page"))

        if form_action in ["delete", "archive"] and current_user.role in ["admin", "cashier"]:
            doc = Document.query.get_or_404(int(request.form.get("document_id") or 0))
            old_summary = document_summary(doc)
            if not doc.deleted_at:
                doc.deleted_at = datetime.utcnow()
                doc.deleted_by_user_id = current_user.id
                db.session.commit()
                audit_log(
                    "Dokumente",
                    "document_archived",
                    f"Dokument archiviert: {doc.title}",
                    details=old_summary,
                    object_type="document",
                    object_id=doc.id,
                    old_value=old_summary,
                    new_value=document_summary(doc),
                )
                if doc.is_protected_category():
                    flash("Revisionsrelevantes Dokument wurde archiviert. Es bleibt als Nachweis erhalten und ist über 'Archivierte anzeigen' weiterhin einsehbar.", "success")
                else:
                    flash("Dokument wurde archiviert. Es bleibt auf dem Server erhalten und kann bei Bedarf wieder eingeblendet werden.", "success")
            return redirect(url_for("documents_page"))

        if form_action == "restore" and current_user.role in ["admin", "cashier"]:
            doc = Document.query.get_or_404(int(request.form.get("document_id") or 0))
            old_summary = document_summary(doc)
            doc.deleted_at = None
            doc.deleted_by_user_id = None
            db.session.commit()
            audit_log(
                "Dokumente",
                "document_restored",
                f"Dokument wiederhergestellt: {doc.title}",
                details=document_summary(doc),
                object_type="document",
                object_id=doc.id,
                old_value=old_summary,
                new_value=document_summary(doc),
            )
            flash("Dokument wurde wieder eingeblendet.", "success")
            return redirect(url_for("documents_page"))

    category_filter = request.args.get("category", "all")
    search = (request.args.get("q") or "").strip()
    show_archived = request.args.get("show_archived") == "1"
    query = Document.query
    if not show_archived:
        query = query.filter(Document.deleted_at.is_(None))
    if category_filter != "all":
        query = query.filter(Document.category == category_filter)
    if search:
        like = f"%{search}%"
        query = query.filter(or_(Document.title.ilike(like), Document.description.ilike(like), Document.original_filename.ilike(like), Document.category.ilike(like)))
    documents = query.order_by(Document.deleted_at.isnot(None), Document.document_date.desc().nullslast(), Document.uploaded_at.desc()).all()

    events = BowlingEvent.query.order_by(BowlingEvent.event_date.desc()).limit(50).all()
    cashbook_entries = CashbookEntry.query.filter(CashbookEntry.is_void.is_(False)).order_by(CashbookEntry.booking_date.desc(), CashbookEntry.id.desc()).limit(80).all()

    return render_template(
        "documents.html",
        documents=documents,
        categories=document_categories(),
        category_filter=category_filter,
        search=search,
        show_archived=show_archived,
        events=events,
        cashbook_entries=cashbook_entries,
    )


@app.route("/documents/download/<int:document_id>")
@login_required
@role_required("admin", "cashier", "auditor")
def document_download(document_id):
    doc = Document.query.get_or_404(document_id)
    path = DOCUMENT_DIR / doc.stored_filename
    if not path.exists():
        flash("Die Datei wurde auf dem Server nicht gefunden.", "error")
        return redirect(url_for("documents_page"))
    audit_log(
        "Dokumente",
        "document_downloaded",
        f"Dokument heruntergeladen: {doc.title}",
        details=document_summary(doc),
        object_type="document",
        object_id=doc.id,
    )
    return send_file(path, as_attachment=True, download_name=doc.original_filename)


@app.route("/documents/preview/<int:document_id>")
@login_required
@role_required("admin", "cashier", "auditor")
def document_preview(document_id):
    doc = Document.query.get_or_404(document_id)
    path = DOCUMENT_DIR / doc.stored_filename
    if not path.exists():
        flash("Die Datei wurde auf dem Server nicht gefunden.", "error")
        return redirect(url_for("documents_page"))

    preview_type = document_preview_type(doc)
    if preview_type == "download_only":
        flash("Für diesen Dateityp gibt es aktuell keine direkte Vorschau. Bitte die Datei herunterladen.", "info")
        return redirect(url_for("documents_page"))

    audit_log(
        "Dokumente",
        "document_previewed",
        f"Dokument-Vorschau geöffnet: {doc.title}",
        details=document_summary(doc),
        object_type="document",
        object_id=doc.id,
    )
    return send_file(path, as_attachment=False, download_name=doc.original_filename)
