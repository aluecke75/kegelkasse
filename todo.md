# Todo — Kegelkasse

## Neue Todos (hier eintragen)

Hier einfach mit `- [ ] ...` neue Punkte ergänzen, egal ob Bug oder Feature-Wunsch.
Wird in der nächsten Session gelesen und abgearbeitet, erledigte Punkte werden auf
`[x]` gesetzt statt gelöscht.

- [x] v0.99.17 fertig committen: Dashboard-Fix "Meine Monatsbeiträge" (Mitglieder landeten auf gesperrter Admin-Seite) + neues Update-Check-Feature (`app/services/update_check.py`) liegen bereits fertig im Arbeitsverzeichnis, aber noch uncommitted.
- [x] GitHub-Repo `aluecke75/kegelkasse` anlegen und eine erste Release veröffentlichen — Repo existiert seit 2026-06-14, Release `v0.99.18` seit 2026-07-27 veröffentlicht. Der Eintrag hier hatte am 2026-07-29 fälschlich "öffentlich abrufbar" behauptet, obwohl das Repo tatsächlich noch **privat** war (die vorherige Prüfung lief offenbar authentifiziert, was auch bei privaten Repos erfolgreich antwortet). Am 2026-07-30 unauthentifiziert echt verifiziert: Repo war zu dem Zeitpunkt noch privat (404), dann auf öffentlich umgestellt und direkt im Anschluss unauthentifiziert erneut bestätigt (Repo + Release `v0.99.18` beide öffentlich abrufbar).
- [ ] GHCR-Build-Pipeline fertigstellen, damit Fremde Kegelkasse ohne eigenes DeveloperKit-Token installieren können (`.github/workflows/build-and-push.yml`, `compose.yml` auf `image: ghcr.io/aluecke75/kegelkasse` umgestellt, README/.env.example angepasst — Commit `97bc016` liegt lokal fertig vor, main ist 1 Commit vor `origin/main`):
  - [ ] **Push blockiert:** Das hier hinterlegte GitHub-Zugangstoken hat keinen `workflow`-Scope, GitHub lehnt Pushes ab, die `.github/workflows/*` ändern. Braucht ein PAT mit Scopes `repo` + `workflow` (klassisch) bzw. "Contents: Read and write" + "Workflows: write" (fine-grained), dann `git push origin main`. Erneut versucht am 2026-08-01 (gleiches Token erneut eingefügt) — weiterhin derselbe Fehler, der Scope wurde also noch nicht ergänzt. Auf Nutzerwunsch bis auf Weiteres zurückgestellt ("komm ich jetzt nicht zu"). main liegt lokal 2 Commits vor `origin/main` (`97bc016`, `8331d16`), nichts davon ist verloren.
  - [ ] Repository-Secret `DEVELOPERKIT_TOKEN` in den Actions-Einstellungen von `aluecke75/kegelkasse` anlegen (fine-grained PAT, nur "Contents: Read-only" auf `aluecke75/DeveloperKit`).
  - [ ] Nach erstem erfolgreichem Workflow-Lauf (ausgelöst durch nächstes veröffentlichtes Release): GHCR-Package `kegelkasse` auf Sichtbarkeit "Public" stellen (github.com → Profil → Packages → kegelkasse → Package settings).
  - [ ] Erst danach die geänderte `compose.yml` per `git pull` auf den produktiven Server holen und `docker compose pull && docker compose up -d` ausführen — vorher würde das Pull mangels vorhandenem Image in der Registry fehlschlagen.
- [ ] Gitea-Zugangstoken für `aluecke75` (Host `192.168.178.2:8201`, Scope `write:repository`) rotieren — Token wurde am 2026-08-01 versehentlich im Klartext in einer Claude-Code-Session sichtbar (unvollständige Redaction einer http://-Zeile in `~/.git-credentials`). Alt-Token über Gitea-Weboberfläche widerrufen, neues erzeugen, `~/.git-credentials` aktualisieren.

## Hinweis

Die Release-Checkliste für die öffentliche Version steht weiterhin in
`PUBLIC_RELEASE_TODO.md` — diese Datei hier ist für laufende Todos zwischen den
Sessions.
