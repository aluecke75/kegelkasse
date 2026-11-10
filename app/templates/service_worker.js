/* Kegelkasse Service Worker (Version {{ app_version }})
 *
 * Bewusst minimal:
 *  - Seitenaufrufe gehen IMMER ans Netz. Fällt das Netz aus, erscheint die
 *    Offline-Seite. Vereins- und Finanzdaten werden nie zwischengespeichert.
 *  - Nur statische Dateien (/static/...: Icons, Diagramm-Skript) werden
 *    zwischengespeichert, damit die App schneller startet.
 *  - Alles andere (POST, Downloads, API) läuft unverändert am Service Worker
 *    vorbei.
 * Der Cache-Name enthält die App-Version: Nach einem Update werden alte
 * Caches beim Aktivieren gelöscht.
 */
const CACHE = "kegelkasse-{{ app_version }}";
const OFFLINE_URL = {{ offline_url|tojson }};
const STATIC_PREFIX = {{ static_prefix|tojson }};
const PRECACHE = [
    OFFLINE_URL,
    STATIC_PREFIX + "pwa/icon-192.png",
    STATIC_PREFIX + "pwa/icon-512.png"
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE)
            .then((cache) => cache.addAll(PRECACHE))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(
                keys.filter((k) => k.startsWith("kegelkasse-") && k !== CACHE)
                    .map((k) => caches.delete(k))
            ))
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    const request = event.request;
    if (request.method !== "GET") {
        return;
    }
    const url = new URL(request.url);
    if (url.origin !== self.location.origin) {
        return;
    }

    // Seitenaufrufe: immer Netz, nur bei Ausfall die Offline-Seite.
    if (request.mode === "navigate") {
        event.respondWith(
            fetch(request).catch(() => caches.match(OFFLINE_URL))
        );
        return;
    }

    // Statische Dateien: aus dem Cache, sonst holen und merken.
    if (url.pathname.startsWith(STATIC_PREFIX)) {
        event.respondWith(
            caches.match(request).then((cached) => {
                if (cached) {
                    return cached;
                }
                return fetch(request).then((response) => {
                    if (response && response.ok) {
                        const copy = response.clone();
                        caches.open(CACHE).then((cache) => cache.put(request, copy));
                    }
                    return response;
                });
            })
        );
    }
});
