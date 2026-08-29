/**
 * Wickham Roofing Field App — Offline-First Service Worker v2
 *
 * Strategy:
 * - App shell (HTML, CSS, manifest): Cache-first. Served instantly offline.
 * - API POST /api/field/leads: Intercepted. Stored in IndexedDB queue.
 *   Flushed via Background Sync when connection restores.
 * - All other API calls: Network-first with silent failure fallback.
 */

const CACHE_NAME = 'field-app-shell-v2';
const SYNC_TAG = 'field-lead-sync';
const IDB_NAME = 'wickham-field-queue';
const IDB_STORE = 'pending-submissions';

const APP_SHELL = [
    '/field',
    '/static/manifest.json',
    'https://cdn.tailwindcss.com',
];

// ── Install: cache app shell ────────────────────────────────────────────
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.addAll(APP_SHELL))
    );
    self.skipWaiting();
});

// ── Activate: purge old caches ──────────────────────────────────────────
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(
                keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
            )
        )
    );
    self.clients.claim();
});

// ── IndexedDB helpers ───────────────────────────────────────────────────
function openDB() {
    return new Promise((resolve, reject) => {
        const req = indexedDB.open(IDB_NAME, 1);
        req.onupgradeneeded = e => {
            e.target.result.createObjectStore(
                IDB_STORE, { keyPath: 'id', autoIncrement: true }
            );
        };
        req.onsuccess = e => resolve(e.target.result);
        req.onerror = e => reject(e.target.error);
    });
}

async function queueRequest(requestData) {
    const db = await openDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(IDB_STORE, 'readwrite');
        tx.objectStore(IDB_STORE).add({
            url: requestData.url,
            method: requestData.method,
            headers: requestData.headers,
            body: requestData.body,
            queuedAt: Date.now(),
        });
        tx.oncomplete = () => resolve();
        tx.onerror = e => reject(e.target.error);
    });
}

async function flushQueue() {
    const db = await openDB();
    const records = await new Promise((resolve, reject) => {
        const tx = db.transaction(IDB_STORE, 'readonly');
        const req = tx.objectStore(IDB_STORE).getAll();
        req.onsuccess = e => resolve(e.target.result);
        req.onerror = e => reject(e.target.error);
    });

    for (const record of records) {
        try {
            const res = await fetch(record.url, {
                method: record.method,
                headers: record.headers,
                body: record.body,
            });
            if (res.ok) {
                // Delete only on confirmed server acceptance
                const tx = db.transaction(IDB_STORE, 'readwrite');
                tx.objectStore(IDB_STORE).delete(record.id);
                await new Promise(r => { tx.oncomplete = r; });
            }
        } catch (e) {
            // Network still down — leave record in queue for next sync
            console.warn('[SW] Flush failed for record', record.id, e);
        }
    }
}

// ── Background Sync ─────────────────────────────────────────────────────
self.addEventListener('sync', (event) => {
    if (event.tag === SYNC_TAG) {
        event.waitUntil(flushQueue());
    }
});

// ── Fetch Interception ──────────────────────────────────────────────────
self.addEventListener('fetch', (event) => {
    const { request } = event;
    const url = new URL(request.url);

    // 1. App shell: cache-first
    if (APP_SHELL.includes(url.pathname) || url.pathname === '/field') {
        event.respondWith(
            caches.match(request).then(cached => cached || fetch(request))
        );
        return;
    }

    if (url.pathname.startsWith('/api/field/') && request.method === 'POST') {
        return; // Exclude from SW handling, let the browser handle it natively
    }

    // 3. All other requests: network-first, silent fail
    event.respondWith(
        fetch(request).catch(() => {
            return new Response(
                JSON.stringify({ error: 'offline' }),
                {
                    status: 503,
                    headers: { 'Content-Type': 'application/json' }
                }
            );
        })
    );
});

// ── Web Push Event ──────────────────────────────────────────────────────
self.addEventListener('push', (event) => {
    let data = { title: 'Wickham CRM Alert', body: 'New update received.' };
    if (event.data) {
        try {
            data = event.data.json();
        } catch (e) {
            data.body = event.data.text();
        }
    }

    const options = {
        body: data.body || 'New notification',
        icon: '/static/icons/icon-192.png',
        badge: '/static/icons/icon-192.png',
        data: data.data || {},
        vibrate: [100, 50, 100],
        requireInteraction: true
    };

    event.waitUntil(
        self.registration.showNotification(data.title || 'Wickham Roofing CRM', options)
    );
});

// ── Notification Click Event ────────────────────────────────────────────
self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const targetUrl = (event.notification.data && event.notification.data.url) || '/field';

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
            for (const client of windowClients) {
                if (client.url.includes(targetUrl) && 'focus' in client) {
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(targetUrl);
            }
        })
    );
});

