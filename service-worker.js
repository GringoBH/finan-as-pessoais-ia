// Service worker: permite instalar o app e receber notificações (avisos de orçamento).
// Não guarda nada offline, por isso os dados e a página vêm sempre atualizados do servidor.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});

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
