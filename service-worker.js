// Service worker: instalar o app, abrir offline (página em cache) e receber notificações.
// Rede primeiro: com internet vem sempre a versão nova; sem internet usa a última guardada.
const CACHE = "financas-v1";
const BASICOS = ["./", "index.html", "manifest.json", "icon-192.png", "icon-512.png"];
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => Promise.all(BASICOS.map((u) => c.add(u).catch(() => {})))).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => e.waitUntil(
  caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())
));
self.addEventListener("fetch", (e) => {
  const r = e.request;
  if (r.method !== "GET") return;
  const u = new URL(r.url);
  if (u.origin !== location.origin) return; // a API nunca passa pelo cache do SW
  e.respondWith(fetch(r).then((res) => {
    if (res.ok) { const copia = res.clone(); caches.open(CACHE).then((c) => c.put(r, copia)); }
    return res;
  }).catch(() => caches.match(r).then((hit) => hit || (r.mode === "navigate" ? caches.match("index.html") : Response.error()))));
});

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { /* sem corpo */ }
  e.waitUntil(self.registration.showNotification(d.titulo || "Minhas Finanças", {
    body: d.corpo || "",
    icon: "icon-192.png",
    badge: "icon-192.png",
    tag: d.tag || "financas",
    data: { url: d.url || "./" },
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((janelas) => {
    for (const j of janelas) if ("focus" in j) return j.focus();
    return self.clients.openWindow((e.notification.data && e.notification.data.url) || "./");
  }));
});
