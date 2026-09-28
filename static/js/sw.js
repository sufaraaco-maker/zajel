// عامل الخدمة: يعرض إشعارات العاجل فقط. لا تخزين مؤقت للصفحات ولا تتبع.
self.addEventListener("push", function (event) {
  var data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { title: "خبر جديد", body: event.data && event.data.text() }; }
  var url = data.url || "/";
  url += (url.indexOf("?") === -1 ? "?" : "&") + "utm_source=push";
  event.waitUntil(
    self.registration.showNotification(data.title || "خبر جديد", {
      body: data.body || "",
      icon: data.icon,
      image: data.image || undefined,
      tag: data.tag,
      dir: "rtl",
      lang: "ar",
      data: { url: url }
    })
  );
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(clients.openWindow(url));
});
