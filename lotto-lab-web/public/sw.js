const CACHE_NAME = "lotto-lab-v83";
const APP_SHELL = [
  "/",
  "/index.html",
  "/styles.css?v=69",
  "/app.js?v=81",
  "/manifest.webmanifest?v=45",
  "/logo-sniper-star.svg?v=45",
  "/logo-sniper-star-192.png?v=45",
  "/logo-sniper-star-512.png?v=45",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(fetch(event.request));
    return;
  }

  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put("/", copy));
          return response;
        })
        .catch(() => caches.match("/") || caches.match("/index.html")),
    );
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request)),
  );
});

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = { title: "摘星狙擊手開獎通知", body: event.data ? event.data.text() : "最新開獎已更新。" };
  }
  const title = payload.title || "摘星狙擊手開獎通知";
  const options = {
    body: payload.body || "最新開獎已更新。",
    icon: payload.icon || "/logo-sniper-star-192.png?v=45",
    badge: payload.badge || "/logo-sniper-star-192.png?v=45",
    tag: payload.tag || "lotto-lab-latest",
    data: {
      url: payload.url || "/",
    },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data?.url || "/";
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      const existing = clientList.find((client) => client.url.includes(self.location.origin));
      if (existing) {
        existing.focus();
        existing.navigate(url);
        return;
      }
      return clients.openWindow(url);
    }),
  );
});
