"""Datensicherung: lokale Backups, Cloud-Ziele (WebDAV/Dropbox/Google Drive/OneDrive)
per OAuth2, automatische Sicherung, Wiederherstellung sowie Vereins-Export/-Import."""
import hashlib
import json
import os
import shutil
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from flask import request, flash, redirect, url_for, render_template, send_file, after_this_request
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from extensions import app, APP_VERSION
from auth import role_required
from config import get_active_database_profile, get_database_path
from models import db, Member, BowlingEvent, CashbookEntry, Document
from services.settings import setting_value, set_setting_value, safe_int_setting, is_secret_setting_key, sanitized_app_settings_dict
from services.audit import audit_log, audit_value
from services.schema_migrations import migrate_schema_extensions
from routes.documents import DOCUMENT_DIR

BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/app/backups"))
DATABASE_PATH = get_database_path(get_active_database_profile())


def backup_dir():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR


def backup_file_list():
    folder = backup_dir()
    files = []
    for path in sorted(folder.glob("kegelkasse_backup_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = path.stat()
        kind = "Automatisch" if "_auto_" in path.name else "Manuell"
        files.append({
            "name": path.name,
            "path": path,
            "size_bytes": stat.st_size,
            "size_label": human_file_size(stat.st_size),
            "created_at": datetime.fromtimestamp(stat.st_mtime),
            "kind": kind,
        })
    return files


def human_file_size(size_bytes):
    size = float(size_bytes or 0)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}".replace(".", ",")
        size /= 1024


def backup_settings():
    target_type = setting_value("backup_target_type", "webdav")
    if target_type == "local_path":
        target_type = "webdav"
    result = {
        "enabled": setting_value("backup_enabled", "0") == "1",
        "interval": setting_value("backup_interval", "daily"),
        "time": setting_value("backup_time", "02:00"),
        "keep_days": safe_int_setting("backup_keep_days", 30, 1),
        "keep_count": safe_int_setting("backup_keep_count", 20, 1),
        "include_documents": setting_value("backup_include_documents", "1") == "1",
        "extra_target_enabled": setting_value("backup_extra_target_enabled", "0") == "1",
        "target_type": target_type,
        "extra_target_path": setting_value("backup_extra_target_path", ""),
        "webdav_url": setting_value("backup_webdav_url", ""),
        "webdav_username": setting_value("backup_webdav_username", ""),
        "webdav_password_set": bool(setting_value("backup_webdav_password", "")),
        "dropbox_app_key": setting_value("backup_dropbox_app_key", ""),
        "dropbox_app_secret_set": bool(setting_value("backup_dropbox_app_secret", "")),
        "dropbox_connected": bool(setting_value("dropbox_refresh_token", "")),
        "dropbox_account_label": setting_value("dropbox_account_label", ""),
        "google_client_id": setting_value("backup_google_client_id", ""),
        "google_client_secret_set": bool(setting_value("backup_google_client_secret", "")),
        "google_drive_connected": bool(setting_value("google_drive_refresh_token", "")),
        "google_drive_account_label": setting_value("google_drive_account_label", ""),
        "onedrive_client_id": setting_value("backup_onedrive_client_id", ""),
        "onedrive_client_secret_set": bool(setting_value("backup_onedrive_client_secret", "")),
        "onedrive_connected": bool(setting_value("onedrive_refresh_token", "")),
        "onedrive_account_label": setting_value("onedrive_account_label", ""),
        "last_auto": setting_value("backup_last_auto", ""),
        "last_result": setting_value("backup_last_result", ""),
        "last_result_failed": "fehlgeschlagen" in (setting_value("backup_last_result", "") or "").lower(),
        "target_label": backup_target_label_raw(target_type),
    }

    result["extra_target_connected"] = (
        result["target_type"] == "webdav" and bool(result["webdav_url"])
        or result["target_type"] == "dropbox" and result["dropbox_connected"]
        or result["target_type"] == "google_drive" and result["google_drive_connected"]
        or result["target_type"] == "onedrive" and result["onedrive_connected"]
    )
    if result["target_type"] == "dropbox":
        result["extra_target_account_label"] = result["dropbox_account_label"]
    elif result["target_type"] == "google_drive":
        result["extra_target_account_label"] = result["google_drive_account_label"]
    elif result["target_type"] == "onedrive":
        result["extra_target_account_label"] = result["onedrive_account_label"]
    else:
        result["extra_target_account_label"] = ""
    return result


def backup_target_label_raw(target_type):
    labels = {
        "webdav": "WebDAV/Nextcloud",
        "dropbox": "Dropbox",
        "onedrive": "OneDrive",
        "google_drive": "Google Drive",
    }
    return labels.get(target_type or "webdav", "WebDAV/Nextcloud")


def backup_target_label(settings=None):
    settings = settings or backup_settings()
    if not settings.get("extra_target_enabled"):
        return "nur lokal"
    target_type = settings.get("target_type")
    if target_type in ("webdav", "dropbox", "google_drive", "onedrive"):
        return backup_target_label_raw(target_type)
    return backup_target_label_raw(target_type) + " (vorbereitet)"


def sanitize_sqlite_settings_for_export(db_path):
    """Entfernt Geheimnisse aus der kopierten SQLite-Datei, bevor sie gezippt wird.

    Die laufende Produktivdatenbank bleibt unverändert. Nur die Export-/Backup-Kopie
    verliert SMTP-, WebDAV- und spätere Cloud-Token. Nach Restore/Import müssen diese
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


def database_info():
    table_count = 0
    row_count = 0
    db_size = 0
    if DATABASE_PATH.exists():
        db_size = DATABASE_PATH.stat().st_size
        try:
            conn = sqlite3.connect(str(DATABASE_PATH))
            cursor = conn.cursor()
            tables = [row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
            table_count = len(tables)
            for table in tables:
                try:
                    row_count += int(cursor.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] or 0)
                except Exception:
                    pass
            conn.close()
        except Exception:
            pass
    return {
        "db_size_label": human_file_size(db_size),
        "table_count": table_count,
        "row_count": row_count,
    }


def webdav_upload_file(source_path):
    base_url = (setting_value("backup_webdav_url", "") or "").strip()
    username = (setting_value("backup_webdav_username", "") or "").strip()
    password = setting_value("backup_webdav_password", "") or ""

    if not base_url:
        raise ValueError("WebDAV ist aktiviert, aber keine WebDAV-URL eingetragen.")
    if not base_url.lower().startswith(("http://", "https://")):
        raise ValueError("Die WebDAV-URL muss mit http:// oder https:// beginnen.")

    encoded_name = urllib.parse.quote(source_path.name)
    target_url = base_url.rstrip("/") + "/" + encoded_name

    data = source_path.read_bytes()
    request_obj = urllib.request.Request(target_url, data=data, method="PUT")
    request_obj.add_header("Content-Type", "application/zip")
    request_obj.add_header("Content-Length", str(len(data)))

    if username or password:
        token = (f"{username}:{password}").encode("utf-8")
        import base64
        request_obj.add_header("Authorization", "Basic " + base64.b64encode(token).decode("ascii"))

    try:
        with urllib.request.urlopen(request_obj, timeout=45) as response:
            if response.status not in (200, 201, 204):
                raise ValueError(f"WebDAV-Upload fehlgeschlagen: HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        raise ValueError(f"WebDAV-Upload fehlgeschlagen: HTTP {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"WebDAV nicht erreichbar: {exc.reason}") from exc

    return target_url


def oauth_token_request(token_url, data):
    """POST-Formularanfrage für OAuth-Token-Austausch/-Refresh, liefert das geparste JSON."""
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(token_url, data=encoded, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"OAuth-Anfrage fehlgeschlagen: HTTP {exc.code} {body[:200]}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Anbieter nicht erreichbar: {exc.reason}") from exc


def oauth_get_request(url, access_token):
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {access_token}")
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


# --- Dropbox ---------------------------------------------------------------

def dropbox_authorize_url(redirect_uri):
    app_key = (setting_value("backup_dropbox_app_key", "") or "").strip()
    if not app_key:
        raise ValueError("Bitte zuerst App-Key und App-Secret für Dropbox eintragen und speichern.")
    params = {
        "client_id": app_key,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "token_access_type": "offline",
    }
    return "https://www.dropbox.com/oauth2/authorize?" + urllib.parse.urlencode(params)


def dropbox_exchange_code(code, redirect_uri):
    app_key = (setting_value("backup_dropbox_app_key", "") or "").strip()
    app_secret = (setting_value("backup_dropbox_app_secret", "") or "").strip()
    if not app_key or not app_secret:
        raise ValueError("Dropbox App-Key/App-Secret fehlen.")
    data = oauth_token_request("https://api.dropboxapi.com/oauth2/token", {
        "code": code,
        "grant_type": "authorization_code",
        "client_id": app_key,
        "client_secret": app_secret,
        "redirect_uri": redirect_uri,
    })
    set_setting_value("dropbox_access_token", data["access_token"])
    if data.get("refresh_token"):
        set_setting_value("dropbox_refresh_token", data["refresh_token"])
    expires_at = (datetime.utcnow() + timedelta(seconds=max(int(data.get("expires_in", 14400)) - 60, 60))).isoformat()
    set_setting_value("dropbox_token_expires_at", expires_at)

    # Dropbox verlangt für get_current_account ein POST mit Body "null", nicht GET.
    info_req = urllib.request.Request("https://api.dropboxapi.com/2/users/get_current_account", data=b"null", method="POST")
    info_req.add_header("Authorization", f"Bearer {data['access_token']}")
    info_req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(info_req, timeout=20) as response:
        info = json.loads(response.read().decode("utf-8"))
    set_setting_value("dropbox_account_label", info.get("email") or info.get("name", {}).get("display_name", "verbunden"))


def dropbox_refresh_access_token():
    app_key = (setting_value("backup_dropbox_app_key", "") or "").strip()
    app_secret = (setting_value("backup_dropbox_app_secret", "") or "").strip()
    refresh_token = setting_value("dropbox_refresh_token", "") or ""
    if not refresh_token:
        raise ValueError("Dropbox ist nicht verbunden. Bitte zuerst 'Verbindung herstellen' klicken.")
    data = oauth_token_request("https://api.dropboxapi.com/oauth2/token", {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": app_key,
        "client_secret": app_secret,
    })
    set_setting_value("dropbox_access_token", data["access_token"])
    expires_at = (datetime.utcnow() + timedelta(seconds=max(int(data.get("expires_in", 14400)) - 60, 60))).isoformat()
    set_setting_value("dropbox_token_expires_at", expires_at)
    return data["access_token"]


def dropbox_valid_access_token():
    token = setting_value("dropbox_access_token", "") or ""
    expires_raw = setting_value("dropbox_token_expires_at", "") or ""
    expired = True
    if expires_raw:
        try:
            expired = datetime.utcnow() >= datetime.fromisoformat(expires_raw)
        except ValueError:
            expired = True
    if not token or expired:
        return dropbox_refresh_access_token()
    return token


def dropbox_upload_file(source_path):
    access_token = dropbox_valid_access_token()
    data = source_path.read_bytes()
    api_arg = json.dumps({"path": f"/{source_path.name}", "mode": "overwrite", "mute": True})
    req = urllib.request.Request("https://content.dropboxapi.com/2/files/upload", data=data, method="POST")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Dropbox-API-Arg", api_arg)
    req.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            if response.status != 200:
                raise ValueError(f"Dropbox-Upload fehlgeschlagen: HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Dropbox-Upload fehlgeschlagen: HTTP {exc.code} {body[:200]}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Dropbox nicht erreichbar: {exc.reason}") from exc
    return f"Dropbox: /{source_path.name}"


# --- Google Drive ------------------------------------------------------------

def google_drive_authorize_url(redirect_uri):
    client_id = (setting_value("backup_google_client_id", "") or "").strip()
    if not client_id:
        raise ValueError("Bitte zuerst Client-ID und Client-Secret für Google Drive eintragen und speichern.")
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": "https://www.googleapis.com/auth/drive.file",
        "access_type": "offline",
        "prompt": "consent",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)


def google_drive_exchange_code(code, redirect_uri):
    client_id = (setting_value("backup_google_client_id", "") or "").strip()
    client_secret = (setting_value("backup_google_client_secret", "") or "").strip()
    if not client_id or not client_secret:
        raise ValueError("Google Client-ID/Client-Secret fehlen.")
    data = oauth_token_request("https://oauth2.googleapis.com/token", {
        "code": code,
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    })
    set_setting_value("google_drive_access_token", data["access_token"])
    if data.get("refresh_token"):
        set_setting_value("google_drive_refresh_token", data["refresh_token"])
    expires_at = (datetime.utcnow() + timedelta(seconds=max(int(data.get("expires_in", 3600)) - 60, 60))).isoformat()
    set_setting_value("google_drive_token_expires_at", expires_at)

    info = oauth_get_request("https://www.googleapis.com/oauth2/v2/userinfo", data["access_token"])
    set_setting_value("google_drive_account_label", info.get("email", "verbunden"))


def google_drive_refresh_access_token():
    client_id = (setting_value("backup_google_client_id", "") or "").strip()
    client_secret = (setting_value("backup_google_client_secret", "") or "").strip()
    refresh_token = setting_value("google_drive_refresh_token", "") or ""
    if not refresh_token:
        raise ValueError("Google Drive ist nicht verbunden. Bitte zuerst 'Verbindung herstellen' klicken.")
    data = oauth_token_request("https://oauth2.googleapis.com/token", {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    })
    set_setting_value("google_drive_access_token", data["access_token"])
    expires_at = (datetime.utcnow() + timedelta(seconds=max(int(data.get("expires_in", 3600)) - 60, 60))).isoformat()
    set_setting_value("google_drive_token_expires_at", expires_at)
    return data["access_token"]


def google_drive_valid_access_token():
    token = setting_value("google_drive_access_token", "") or ""
    expires_raw = setting_value("google_drive_token_expires_at", "") or ""
    expired = True
    if expires_raw:
        try:
            expired = datetime.utcnow() >= datetime.fromisoformat(expires_raw)
        except ValueError:
            expired = True
    if not token or expired:
        return google_drive_refresh_access_token()
    return token


def google_drive_upload_file(source_path):
    access_token = google_drive_valid_access_token()
    data = source_path.read_bytes()
    boundary = "kegelkasseBackupBoundary"
    metadata = json.dumps({"name": source_path.name})
    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{metadata}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: application/zip\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--".encode("utf-8")

    req = urllib.request.Request(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
        data=body, method="POST",
    )
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", f"multipart/related; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            if response.status not in (200, 201):
                raise ValueError(f"Google-Drive-Upload fehlgeschlagen: HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Google-Drive-Upload fehlgeschlagen: HTTP {exc.code} {body_text[:200]}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Google Drive nicht erreichbar: {exc.reason}") from exc
    return f"Google Drive: {source_path.name}"


# --- OneDrive ----------------------------------------------------------------

def onedrive_authorize_url(redirect_uri):
    client_id = (setting_value("backup_onedrive_client_id", "") or "").strip()
    if not client_id:
        raise ValueError("Bitte zuerst Anwendungs-ID (Client-ID) und Client-Secret für OneDrive eintragen und speichern.")
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": "Files.ReadWrite offline_access",
    }
    return "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?" + urllib.parse.urlencode(params)


def onedrive_exchange_code(code, redirect_uri):
    client_id = (setting_value("backup_onedrive_client_id", "") or "").strip()
    client_secret = (setting_value("backup_onedrive_client_secret", "") or "").strip()
    if not client_id or not client_secret:
        raise ValueError("OneDrive Client-ID/Client-Secret fehlen.")
    data = oauth_token_request("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
        "code": code,
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "scope": "Files.ReadWrite offline_access",
    })
    set_setting_value("onedrive_access_token", data["access_token"])
    if data.get("refresh_token"):
        set_setting_value("onedrive_refresh_token", data["refresh_token"])
    expires_at = (datetime.utcnow() + timedelta(seconds=max(int(data.get("expires_in", 3600)) - 60, 60))).isoformat()
    set_setting_value("onedrive_token_expires_at", expires_at)

    info = oauth_get_request("https://graph.microsoft.com/v1.0/me", data["access_token"])
    set_setting_value("onedrive_account_label", info.get("userPrincipalName") or info.get("mail") or "verbunden")


def onedrive_refresh_access_token():
    client_id = (setting_value("backup_onedrive_client_id", "") or "").strip()
    client_secret = (setting_value("backup_onedrive_client_secret", "") or "").strip()
    refresh_token = setting_value("onedrive_refresh_token", "") or ""
    if not refresh_token:
        raise ValueError("OneDrive ist nicht verbunden. Bitte zuerst 'Verbindung herstellen' klicken.")
    data = oauth_token_request("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "Files.ReadWrite offline_access",
    })
    set_setting_value("onedrive_access_token", data["access_token"])
    if data.get("refresh_token"):
        set_setting_value("onedrive_refresh_token", data["refresh_token"])
    expires_at = (datetime.utcnow() + timedelta(seconds=max(int(data.get("expires_in", 3600)) - 60, 60))).isoformat()
    set_setting_value("onedrive_token_expires_at", expires_at)
    return data["access_token"]


def onedrive_valid_access_token():
    token = setting_value("onedrive_access_token", "") or ""
    expires_raw = setting_value("onedrive_token_expires_at", "") or ""
    expired = True
    if expires_raw:
        try:
            expired = datetime.utcnow() >= datetime.fromisoformat(expires_raw)
        except ValueError:
            expired = True
    if not token or expired:
        return onedrive_refresh_access_token()
    return token


def onedrive_upload_file(source_path):
    access_token = onedrive_valid_access_token()
    data = source_path.read_bytes()
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("Die Datei ist größer als 4 MB - das einfache OneDrive-Hochladen unterstützt aktuell nur kleinere Dateien.")
    encoded_name = urllib.parse.quote(source_path.name)
    req = urllib.request.Request(
        f"https://graph.microsoft.com/v1.0/me/drive/root:/{encoded_name}:/content",
        data=data, method="PUT",
    )
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/zip")
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            if response.status not in (200, 201):
                raise ValueError(f"OneDrive-Upload fehlgeschlagen: HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"OneDrive-Upload fehlgeschlagen: HTTP {exc.code} {body[:200]}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"OneDrive nicht erreichbar: {exc.reason}") from exc
    return f"OneDrive: {source_path.name}"


def copy_backup_to_extra_target(source_path):
    settings = backup_settings()
    if not settings.get("extra_target_enabled"):
        return None

    target_type = settings.get("target_type")
    if target_type == "webdav":
        return webdav_upload_file(source_path)
    if target_type == "dropbox":
        return dropbox_upload_file(source_path)
    if target_type == "google_drive":
        return google_drive_upload_file(source_path)
    if target_type == "onedrive":
        return onedrive_upload_file(source_path)

    raise ValueError(f"Das Backup-Ziel '{backup_target_label_raw(target_type)}' ist noch nicht angebunden.")


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

    restore_before = create_database_backup(kind="manual")
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

    Unterschied zum normalen Backup:
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
            "Hinweis: Dieser Export ersetzt kein regelmäßiges Backup mit zweitem Speicherziel.\n",
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


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_database_backup(kind="manual"):
    folder = backup_dir()
    now = datetime.now()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    filename = f"kegelkasse_backup_{'auto_' if kind == 'auto' else ''}{stamp}.zip"
    target = folder / filename
    temp_db = folder / f".{filename}.sqlite.tmp"

    # SQLite-Backup API nutzt einen konsistenten Snapshot, statt die laufende Datei roh zu kopieren.
    source = sqlite3.connect(str(DATABASE_PATH))
    dest = sqlite3.connect(str(temp_db))
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()

    sanitize_sqlite_settings_for_export(temp_db)

    include_documents = backup_settings().get("include_documents", True)

    metadata = {
        "format_version": 3,
        "backup_uuid": str(uuid.uuid4()),
        "created_at": now.isoformat(timespec="seconds"),
        "created_by": current_user.username if current_user and current_user.is_authenticated else "system",
        "kind": kind,
        "app": "Kegelkasse",
        "app_version": APP_VERSION,
        "database_profile": get_active_database_profile(),
        "club_name": setting_value("club_name", "Alle 8te") or "Alle 8te",
        "database_file": DATABASE_PATH.name,
        "include_documents": include_documents,
        "includes": {
            "database": True,
            "documents": include_documents,
            "settings": True,
            "secrets": False,
        },
        "statistics": {
            "members": Member.query.count(),
            "events": BowlingEvent.query.count(),
            "cashbook_entries": CashbookEntry.query.count(),
            "documents": Document.query.filter(Document.deleted_at.is_(None)).count(),
        },
        "secrets_exported": False,
        "secrets_hint": "SMTP-, WebDAV- und Cloud-Zugangsdaten werden aus Sicherheitsgründen nicht in Backup-ZIPs gespeichert und müssen nach Restore neu eingetragen werden.",
    }

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(temp_db, arcname="kegelkasse.db")
        zf.writestr("backup_info.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        zf.writestr(
            "README_BACKUP.txt",
            "Kegelkasse Backup\n"
            "=================\n\n"
            f"Erstellt: {metadata['created_at']}\n"
            f"Version: {metadata['app_version']}\n"
            f"Verein: {metadata['club_name']}\n"
            f"Backup-ID: {metadata['backup_uuid']}\n\n"
            "Dieses Backup enthält die Kegelkasse-Datenbank"
            + (" und die Dokumentenablage" if metadata["include_documents"] else "")
            + ".\n\n"
            "Vor einer Wiederherstellung erstellt die Kegelkasse automatisch eine Sicherheitskopie der aktuellen Daten.\n\n"
            "Sicherheitshinweis: SMTP-, WebDAV- und Cloud-Zugangsdaten werden nicht exportiert und müssen nach einer Wiederherstellung neu eingetragen werden.\n",
        )
        # Einstellungen zusätzlich lesbar ablegen. Die eigentlichen Daten sind weiterhin in der DB.
        zf.writestr(
            "app_settings.json",
            json.dumps(sanitized_app_settings_dict(), ensure_ascii=False, indent=2),
        )
        if metadata["include_documents"] and DOCUMENT_DIR.exists():
            for doc_path in DOCUMENT_DIR.rglob("*"):
                if doc_path.is_file():
                    zf.write(doc_path, arcname=f"documents/{doc_path.relative_to(DOCUMENT_DIR)}")

    try:
        temp_db.unlink()
    except FileNotFoundError:
        pass

    copied_to = None
    try:
        copied_to = copy_backup_to_extra_target(target)
        set_setting_value("backup_last_result", f"OK: {target.name}" + (f"; Kopie: {copied_to}" if copied_to else ""))
        db.session.commit()
    except Exception as exc:
        set_setting_value("backup_last_result", f"Lokales Backup OK, Kopie fehlgeschlagen: {exc}")
        db.session.commit()
        # Lokale Sicherung bleibt gültig; den Fehler zeigen wir dem Benutzer zusätzlich an.
        if kind == "manual":
            raise

    return target


def cleanup_old_backups():
    settings = backup_settings()
    keep_days = max(1, settings["keep_days"])
    keep_count = max(1, settings.get("keep_count", 20))
    cutoff = datetime.now() - timedelta(days=keep_days)
    deleted = []

    auto_files = [item for item in backup_file_list() if item["kind"] == "Automatisch"]
    for index, item in enumerate(auto_files):
        too_old = item["created_at"] < cutoff
        too_many = index >= keep_count
        if too_old or too_many:
            try:
                item["path"].unlink()
                deleted.append(item["name"])
            except FileNotFoundError:
                pass
    return deleted


def restore_database_from_backup(path):
    if not path.exists() or path.parent != backup_dir():
        raise ValueError("Sicherungsdatei wurde nicht gefunden.")

    restore_before = create_database_backup(kind="manual")

    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    work_dir = backup_dir() / f".restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    temp_restore = DATABASE_PATH.parent / f".restore_{DATABASE_PATH.name}.tmp"

    try:
        if temp_restore.exists():
            temp_restore.unlink()
        work_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            if "kegelkasse.db" not in names:
                raise ValueError("Die Sicherung enthält keine kegelkasse.db.")
            zf.extract("kegelkasse.db", work_dir)
            for name in names:
                if name.startswith("documents/") and not name.endswith("/"):
                    zf.extract(name, work_dir)

        extracted_db = work_dir / "kegelkasse.db"
        if not extracted_db.exists():
            raise ValueError("Die Datenbank konnte aus der Sicherung nicht entpackt werden.")

        # Bestehende SQLAlchemy-Verbindungen lösen, bevor die SQLite-Datei ersetzt wird.
        db.session.remove()
        db.engine.dispose()

        # Nicht per rename/move aus dem Backup-Ordner ersetzen:
        # Backup- und Datenbankverzeichnis können in Docker verschiedene Mounts sein.
        # copy2 funktioniert zuverlässig über Dateisystemgrenzen hinweg; das finale replace
        # passiert anschließend im Datenbankverzeichnis selbst.
        shutil.copy2(extracted_db, temp_restore)
        os.replace(temp_restore, DATABASE_PATH)

        extracted_documents = work_dir / "documents"
        if extracted_documents.exists():
            if DOCUMENT_DIR.exists():
                shutil.rmtree(DOCUMENT_DIR)
            DOCUMENT_DIR.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(extracted_documents, DOCUMENT_DIR)

        db.engine.dispose()
        db.create_all()
        migrate_schema_extensions()

    finally:
        for leftover in (temp_restore,):
            try:
                leftover.unlink()
            except FileNotFoundError:
                pass
        try:
            shutil.rmtree(work_dir)
        except Exception:
            pass

    return restore_before


def verify_backup_file(path):
    if not path.exists() or path.parent != backup_dir():
        raise ValueError("Sicherungsdatei wurde nicht gefunden.")

    result = {
        "database": False,
        "documents": 0,
        "settings": False,
        "metadata": False,
        "quick_check": "nicht geprüft",
    }

    temp_db = backup_dir() / f".verify_{path.stem}.sqlite.tmp"
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            if "kegelkasse.db" not in names:
                raise ValueError("Die Sicherung enthält keine kegelkasse.db.")
            result["metadata"] = "backup_info.json" in names
            result["settings"] = "app_settings.json" in names
            result["documents"] = len([name for name in names if name.startswith("documents/") and not name.endswith("/")])
            zf.extract("kegelkasse.db", backup_dir())

        extracted = backup_dir() / "kegelkasse.db"
        extracted.replace(temp_db)

        conn = sqlite3.connect(str(temp_db))
        try:
            check = conn.execute("PRAGMA quick_check").fetchone()
            result["quick_check"] = check[0] if check else "kein Ergebnis"
            result["database"] = result["quick_check"].lower() == "ok"
        finally:
            conn.close()

        if not result["database"]:
            raise ValueError(f"SQLite-Prüfung fehlgeschlagen: {result['quick_check']}")

        parts = ["Datenbank lesbar"]
        if result["metadata"]:
            parts.append("Metadaten vorhanden")
        if result["settings"]:
            parts.append("Einstellungen vorhanden")
        parts.append(f"{result['documents']} Dokument(e)")
        return "; ".join(parts)
    finally:
        try:
            temp_db.unlink()
        except FileNotFoundError:
            pass


def club_import_preview_from_settings():
    preview_raw = setting_value("club_import_preview_json", "")
    if not preview_raw:
        return None
    try:
        preview = json.loads(preview_raw)
        return preview if isinstance(preview, dict) else None
    except Exception:
        return None


@app.before_request
def maybe_create_scheduled_backup():
    # Sehr leichte eingebaute Automatik: läuft beim ersten App-Aufruf nach der geplanten Uhrzeit.
    if request.endpoint in ("static", "download_backup"):
        return
    try:
        settings = backup_settings()
        if not settings["enabled"]:
            return
        now = datetime.now()
        try:
            scheduled_time = datetime.strptime(settings["time"], "%H:%M").time()
        except ValueError:
            scheduled_time = datetime.strptime("02:00", "%H:%M").time()
        if now.time() < scheduled_time:
            return

        last_value = settings.get("last_auto") or ""
        last_date = None
        if last_value:
            try:
                last_date = datetime.fromisoformat(last_value).date()
            except ValueError:
                last_date = None

        due = False
        if last_date is None:
            due = True
        elif settings["interval"] == "daily":
            due = now.date() > last_date
        elif settings["interval"] == "weekly":
            due = (now.date() - last_date).days >= 7
        elif settings["interval"] == "monthly":
            due = (now.year, now.month) != (last_date.year, last_date.month)

        if due:
            backup = create_database_backup(kind="auto")
            set_setting_value("backup_last_auto", now.isoformat(timespec="seconds"))
            cleanup_old_backups()
            audit_log(
                "Datensicherung",
                "auto_backup_created",
                "Automatische Sicherung erstellt",
                details=f"Automatische Sicherung erstellt: {backup.name}",
                object_type="Backup",
                new_value=backup.name,
            )
            db.session.commit()
    except Exception:
        db.session.rollback()


@app.route("/backups/dropbox/connect")
@login_required
@role_required("admin")
def dropbox_oauth_connect():
    redirect_uri = url_for("dropbox_oauth_callback", _external=True)
    try:
        auth_url = dropbox_authorize_url(redirect_uri)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("backups_page"))
    return redirect(auth_url)


@app.route("/backups/dropbox/callback")
@login_required
@role_required("admin")
def dropbox_oauth_callback():
    error = request.args.get("error")
    if error:
        flash(f"Dropbox-Verbindung abgebrochen: {error}", "warning")
        return redirect(url_for("backups_page"))

    code = request.args.get("code")
    if not code:
        flash("Dropbox hat keinen Anmeldecode zurückgegeben.", "danger")
        return redirect(url_for("backups_page"))

    redirect_uri = url_for("dropbox_oauth_callback", _external=True)
    try:
        dropbox_exchange_code(code, redirect_uri)
        db.session.commit()
        audit_log("Datensicherung", "dropbox_connected", "Dropbox-Verbindung hergestellt", object_type="Backup")
        db.session.commit()
        flash("Dropbox wurde erfolgreich verbunden.", "success")
    except ValueError as exc:
        flash(f"Dropbox-Verbindung fehlgeschlagen: {exc}", "danger")
    return redirect(url_for("backups_page"))


@app.route("/backups/dropbox/disconnect", methods=["POST"])
@login_required
@role_required("admin")
def dropbox_oauth_disconnect():
    for key in ("dropbox_access_token", "dropbox_refresh_token", "dropbox_token_expires_at", "dropbox_account_label"):
        set_setting_value(key, "")
    audit_log("Datensicherung", "dropbox_disconnected", "Dropbox-Verbindung getrennt", object_type="Backup")
    db.session.commit()
    flash("Dropbox-Verbindung wurde getrennt.", "success")
    return redirect(url_for("backups_page"))


@app.route("/backups/google-drive/connect")
@login_required
@role_required("admin")
def google_drive_oauth_connect():
    redirect_uri = url_for("google_drive_oauth_callback", _external=True)
    try:
        auth_url = google_drive_authorize_url(redirect_uri)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("backups_page"))
    return redirect(auth_url)


@app.route("/backups/google-drive/callback")
@login_required
@role_required("admin")
def google_drive_oauth_callback():
    error = request.args.get("error")
    if error:
        flash(f"Google-Drive-Verbindung abgebrochen: {error}", "warning")
        return redirect(url_for("backups_page"))

    code = request.args.get("code")
    if not code:
        flash("Google hat keinen Anmeldecode zurückgegeben.", "danger")
        return redirect(url_for("backups_page"))

    redirect_uri = url_for("google_drive_oauth_callback", _external=True)
    try:
        google_drive_exchange_code(code, redirect_uri)
        db.session.commit()
        audit_log("Datensicherung", "google_drive_connected", "Google-Drive-Verbindung hergestellt", object_type="Backup")
        db.session.commit()
        flash("Google Drive wurde erfolgreich verbunden.", "success")
    except ValueError as exc:
        flash(f"Google-Drive-Verbindung fehlgeschlagen: {exc}", "danger")
    return redirect(url_for("backups_page"))


@app.route("/backups/google-drive/disconnect", methods=["POST"])
@login_required
@role_required("admin")
def google_drive_oauth_disconnect():
    for key in ("google_drive_access_token", "google_drive_refresh_token", "google_drive_token_expires_at", "google_drive_account_label"):
        set_setting_value(key, "")
    audit_log("Datensicherung", "google_drive_disconnected", "Google-Drive-Verbindung getrennt", object_type="Backup")
    db.session.commit()
    flash("Google-Drive-Verbindung wurde getrennt.", "success")
    return redirect(url_for("backups_page"))


@app.route("/backups/onedrive/connect")
@login_required
@role_required("admin")
def onedrive_oauth_connect():
    redirect_uri = url_for("onedrive_oauth_callback", _external=True)
    try:
        auth_url = onedrive_authorize_url(redirect_uri)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("backups_page"))
    return redirect(auth_url)


@app.route("/backups/onedrive/callback")
@login_required
@role_required("admin")
def onedrive_oauth_callback():
    error = request.args.get("error")
    if error:
        flash(f"OneDrive-Verbindung abgebrochen: {error}", "warning")
        return redirect(url_for("backups_page"))

    code = request.args.get("code")
    if not code:
        flash("Microsoft hat keinen Anmeldecode zurückgegeben.", "danger")
        return redirect(url_for("backups_page"))

    redirect_uri = url_for("onedrive_oauth_callback", _external=True)
    try:
        onedrive_exchange_code(code, redirect_uri)
        db.session.commit()
        audit_log("Datensicherung", "onedrive_connected", "OneDrive-Verbindung hergestellt", object_type="Backup")
        db.session.commit()
        flash("OneDrive wurde erfolgreich verbunden.", "success")
    except ValueError as exc:
        flash(f"OneDrive-Verbindung fehlgeschlagen: {exc}", "danger")
    return redirect(url_for("backups_page"))


@app.route("/backups/onedrive/disconnect", methods=["POST"])
@login_required
@role_required("admin")
def onedrive_oauth_disconnect():
    for key in ("onedrive_access_token", "onedrive_refresh_token", "onedrive_token_expires_at", "onedrive_account_label"):
        set_setting_value(key, "")
    audit_log("Datensicherung", "onedrive_disconnected", "OneDrive-Verbindung getrennt", object_type="Backup")
    db.session.commit()
    flash("OneDrive-Verbindung wurde getrennt.", "success")
    return redirect(url_for("backups_page"))


@app.route("/backups", methods=["GET", "POST"])
@login_required
@role_required("admin")
def backups_page():
    if request.method == "POST":
        actions = request.form.getlist("form_action")
        action = actions[-1] if actions else ""
        filename = secure_filename(request.form.get("filename", ""))
        redirect_target = request.form.get("redirect_to", "backups")
        redirect_endpoint = "import_export_page" if redirect_target == "import_export" else "backups_page"
        try:
            if action == "create_backup":
                backup = create_database_backup(kind="manual")
                audit_log(
                    "Datensicherung",
                    "backup_created",
                    "Manuelle Sicherung erstellt",
                    details=f"Sicherung erstellt: {backup.name}",
                    object_type="Backup",
                    new_value=backup.name,
                )
                db.session.commit()
                flash("Sicherung wurde erstellt.", "success")

            elif action == "upload_backup":
                uploaded = request.files.get("backup_upload")
                if not uploaded or not uploaded.filename:
                    flash("Bitte eine Backup-ZIP auswählen.", "danger")
                else:
                    original_name = secure_filename(uploaded.filename)
                    if not original_name.lower().endswith(".zip"):
                        flash("Bitte nur ZIP-Sicherungen hochladen.", "danger")
                    else:
                        folder = backup_dir()
                        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                        target_name = f"kegelkasse_backup_import_{stamp}_{original_name}"
                        target = folder / target_name
                        uploaded.save(target)
                        try:
                            check_summary = verify_backup_file(target)
                        except Exception:
                            try:
                                target.unlink()
                            except FileNotFoundError:
                                pass
                            raise
                        audit_log(
                            "Datensicherung",
                            "backup_uploaded",
                            "Sicherung hochgeladen",
                            details=f"Sicherung hochgeladen und geprüft: {target_name}. Ergebnis: {check_summary}",
                            object_type="Backup",
                            new_value=target_name,
                        )
                        db.session.commit()
                        flash(f"Sicherung wurde hochgeladen und geprüft: {check_summary}", "success")

            elif action == "upload_club_import":
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
                            flash(f"Vereins-Export geprüft: {members} Mitglieder, {events} Kegelabende, {docs} Dokumentdateien erkannt. Bitte Import unten bestätigen.", "success")

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
                        flash(f"Vereins-Export wurde importiert. Vorherige Installation wurde gesichert als: {restore_before.name}. Bitte neu anmelden.", "success")
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

            elif action == "backup_settings":
                old_summary = f"Aktiv: {audit_value(setting_value('backup_enabled', '0'))}; Intervall: {audit_value(setting_value('backup_interval', 'daily'))}; Uhrzeit: {audit_value(setting_value('backup_time', '02:00'))}; Aufbewahrung: {audit_value(setting_value('backup_keep_days', '30'))} Tage; Generationen: {audit_value(setting_value('backup_keep_count', '20'))}; Ziel: {audit_value(backup_target_label())}"
                set_setting_value("backup_enabled", "1" if request.form.get("backup_enabled") else "0")
                set_setting_value("backup_interval", request.form.get("backup_interval", "daily"))
                set_setting_value("backup_time", request.form.get("backup_time", "02:00") or "02:00")
                set_setting_value("backup_keep_days", request.form.get("backup_keep_days", "30") or "30")
                set_setting_value("backup_keep_count", request.form.get("backup_keep_count", "20") or "20")
                set_setting_value("backup_include_documents", "1" if request.form.get("backup_include_documents") else "0")
                set_setting_value("backup_extra_target_enabled", "1" if request.form.get("backup_extra_target_enabled") else "0")
                target_type = request.form.get("backup_target_type", "webdav")
                if target_type not in ("webdav", "dropbox", "onedrive", "google_drive"):
                    target_type = "webdav"
                set_setting_value("backup_target_type", target_type)
                set_setting_value("backup_webdav_url", request.form.get("backup_webdav_url", "").strip())
                set_setting_value("backup_webdav_username", request.form.get("backup_webdav_username", "").strip())
                webdav_password = request.form.get("backup_webdav_password", "")
                if webdav_password:
                    set_setting_value("backup_webdav_password", webdav_password)
                if request.form.get("backup_webdav_password_clear"):
                    set_setting_value("backup_webdav_password", "")

                set_setting_value("backup_dropbox_app_key", request.form.get("backup_dropbox_app_key", "").strip())
                dropbox_secret = request.form.get("backup_dropbox_app_secret", "")
                if dropbox_secret:
                    set_setting_value("backup_dropbox_app_secret", dropbox_secret)

                set_setting_value("backup_google_client_id", request.form.get("backup_google_client_id", "").strip())
                google_secret = request.form.get("backup_google_client_secret", "")
                if google_secret:
                    set_setting_value("backup_google_client_secret", google_secret)

                set_setting_value("backup_onedrive_client_id", request.form.get("backup_onedrive_client_id", "").strip())
                onedrive_secret = request.form.get("backup_onedrive_client_secret", "")
                if onedrive_secret:
                    set_setting_value("backup_onedrive_client_secret", onedrive_secret)

                new_summary = f"Aktiv: {audit_value(setting_value('backup_enabled', '0'))}; Intervall: {audit_value(setting_value('backup_interval', 'daily'))}; Uhrzeit: {audit_value(setting_value('backup_time', '02:00'))}; Aufbewahrung: {audit_value(setting_value('backup_keep_days', '30'))} Tage; Generationen: {audit_value(setting_value('backup_keep_count', '20'))}; Ziel: {audit_value(backup_target_label())}"
                audit_log(
                    "Datensicherung",
                    "backup_settings_updated",
                    "Sicherungseinstellungen geändert",
                    details="Einstellungen für automatische Sicherungen wurden aktualisiert.",
                    object_type="AppSetting",
                    old_value=old_summary,
                    new_value=new_summary,
                )
                db.session.commit()
                cleanup_old_backups()
                flash("Sicherungseinstellungen wurden gespeichert.", "success")

            elif action == "test_backup_target":
                test_file = backup_dir() / "kegelkasse_backup_test.txt"
                test_file.write_text(f"Kegelkasse Backup-Test\n{datetime.now().isoformat(timespec='seconds')}\n", encoding="utf-8")
                try:
                    try:
                        copied_to = copy_backup_to_extra_target(test_file)
                    except Exception as exc:
                        set_setting_value("backup_last_result", f"Test fehlgeschlagen: {exc}")
                        audit_log(
                            "Datensicherung",
                            "backup_target_tested",
                            "Backup-Ziel-Test fehlgeschlagen",
                            details=f"Backup-Ziel-Test fehlgeschlagen: {exc}",
                            object_type="Backup",
                            new_value=str(exc),
                        )
                        db.session.commit()
                        raise
                    set_setting_value("backup_last_result", f"Test OK: {copied_to}")
                    audit_log(
                        "Datensicherung",
                        "backup_target_tested",
                        "Backup-Ziel getestet",
                        details=f"Backup-Ziel erfolgreich getestet: {copied_to}",
                        object_type="Backup",
                        new_value=str(copied_to),
                    )
                    db.session.commit()
                    flash("Backup-Ziel wurde erfolgreich getestet.", "success")
                finally:
                    try:
                        test_file.unlink()
                    except FileNotFoundError:
                        pass

            elif action == "delete_backup":
                path = backup_dir() / filename
                if not filename or not path.exists() or path.parent != backup_dir():
                    flash("Sicherungsdatei wurde nicht gefunden.", "danger")
                else:
                    path.unlink()
                    audit_log(
                        "Datensicherung",
                        "backup_deleted",
                        "Sicherung gelöscht",
                        details=f"Sicherung gelöscht: {filename}",
                        object_type="Backup",
                        old_value=filename,
                    )
                    db.session.commit()
                    flash("Sicherung wurde gelöscht.", "success")

            elif action == "check_backup":
                path = backup_dir() / filename
                check_summary = verify_backup_file(path)
                audit_log(
                    "Datensicherung",
                    "backup_checked",
                    "Sicherung geprüft",
                    details=f"Sicherung geprüft: {filename}. Ergebnis: {check_summary}",
                    object_type="Backup",
                    new_value=filename,
                )
                db.session.commit()
                flash(f"Sicherung ist lesbar: {check_summary}", "success")

            elif action == "restore_backup":
                confirm = request.form.get("confirm_restore", "").strip().upper()
                if confirm != "WIEDERHERSTELLEN":
                    flash("Bitte zur Sicherheit WIEDERHERSTELLEN eingeben.", "danger")
                else:
                    path = backup_dir() / filename
                    restore_before = restore_database_from_backup(path)
                    audit_log(
                        "Datensicherung",
                        "backup_restored",
                        "Sicherung wiederhergestellt",
                        details=f"Sicherung wiederhergestellt: {filename}. Vorher wurde automatisch eine Sicherung erstellt: {restore_before.name}",
                        object_type="Backup",
                        old_value=restore_before.name,
                        new_value=filename,
                    )
                    db.session.commit()
                    flash("Sicherung wurde wiederhergestellt. Bitte Anwendung neu laden.", "success")

        except Exception as exc:
            db.session.rollback()
            flash(f"Aktion konnte nicht ausgeführt werden: {exc}", "danger")
        return redirect(url_for(redirect_endpoint))

    files = backup_file_list()
    last_manual = next((item for item in files if item["kind"] == "Manuell"), None)
    last_auto = next((item for item in files if item["kind"] == "Automatisch"), None)
    club_import_preview = None
    preview_raw = setting_value("club_import_preview_json", "")
    if preview_raw:
        try:
            club_import_preview = json.loads(preview_raw)
        except Exception:
            club_import_preview = None
    return render_template(
        "backups.html",
        files=files,
        settings=backup_settings(),
        db_info=database_info(),
        last_manual=last_manual,
        last_auto=last_auto,
        club_import_preview=club_import_preview,
        dropbox_redirect_uri=url_for("dropbox_oauth_callback", _external=True),
        google_drive_redirect_uri=url_for("google_drive_oauth_callback", _external=True),
        onedrive_redirect_uri=url_for("onedrive_oauth_callback", _external=True),
    )


@app.route("/import-export")
@login_required
@role_required("admin")
def import_export_page():
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
        return redirect(url_for("backups_page"))

    @after_this_request
    def cleanup_export(response):
        try:
            export_path.unlink()
        except Exception:
            pass
        return response

    return send_file(export_path, as_attachment=True, download_name=export_path.name)


@app.route("/backups/download/<path:filename>")
@login_required
@role_required("admin")
def download_backup(filename):
    safe_name = secure_filename(filename)
    path = backup_dir() / safe_name
    if not safe_name or not path.exists() or path.parent != backup_dir():
        flash("Sicherungsdatei wurde nicht gefunden.", "danger")
        return redirect(url_for("backups_page"))
    return send_file(path, as_attachment=True, download_name=safe_name)
