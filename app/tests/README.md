# Kegelkasse-Testskripte

`run_checks.py` prüft nach einer Änderung schnell, ob nichts Grundlegendes
kaputtgegangen ist - sowohl lesende Seiten (GET) als auch die wichtigsten
schreibenden Abläufe (Formular absenden, Datenbank ändern). Ein reiner
GET-Test hätte den `audit_log`-Importfehler aus v0.99.9 nie gefunden, weil
der erst auftrat, sobald tatsächlich etwas gespeichert wurde.

## Ausführen

Im laufenden Container (Änderungen davor per `docker compose up -d --build
--force-recreate` einspielen, sonst wird der alte Stand getestet):

```
docker exec kegelkasse python3 tests/run_checks.py
```

Exit-Code `0` = alles bestanden, `1` = mindestens eine Prüfung fehlgeschlagen
(Details stehen in der Ausgabe).

## Was geprüft wird

- Alle wichtigen Seiten per GET (Status 200)
- Eigenes Konto bearbeiten (E-Mail ändern, danach zurücksetzen)
- Passwort-vergessen-Formular
- Eine Admin-Einstellung ändern und zurücksetzen
- Lokale Sicherung erstellen, prüfen, löschen
- Kegelabend anlegen und als ausgefallen markieren, danach aufräumen

Jeder Test räumt seine eigenen Testdaten wieder auf. Der volle
Kegelabend-Abrechnungsablauf (Barzahlungen, Bahnkosten) ist bewusst nicht
abgedeckt, da er viele dynamisch benannte Teilnehmerfelder braucht - der
wurde beim Bau des Features bereits ausführlich manuell getestet.

## Wenn ein neues Feature dazukommt

Bei einer neuen schreibenden Aktion (Formular, das etwas in der Datenbank
ändert) idealerweise eine `check_*`-Funktion nach demselben Muster ergänzen:
POST absenden, auf Status 200 und "kein Internal-Server-Error" prüfen,
danach den ursprünglichen Zustand wiederherstellen.
