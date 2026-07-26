"""Prüft (gecacht) über die GitHub-Releases-API, ob eine neuere Kegelkasse-
Version veröffentlicht wurde, und zeigt dem Admin dazu einen Hinweis in der
Kopfzeile. Nutzt urllib statt einer zusätzlichen requests-Abhängigkeit;
schlägt niemals sichtbar fehl (offline/GitHub down -> einfach kein Hinweis),
damit ein Netzwerkproblem nie eine Seite kaputt machen kann."""
import json
import urllib.request
from datetime import datetime, timezone

from services.settings import setting_value, set_setting_value

GITHUB_REPO = "aluecke75/kegelkasse"
CHECK_INTERVAL_SECONDS = 6 * 60 * 60


def _version_tuple(version):
    parts = []
    for part in (version or "").lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _fetch_latest_release():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "kegelkasse-update-check"},
    )
    with urllib.request.urlopen(request, timeout=4) as response:
        data = json.load(response)
    return data.get("tag_name"), data.get("html_url")


def check_for_update(current_version):
    """Liefert (update_verfuegbar, neueste_version, release_url)."""
    now = datetime.now(timezone.utc)
    last_checked_raw = setting_value("update_check_last_checked_at")
    should_refresh = True
    if last_checked_raw:
        try:
            last_checked = datetime.fromisoformat(last_checked_raw)
            should_refresh = (now - last_checked).total_seconds() > CHECK_INTERVAL_SECONDS
        except ValueError:
            should_refresh = True

    if should_refresh:
        try:
            tag_name, html_url = _fetch_latest_release()
            if tag_name:
                from models import db

                set_setting_value("update_check_latest_version", tag_name)
                set_setting_value("update_check_latest_url", html_url or "")
                set_setting_value("update_check_last_checked_at", now.isoformat())
                db.session.commit()
        except Exception:
            pass

    latest_version = setting_value("update_check_latest_version")
    latest_url = setting_value("update_check_latest_url")
    if not latest_version:
        return False, None, None

    try:
        update_available = _version_tuple(latest_version) > _version_tuple(current_version)
    except (ValueError, TypeError):
        update_available = False

    return update_available, latest_version, latest_url
