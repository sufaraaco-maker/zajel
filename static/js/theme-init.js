// يُحمَّل في الترويسة قبل الرسم لتفادي وميض الوضع الفاتح. لا يعمل شيء آخر هنا.
(function () {
  try {
    var t = localStorage.getItem("arcms-theme");
    if (t === "dark" || t === "light") document.documentElement.setAttribute("data-theme", t);
    var s = localStorage.getItem("arcms-font");
    if (s) document.documentElement.style.setProperty("--body-size", s);
  } catch (e) {}
})();
