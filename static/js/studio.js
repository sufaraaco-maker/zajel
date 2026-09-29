// غرفة التحرير: المحرر، مكتبة الوسائط، الوسوم، الحفظ التلقائي، أقفال التحرير.
(function () {
  "use strict";
  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function csrf() {
    var m = document.cookie.match(/(?:^|; )arcms_csrf=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    var i = $("input[name=csrfmiddlewaretoken]");
    return i ? i.value : "";
  }
  function post(url, data, json) {
    var opts = { method: "POST", headers: { "X-CSRFToken": csrf() }, credentials: "same-origin" };
    if (json) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(data); }
    else opts.body = data;
    return fetch(url, opts);
  }
  function el(tag, attrs, text) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (text) n.textContent = text;
    return n;
  }

  // --- عام ---
  $$("[data-confirm]").forEach(function (node) {
    var ev = node.tagName === "FORM" ? "submit" : "click";
    node.addEventListener(ev, function (e) { if (!confirm(node.getAttribute("data-confirm"))) e.preventDefault(); });
  });
  var sideBtn = $("[data-side-toggle]");
  if (sideBtn) sideBtn.addEventListener("click", function () { $("#side").classList.toggle("open"); });
  $$("[data-close-modal]").forEach(function (b) { b.addEventListener("click", function () { b.closest("dialog").close(); }); });

  // --- عدّادات الأحرف ---
  $$("[data-counter-for]").forEach(function (c) {
    var input = document.getElementById(c.getAttribute("data-counter-for"));
    var soft = +c.getAttribute("data-soft");
    if (!input) return;
    var upd = function () {
      var n = input.value.length;
      c.textContent = n + " حرفاً" + (soft ? " (المستحسن حتى " + soft + ")" : "");
      c.classList.toggle("over", soft && n > soft);
    };
    input.addEventListener("input", upd); upd();
  });

  // --- مكتبة الوسائط (نافذة الاختيار) ---
  var modal = $("#media-modal");
  var pickCallback = null, multi = false, nextOffset = 0, searchTimer = null;
  function renderItems(items, append) {
    var grid = $("#media-grid");
    if (!append) grid.innerHTML = "";
    items.forEach(function (a) {
      var b = el("button", { type: "button", "class": "media-item", title: a.caption || "" });
      var img = el("img", { src: a.thumb, alt: a.alt || "", loading: "lazy" });
      b.appendChild(img);
      b.appendChild(el("div", { "class": "cap" }, a.caption || a.credit || ("#" + a.id)));
      b.addEventListener("click", function () {
        if (pickCallback) pickCallback(a);
        if (!multi) modal.close();
        else b.style.outline = "3px solid var(--ok)";
      });
      grid.appendChild(b);
    });
  }
  function loadMedia(offset) {
    var q = $("#media-search").value.trim();
    fetch("/studio/media/picker/?offset=" + offset + "&q=" + encodeURIComponent(q), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        renderItems(data.items, offset > 0);
        nextOffset = data.next;
        $("#media-more").hidden = !data.next;
      });
  }
  function uploadFiles(files) {
    var status = $("#media-status");
    var fd = new FormData();
    Array.prototype.forEach.call(files, function (f) { fd.append("file", f); });
    fd.append("caption", $("#media-caption").value);
    fd.append("credit", $("#media-credit").value);
    fd.append("json", "1");
    status.textContent = "جارٍ الرفع ونزع البيانات المخفية…";
    return post("/studio/media/upload/", fd).then(function (r) { return r.json(); }).then(function (data) {
      var removed = [];
      data.items.forEach(function (a) { (a.removed || []).forEach(function (x) { if (removed.indexOf(x) < 0) removed.push(x); }); });
      status.textContent = data.items.length ? ("رُفعت " + data.items.length + " صورة." + (removed.length ? " نُزع منها: " + removed.join("، ") : " لم تحمل بيانات مخفية.")) : "";
      if (data.errors && data.errors.length) status.textContent += " " + data.errors.join(" | ");
      renderItems(data.items, false);
      if (data.items.length === 1 && pickCallback && !multi) { pickCallback(data.items[0]); modal.close(); }
      loadMedia(0);
      return data;
    });
  }
  function openPicker(cb, isMulti) {
    if (!modal) return;
    pickCallback = cb; multi = !!isMulti;
    $("#media-status").textContent = "";
    modal.showModal();
    loadMedia(0);
  }
  if (modal) {
    var drop = $("#media-drop"), file = $("#media-file");
    drop.addEventListener("click", function (e) { if (e.target !== file) file.click(); });
    file.addEventListener("change", function () { if (file.files.length) uploadFiles(file.files); file.value = ""; });
    ["dragenter", "dragover"].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("over"); }); });
    ["dragleave", "drop"].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("over"); }); });
    drop.addEventListener("drop", function (e) { if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files); });
    $("#media-search").addEventListener("input", function () { clearTimeout(searchTimer); searchTimer = setTimeout(function () { loadMedia(0); }, 300); });
    $("#media-more").addEventListener("click", function () { if (nextOffset) loadMedia(nextOffset); });
  }

  // --- حقول الصور (الصورة الرئيسية، الغلاف، صورة الكاتب…) ---
  // زر «إزالة» خارج المربع لا داخله: المربع نفسه زر، ولا تتداخل الأزرار (لقارئ الشاشة ولوحة المفاتيح)
  function showAsset(box, a) {
    var wrap = box.parentNode;
    box.innerHTML = "";
    box.appendChild(el("img", { src: a.card, alt: "" }));
    if (!box.hasAttribute("data-label")) box.setAttribute("data-label", box.getAttribute("aria-label") || "");
    box.setAttribute("aria-label", "غيّر الصورة");
    var old = wrap.querySelector(":scope > .clear");
    if (old) old.remove();
    var clear = el("button", { type: "button", "class": "btn sm clear" }, "إزالة الصورة");
    clear.addEventListener("click", function () {
      document.getElementById(box.getAttribute("data-image-field")).value = "";
      box.innerHTML = '<span class="placeholder-text">اختر صورة أو ارفع جديدة</span>';
      box.setAttribute("aria-label", box.getAttribute("data-label"));
      clear.remove();
      box.focus();
      markDirty();
    });
    wrap.appendChild(clear);
    var meta = wrap.parentNode.querySelector("[data-removed-meta]");
    if (meta) {
      meta.hidden = !(a.removed && a.removed.length);
      meta.querySelector("span").textContent = "نُزع من هذه الصورة: " + (a.removed || []).join("، ");
    }
    var cap = document.getElementById("id_image_caption");
    if (cap && !cap.value && a.caption) cap.value = a.caption;
  }
  $$("[data-image-field]").forEach(function (box) {
    var wrap = el("div", { "class": "image-field" });
    box.parentNode.insertBefore(wrap, box);
    wrap.appendChild(box);
    var input = document.getElementById(box.getAttribute("data-image-field"));
    var initial = box.getAttribute("data-initial");
    if (initial) fetch("/studio/media/picker/?id=" + initial, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) { if (d.items[0]) showAsset(box, d.items[0]); });
    var open = function () { openPicker(function (a) { input.value = a.id; showAsset(box, a); markDirty(); }); };
    box.addEventListener("click", open);
    box.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
  });

  // --- المحرر النصي (Quill) ---
  var dirty = false;
  function markDirty() { dirty = true; }
  var quill = null;
  function initQuill() {
    var holder = $("#editor");
    if (!holder || typeof Quill === "undefined") return;
    var form = holder.closest("form") || document.getElementById("article-form");
    var hidden = form ? (form.querySelector("input[name=body]") || document.querySelector("input[name=body]")) : null;
    var readOnly = hidden && hidden.disabled;
    quill = new Quill(holder, {
      theme: "snow",
      readOnly: readOnly,
      placeholder: "اكتب المتن هنا… يمكنك لصق النص من أي مصدر وسيُنظَّف تلقائياً.",
      modules: {
        toolbar: {
          container: [
            [{ header: [2, 3, false] }],
            ["bold", "italic", "underline", "link"],
            ["blockquote", { list: "ordered" }, { list: "bullet" }],
            [{ align: [] }],
            ["image", "video"],
            ["clean"]
          ],
          handlers: {
            image: function () {
              openPicker(function (a) {
                var range = quill.getSelection(true);
                quill.insertEmbed(range.index, "image", a.large, "user");
                quill.setSelection(range.index + 1);
              });
            }
          }
        }
      }
    });
    quill.format("direction", "rtl");
    quill.format("align", "right");
    labelToolbar(holder);
    var wc = $("[data-wordcount]");
    var count = function () {
      if (!wc) return;
      var words = quill.getText().trim().split(/\s+/).filter(Boolean).length;
      wc.textContent = words + " كلمة · نحو " + Math.max(1, Math.ceil(words / 180)) + " دقيقة قراءة";
    };
    count();
    quill.on("text-change", function () { markDirty(); count(); });
    if (form && hidden) form.addEventListener("submit", function () { hidden.value = quill.root.innerHTML === "<p><br></p>" ? "" : quill.root.innerHTML; dirty = false; });
  }
  // أسماء عربية لأزرار شريط المحرر (Quill يسمّيها بالإنجليزية وقوائمه المنسدلة بلا اسم)
  var TOOL_LABELS = {
    ".ql-bold": "عريض", ".ql-italic": "مائل", ".ql-underline": "تسطير", ".ql-link": "رابط",
    ".ql-blockquote": "اقتباس", '.ql-list[value="ordered"]': "قائمة مرقمة", '.ql-list[value="bullet"]': "قائمة نقطية",
    ".ql-image": "صورة من المكتبة", ".ql-video": "فيديو", ".ql-clean": "إزالة التنسيق",
    ".ql-header .ql-picker-label": "نوع الفقرة", ".ql-align .ql-picker-label": "المحاذاة"
  };
  function labelToolbar(holder) {
    var bar = holder.previousElementSibling;
    if (!bar || !bar.classList.contains("ql-toolbar")) return;
    bar.setAttribute("role", "toolbar");
    bar.setAttribute("aria-label", "تنسيق المتن");
    Object.keys(TOOL_LABELS).forEach(function (sel) {
      $$(sel, bar).forEach(function (n) { n.setAttribute("aria-label", TOOL_LABELS[sel]); n.setAttribute("title", TOOL_LABELS[sel]); });
    });
    var body = holder.querySelector(".ql-editor");
    body.setAttribute("role", "textbox");
    body.setAttribute("aria-multiline", "true");
    body.setAttribute("aria-label", "المتن");
  }
  if (typeof Quill !== "undefined") initQuill();
  else window.addEventListener("load", initQuill);

  // --- محرر المواد تحديداً ---
  var form = $("[data-article-editor]");
  if (form) {
    $$("input, textarea, select").forEach(function (i) {
      if (i.form === form) { i.addEventListener("input", markDirty); i.addEventListener("change", markDirty); }
    });
    window.addEventListener("beforeunload", function (e) { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
    document.addEventListener("keydown", function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        var btn = document.querySelector('button[form="article-form"][value="save"]');
        if (btn) btn.click();
      }
    });

    // إظهار الحقول الخاصة بنوع المادة
    var syncKind = function () {
      var checked = document.querySelector('input[name="kind"]:checked');
      var kind = checked ? checked.value : "news";
      $$("[data-kind-only]").forEach(function (n) { n.hidden = n.getAttribute("data-kind-only").split(" ").indexOf(kind) < 0; });
    };
    $$('input[name="kind"]').forEach(function (r) { r.addEventListener("change", syncKind); });
    syncKind();

    // القفل
    var hb = form.getAttribute("data-heartbeat");
    if (hb) setInterval(function () {
      post(hb, "", false).then(function (r) { return r.json(); }).then(function (d) {
        if (!d.ok && d.holder) alert("انتقل تحرير هذه المادة إلى " + d.holder + ". احفظ نسخة من تعديلاتك قبل المغادرة.");
      }).catch(function () {});
    }, 30000);

    // الحفظ التلقائي للمسودات
    var auto = form.getAttribute("data-autosave");
    var status = $("[data-autosave-status]");
    var st = form.getAttribute("data-status");
    if (auto && (st === "draft" || st === "changes")) setInterval(function () {
      if (!dirty || !quill) return;
      var val = function (n) { var i = document.querySelector('[name="' + n + '"][form="article-form"]') || form.querySelector('[name="' + n + '"]'); return i ? i.value : undefined; };
      post(auto, { title: val("title"), subtitle: val("subtitle"), excerpt: val("excerpt"), kicker: val("kicker"), dateline: val("dateline"), body: quill.root.innerHTML }, true)
        .then(function (r) { return r.json(); })
        .then(function (d) { if (d.ok && status) { status.textContent = "حُفظ تلقائياً " + d.saved_at; } });
    }, 45000);

    // الوسوم: اقتراحات من الوسوم الموجودة
    var tagInput = document.getElementById("id_tag_names");
    if (tagInput) {
      var box = el("div", { "class": "suggest", hidden: "" });
      tagInput.parentNode.appendChild(box);
      var timer;
      tagInput.addEventListener("input", function () {
        clearTimeout(timer);
        var parts = tagInput.value.split(/[,،]/);
        var cur = parts[parts.length - 1].trim();
        if (cur.length < 2) { box.hidden = true; return; }
        timer = setTimeout(function () {
          fetch("/studio/api/tags?q=" + encodeURIComponent(cur), { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
            box.innerHTML = "";
            d.items.forEach(function (t) {
              var b = el("button", { type: "button" }, t.name + " (" + t.count + ")");
              b.addEventListener("click", function () {
                parts[parts.length - 1] = " " + t.name;
                tagInput.value = parts.join("،").replace(/^\s+/, "") + "، ";
                box.hidden = true; tagInput.focus(); markDirty();
              });
              box.appendChild(b);
            });
            box.hidden = !d.items.length;
          });
        }, 200);
      });
      tagInput.addEventListener("blur", function () { setTimeout(function () { box.hidden = true; }, 200); });
    }

    // المواد ذات الصلة
    var relHidden = document.getElementById("id_related_ids");
    var relSearch = $("[data-related-search]");
    var relChips = $("[data-related-chips]");
    if (relHidden && relSearch) {
      var rel = {};
      var renderRel = function () {
        relChips.innerHTML = "";
        Object.keys(rel).forEach(function (id) {
          var c = el("span", { "class": "chip" }, rel[id]);
          var x = el("button", { type: "button", "aria-label": "إزالة" }, "×");
          x.addEventListener("click", function () { delete rel[id]; relHidden.value = Object.keys(rel).join(","); renderRel(); markDirty(); });
          c.appendChild(x); relChips.appendChild(c);
        });
      };
      (relHidden.value || "").split(",").filter(Boolean).forEach(function (id) { rel[id] = "#" + id; });
      renderRel();
      var rbox = el("div", { "class": "suggest", hidden: "" });
      relSearch.parentNode.appendChild(rbox);
      var rt;
      relSearch.addEventListener("input", function () {
        clearTimeout(rt);
        rt = setTimeout(function () {
          fetch("/studio/api/articles?q=" + encodeURIComponent(relSearch.value), { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
            rbox.innerHTML = "";
            d.items.forEach(function (a) {
              var b = el("button", { type: "button" }, a.title + " · " + a.date);
              b.addEventListener("click", function () { rel[a.id] = a.title; relHidden.value = Object.keys(rel).join(","); renderRel(); rbox.hidden = true; relSearch.value = ""; markDirty(); });
              rbox.appendChild(b);
            });
            rbox.hidden = !d.items.length;
          });
        }, 250);
      });
    }

    // معرض الصور
    var galHidden = document.getElementById("id_gallery_ids");
    var galGrid = $("[data-gallery]");
    if (galHidden && galGrid) {
      var gal = (galHidden.value || "").split(",").filter(Boolean);
      var renderGal = function () {
        galHidden.value = gal.join(",");
        galGrid.innerHTML = "";
        gal.forEach(function (id, idx) {
          var cell = el("div", { "class": "media-item" });
          fetch("/studio/media/picker/?id=" + id, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
            if (d.items[0]) cell.insertBefore(el("img", { src: d.items[0].thumb, alt: "" }), cell.firstChild);
          });
          var rm = el("button", { type: "button", "class": "btn sm" }, "إزالة");
          rm.addEventListener("click", function () { gal.splice(idx, 1); renderGal(); markDirty(); });
          cell.appendChild(rm);
          galGrid.appendChild(cell);
        });
      };
      renderGal();
      $("[data-gallery-add]").addEventListener("click", function () {
        openPicker(function (a) { if (gal.indexOf(String(a.id)) < 0) { gal.push(String(a.id)); renderGal(); markDirty(); } }, true);
      });
    }
  }

  // --- نماذج بمحرر نصي خارج محرر المواد (الصفحات الثابتة) ---
  // يتولاها initQuill أعلاه عبر وسم #editor.

  // --- رفع الصور من صفحة المكتبة ---
  var libDrop = $("[data-library-drop]");
  if (libDrop) {
    var lf = libDrop.querySelector("input[type=file]");
    ["dragenter", "dragover"].forEach(function (ev) { libDrop.addEventListener(ev, function (e) { e.preventDefault(); libDrop.classList.add("over"); }); });
    ["dragleave", "drop"].forEach(function (ev) { libDrop.addEventListener(ev, function (e) { e.preventDefault(); libDrop.classList.remove("over"); }); });
    libDrop.addEventListener("drop", function (e) { lf.files = e.dataTransfer.files; libDrop.closest("form").submit(); });
    lf.addEventListener("change", function () { if (lf.files.length) libDrop.closest("form").submit(); });
  }

  // --- محدد المادة للعاجل ---
  var brSearch = $("[data-breaking-article]");
  if (brSearch) {
    var brHidden = document.getElementById("id_article");
    var brBox = el("div", { "class": "suggest", hidden: "" });
    brSearch.parentNode.appendChild(brBox);
    var bt;
    brSearch.addEventListener("input", function () {
      clearTimeout(bt);
      bt = setTimeout(function () {
        fetch("/studio/api/articles?q=" + encodeURIComponent(brSearch.value), { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
          brBox.innerHTML = "";
          d.items.forEach(function (a) {
            var b = el("button", { type: "button" }, a.title);
            b.addEventListener("click", function () { brHidden.value = a.id; brSearch.value = a.title; brBox.hidden = true; });
            brBox.appendChild(b);
          });
          brBox.hidden = !d.items.length;
        });
      }, 250);
    });
  }

  // --- مدقق الأسلوب: ملاحظات الترقيم والهمزات ودليل المؤسسة، والتصحيح بنقرة ---
  var stylePanel = $("[data-style-panel]");
  if (stylePanel) {
    var OBJ = "\uFFFC";  // كل عنصر مضمَّن في المحرر (صورة، فيديو) يُحتسب محرفاً واحداً
    var styleList = $("[data-style-list]", stylePanel), styleStatus = $("[data-style-status]", stylePanel);
    var styleCount = $("[data-style-count]", stylePanel), fixAllBtn = $("[data-style-fixall]", stylePanel);
    var ignored = {}, styleTimer = null, styleSeq = 0, lastIssues = {};
    var sources = {};
    var addSource = function (name, input, label) {
      if (!input) return;
      sources[name] = { input: input, label: label };
      input.addEventListener("input", function () { scheduleStyle(1500); });
    };
    // [[الاسم، معرّف الحقل، عنوانه]…] من القالب، أو حقول تحمل data-style-field
    JSON.parse(stylePanel.getAttribute("data-style-inputs") || "[]").forEach(function (f) {
      addSource(f[0], document.getElementById(f[1]), f[2]);
    });
    $$("[data-style-field]").forEach(function (input) {
      addSource(input.getAttribute("data-style-field"), input, input.getAttribute("data-style-label") || "");
    });
    var deltaText = function (delta) {
      return delta.ops.map(function (op) { return typeof op.insert === "string" ? op.insert : OBJ; }).join("");
    };
    var bodyAvailable = function () { return stylePanel.hasAttribute("data-style-body") && quill; };
    var collect = function () {
      var fields = {};
      Object.keys(sources).forEach(function (k) { fields[k] = sources[k].input.value; });
      if (bodyAvailable()) fields.body = deltaText(quill.getContents());
      return fields;
    };
    var editable = function (name) {
      if (name === "body") return bodyAvailable() && quill.isEnabled();
      return sources[name] && !sources[name].input.disabled && !sources[name].input.readOnly;
    };
    var fieldLabel = function (name) { return name === "body" ? "المتن" : (sources[name] ? sources[name].label : ""); };
    var shown = function (text) { return text.trim() ? text : text.replace(/[ \t\u00a0]/g, "␣"); };

    // يطبّق التصحيح إن لم يتغير النص منذ الفحص؛ وإلا يعيد الفحص
    var applyFix = function (name, it) {
      if (it.fix === null || !editable(name)) return false;
      if (name === "body") {
        var len = it.end - it.start;
        if (deltaText(quill.getContents(it.start, len)) !== it.text) return false;
        var fmt = quill.getFormat(it.start, len);
        quill.deleteText(it.start, len, "user");
        if (it.fix) quill.insertText(it.start, it.fix, fmt, "user");
        return true;
      }
      var input = sources[name].input;
      if (input.value.slice(it.start, it.end) !== it.text) return false;
      input.value = input.value.slice(0, it.start) + it.fix + input.value.slice(it.end);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    };
    var reveal = function (name, it) {
      if (name === "body") {
        if (bodyAvailable()) { quill.focus(); quill.setSelection(it.start, it.end - it.start, "user"); }
        return;
      }
      var input = sources[name].input;
      input.focus();
      try { input.setSelectionRange(it.start, it.end); } catch (e) { /* حقول لا تدعم التحديد */ }
    };

    var renderStyle = function (data) {
      lastIssues = data.fields || {};
      styleList.innerHTML = "";
      var total = 0, fixable = 0;
      Object.keys(lastIssues).forEach(function (name) {
        lastIssues[name].forEach(function (it) {
          var key = name + "|" + it.rule + "|" + it.text;
          if (ignored[key]) return;
          total += 1;
          var canFix = it.fix !== null && editable(name);
          if (canFix) fixable += 1;
          var li = el("li", { "class": "style-issue rule-" + it.rule });
          li.appendChild(el("div", { "class": "where" }, fieldLabel(name) + " · " + it.label));
          var ctx = el("button", { type: "button", "class": "ctx", title: "أظهر الموضع" });
          ctx.appendChild(document.createTextNode((it.before.length >= 20 ? "…" : "") + it.before));
          ctx.appendChild(el("mark", {}, shown(it.text)));
          ctx.appendChild(document.createTextNode(it.after + (it.after.length >= 20 ? "…" : "")));
          ctx.addEventListener("click", function () { reveal(name, it); });
          li.appendChild(ctx);
          li.appendChild(el("div", { "class": "msg" }, it.message));
          var row = el("div", { "class": "btn-row" });
          if (canFix) {
            var fixText = it.fix.trim() ? "صحّح ← " + it.fix.trim() : (it.fix ? "صحّح المسافة" : "احذف");
            var fb = el("button", { type: "button", "class": "btn sm primary" }, fixText);
            fb.addEventListener("click", function () {
              if (!applyFix(name, it)) styleStatus.textContent = "تغيّر النص منذ الفحص؛ أُعيد التدقيق.";
              runStyle();
            });
            row.appendChild(fb);
          }
          var ib = el("button", { type: "button", "class": "btn sm" }, "تجاهل");
          ib.addEventListener("click", function () { ignored[key] = true; renderStyle({ fields: lastIssues }); });
          row.appendChild(ib);
          li.appendChild(row);
          styleList.appendChild(li);
        });
      });
      styleCount.textContent = total ? String(total) : "";
      styleCount.hidden = !total;
      fixAllBtn.hidden = fixable < 2;
      fixAllBtn.textContent = "صحّح الكل (" + fixable + ")";
      styleStatus.textContent = total ? "" : "لا ملاحظات على النص.";
    };

    var runStyle = function () {
      clearTimeout(styleTimer);
      var fields = collect();
      if (!Object.keys(fields).some(function (k) { return fields[k].trim(); })) { renderStyle({ fields: {} }); return; }
      var seq = ++styleSeq;
      stylePanel.setAttribute("aria-busy", "true");
      post(stylePanel.getAttribute("data-style-url"), { fields: fields }, true)
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          if (seq !== styleSeq) return;
          if (!res.ok) { styleStatus.textContent = res.d.error || "تعذّر التدقيق."; return; }
          renderStyle(res.d);
        })
        .catch(function () { if (seq === styleSeq) styleStatus.textContent = "تعذّر الاتصال بالمدقق."; })
        .then(function () { stylePanel.removeAttribute("aria-busy"); });
    };
    var scheduleStyle = function (ms) { clearTimeout(styleTimer); styleTimer = setTimeout(runStyle, ms); };

    fixAllBtn.addEventListener("click", function () {
      var failed = 0;
      Object.keys(lastIssues).forEach(function (name) {
        // من الآخر إلى الأول كي لا تتزحزح مواضع ما لم يُصحَّح بعد
        lastIssues[name].slice().reverse().forEach(function (it) {
          if (it.fix === null || ignored[name + "|" + it.rule + "|" + it.text]) return;
          if (!applyFix(name, it)) failed += 1;
        });
      });
      if (failed) styleStatus.textContent = "تغيّر بعض النص منذ الفحص؛ أُعيد التدقيق.";
      runStyle();
    });
    $("[data-style-run]", stylePanel).addEventListener("click", runStyle);
    var hookQuill = function () {
      if (!bodyAvailable()) return;
      quill.on("text-change", function (d, o, source) { if (source === "user") scheduleStyle(1500); });
      scheduleStyle(300);
    };
    if (quill) hookQuill();
    else window.addEventListener("load", function () { setTimeout(hookQuill, 0); });
    if (!stylePanel.hasAttribute("data-style-body")) scheduleStyle(300);
  }
})();

// --- تلميحات الرسم البياني: القيمة أولاً ثم التسمية، وعلى التركيز بلوحة المفاتيح كما على المرور ---
(function () {
  "use strict";
  Array.prototype.forEach.call(document.querySelectorAll("[data-viz]"), function (viz) {
    var tip = viz.querySelector(".viz-tip");
    var svg = viz.querySelector("svg");
    if (!tip || !svg) return;
    function show(hit) {
      tip.textContent = "";
      var v = document.createElement("strong");
      v.textContent = Number(hit.getAttribute("data-views")).toLocaleString("ar-EG-u-nu-latn") + " مشاهدة";
      var row = document.createElement("div");
      row.className = "row";
      var key = document.createElement("span");
      key.className = "key";
      row.appendChild(key);
      row.appendChild(document.createTextNode(Number(hit.getAttribute("data-visitors")).toLocaleString("ar-EG-u-nu-latn") + " زائر"));
      var lbl = document.createElement("div");
      lbl.className = "row";
      lbl.textContent = hit.getAttribute("data-label");
      tip.appendChild(v); tip.appendChild(row); tip.appendChild(lbl);
      tip.hidden = false;
      var box = viz.getBoundingClientRect();
      var r = hit.getBoundingClientRect();
      var left = r.left - box.left + r.width / 2 - tip.offsetWidth / 2;
      tip.style.left = Math.max(0, Math.min(box.width - tip.offsetWidth, left)) + "px";
      tip.style.top = (svg.getBoundingClientRect().top - box.top + 4) + "px";
    }
    Array.prototype.forEach.call(svg.querySelectorAll(".hit"), function (hit) {
      hit.addEventListener("pointerenter", function () { show(hit); });
      hit.addEventListener("focus", function () { show(hit); });
      hit.addEventListener("pointerleave", function () { tip.hidden = true; });
      hit.addEventListener("blur", function () { tip.hidden = true; });
    });
  });
})();

(function () {
  "use strict";
  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function el(tag, attrs, text) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (text) n.textContent = text;
    return n;
  }

  // --- نموذج كتلة الصفحة الرئيسية: إظهار حقول النوع المختار فقط، واختيار المواد ---
  var blockForm = $("[data-block-form]");
  if (blockForm) {
    var kindsData = document.getElementById("block-field-kinds");
    var fieldKinds = kindsData ? JSON.parse(kindsData.textContent) : {};
    var kindSelect = blockForm.querySelector('[name="kind"]');
    var syncBlock = function () {
      var k = kindSelect ? kindSelect.value : "";
      $$("[data-block-field]", blockForm).forEach(function (row) {
        var only = fieldKinds[row.getAttribute("data-block-field")];
        row.hidden = !!only && only.split(" ").indexOf(k) < 0;
      });
    };
    if (kindSelect) kindSelect.addEventListener("change", syncBlock);
    syncBlock();

    var pickHidden = blockForm.querySelector('[name="pick_ids"]');
    var pickSearch = $("[data-picker-search]", blockForm);
    var pickChips = $("[data-picker-chips]", blockForm);
    if (pickHidden && pickSearch && pickChips) {
      var picks = [];
      $$("[data-id]", pickChips).forEach(function (c) { picks.push({ id: c.getAttribute("data-id"), title: c.textContent }); });
      var renderPicks = function () {
        pickChips.innerHTML = "";
        picks.forEach(function (p, i) {
          var c = el("span", { "class": "chip" }, (i + 1) + ". " + p.title);
          var x = el("button", { type: "button", "aria-label": "إزالة" }, "×");
          x.addEventListener("click", function () { picks.splice(i, 1); renderPicks(); });
          c.appendChild(x);
          pickChips.appendChild(c);
        });
        pickHidden.value = picks.map(function (p) { return p.id; }).join(",");
      };
      renderPicks();
      var pbox = el("div", { "class": "suggest", hidden: "" });
      pickSearch.parentNode.appendChild(pbox);
      var pt;
      pickSearch.addEventListener("input", function () {
        clearTimeout(pt);
        pt = setTimeout(function () {
          fetch("/studio/api/articles?q=" + encodeURIComponent(pickSearch.value), { credentials: "same-origin" })
            .then(function (r) { return r.json(); })
            .then(function (d) {
              pbox.innerHTML = "";
              d.items.forEach(function (a) {
                var b = el("button", { type: "button" }, a.title + " · " + a.date);
                b.addEventListener("click", function () {
                  if (!picks.some(function (p) { return String(p.id) === String(a.id); })) picks.push({ id: a.id, title: a.title });
                  renderPicks(); pbox.hidden = true; pickSearch.value = "";
                });
                pbox.appendChild(b);
              });
              pbox.hidden = !d.items.length;
            });
        }, 250);
      });
    }
  }
})();

// --- الألوان والهوية البصرية: منتقي لون لكل حقل اختياري، واقتراحات الشعار، ومعاينة حيّة ---
(function () {
  "use strict";
  var preview = document.querySelector("[data-brand-preview]");
  var form = preview ? preview.closest("form") : null;
  if (!form) return;
  var HEX = /^#[0-9a-fA-F]{6}$/;
  function rgb(h) { h = h.replace("#", ""); return [0, 2, 4].map(function (i) { return parseInt(h.substr(i, 2), 16); }); }
  function hex(c) { return "#" + c.map(function (v) { v = Math.max(0, Math.min(255, Math.round(v))); return (v < 16 ? "0" : "") + v.toString(16); }).join(""); }
  function lum(h) {
    return rgb(h).map(function (c) { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); })
      .reduce(function (acc, c, i) { return acc + c * [0.2126, 0.7152, 0.0722][i]; }, 0);
  }
  function contrast(a, b) { var x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }
  function readable(bg) { return contrast(bg, "#ffffff") >= contrast(bg, "#111418") ? "#ffffff" : "#111418"; }
  function mix(a, b, t) { var x = rgb(a), y = rgb(b); return hex(x.map(function (v, i) { return v + (y[i] - v) * t; })); }
  function muted(bg) { return mix(readable(bg), bg, 0.22); }
  function forDark(h) {
    var c = h;
    for (var s = 1; s <= 10 && contrast(c, "#0f1113") < 3; s++) c = mix(h, "#ffffff", s * 0.08);
    return c;
  }
  function forTextOn(h, bg) {
    var target = lum(bg) > 0.4 ? "#000000" : "#ffffff", c = h;
    for (var s = 1; s <= 12 && contrast(c, bg) < 4.5; s++) c = mix(h, target, s * 0.07);
    return c;
  }
  function val(name) {
    var el = form.querySelector('[name="' + name + '"]');
    var v = el ? (el.value || "").trim() : "";
    return HEX.test(v) ? v.toLowerCase() : "";
  }

  // منتقي لون بجانب كل حقل اختياري، وزر «تلقائي» يفرغه
  Array.prototype.forEach.call(form.querySelectorAll("[data-color-optional]"), function (text) {
    var box = document.createElement("div");
    box.className = "color-opt";
    text.parentNode.insertBefore(box, text);
    var picker = document.createElement("input");
    picker.type = "color";
    picker.setAttribute("aria-label", "اختر اللون");
    var auto = document.createElement("button");
    auto.type = "button";
    auto.className = "btn sm";
    auto.textContent = "تلقائي";
    box.appendChild(picker);
    box.appendChild(text);
    box.appendChild(auto);
    function syncPicker() { picker.value = HEX.test(text.value) ? text.value : "#999999"; picker.style.opacity = text.value ? 1 : 0.45; }
    picker.addEventListener("input", function () { text.value = picker.value; syncPicker(); update(); });
    text.addEventListener("input", function () { syncPicker(); update(); });
    auto.addEventListener("click", function () { text.value = ""; syncPicker(); update(); });
    syncPicker();
  });

  // ألوان الشعار: ضغطة تضعها في الرئيسي أو الثانوي
  Array.prototype.forEach.call(form.querySelectorAll("[data-set-color]"), function (btn) {
    btn.addEventListener("click", function () {
      var target = document.getElementById(btn.getAttribute("data-set-color"));
      if (!target) return;
      target.value = btn.getAttribute("data-value");
      target.dispatchEvent(new Event("input", { bubbles: true }));
    });
  });

  function update() {
    var primary = val("primary_color") || "#b0101c";
    var accent = val("accent_color") || "#111418";
    var page = val("page_color") || "#ffffff";
    var darkHeader = form.querySelector('[name="header_dark"]');
    var header = val("header_color") || (darkHeader && darkHeader.checked ? accent : page);
    var p = {
      primary: primary, accent: accent, page: page, header: header,
      topbar: val("topbar_color") || accent, nav: val("nav_color") || primary,
      breaking: val("breaking_color") || primary, footer: val("footer_color") || accent,
      link: val("link_color") || forTextOn(primary, page), "primary-dark": val("primary_dark_mode") || forDark(primary)
    };
    var s = preview.style;
    Object.keys(p).forEach(function (k) { s.setProperty("--pv-" + k, p[k]); });
    ["primary", "accent", "header", "nav", "breaking", "primary-dark"].forEach(function (k) { s.setProperty("--pv-on-" + k, readable(p[k])); });
    s.setProperty("--pv-on-topbar", muted(p.topbar));
    s.setProperty("--pv-on-footer", muted(p.footer));
    s.setProperty("--pv-surface", mix(page, "#000000", 0.04));
  }
  form.addEventListener("input", update);
  form.addEventListener("change", update);
})();

// أزرار ألوان الشعار في معالج الإعداد (خارج صفحة المعاينة)
(function () {
  "use strict";
  if (document.querySelector("[data-brand-preview]")) return;
  Array.prototype.forEach.call(document.querySelectorAll("[data-set-color]"), function (btn) {
    btn.addEventListener("click", function () {
      var target = document.getElementById(btn.getAttribute("data-set-color"));
      if (target) { target.value = btn.getAttribute("data-value"); target.dispatchEvent(new Event("input", { bubbles: true })); }
    });
  });
})();

// مكتب الوكالات: تحديث تلقائي ما لم يكن المحرر في منتصف عمل (تحديد، نص مفتوح، كتابة)
(function () {
  "use strict";
  var list = document.querySelector("[data-autorefresh]");
  if (!list) return;
  var seconds = parseInt(list.getAttribute("data-autorefresh"), 10) || 60;
  function busy() {
    var active = document.activeElement;
    return document.hidden ||
      document.querySelector(".wire-card input[type=checkbox]:checked, .wire-card details[open]") ||
      (active && /INPUT|TEXTAREA|SELECT/.test(active.tagName)) ||
      String(window.getSelection ? window.getSelection() : "").length > 0;
  }
  setInterval(function () { if (!busy()) window.location.reload(); }, seconds * 1000);
})();

// قوائم تصفية تُرسل نموذجها عند التغيير (دون معالجات مضمّنة تمنعها سياسة المحتوى)
(function () {
  "use strict";
  Array.prototype.forEach.call(document.querySelectorAll("select[data-autosubmit]"), function (sel) {
    sel.addEventListener("change", function () { if (sel.form) sel.form.submit(); });
  });
})();

// نقطة التركيز: نقرة على الصورة تحدد ما يبقى في الإطار عند القص، مع معاينة فورية
(function () {
  "use strict";
  var box = document.querySelector("[data-focal]");
  if (!box) return;
  var form = document.querySelector("form.panel");
  var fx = form && form.querySelector("input[name=focal_x]");
  var fy = form && form.querySelector("input[name=focal_y]");
  if (!fx || !fy) return;
  var dot = box.querySelector(".focal-dot");
  function apply(x, y) {
    dot.style.left = (x * 100) + "%";
    dot.style.top = (y * 100) + "%";
    Array.prototype.forEach.call(document.querySelectorAll(".focal-previews img"), function (img) {
      img.style.objectPosition = (x * 100).toFixed(1) + "% " + (y * 100).toFixed(1) + "%";
    });
  }
  apply(parseFloat(box.getAttribute("data-x")) || 0.5, parseFloat(box.getAttribute("data-y")) || 0.4);
  box.addEventListener("click", function (e) {
    var r = box.getBoundingClientRect();
    // الإحداثيات من يسار الصورة وأعلاها بصرف النظر عن اتجاه الصفحة
    var x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    var y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
    fx.value = x.toFixed(3);
    fy.value = y.toFixed(3);
    apply(x, y);
  });
})();
