// Service worker mínimo: permite instalar o app. Não guarda nada offline,
// por isso os dados e a página vêm sempre atualizados do servidor.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
