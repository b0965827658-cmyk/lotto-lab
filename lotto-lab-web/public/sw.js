const CACHE_NAME = "lotto-lab-product-v2";
const APP_SHELL = ["/", "/index.html", "/product.css?v=2", "/product-v2.css?v=2", "/product.js?v=2", "/manifest.webmanifest", "/icon.svg", "/icon-192.png", "/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))));
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(fetch(event.request));
    return;
  }
  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).then((response) => {
      caches.open(CACHE_NAME).then((cache) => cache.put("/", response.clone()));
      return response;
    }).catch(() => caches.match("/") || caches.match("/index.html")));
    return;
  }
  event.respondWith(fetch(event.request).then((response) => {
    caches.open(CACHE_NAME).then((cache) => cache.put(event.request, response.clone()));
    return response;
  }).catch(() => caches.match(event.request)));
});

function postReceipt(notificationId, state) {
  if (!notificationId) return Promise.resolve();
  return fetch("/api/notification-receipt", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ notification_id: notificationId, state }),
  }).catch(() => undefined);
}

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = { title: "Star Engine", summary: event.data ? event.data.text() : "New approved notification" };
  }
  const options = {
    body: payload.summary || payload.body || "New approved notification",
    icon: "/icon-192.png",
    badge: "/icon-192.png",
    tag: payload.notification_id || "star-engine-notification",
    renotify: false,
    requireInteraction: true,
    timestamp: Date.now(),
    data: { url: payload.url || "/#system", notification_id: payload.notification_id || "" },
  };
  event.waitUntil(Promise.all([
    self.registration.showNotification(payload.title || "Star Engine", options),
    postReceipt(payload.notification_id, "CLIENT_RECEIVED"),
  ]));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data?.url || "/#system";
  event.waitUntil(Promise.all([
    postReceipt(event.notification.data?.notification_id, "USER_OPENED"),
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      const existing = clientList.find((client) => client.url.includes(self.location.origin));
      if (existing) return existing.focus().then(() => existing.navigate(url));
      return clients.openWindow(url);
    }),
  ]));
});
