// مفاتيح الأمان (WebAuthn): التسجيل من «حسابي» والدخول في خطوة التحقق الثنائي.
(function () {
  "use strict";
  function $(s, r) { return (r || document).querySelector(s); }
  function b64u(buf) {
    var bytes = new Uint8Array(buf), s = "";
    for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function unb64u(str) {
    str = str.replace(/-/g, "+").replace(/_/g, "/");
    while (str.length % 4) str += "=";
    var bin = atob(str), out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out.buffer;
  }
  function csrf() {
    var m = document.cookie.match(/(?:^|; )arcms_csrf=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    var i = $("input[name=csrfmiddlewaretoken]");
    return i ? i.value : "";
  }
  function post(url, body) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error(j.error || "تعذّر إكمال العملية.");
        return j;
      });
    });
  }
  function showError(box, err) {
    var el = $("[data-key-error]", box);
    if (!el) return;
    var msg = err && err.name === "NotAllowedError" ? "أُلغيت العملية أو انتهت مهلتها." :
      err && err.name === "InvalidStateError" ? "هذا المفتاح مسجّل من قبل." : (err && err.message) || "تعذّر إكمال العملية.";
    el.textContent = msg;
    el.hidden = false;
  }
  var supported = !!(window.PublicKeyCredential && navigator.credentials);

  var reg = $("[data-key-register]");
  if (reg) {
    if (!supported) { $("[data-key-unsupported]", reg).hidden = false; $("button", reg).disabled = true; }
    reg.addEventListener("submit", function (e) {
      e.preventDefault();
      var btn = $("button", reg);
      btn.disabled = true;
      $("[data-key-error]", reg).hidden = true;
      post(reg.getAttribute("data-begin"), { name: reg.elements.name.value, password: reg.elements.password.value })
        .then(function (o) {
          o.challenge = unb64u(o.challenge);
          o.user.id = unb64u(o.user.id);
          o.excludeCredentials = (o.excludeCredentials || []).map(function (c) { return { type: c.type, id: unb64u(c.id) }; });
          return navigator.credentials.create({ publicKey: o });
        })
        .then(function (cred) {
          var r = cred.response;
          return post(reg.getAttribute("data-finish"), {
            clientDataJSON: b64u(r.clientDataJSON), attestationObject: b64u(r.attestationObject),
            transports: r.getTransports ? r.getTransports() : []
          });
        })
        .then(function () { window.location.reload(); })
        .catch(function (err) { showError(reg, err); btn.disabled = false; });
    });
  }

  var login = $("[data-key-login]");
  if (login) {
    var start = $("[data-key-start]", login);
    if (!supported) { $("[data-key-unsupported]", login).hidden = false; start.disabled = true; return; }
    start.addEventListener("click", function () {
      start.disabled = true;
      $("[data-key-error]", login).hidden = true;
      post(login.getAttribute("data-begin"))
        .then(function (o) {
          o.challenge = unb64u(o.challenge);
          o.allowCredentials = (o.allowCredentials || []).map(function (c) { return { type: c.type, id: unb64u(c.id) }; });
          return navigator.credentials.get({ publicKey: o });
        })
        .then(function (cred) {
          var r = cred.response;
          return post(login.getAttribute("data-finish"), {
            id: b64u(cred.rawId), clientDataJSON: b64u(r.clientDataJSON),
            authenticatorData: b64u(r.authenticatorData), signature: b64u(r.signature)
          });
        })
        .then(function (res) { window.location.href = res.redirect || "/studio/"; })
        .catch(function (err) { showError(login, err); start.disabled = false; });
    });
  }
})();
