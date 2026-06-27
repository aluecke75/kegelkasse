import os
from pathlib import Path


DATABASE_DIR = Path(os.getenv("DATABASE_DIR", "/app/database"))
ACTIVE_DATABASE_FILE = DATABASE_DIR / "active_database.txt"

TEST_DATABASE_PROFILES = {
    "production": {
        "label": "Echte Vereinsdatenbank",
        "description": "Produktive Datenbank. Diese Daten bleiben geschützt.",
        "database": "kegelkasse.db",
        "documents": "/app/uploads/documents",
        "is_test": False,
    },
    "demo": {
        "label": "Demo-Datenbank",
        "description": "Getrennte Testdatenbank für Demo- und Beispieldaten.",
        "database": "kegelkasse_demo.db",
        "documents": "/app/uploads/documents_demo",
        "is_test": True,
    },
    "setup_empty": {
        "label": "Leere Testdatenbank – Setup-Assistent",
        "description": "Leere Datenbank zum Prüfen der Ersteinrichtung.",
        "database": "kegelkasse_setup_test.db",
        "documents": "/app/uploads/documents_setup_test",
        "is_test": True,
    },
    "import_empty": {
        "label": "Leere Testdatenbank – Import-Test",
        "description": "Leere Datenbank zum Prüfen von Vereinsimporten.",
        "database": "kegelkasse_import_test.db",
        "documents": "/app/uploads/documents_import_test",
        "is_test": True,
    },
}


def normalize_database_profile(profile):
    profile = (profile or "production").strip()
    return profile if profile in TEST_DATABASE_PROFILES else "production"


def get_active_database_profile():
    env_profile = os.getenv("KEGELKASSE_DATABASE_PROFILE")
    if env_profile:
        return normalize_database_profile(env_profile)

    try:
        if ACTIVE_DATABASE_FILE.exists():
            return normalize_database_profile(ACTIVE_DATABASE_FILE.read_text(encoding="utf-8").strip())
    except OSError:
        pass
    return "production"


def set_active_database_profile(profile):
    profile = normalize_database_profile(profile)
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_DATABASE_FILE.write_text(profile, encoding="utf-8")
    return profile


def get_database_path(profile=None):
    profile = normalize_database_profile(profile or get_active_database_profile())
    return DATABASE_DIR / TEST_DATABASE_PROFILES[profile]["database"]


def get_document_dir(profile=None):
    profile = normalize_database_profile(profile or get_active_database_profile())
    return Path(TEST_DATABASE_PROFILES[profile]["documents"])


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

    SQLALCHEMY_DATABASE_URI = f"sqlite:///{get_database_path()}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
