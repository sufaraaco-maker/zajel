// سلوك صفحات القرّاء. بلا مكتبات خارجية، ويعمل الموقع كاملاً دونه.
(function () {
  "use strict";
  var doc = document.documentElement;
  var body = document.body;

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function csrf() {
    var m = document.cookie.match(/(?:^|; )arcms_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }
  function store(k, v) { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) {} }
  function read(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }

  // --- الوضع الداكن ---
  var themeBtn = $("[data-theme-toggle]");
  function currentTheme() {
    var t = doc.getAttribute("data-theme");
    if (t) return t;
    return window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function syncThemeIcon() {
    if (!themeBtn) return;
    var use = themeBtn.querySelector("use");
    if (use) use.setAttribute("href", use.getAttribute("href").replace(/#.*$/, currentTheme() === "dark" ? "#sun" : "#moon"));
    themeBtn.setAttribute("aria-label", currentTheme() === "dark" ? "الوضع الفاتح" : "الوضع الداكن");
  }
  if (themeBtn) {
    syncThemeIcon();
    themeBtn.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      doc.setAttribute("data-theme", next);
      store("arcms-theme", next);
      syncThemeIcon();
    });
  }

  // --- القائمة على الجوال والبحث ---
  var menuBtn = $("[data-menu-toggle]");
  if (menuBtn) menuBtn.addEventListener("click", function () {
    var nav = $(".main-nav");
    var open = nav.classList.toggle("open");
    menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
  });
  var searchBtn = $("[data-search-toggle]");
  if (searchBtn) searchBtn.addEventListener("click", function () {
    var f = $(".search-form");
    if (window.innerWidth > 760) { f.querySelector("input").focus(); return; }
    f.classList.toggle("open");
    if (f.classList.contains("open")) f.querySelector("input").focus();
  });

  // --- شريط العاجل: سرعة ثابتة مهما طال النص، وتكرار سلس ---
  var items = $(".ticker-items");
  if (items && items.children.length) {
    var clone = items.innerHTML;
    items.innerHTML = clone + clone;
    var width = items.scrollWidth / 2;
    items.style.setProperty("--ticker-duration", Math.max(20, Math.round(width / 60)) + "s");
  }

  // --- حجم خط المتن ---
  $$("[data-font]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var cur = parseFloat(getComputedStyle(doc).getPropertyValue("--body-size")) || 1.18;
      var next = Math.min(1.6, Math.max(0.95, cur + (btn.getAttribute("data-font") === "up" ? 0.08 : -0.08)));
      doc.style.setProperty("--body-size", next.toFixed(2) + "rem");
      store("arcms-font", next.toFixed(2) + "rem");
    });
  });

  // --- نسخ الرابط المختصر ---
  $$("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var text = btn.getAttribute("data-copy");
      var done = function () {
        btn.classList.add("copied");
        btn.setAttribute("title", "نُسخ الرابط");
        setTimeout(function () { btn.classList.remove("copied"); }, 1600);
      };
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(done);
      else { var t = document.createElement("textarea"); t.value = text; body.appendChild(t); t.select(); document.execCommand("copy"); t.remove(); done(); }
    });
  });
  $$("[data-print]").forEach(function (b) { b.addEventListener("click", function () { window.print(); }); });
  $$("[data-share-native]").forEach(function (b) {
    if (!navigator.share) { b.hidden = true; return; }
    b.addEventListener("click", function () { navigator.share({ title: document.title, url: b.getAttribute("data-share-native") }); });
  });

  // --- قياس الجمهور: طلب واحد صغير، بلا كوكيز، إلى خادمنا فقط ---
  if (body.hasAttribute("data-track")) {
    var params = new URLSearchParams(location.search);
    var payload = JSON.stringify({
      p: location.pathname,
      r: document.referrer || "",
      a: body.getAttribute("data-article") || "",
      c: body.getAttribute("data-category") || "",
      u: params.get("utm_source") || ""
    });
    var send = function () {
      if (navigator.sendBeacon) navigator.sendBeacon("/a/v", new Blob([payload], { type: "application/json" }));
      else fetch("/a/v", { method: "POST", body: payload, keepalive: true, headers: { "Content-Type": "application/json" } });
    };
    if (document.visibilityState === "prerender") document.addEventListener("visibilitychange", send, { once: true });
    else send();
  }

  // --- التغطية المباشرة: جلب التحديثات الجديدة كل 30 ثانية ---
  var feed = $("[data-live-feed]");
  if (feed) {
    var status = $("[data-live-status]");
    var poll = function () {
      var first = feed.querySelector("[data-entry-id]:not(.pinned)");
      var last = 0;
      $$("[data-entry-id]", feed).forEach(function (el) { last = Math.max(last, +el.getAttribute("data-entry-id")); });
      fetch(feed.getAttribute("data-live-feed") + "?after=" + last, { headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          data.entries.forEach(function (e) {
            var tmp = document.createElement("div");
            tmp.innerHTML = e.html.trim();
            var node = tmp.firstElementChild;
            node.classList.add("new");
            feed.insertBefore(node, first || feed.firstChild);
            first = node;
          });
          if (status && data.entries.length) status.textContent = "وصلت " + data.entries.length + " تحديثات جديدة";
          if (data.is_live) setTimeout(poll, 30000);
          else if (status) status.textContent = "انتهت التغطية المباشرة";
        })
        .catch(function () { setTimeout(poll, 60000); });
    };
    setTimeout(poll, 30000);
  }

  // --- الإشعارات الفورية ---
  var bell = $("[data-push]");
  function b64ToBytes(b64) {
    var pad = "=".repeat((4 - (b64.length % 4)) % 4);
    var raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }
  if (bell && "serviceWorker" in navigator && "PushManager" in window) {
    fetch("/push/config").then(function (r) { return r.json(); }).then(function (cfg) {
      if (!cfg.enabled) return;
      bell.hidden = false;
      navigator.serviceWorker.register("/sw.js").then(function (reg) {
        reg.pushManager.getSubscription().then(function (sub) {
          if (sub) bell.classList.add("on");
          bell.addEventListener("click", function () {
            if (bell.classList.contains("on")) {
              reg.pushManager.getSubscription().then(function (s) {
                if (!s) return;
                fetch("/push/unsubscribe", { method: "POST", body: JSON.stringify({ endpoint: s.endpoint }) });
                s.unsubscribe(); bell.classList.remove("on");
                bell.setAttribute("title", "فعّل إشعارات العاجل");
              });
              return;
            }
            reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(cfg.key) }).then(function (s) {
              fetch("/push/subscribe", { method: "POST", body: JSON.stringify(s.toJSON()) });
              bell.classList.add("on");
              bell.setAttribute("title", "الإشعارات مفعّلة");
            }).catch(function () {});
          });
        });
      });
    });
  }
  // --- الشرائط المتحركة: أزرار ونقاط فوق التمرير الأصلي (يعمل السحب دون سكربت) ---
  $$("[data-carousel]").forEach(function (root) {
    var track = $(".carousel-track", root);
    var nav = $(".carousel-nav", root);
    var dotsBox = $("[data-carousel-dots]", root);
    var prev = $("[data-carousel-prev]", root);
    var next = $("[data-carousel-next]", root);
    if (!track || !nav) return;
    var rtl = getComputedStyle(track).direction === "rtl";
    function pages() { return Math.max(1, Math.round(track.scrollWidth / Math.max(1, track.clientWidth))); }
    function current() { return Math.round(Math.abs(track.scrollLeft) / Math.max(1, track.clientWidth)); }
    function go(i) {
      var x = Math.max(0, Math.min(i, pages() - 1)) * track.clientWidth;
      track.scrollTo({ left: rtl ? -x : x, behavior: "smooth" });
    }
    function build() {
      var n = pages();
      nav.hidden = n < 2;
      dotsBox.innerHTML = "";
      for (var i = 0; i < n; i++) {
        var d = document.createElement("button");
        d.type = "button";
        d.setAttribute("aria-label", "الصفحة " + (i + 1));
        d.addEventListener("click", go.bind(null, i));
        dotsBox.appendChild(d);
      }
      sync();
    }
    function sync() {
      var c = current(), n = pages();
      $$("button", dotsBox).forEach(function (d, i) { d.setAttribute("aria-current", i === c ? "true" : "false"); });
      if (prev) prev.disabled = c <= 0;
      if (next) next.disabled = c >= n - 1;
    }
    if (prev) prev.addEventListener("click", function () { go(current() - 1); });
    if (next) next.addEventListener("click", function () { go(current() + 1); });
    var t;
    track.addEventListener("scroll", function () { clearTimeout(t); t = setTimeout(sync, 80); }, { passive: true });
    window.addEventListener("resize", function () { clearTimeout(t); t = setTimeout(build, 150); });
    build();
  });

  // --- مشغّل الفيديو: لا يُطلب شيء من منصة الفيديو قبل ضغط القارئ ---
  $$("[data-video-embed]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var src = btn.getAttribute("data-video-embed");
      if (!/^https:\/\//.test(src)) return;
      var f = document.createElement("iframe");
      f.src = src + (src.indexOf("?") >= 0 ? "&" : "?") + "autoplay=1";
      f.title = btn.getAttribute("aria-label") || "فيديو";
      f.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
      f.referrerPolicy = "strict-origin-when-cross-origin";
      f.allowFullscreen = true;
      var box = document.createElement("div");
      box.className = "video-facade";
      box.appendChild(f);
      btn.replaceWith(box);
    }, { once: true });
  });

  // --- تبويبات (الأكثر قراءة اليوم / هذا الأسبوع) ---
  $$(".tabs-inline").forEach(function (list) {
    var tabs = $$("[data-tab]", list);
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        tabs.forEach(function (t) {
          var on = t === tab;
          t.setAttribute("aria-selected", on ? "true" : "false");
          var panel = document.getElementById(t.getAttribute("data-tab"));
          if (panel) panel.hidden = !on;
        });
      });
    });
  });
})();
