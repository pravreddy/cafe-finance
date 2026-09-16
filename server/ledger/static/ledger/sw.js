// Minimal service worker: enough for Chrome to treat the site as an installable
// PWA (which is what puts it in the Android share sheet). Share-target POSTs
// go straight to the server; we never intercept them. Only the shell is cached.
const CACHE = 'cafe-finance-v1';
const SHELL = ['/offline/', '/static/ledger/icon-192.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;              // share POSTs, form posts: hands off
  const url = new URL(e.request.url);
  if (url.pathname.startsWith('/proofs/') || url.pathname.startsWith('/api/')) return;
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request).then((r) => r || caches.match('/offline/')))
  );
});
