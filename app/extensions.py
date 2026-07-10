"""Erstellt die zentralen Flask-Objekte (App, DB-Bindung, Login-Manager) sowie
ein paar App-weit genutzte Konstanten. Wird von app.py und allen
services/*.py- und routes/*.py-Modulen importiert."""
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_login import LoginManager

from config import Config
from models import db

app = Flask(__name__)
app.config.from_object(Config)

# Hinter einem Reverse Proxy (z. B. Synology) läuft die App intern über HTTP.
# Damit generierte externe URLs (z. B. für OAuth-Redirects) das richtige
# https://... und den richtigen Hostnamen bekommen, X-Forwarded-* auswerten.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Bitte zuerst anmelden."
login_manager.init_app(app)

APP_VERSION = "0.99.2"

RATE_TYPES = {
    "monthly_fee": "Monatsbeitrag",
    "guest_fee": "Gastgebühr",
    "absence_excused": "Fehlen entschuldigt",
    "absence_unexcused": "Fehlen unentschuldigt",
    "lane_cost_default": "Bahnkosten Standard",
    "penalty_pump": "Pumpe",
    "penalty_wreath": "Kranz",
    "penalty_all_nine": "Alle Neune",
    "penalty_lost_game": "Verlorenes Spiel",
}

# Grundeinstellungen ohne Strafarten. Strafarten werden separat im Modul "Strafarten" gepflegt.
BASE_RATE_KEYS = [
    "monthly_fee",
    "guest_fee",
    "absence_excused",
    "absence_unexcused",
    "lane_cost_default",
]
