/* StockLens 서비스워커 — 탭이 닫혀 있어도 이 코드가 푸시를 받아 알림을 띄운다. */

self.addEventListener("install", function (event) {
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", function (event) {
  var data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: "StockLens", body: event.data ? event.data.text() : "" };
  }

  var options = {
    body: data.body || "",
    tag: data.tag || "stocklens",
    renotify: !!data.renotify,
    timestamp: Date.now(),
    data: { url: data.url || "/" },
  };

  event.waitUntil(
    self.registration.showNotification(data.title || "StockLens", options)
  );
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var target = (event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (list) {
      for (var i = 0; i < list.length; i++) {
        var client = list[i];
        if ("focus" in client) {
          client.focus();
          if ("navigate" in client) client.navigate(target);
          return;
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(target);
    })
  );
});
