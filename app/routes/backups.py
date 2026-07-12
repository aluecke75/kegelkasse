"""Vereins-Export/-Import: vollständiger Datenaustausch für Umzug, Archivierung
oder späteren Import in der öffentlichen Version.

Die allgemeine Datensicherung (lokale Backups, Cloud-Ziele, Verschlüsselung,
automatische Zeitplanung) lebt seit der Umstellung auf das gemeinsame
DeveloperKit-Backup-Modul nicht mehr hier, sondern unter
developerkit.backup (Seite: /verwaltung/backups, siehe app.py für die
Einbindung). Der Vereins-Export/-Import ist Kegelkasse-spezifisch (Vereins-
Datenportabilität, kein allgemeines Backup-Konzept) und bleibt daher
eigenständiger Code."""
import json
import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

from flask import request, flash, redirect, url_for, render_template, send_file, after_this_request
from flask_login import login_required
from werkzeug.utils import secure_filename

from extensions import app
from auth import role_required
from config import get_active_database_profile, get_database_path
from models import db
from services.settings import setting_value, set_setting_value, sanitized_app_settings_dict, is_secret_setting_key
from services.audit import audit_log
from services.schema_migrations import migrate_schema_extensions
from routes.documents import DOCUMENT_DIR
from developerkit.backup.service import backup_dir, create_database_backup

DATABASE_PATH = get_database_path(get_active_database_profile())


def sanitize_sqlite_settings_for_export(db_path):
    """Entfernt Geheimnisse aus der kopierten SQLite-Datei, bevor sie gezippt wird.

    Die laufende Produktivdatenbank bleibt unverändert. Nur die Export-Kopie
    verliert SMTP-, WebDAV- und Cloud-Token. Nach einem Import müssen diese
    Zugangsdaten neu eingetragen werden.
    """
    if not db_path or not Path(db_path).exists():
        return

    con = sqlite3.connect(str(db_path))
    try:
        table_names = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for table in ("app_settings", "app_setting"):
            if table not in table_names:
                continue
            rows = con.execute(f'SELECT key FROM "{table}"').fetchall()
            for (key,) in rows:
                if is_secret_setting_key(key):
                    con.execute(f'UPDATE "{table}" SET value = ? WHERE key = ?', ("", key))
        con.commit()
    finally:
        con.close()


def club_import_dir():
    folder = backup_dir() / "club_imports"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def validate_club_export_zip(path):
    """Prüft einen Vereins-Export und liefert eine Import-Vorschau."""
    result = {
        "ok": False,
        "errors": [],
        "warnings": [],
        "metadata": {},
        "counts": {},
        "documents_count": 0,
        "filename": path.name if path else "",
    }

    if not path or not path.exists():
        result["errors"].append("Datei nicht gefunden.")
        return result

    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
            if "export_info.json" not in names:
                result["errors"].append("export_info.json fehlt. Das ist kein gültiger Vereins-Export.")
            else:
                try:
                    result["metadata"] = json.loads(zf.read("export_info.json").decode("utf-8"))
                except Exception:
                    result["errors"].append("export_info.json konnte nicht gelesen werden.")

            if "kegelkasse.db" not in names:
                result["errors"].append("kegelkasse.db fehlt.")

            export_type = result.get("metadata", {}).get("type")
            if export_type and export_type != "club_export":
                result["warnings"].append(f"Export-Typ ist '{export_type}', erwartet wurde 'club_export'.")

            result["documents_count"] = len([name for name in names if name.startswith("documents/") and not name.endswith("/")])

            if result["errors"]:
                return result

            temp_db = club_import_dir() / f".check_{path.stem}.sqlite.tmp"
            try:
                zf.extract("kegelkasse.db", club_import_dir())
                extracted = club_import_dir() / "kegelkasse.db"
                if temp_db.exists():
                    temp_db.unlink()
                extracted.rename(temp_db)

                con = sqlite3.connect(str(temp_db))
                try:
                    quick = con.execute("PRAGMA quick_check").fetchone()
                    if not quick or quick[0] != "ok":
                        result["errors"].append("SQLite quick_check ist fehlgeschlagen.")
                    table_names = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                    for table in ["user", "member", "bowling_event", "cashbook_entry", "document", "audit_log"]:
                        if table in table_names:
                            try:
                                result["counts"][table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                            except Exception:
                                result["counts"][table] = "?"
                    if "app_setting" in table_names:
                        try:
                            club = con.execute("SELECT value FROM app_setting WHERE key='club_name'").fetchone()
                            if club and club[0]:
                                result.setdefault("metadata", {})["club_name_from_db"] = club[0]
                        except Exception:
                            pass
                finally:
                    con.close()
            finally:
                try:
                    temp_db.unlink()
                except FileNotFoundError:
                    pass
    except zipfile.BadZipFile:
        result["errors"].append("ZIP-Datei ist ungültig oder beschädigt.")
    except Exception as exc:
        result["errors"].append(str(exc))

    result["ok"] = not result["errors"]
    return result


def import_club_export_zip(path):
    """Importiert einen geprüften Vereins-Export. Ersetzt aktuelle DB und Dokumente."""
    validation = validate_club_export_zip(path)
    if not validation.get("ok"):
        raise ValueError("Vereins-Export ist nicht gültig: " + "; ".join(validation.get("errors", [])))

    # kind="pre_restore" statt "manual": ein fehlgeschlagener Cloud-Upload
    # dieser internen Vorab-Sicherung darf den eigentlichen Import nicht
    # verhindern (siehe developerkit.backup.service.create_database_backup).
    restore_before = create_database_backup(kind="pre_restore")
    work_dir = club_import_dir() / f"import_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    work_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(path, "r") as zf:
        zf.extractall(work_dir)

    imported_db = work_dir / "kegelkasse.db"
    if not imported_db.exists():
        raise ValueError("Importierte Datenbank fehlt nach dem Entpacken.")

    # Bestehende Verbindungen lösen, damit die SQLite-Datei sicher ersetzt werden kann.
    db.session.remove()
    db.engine.dispose()

    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(imported_db, DATABASE_PATH)

    imported_docs = work_dir / "documents"
    if imported_docs.exists():
        if DOCUMENT_DIR.exists():
            shutil.rmtree(DOCUMENT_DIR)
        shutil.copytree(imported_docs, DOCUMENT_DIR)

    # Neue DB initialisieren/migrieren und Import im neuen Revisionsprotokoll dokumentieren.
    db.engine.dispose()
    db.create_all()
    migrate_schema_extensions()
    audit_log(
        "Vereins-Import",
        "club_import_restored",
        "Vereins-Export importiert",
        details=f"Importiert aus: {path.name}; automatische Sicherung vorher: {restore_before.name}",
        object_type="ClubImport",
        old_value=restore_before.name,
        new_value=path.name,
    )
    db.session.commit()

    try:
        shutil.rmtree(work_dir)
    except Exception:
        pass

    return restore_before


def create_club_export(include_documents=True):
    """Erstellt einen kompletten Vereins-Export als ZIP.

    Unterschied zur allgemeinen Datensicherung (developerkit.backup):
    - Dateiname und Metadaten sind auf Umzug/Archivierung ausgelegt.
    - Format ist versioniert, damit spätere Importe in der öffentlichen Version Migrationen erkennen können.
    - Die Datei wird nicht als automatische Sicherung gezählt.
    """
    folder = backup_dir()
    now = datetime.now()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    club_name = setting_value("club_name", "Kegelkasse") or "Kegelkasse"
    safe_club = secure_filename(club_name).strip("._-") or "kegelkasse"
    filename = f"kegelkasse_vereinsexport_{safe_club}_{stamp}.zip"
    target = folder / filename
    temp_db = folder / f".{filename}.sqlite.tmp"

    source = sqlite3.connect(str(DATABASE_PATH))
    dest = sqlite3.connect(str(temp_db))
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()

    sanitize_sqlite_settings_for_export(temp_db)

    metadata = {
        "created_at": now.isoformat(timespec="seconds"),
        "app": "Kegelkasse",
        "type": "club_export",
        "format_version": 1,
        "database_file": DATABASE_PATH.name,
        "club_name": club_name,
        "include_documents": bool(include_documents),
        "secrets_exported": False,
        "secrets_hint": "SMTP-, WebDAV- und Cloud-Zugangsdaten werden aus Sicherheitsgründen nicht exportiert und müssen nach Import/Restore neu eingetragen werden.",
        "hint": "Vollständiger Vereins-Export für Umzug, Archivierung oder späteren Import in der öffentlichen Version.",
    }

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(temp_db, arcname="kegelkasse.db")
        zf.writestr("export_info.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        zf.writestr(
            "app_settings.json",
            json.dumps(sanitized_app_settings_dict(), ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "README_EXPORT.txt",
            "Kegelkasse Vereins-Export\n"
            "==========================\n\n"
            "Diese ZIP-Datei enthält den vollständigen Vereinsstand für Umzug, Archivierung oder späteren Import.\n\n"
            "Enthalten:\n"
            "- kegelkasse.db: SQLite-Datenbank inkl. Mitglieder, Buchungen, Kegelabende, Revisionen und Einstellungen\n"
            "- app_settings.json: Einstellungen zusätzlich lesbar als JSON, ohne geheime Zugangsdaten\n"
            "- export_info.json: Export-Metadaten und Format-Version\n"
            "- documents/: Dokumentenablage, sofern beim Export ausgewählt\n\n"
            "Sicherheitshinweis: SMTP-, WebDAV- und Cloud-Passwörter/Tokens werden nicht exportiert.\n"
            "Diese Zugangsdaten müssen nach Import oder Wiederherstellung neu eingetragen werden.\n\n"
            "Hinweis: Dieser Export ersetzt keine regelmäßige Datensicherung mit zweitem Speicherziel "
            "(siehe Administration → Datensicherung).\n",
        )
        if include_documents and DOCUMENT_DIR.exists():
            for doc_path in DOCUMENT_DIR.rglob("*"):
                if doc_path.is_file():
                    zf.write(doc_path, arcname=f"documents/{doc_path.relative_to(DOCUMENT_DIR)}")

    try:
        temp_db.unlink()
    except FileNotFoundError:
        pass

    return target


def club_import_preview_from_settings():
    preview_raw = setting_value("club_import_preview_json", "")
    if not preview_raw:
        return None
    try:
        preview = json.loads(preview_raw)
        return preview if isinstance(preview, dict) else None
    except Exception:
        return None


@app.route("/import-export", methods=["GET", "POST"])
@login_required
@role_required("admin")
def import_export_page():
    if request.method == "POST":
        action = request.form.get("form_action", "")
        try:
            if action == "upload_club_import":
                uploaded = request.files.get("club_import_upload")
                if not uploaded or not uploaded.filename:
                    flash("Bitte einen Vereins-Export als ZIP auswählen.", "danger")
                else:
                    original_name = secure_filename(uploaded.filename)
                    if not original_name.lower().endswith(".zip"):
                        flash("Bitte nur ZIP-Dateien hochladen.", "danger")
                    else:
                        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                        target_name = f"kegelkasse_club_import_{stamp}_{original_name}"
                        target = club_import_dir() / target_name
                        uploaded.save(target)
                        preview = validate_club_export_zip(target)
                        if not preview.get("ok"):
                            try:
                                target.unlink()
                            except FileNotFoundError:
                                pass
                            flash("Vereins-Export ist ungültig: " + "; ".join(preview.get("errors", [])), "danger")
                        else:
                            set_setting_value("club_import_pending_file", target.name)
                            set_setting_value("club_import_preview_json", json.dumps(preview, ensure_ascii=False))
                            db.session.commit()
                            members = preview.get("counts", {}).get("member", "?")
                            events = preview.get("counts", {}).get("bowling_event", "?")
                            docs = preview.get("documents_count", 0)
                            flash(
                                f"Vereins-Export geprüft: {members} Mitglieder, {events} Kegelabende, "
                                f"{docs} Dokumentdateien erkannt. Bitte Import unten bestätigen.",
                                "success",
                            )

            elif action == "execute_club_import":
                confirm = (request.form.get("confirm_club_import") or "").strip().upper()
                if confirm != "IMPORTIEREN":
                    flash("Import nicht gestartet. Bitte zur Bestätigung IMPORTIEREN eingeben.", "danger")
                else:
                    pending_name = secure_filename(setting_value("club_import_pending_file", "") or "")
                    pending_path = club_import_dir() / pending_name
                    if not pending_name or not pending_path.exists() or pending_path.parent != club_import_dir():
                        flash("Kein geprüfter Vereins-Export für den Import gefunden.", "danger")
                    else:
                        restore_before = import_club_export_zip(pending_path)
                        set_setting_value("club_import_pending_file", "")
                        set_setting_value("club_import_preview_json", "")
                        db.session.commit()
                        flash(
                            f"Vereins-Export wurde importiert. Vorherige Installation wurde gesichert als: "
                            f"{restore_before.name}. Bitte neu anmelden.",
                            "success",
                        )
                        return redirect(url_for("logout"))

            elif action == "cancel_club_import":
                pending_name = secure_filename(setting_value("club_import_pending_file", "") or "")
                pending_path = club_import_dir() / pending_name
                if pending_name and pending_path.exists() and pending_path.parent == club_import_dir():
                    try:
                        pending_path.unlink()
                    except FileNotFoundError:
                        pass
                set_setting_value("club_import_pending_file", "")
                set_setting_value("club_import_preview_json", "")
                db.session.commit()
                flash("Vereins-Import wurde verworfen.", "info")

        except Exception as exc:
            db.session.rollback()
            flash(f"Aktion konnte nicht ausgeführt werden: {exc}", "danger")
        return redirect(url_for("import_export_page"))

    from routes.reports import export_year_options

    return render_template(
        "import_export.html",
        years=export_year_options(),
        current_year=datetime.now().year,
        club_import_preview=club_import_preview_from_settings(),
    )


@app.route("/club/export/download")
@login_required
@role_required("admin")
def download_club_export():
    include_documents = request.args.get("include_documents", "1") == "1"
    try:
        export_path = create_club_export(include_documents=include_documents)
        audit_log(
            "Vereins-Export",
            "club_export_created",
            "Vereins-Export erstellt",
            details=f"Vereins-Export erstellt: {export_path.name}",
            object_type="ClubExport",
            new_value=export_path.name,
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        flash(f"Vereins-Export konnte nicht erstellt werden: {exc}", "danger")
        return redirect(url_for("import_export_page"))

    @after_this_request
    def cleanup_export(response):
        try:
            export_path.unlink()
        except Exception:
            pass
        return response

    return send_file(export_path, as_attachment=True, download_name=export_path.name)
