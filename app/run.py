"""Startet die Flask-App als richtig importiertes Modul statt als __main__.

Würde app.py direkt mit "python app.py" gestartet, liefe es unter dem
Modulnamen __main__ statt app - jeder spätere "from app import ..." innerhalb
eines Request-Handlers (genutzt um Zirkelimporte zwischen den routes/*.py-
Dateien zu vermeiden) würde dann app.py ein zweites Mal komplett neu
ausführen und beim erneuten Registrieren der Routen/Context-Processors
abstürzen. Über diesen Entrypoint wird app.py einmalig sauber als Modul
"app" importiert und bleibt danach im Cache (sys.modules).
"""
from app import app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
