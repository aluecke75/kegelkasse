#!/usr/bin/env python3
"""Simuliert einen verfügbaren Update-Hinweis in der Kegelkasse-Datenbank.

Da das GitHub-Repo aluecke75/kegelkasse noch nicht existiert (404), schlägt der
echte API-Aufruf fehl und kein Update-Hinweis wird angezeigt. Dieses Skript
setzt die AppSetting-Werte direkt in der SQLite-Datenbank, sodass das
Update-Check-Feature im Header sichtbar wird — ohne das GitHub-Repo anlegen zu
müssen.

Nutzung:
    python scripts/simuliere_update_check.py [--db PFAD] [--version v0.99.18]

Um die Simulation zu entfernen:
    python scripts/simuliere_update_check.py --clear
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "database" / "kegelkasse.db"

SETTINGS = {
    "update_check_latest_version",
    "update_check_latest_url",
    "update_check_last_checked_at",
}


def find_db():
    candidates = [
        DEFAULT_DB,
        Path("/app/database/kegelkasse.db"),
        Path("./data/database/kegelkasse.db"),
    ]
    for c in candidates:
        if c.exists():
            return c
    return DEFAULT_DB


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO app_setting (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def clear_settings(conn):
    for key in SETTINGS:
        conn.execute("DELETE FROM app_setting WHERE key=?", (key,))


def main():
    parser = argparse.ArgumentParser(description="Simuliere Update-Check in Kegelkasse-DB")
    parser.add_argument("--db", type=Path, default=None, help="Pfad zur SQLite-Datenbank")
    parser.add_argument("--version", default="v0.99.18", help="zu simulierende neue Version")
    parser.add_argument("--clear", action="store_true", help="Simulation entfernen")
    args = parser.parse_args()

    db_path = args.db or find_db()
    if not db_path.exists():
        print(f"❌  Datenbank nicht gefunden: {db_path}")
        print("    Starte zuerst die App oder gib --db PFAD an.")
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    try:
        if args.clear:
            clear_settings(conn)
            conn.commit()
            print(f"✅  Update-Check-Simulation aus {db_path} entfernt.")
        else:
            now = datetime.now(timezone.utc).isoformat()
            set_setting(conn, "update_check_latest_version", args.version)
            set_setting(conn, "update_check_latest_url",
                        f"https://github.com/aluecke75/kegelkasse/releases/tag/{args.version}")
            set_setting(conn, "update_check_last_checked_at", now)
            conn.commit()
            print(f"✅  Update-Check simuliert in {db_path}:")
            print(f"    latest_version  = {args.version}")
            print(f"    latest_url      = https://github.com/aluecke75/kegelkasse/releases/tag/{args.version}")
            print(f"    last_checked_at = {now}")
            print(f"\n💡  Starte die App neu und melde dich als Admin an —")
            print(f"    die Update-Badge erscheint im Header (🔔 Update {args.version}).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
