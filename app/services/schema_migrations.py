"""Robuste SQLite-Schema-Migrationen für bestehende Installationen (ZIP-Updates).

Läuft beim Containerstart in app.py. Neuer Code darf nicht mit Internal
Server Error starten, nur weil eine bestehende Vereinsdatenbank einzelne
neue Spalten noch nicht besitzt.
"""
from models import db, AuditLog, User


def ensure_column(table_name, column_name, column_sql):
    """Robuste SQLite-Migration für bestehende Installationen.

    Gibt True zurück, wenn eine Spalte neu angelegt wurde. Dadurch können
    Migrationen beim Containerstart nachvollziehbar in Docker-Logs und im
    Revisionsprotokoll dokumentiert werden.
    """
    allowed_ident = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
    if not table_name or not column_name:
        return False
    if any(ch not in allowed_ident for ch in table_name + column_name):
        raise ValueError(f"Ungültiger Tabellen-/Spaltenname: {table_name}.{column_name}")

    rows = db.session.execute(db.text(f"PRAGMA table_info({table_name})")).fetchall()
    if not rows:
        print(f"[DB-Migration] Tabelle nicht gefunden oder noch leer: {table_name}")
        return False

    existing = {row[1] for row in rows}
    if column_name in existing:
        return False

    db.session.execute(db.text(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}"))
    db.session.commit()
    print(f"[DB-Migration] Spalte angelegt: {table_name}.{column_name}")
    return True


def record_schema_migration(changes):
    """Dokumentiert automatische Schema-Migrationen ohne angemeldeten Benutzer."""
    if not changes:
        return

    try:
        details = "\n".join(changes)
        db.session.add(AuditLog(
            user_id=None,
            username="System",
            category="system",
            action="schema_migration",
            object_type="database",
            title="Automatische Datenbankmigration ausgeführt",
            details=details,
            new_value=details,
        ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"[DB-Migration] Revisionsprotokoll konnte nicht geschrieben werden: {exc}")


def migrate_schema_extensions():
    """Ergänzt fehlende Spalten in älteren SQLite-Datenbanken.

    Wichtig für ZIP-Updates: neuer Code darf nicht mit Internal Server Error
    starten, nur weil eine bestehende Vereinsdatenbank einzelne neue Spalten
    noch nicht besitzt.
    """
    changes = []

    def add(table, column, sql):
        if ensure_column(table, column, sql):
            changes.append(f"{table}.{column}")

    add("users", "email", "email VARCHAR(255)")
    add("users", "is_system_user", "is_system_user BOOLEAN NOT NULL DEFAULT 0")

    add("app_settings", "created_at", "created_at DATETIME")
    add("app_settings", "updated_at", "updated_at DATETIME")

    add("penalty_types", "target_mode", "target_mode VARCHAR(20) NOT NULL DEFAULT 'self'")
    add("monthly_contribution_payments", "paid_date", "paid_date DATE")
    add("members", "monthly_value_day", "monthly_value_day INTEGER")

    add("interest_bookings", "tax_rate_basis_points", "tax_rate_basis_points INTEGER DEFAULT 0")
    add("interest_bookings", "tax_cents", "tax_cents INTEGER DEFAULT 0")
    add("interest_bookings", "net_interest_cents", "net_interest_cents INTEGER DEFAULT 0")
    add("interest_bookings", "capital_gains_tax_cents", "capital_gains_tax_cents INTEGER DEFAULT 0")
    add("interest_bookings", "solidarity_tax_cents", "solidarity_tax_cents INTEGER DEFAULT 0")
    add("interest_bookings", "church_tax_cents", "church_tax_cents INTEGER DEFAULT 0")

    # Dokumente 2.0: Archivieren statt physisch löschen.
    add("documents", "deleted_at", "deleted_at DATETIME")
    add("documents", "deleted_by_user_id", "deleted_by_user_id INTEGER")

    # CSV-Kontoauszug-Import: technische Dublettenprüfung je Dokument/Zeile
    # (siehe routes/monthly_contributions.py, monthly_bank_closing_csv_import).
    add("cashbook_entries", "source_document_id", "source_document_id INTEGER")
    add("cashbook_entries", "source_row_index", "source_row_index INTEGER")

    # Kassenprüfer-/Bestätigungsfelder für bestehende SQLite-Datenbanken.
    for table_name in ["cash_audits", "annual_closings", "interest_settings", "interest_bookings"]:
        add(table_name, "confirmed_by_user_id", "confirmed_by_user_id INTEGER")
        add(table_name, "confirmed_at", "confirmed_at DATETIME")
        add(table_name, "auditor_note", "auditor_note TEXT")

    db.session.execute(db.text("UPDATE interest_bookings SET net_interest_cents = actual_interest_cents WHERE net_interest_cents IS NULL OR net_interest_cents = 0"))
    db.session.execute(db.text("UPDATE interest_bookings SET capital_gains_tax_cents = tax_cents WHERE (capital_gains_tax_cents IS NULL OR capital_gains_tax_cents = 0) AND tax_cents > 0"))
    db.session.commit()

    if changes:
        print("[DB-Migration] Angelegte Spalten: " + ", ".join(changes))
        record_schema_migration(changes)
    else:
        print("[DB-Migration] Schema aktuell, keine Änderungen nötig.")


def migrate_viewer_roles_to_member():
    """Alte Rollenbezeichnung viewer in member überführen."""
    changed = False
    for user in User.query.filter_by(role="viewer").all():
        user.role = "member"
        changed = True
    if changed:
        db.session.commit()


def ensure_interest_booking_cancel_columns():
    inspector = db.inspect(db.engine)
    columns = {
        column["name"]
        for column in inspector.get_columns("interest_bookings")
    }

    statements = []

    if "reversal_cashbook_entry_id" not in columns:
        statements.append("ALTER TABLE interest_bookings ADD COLUMN reversal_cashbook_entry_id INTEGER")

    if "is_cancelled" not in columns:
        statements.append("ALTER TABLE interest_bookings ADD COLUMN is_cancelled BOOLEAN NOT NULL DEFAULT 0")

    if "cancelled_at" not in columns:
        statements.append("ALTER TABLE interest_bookings ADD COLUMN cancelled_at DATETIME")

    if "cancelled_by_user_id" not in columns:
        statements.append("ALTER TABLE interest_bookings ADD COLUMN cancelled_by_user_id INTEGER")

    if "cancel_reason" not in columns:
        statements.append("ALTER TABLE interest_bookings ADD COLUMN cancel_reason TEXT")

    for statement in statements:
        db.session.execute(db.text(statement))

    if statements:
        db.session.commit()
