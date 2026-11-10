"""PWA-Grundlagen: Web-App-Manifest, Service Worker und Offline-Seite.

Bewusst schlank: Die App ist installierbar (Startbildschirm, eigenes Fenster
ohne Browserleiste). Offline gibt es nur eine freundliche Hinweisseite. Seiten
mit Vereins- und Finanzdaten werden NICHT zwischengespeichert - sie enthalten
personenbezogene Daten und müssen immer vom Server kommen (Rechte, Abmeldung).

Alle drei Routen sind ohne Anmeldung erreichbar, weil der Browser Manifest und
Service Worker auch vor dem Login abruft."""
from flask import jsonify, render_template, request, url_for, make_response

from extensions import app, APP_VERSION

# Endpunkte, die auch vor der Ersteinrichtung erreichbar sein müssen
# (siehe redirect_to_first_start_setup in app.py).
PWA_ENDPOINTS = ("pwa_manifest", "pwa_service_worker", "pwa_offline")


@app.route("/manifest.webmanifest")
def pwa_manifest():
    root = request.script_root or ""
    manifest = {
        "name": "Kegelkasse",
        "short_name": "Kegelkasse",
        "description": "Vereinsverwaltung für Kegelvereine: Kassenbuch, Kegelabende, Strafkonto.",
        "lang": "de",
        "start_url": f"{root}/",
        "scope": f"{root}/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#f5f7fb",
        "theme_color": "#122017",
        "icons": [
            {"src": url_for("static", filename="pwa/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": url_for("static", filename="pwa/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": url_for("static", filename="pwa/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    response = make_response(jsonify(manifest))
    response.headers["Content-Type"] = "application/manifest+json"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/service-worker.js")
def pwa_service_worker():
    # Muss unter "/" ausgeliefert werden, damit der Service Worker die ganze App
    # abdeckt (ein Pfad unter /static/ hätte nur diesen Scope).
    response = make_response(render_template(
        "service_worker.js",
        app_version=APP_VERSION,
        offline_url=url_for("pwa_offline"),
        static_prefix=url_for("static", filename=""),
    ))
    response.headers["Content-Type"] = "application/javascript; charset=utf-8"
    response.headers["Service-Worker-Allowed"] = (request.script_root or "") + "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/offline")
def pwa_offline():
    return render_template("offline.html")
