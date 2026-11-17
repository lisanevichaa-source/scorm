(function () {
  "use strict";

  var state = {
    uploads: [],           // [{id, name, chars}]
    scope: "standard",
    detail: "standard",
    access: "owner",
    me: null,
    course: null,          // текущий объект курса с бэкенда
    currentLessonId: null,
    mode: "start",         // start - настройки и структура; work - структура и редактор уроков
  };

  var $ = function (sel) { return document.querySelector(sel); };

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function toast(msg, isError) {
    var el = $("#toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    el.classList.toggle("error", !!isError);
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.classList.add("hidden"); }, 4500);
  }

  async function api(path, options) {
    options = options || {};
    options.headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
    var res = await fetch(path, options);
    if (res.status === 401) {  // сессия закончилась: возвращаемся на вход через корпоративный OpenID
      window.location.href = "/auth/login?next=" + encodeURIComponent(window.location.pathname);
      throw new Error("Требуется вход");
    }
    if (!res.ok) {
      var body = await res.json().catch(function () { return {}; });
      throw new Error(body.detail || ("Ошибка запроса: " + res.status));
    }
    return res.json();
  }

  // ---------- режимы ----------

  function setMode(mode) {
    state.mode = mode;
    var work = mode === "work";
    document.body.classList.toggle("mode-work", work);
    document.body.classList.toggle("mode-start", !work);
    $("#panelSettings").classList.toggle("hidden", work);
    $("#panelNav").classList.toggle("hidden", !work);
    $("#panelStructure").classList.toggle("hidden", work);
    $("#panelEditor").classList.toggle("hidden", !work);
    $("#btnSettings").classList.toggle("hidden", !work);
    $("#btnBuildScorm").disabled = !work;
    if (work) {
      renderNav();
      resumeGenerationIfRunning();
      resumePodcastsIfRunning();
    } else {
      showPreview(null);
    }
    applyAccess();
  }

  function setCourseTitle(title) {
    var el = $("#courseTitle");
    el.value = title || "Новый курс";
    el.disabled = !state.course;
  }

  // Расчётное время прохождения: слов / 180 в минуту + около 40 секунд на каждое задание
  var DETAIL_WORDS = { brief: 200, standard: 400, full: 650 };
  var DETAIL_QUIZZES = { brief: 1, standard: 1, full: 2 };

  function countWords(html) {
    var doc = new DOMParser().parseFromString(html || "", "text/html"); // безопасный разбор: скрипты не выполняются
    var t = (doc.body.textContent || "").trim();
    return t ? t.split(/\s+/).length : 0;
  }

  function estimateMinutes() {
    if (!state.course) return 0;
    var detail = state.course.setup.detail || state.detail;
    var total = 0;
    state.course.chapters.forEach(function (ch) {
      ch.lessons.forEach(function (ls) {
        var written = ls.generated || (ls.content_html && ls.content_html.trim());
        var words = written ? countWords(ls.content_html) : (DETAIL_WORDS[detail] || 400);
        var quizzes = written ? (ls.quizzes || []).length : (DETAIL_QUIZZES[detail] || 1);
        total += words / 180 + quizzes * 0.7;
      });
    });
    return total <= 15 ? Math.max(1, Math.round(total)) : Math.round(total / 5) * 5;
  }

  function renderTopStatus() {
    var el = $("#topStatus");
    var parts = [];
    if (state.topText) parts.push(state.topText);
    if (state.course) {
      var m = estimateMinutes();
      if (m) parts.push("≈ " + m + " мин");
    }
    el.textContent = parts.join(", ");
    el.className = "top-status" + (state.topCls ? " " + state.topCls : "");
  }

  function setTopStatus(text, cls) {
    state.topText = text || "";
    state.topCls = cls || "";
    renderTopStatus();
  }

  // ---------- загрузка файлов ----------

  function renderUploads() {
    var list = $("#uploadList");
    list.innerHTML = "";
    state.uploads.forEach(function (u, i) {
      var li = document.createElement("li");
      var meta = u.error ? '<span class="f-meta err">' + esc(u.error) + "</span>"
        : u.pending ? '<span class="f-meta">загрузка' + (u.progress ? " " + u.progress + "%" : "…") + "</span>"
        : u.warn ? '<span class="f-meta warn">' + esc(u.warn) + "</span>"
        : '<span class="f-meta">прочитано' + (u.images ? ", картинок: " + u.images : "") + "</span>";
      li.innerHTML = '<span class="f-name">' + esc(u.name) + "</span>" + meta +
        '<button type="button" aria-label="Убрать файл">×</button>';
      li.querySelector("button").addEventListener("click", function () {
        state.uploads.splice(i, 1);
        renderUploads();
      });
      list.appendChild(li);
    });
  }

  // Файл уходит на сервер частями по 512 КБ: прокси перед приложением (Amvera, корпоративный шлюз)
  // часто ограничивает размер одного запроса, а так каждый запрос маленький.
  var CHUNK_SIZE = 512 * 1024;

  function randomId() {
    var a = new Uint8Array(12);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return Array.prototype.map.call(a, function (x) { return ("0" + x.toString(16)).slice(-2); }).join("");
  }

  // Превращает ответ сервера или прокси в понятное сообщение
  async function describeHttpError(r, file) {
    var body = await r.json().catch(function () { return null; });
    if (body && body.detail) return body.detail;
    if (r.status === 413) return "прокси отклонил даже небольшую часть файла (413). Нужно увеличить лимит размера запроса на сервере";
    if (r.status === 502 || r.status === 503 || r.status === 504) return "сервер не ответил вовремя (" + r.status + "). Попробуйте ещё раз";
    return "ошибка сервера " + r.status;
  }

  async function sendWithRetry(url, options, file) {
    var lastMsg = "";
    for (var attempt = 0; attempt < 3; attempt++) {
      try {
        var r = await fetch(url, options);
        if (r.ok) return r;
        lastMsg = await describeHttpError(r, file);
        if (r.status >= 400 && r.status < 500 && r.status !== 408 && r.status !== 429) break; // повтор не поможет
      } catch (netErr) {
        lastMsg = "оборвалась связь с сервером";
      }
      await new Promise(function (res) { setTimeout(res, 800 * (attempt + 1)); });
    }
    throw new Error(lastMsg);
  }

  async function uploadInChunks(file, item) {
    var total = Math.max(1, Math.ceil(file.size / CHUNK_SIZE));
    var id = randomId();
    for (var i = 0; i < total; i++) {
      var blob = file.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE);
      await sendWithRetry("/api/upload/chunk?upload_id=" + id + "&index=" + i + "&total=" + total,
        { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: blob }, file);
      item.progress = Math.round((i + 1) * 100 / total);
      renderUploads();
    }
    var r = await sendWithRetry("/api/upload/complete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ upload_id: id, filename: file.name, total: total }),
    }, file);
    return r.json();
  }

  async function handleFiles(fileList) {
    var files = Array.from(fileList);
    var room = 2 - state.uploads.length;
    if (room <= 0) { toast("Можно загрузить не больше двух файлов", true); return; }
    files = files.slice(0, room);
    for (var file of files) {
      var item = { name: file.name, pending: true };
      state.uploads.push(item);
      renderUploads();
      try {
        var result = await uploadInChunks(file, item);
        item.id = result.upload_id;
        item.chars = result.chars_extracted;
        item.images = result.images || 0;
        if (result.truncated) {
          item.warn = "учтено " + Math.round(result.chars_extracted * 100 / result.chars_total) + "% текста документа (" +
            Math.round(result.chars_extracted / 1000) + " из " + Math.round(result.chars_total / 1000) +
            " тыс. символов), конец документа в курс не попадёт";
        }
        item.pending = false;
      } catch (err) {
        item.pending = false;
        item.error = err.message;
      }
      renderUploads();
    }
    // ошибочные файлы не держим в списке дольше, чем нужно для показа ошибки
    $("#fileInput").value = "";
  }

  $("#fileInput").addEventListener("change", function (e) { handleFiles(e.target.files); });
  ["dragenter", "dragover"].forEach(function (ev) {
    $("#dropzone").addEventListener(ev, function (e) { e.preventDefault(); $("#dropzone").classList.add("drag"); });
  });
  ["dragleave", "drop"].forEach(function (ev) {
    $("#dropzone").addEventListener(ev, function () { $("#dropzone").classList.remove("drag"); });
  });
  $("#dropzone").addEventListener("drop", function (e) {
    e.preventDefault();
    if (e.dataTransfer && e.dataTransfer.files.length) handleFiles(e.dataTransfer.files);
  });

  var SCOPE_NOTES = {
    brief: "Только ключевые темы: 2–3 главы, до 6 уроков.",
    standard: "Курс средней длины: 3–5 глав.",
    full: "Курс по всему документу: главы и уроки следуют его разделам.",
  };

  function setScope(v) {
    if (!SCOPE_NOTES[v]) v = "standard";
    state.scope = v;
    document.querySelectorAll("#scopeGroup .seg-btn").forEach(function (b) {
      var on = b.dataset.value === v;
      b.classList.toggle("active", on);
      b.setAttribute("aria-checked", on ? "true" : "false");
    });
    $("#scopeNote").textContent = SCOPE_NOTES[v];
  }

  $("#scopeGroup").addEventListener("click", function (e) {
    var btn = e.target.closest(".seg-btn");
    if (btn) setScope(btn.dataset.value);
  });

  var DETAIL_NOTES = {
    brief: "Около 150–250 слов на урок и одно задание.",
    standard: "Около 300–500 слов на урок и одно задание.",
    full: "Около 500–800 слов на урок и два задания.",
  };

  function setDetail(v) {
    if (!DETAIL_NOTES[v]) v = "standard";
    state.detail = v;
    document.querySelectorAll("#detailGroup .seg-btn").forEach(function (b) {
      var on = b.dataset.value === v;
      b.classList.toggle("active", on);
      b.setAttribute("aria-checked", on ? "true" : "false");
    });
    $("#detailNote").textContent = DETAIL_NOTES[v];
  }

  $("#detailGroup").addEventListener("click", function (e) {
    var btn = e.target.closest(".seg-btn");
    if (btn) setDetail(btn.dataset.value);
  });
  // ---------- создание структуры ----------

  function setGenerating(on, text) {
    $("#genState").classList.toggle("hidden", !on);
    if (text) $("#genStateText").textContent = text;
    $("#structureSkeleton").classList.toggle("hidden", !on);
    if (on) {
      $("#structureEmpty").classList.add("hidden");
      $("#structureList").classList.add("hidden");
      $("#structureActions").classList.add("hidden");
    }
  }

  $("#btnCreateStructure").addEventListener("click", async function () {
    var description = $("#descriptionInput").value.trim();
    var uploadIds = state.uploads.filter(function (u) { return u.id; }).map(function (u) { return u.id; });
    if (state.uploads.some(function (u) { return u.pending; })) {
      toast("Дождитесь окончания загрузки файла", true);
      return;
    }
    if (!uploadIds.length && !description) {
      toast("Загрузите файл или опишите курс словами", true);
      return;
    }
    if (state.course && !confirm("Структура будет собрана заново, текущие правки структуры пропадут. Продолжить?")) return;

    var btn = this;
    btn.disabled = true;
    setGenerating(true, "Пишем структуру…");
    try {
      var course = await api("/api/courses", {
        method: "POST",
        body: JSON.stringify({
          scope: state.scope,
          detail: state.detail,
          language: $("#languageSelect").value,
          description: description,
          audience: $("#audienceInput").value.trim(),
          desired_structure: $("#structureInput").value.trim() || null,
          upload_ids: uploadIds,
        }),
      });
      state.course = course;
      state.access = course.access || "owner";
      applyAccess();
      // что автор не заполнил, подставила модель: показываем это в полях
      if (!description) $("#descriptionInput").value = course.setup.description || "";
      if (!$("#audienceInput").value.trim()) $("#audienceInput").value = course.setup.audience || "";
      setCourseTitle(course.title);
      setTopStatus("Черновик");
      btn.textContent = "Собрать структуру заново";
      $("#settingsNote").textContent = "Уроки, которые вы уже правили, останутся в старом курсе. Новая структура создаётся как новый курс.";
      renderStructure();
    } catch (err) {
      toast(err.message, true);
      if (state.course) renderStructure(); else $("#structureEmpty").classList.remove("hidden");
    } finally {
      setGenerating(false);
      btn.disabled = false;
    }
  });

  // ---------- структура курса ----------

  function renderStructure() {
    var root = $("#structureList");
    root.classList.remove("hidden");
    root.innerHTML = "";
    $("#structureEmpty").classList.add("hidden");
    $("#structureHint").textContent = "Переименуйте главу, перетащите урок или удалите лишнее. Изменения учтутся при создании уроков.";
    $("#structureActions").classList.remove("hidden");

    state.course.chapters.forEach(function (chapter, ci) {
      var card = document.createElement("div");
      card.className = "chapter-card";
      card.draggable = true;
      card.dataset.chapterIndex = ci;

      var head = document.createElement("div");
      head.className = "chapter-row";
      head.innerHTML =
        '<span class="drag-handle" aria-hidden="true">⋮⋮</span>' +
        (chapter.cover_image ? '<img class="chapter-badge" alt="" src="' + esc(chapter.cover_image) + '">' : '<span class="chapter-badge" aria-hidden="true"></span>') +
        '<input class="row-title" value="' + esc(chapter.title) + '" aria-label="Название главы">' +
        '<button type="button" class="icon-btn" title="Удалить главу" aria-label="Удалить главу">×</button>';
      head.querySelector(".row-title").addEventListener("change", function () { chapter.title = this.value; });
      head.querySelector(".icon-btn").addEventListener("click", function () {
        state.course.chapters.splice(ci, 1);
        renderStructure();
      });
      card.appendChild(head);
      wireDrag(card, "chapter", ci, null);

      chapter.lessons.forEach(function (lesson, li) {
        var row = document.createElement("div");
        row.className = "lesson-row";
        row.draggable = true;
        row.innerHTML =
          '<span class="drag-handle" aria-hidden="true">⋮⋮</span>' +
          '<input class="row-title" value="' + esc(lesson.title) + '" aria-label="Название урока">' +
          '<button type="button" class="icon-btn" title="Удалить урок" aria-label="Удалить урок">×</button>';
        row.querySelector(".row-title").addEventListener("change", function () { lesson.title = this.value; });
        row.querySelector(".icon-btn").addEventListener("click", function () {
          chapter.lessons.splice(li, 1);
          renderStructure();
        });
        wireDrag(row, "lesson", ci, li);
        card.appendChild(row);
      });
      root.appendChild(card);
    });
    if (!state.course.chapters.length) {
      root.innerHTML = '<div class="muted">В структуре не осталось глав. Соберите её заново.</div>';
    }
    renderTopStatus();
  }

  // Перетаскивание: главы меняются местами с главами, уроки с уроками (в том числе между главами)
  var dragSrc = null;
  function wireDrag(el, kind, ci, li) {
    el.addEventListener("dragstart", function (e) {
      e.stopPropagation();
      dragSrc = { kind: kind, ci: ci, li: li };
      if (e.dataTransfer) { e.dataTransfer.effectAllowed = "move"; try { e.dataTransfer.setData("text/plain", "x"); } catch (x) {} }
    });
    el.addEventListener("dragover", function (e) {
      if (!dragSrc || dragSrc.kind !== kind) return;
      e.preventDefault();
      e.stopPropagation();
      el.classList.add("drag-over");
    });
    el.addEventListener("dragleave", function () { el.classList.remove("drag-over"); });
    el.addEventListener("drop", function (e) {
      el.classList.remove("drag-over");
      if (!dragSrc || dragSrc.kind !== kind) return;
      e.preventDefault();
      e.stopPropagation();
      var chapters = state.course.chapters;
      if (kind === "chapter") {
        var moved = chapters.splice(dragSrc.ci, 1)[0];
        chapters.splice(ci, 0, moved);
      } else {
        var lesson = chapters[dragSrc.ci].lessons.splice(dragSrc.li, 1)[0];
        chapters[ci].lessons.splice(li, 0, lesson);
      }
      dragSrc = null;
      renderStructure();
    });
    el.addEventListener("dragend", function () { dragSrc = null; });
  }

  $("#btnConfirmStructure").addEventListener("click", async function () {
    var btn = this;
    var hasLessons = state.course.chapters.some(function (c) { return c.lessons.length; });
    if (!hasLessons) { toast("В курсе нет ни одного урока", true); return; }
    btn.disabled = true;
    try {
      await api("/api/courses/" + state.course.id + "/structure", {
        method: "PUT",
        body: JSON.stringify({ chapters: state.course.chapters, title: $("#courseTitle").value }),
      });
      state.course = await api("/api/courses/" + state.course.id + "/confirm-structure", { method: "POST" });
      state.currentLessonId = null;
      setMode("work");
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  $("#btnSettings").addEventListener("click", async function () {
    await flushPending();
    setMode("start");
    renderStructure();
  });

  $("#courseTitle").addEventListener("change", async function () {
    if (!state.course) return;
    state.course.title = this.value.trim() || "Новый курс";
    this.value = state.course.title;
    if (state.mode !== "work") return;
    try {
      await api("/api/courses/" + state.course.id + "/structure", {
        method: "PUT",
        body: JSON.stringify({ chapters: state.course.chapters, title: state.course.title }),
      });
    } catch (err) { toast("Не удалось сохранить название: " + err.message, true); }
  });

  // ---------- рабочая область: структура слева ----------

  function allLessons() {
    var out = [];
    if (!state.course) return out;
    state.course.chapters.forEach(function (ch) {
      ch.lessons.forEach(function (ls) { out.push({ chapter: ch, lesson: ls }); });
    });
    return out;
  }

  function currentLessonEntry() {
    return allLessons().find(function (x) { return x.lesson.id === state.currentLessonId; });
  }

  function renderNav() {
    var nav = $("#editorNav");
    nav.innerHTML = "";
    state.course.chapters.forEach(function (ch) {
      var chTitle = document.createElement("div");
      chTitle.className = "nav-chapter";
      chTitle.innerHTML = (ch.cover_image
        ? '<img class="nav-chapter-cover" alt="" src="' + esc(ch.cover_image) + '">'
        : '<span class="nav-chapter-badge" aria-hidden="true"></span>') + "<span>" + esc(ch.title) + "</span>";
      nav.appendChild(chTitle);
      ch.lessons.forEach(function (ls) {
        var item = document.createElement("div");
        item.className = "nav-lesson" + (ls.generated ? " generated" : "");
        item.textContent = ls.title;
        item.dataset.lessonId = ls.id;
        item.tabIndex = 0;
        item.setAttribute("role", "button");
        item.addEventListener("click", function () { selectLesson(ls.id); });
        item.addEventListener("keydown", function (e) {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectLesson(ls.id); }
        });
        nav.appendChild(item);
      });
    });
    var target = currentLessonEntry() || allLessons()[0];
    if (target) selectLesson(target.lesson.id);
  }

  function selectLesson(lessonId) {
    flushPending(); // отправляем несохранённые правки предыдущего урока
    state.currentLessonId = lessonId;
    document.querySelectorAll(".nav-lesson").forEach(function (el) {
      el.classList.toggle("active", el.dataset.lessonId === lessonId);
    });
    var entry = currentLessonEntry();
    if (!entry) return;
    $("#editorLessonHeader").innerHTML = esc(entry.chapter.title) + "<b>" + esc(entry.lesson.title) + "</b>";
    ensureEditor().setHTML(entry.lesson.content_html || "");
    setSaveStatus("", "");
    renderQuizzesEditor(entry.lesson);
    renderPodcastBox();
    showPreview(entry.lesson.id);
    $("#btnGenerateLesson").textContent = entry.lesson.generated ? "Перегенерировать урок" : "Сгенерировать урок";
  }

  // ---------- превью ----------

  function showPreview(lessonId) {
    var frame = $("#previewFrame");
    if (!lessonId || !state.course) {
      frame.classList.add("hidden");
      $("#previewEmpty").classList.remove("hidden");
      frame.removeAttribute("src");
      return;
    }
    $("#previewEmpty").classList.add("hidden");
    frame.classList.remove("hidden");
    frame.src = "/api/courses/" + state.course.id + "/preview/" + lessonId + "?t=" + Date.now();
  }


  // ---------- видео в уроке ----------

  var VIDEO_CHUNK = 1024 * 1024;
  var videoResolve = null;
  var videoBusy = false;

  function videoDialogError(msg) {
    var el = $("#vdError");
    el.textContent = msg || "";
    el.classList.toggle("hidden", !msg);
  }

  function closeVideoDialog(result) {
    if (videoBusy && !result) return; // пока файл загружается, закрыть нельзя
    $("#videoDialog").classList.add("hidden");
    var r = videoResolve;
    videoResolve = null;
    if (r) r(result || null);
  }

  function showVideoTab(name) {
    document.querySelectorAll("[data-vtab]").forEach(function (b) {
      b.classList.toggle("active", b.dataset.vtab === name);
    });
    $("#vdFile").classList.toggle("hidden", name !== "file");
    $("#vdLink").classList.toggle("hidden", name !== "link");
    videoDialogError("");
    if (name === "link") setTimeout(function () { $("#vdUrl").focus(); }, 0);
  }

  // Принимает ссылку на страницу видео или код вставки (<iframe src="...">), возвращает адрес для встраивания
  function normalizeEmbedUrl(raw) {
    var text = String(raw || "").trim();
    var m = text.match(/src=["']([^"']+)["']/i);
    if (m) text = m[1];
    if (text.indexOf("//") === 0) text = "https:" + text;
    var u;
    try { u = new URL(text); } catch (e) { return ""; }
    if (u.protocol !== "https:") return "";
    var host = u.hostname.replace(/^www\./, "").replace(/^m\./, "");
    var id;
    var fromCode = !!m;
    if (host === "youtube.com" || host === "youtube-nocookie.com") {
      if (u.pathname.indexOf("/embed/") === 0) return "https://www.youtube.com" + u.pathname;
      id = u.searchParams.get("v") || (u.pathname.match(/^\/(?:shorts|live)\/([\w-]{6,})/) || [])[1];
      return id ? "https://www.youtube.com/embed/" + id : "";
    }
    if (host === "youtu.be") {
      id = u.pathname.slice(1).split("/")[0];
      return id ? "https://www.youtube.com/embed/" + id : "";
    }
    if (host === "rutube.ru") {
      if (u.pathname.indexOf("/play/embed/") === 0) return "https://rutube.ru" + u.pathname;
      id = (u.pathname.match(/^\/video\/([0-9a-f]{20,})/i) || [])[1];
      return id ? "https://rutube.ru/play/embed/" + id : "";
    }
    if (host === "vimeo.com" || host === "player.vimeo.com") {
      id = (u.pathname.match(/(\d{5,})/) || [])[1];
      return id ? "https://player.vimeo.com/video/" + id : "";
    }
    if ((host === "vk.com" || host === "vkvideo.ru") && u.pathname.indexOf("/video_ext.php") === 0) return u.href;
    // любой другой плеер (например, корпоративный портал): берём адрес из кода <iframe> или из ссылки вида .../embed/...
    if (fromCode || u.pathname.indexOf("/embed") !== -1) return u.href;
    return "";
  }

  async function uploadVideo(file) {
    var total = Math.max(1, Math.ceil(file.size / VIDEO_CHUNK));
    var id = randomId();
    videoBusy = true;
    $("#vdDrop").classList.add("busy");
    $("#vdProgress").classList.remove("hidden");
    videoDialogError("");
    try {
      for (var i = 0; i < total; i++) {
        var blob = file.slice(i * VIDEO_CHUNK, (i + 1) * VIDEO_CHUNK);
        await sendWithRetry("/api/media/chunk?media_id=" + id + "&index=" + i + "&total=" + total,
          { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: blob }, file);
        var pct = Math.round((i + 1) * 100 / total);
        $("#vdBar").style.width = pct + "%";
        $("#vdPct").textContent = pct + "%";
      }
      var r = await sendWithRetry("/api/media/complete", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ media_id: id, total: total }),
      }, file);
      var res = await r.json();
      videoBusy = false;
      closeVideoDialog({ embed: false, src: res.url });
    } catch (err) {
      videoDialogError("Не удалось загрузить видео: " + err.message);
    } finally {
      videoBusy = false;
      $("#vdDrop").classList.remove("busy");
      $("#vdProgress").classList.add("hidden");
      $("#vdBar").style.width = "0";
    }
  }

  function handleVideoFile(file) {
    if (!file) return;
    if (!/\.(mp4|webm)$/i.test(file.name)) {
      videoDialogError("Поддерживаются видео MP4 (H.264) и WebM. Файлы MOV, AVI, MKV сначала нужно сконвертировать в MP4.");
      return;
    }
    var max = openVideoDialog.maxMb || 150;
    if (file.size > max * 1024 * 1024) {
      videoDialogError("Файл " + Math.round(file.size / 1048576) + " МБ, а можно не больше " + max + " МБ. Сожмите видео или вставьте ссылкой.");
      return;
    }
    uploadVideo(file);
  }

  // Открывает диалог; редактор получает {embed, src} или null, если автор закрыл окно
  function openVideoDialog() {
    if (videoResolve) closeVideoDialog(null);
    videoDialogError("");
    $("#vdUrl").value = "";
    $("#vdInput").value = "";
    showVideoTab("file");
    $("#videoDialog").classList.remove("hidden");
    fetch("/api/media/info").then(function (r) { return r.json(); }).then(function (info) {
      openVideoDialog.maxMb = info.max_mb;
      $("#vdLimit").textContent = "MP4 (H.264) или WebM, до " + info.max_mb + " МБ";
    }).catch(function () { /* лимит проверит сервер */ });
    return new Promise(function (resolve) { videoResolve = resolve; });
  }

  document.querySelectorAll("[data-vtab]").forEach(function (b) {
    b.addEventListener("click", function () { if (!videoBusy) showVideoTab(b.dataset.vtab); });
  });
  $("#vdClose").addEventListener("click", function () { closeVideoDialog(null); });
  $("#videoDialog").addEventListener("mousedown", function (e) { if (e.target === this) closeVideoDialog(null); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !$("#videoDialog").classList.contains("hidden")) closeVideoDialog(null);
  });
  $("#vdInput").addEventListener("change", function () { handleVideoFile(this.files[0]); });
  var vdDrop = $("#vdDrop");
  ["dragenter", "dragover"].forEach(function (ev) {
    vdDrop.addEventListener(ev, function (e) { e.preventDefault(); vdDrop.classList.add("over"); });
  });
  ["dragleave", "drop"].forEach(function (ev) {
    vdDrop.addEventListener(ev, function (e) { e.preventDefault(); vdDrop.classList.remove("over"); });
  });
  vdDrop.addEventListener("drop", function (e) { handleVideoFile(e.dataTransfer.files[0]); });
  function submitVideoLink() {
    var url = normalizeEmbedUrl($("#vdUrl").value);
    if (!url) {
      videoDialogError("Не получилось распознать код. Вставьте целиком код для встраивания (<iframe src=\"https://…\">) или ссылку на видео в YouTube, RuTube, Vimeo, VK Видео.");
      return;
    }
    closeVideoDialog({ embed: true, src: url });
  }
  $("#vdInsert").addEventListener("click", submitVideoLink);
  $("#vdUrl").addEventListener("keydown", function (e) { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submitVideoLink(); } });


  // ---------- своя картинка в уроке ----------

  var IMAGE_CHUNK = 512 * 1024;
  var imageUploads = 0;

  // Загружает картинку (файл, скриншот из буфера) и возвращает её адрес; при ошибке показывает сообщение и возвращает null
  async function uploadImageFile(file) {
    if (!file || !/^image\//.test(file.type)) { toast("Это не картинка. Подойдут PNG, JPEG, WebP, GIF.", true); return null; }
    var id = randomId();
    var total = Math.max(1, Math.ceil(file.size / IMAGE_CHUNK));
    imageUploads++;
    toast("Загружаем картинку…");
    try {
      for (var i = 0; i < total; i++) {
        await sendWithRetry("/api/media/chunk?media_id=" + id + "&index=" + i + "&total=" + total,
          { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file.slice(i * IMAGE_CHUNK, (i + 1) * IMAGE_CHUNK) }, file);
      }
      var r = await sendWithRetry("/api/media/complete", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ media_id: id, total: total, kind: "image" }),
      }, file);
      var res = await r.json();
      toast("Картинка добавлена");
      return res.url;
    } catch (err) {
      toast("Не удалось загрузить картинку: " + err.message, true);
      return null;
    } finally {
      imageUploads--;
    }
  }

  // ---------- визуальный редактор текста урока ----------

  var lessonEditor = null;
  var pendingSave = null; // { lessonId, html, timer }

  function setSaveStatus(text, cls) {
    var el = $("#saveStatus");
    el.textContent = text;
    el.className = "save-status" + (cls ? " " + cls : "");
  }

  function ensureEditor() {
    if (lessonEditor) return lessonEditor;
    lessonEditor = window.LessonEditor.create({
      toolbar: $("#editorToolbar"),
      mount: $("#editorMount"),
      html: "",
      onChange: onEditorChange,
      requestVideo: openVideoDialog,
      uploadImage: uploadImageFile,
    });
    lessonEditor.setEditable(canEdit());
    return lessonEditor;
  }

  function onEditorChange(html) {
    if (!canEdit()) return;  // у коллеги с правом просмотра ничего не сохраняется
    var entry = currentLessonEntry();
    if (!entry) return;
    entry.lesson.content_html = html;
    if (pendingSave && pendingSave.timer) clearTimeout(pendingSave.timer);
    pendingSave = { lessonId: entry.lesson.id, html: html, timer: setTimeout(flushPending, 1500) };
    setSaveStatus("Есть несохранённые изменения…", "dirty");
  }

  function cancelPending() {
    if (pendingSave && pendingSave.timer) clearTimeout(pendingSave.timer);
    pendingSave = null;
  }

  async function flushPending() {
    if (!pendingSave) return true;
    var p = pendingSave;
    pendingSave = null;
    if (p.timer) clearTimeout(p.timer);
    setSaveStatus("Сохраняем…", "");
    try {
      var saved = await api("/api/courses/" + state.course.id + "/lessons/" + p.lessonId, {
        method: "PUT",
        body: JSON.stringify({ content_html: p.html }),
      });
      var savedEntry = allLessons().find(function (x) { return x.lesson.id === p.lessonId; });
      if (savedEntry && saved && saved.podcast) {
        savedEntry.lesson.podcast = Object.assign(savedEntry.lesson.podcast || {}, { text_changed: saved.podcast.text_changed });
        if (p.lessonId === state.currentLessonId) syncPodcastFlags();
      }
      if (!pendingSave) {
        setSaveStatus("Сохранено", "ok");
        renderTopStatus();
        if (p.lessonId === state.currentLessonId && state.mode === "work") showPreview(p.lessonId);
      }
      return true;
    } catch (err) {
      if (!pendingSave) pendingSave = { lessonId: p.lessonId, html: p.html, timer: null };
      setSaveStatus("Не удалось сохранить: " + err.message, "err");
      return false;
    }
  }

  // ---------- проверочное задание ----------

  var QUIZ_TYPE_LABELS = {
    matching: "Сопоставление",
    single_choice: "Один правильный ответ",
    true_false: "Верно или неверно",
    sequence: "Порядок",
  };

  var quizSaveTimer = null;
  function saveQuizzesSoon(lesson) {
    clearTimeout(quizSaveTimer);
    quizSaveTimer = setTimeout(async function () {
      try {
        await api("/api/courses/" + state.course.id + "/lessons/" + lesson.id, {
          method: "PUT",
          body: JSON.stringify({ quizzes: lesson.quizzes }),
        });
        setSaveStatus("Сохранено", "ok");
        if (lesson.id === state.currentLessonId) showPreview(lesson.id);
      } catch (err) { setSaveStatus("Не удалось сохранить: " + err.message, "err"); }
    }, 1200);
  }

  function mk(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function inputEl(value, label, onInput, cls) {
    var i = document.createElement("input");
    i.type = "text";
    i.value = value || "";
    i.setAttribute("aria-label", label);
    if (cls) i.className = cls;
    i.addEventListener("input", function () { onInput(i.value); });
    return i;
  }
  function miniBtn(text, label, disabled, onClick) {
    var b = mk("button", "q-mini", text);
    b.type = "button";
    b.setAttribute("aria-label", label);
    b.title = label;
    b.disabled = !!disabled;
    b.addEventListener("click", onClick);
    return b;
  }
  function addBtn(text, onClick) {
    var b = mk("button", "q-add", text);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  function renderQuizCard(lesson, q, qi, rerender) {
    var card = mk("div", "quiz-card");
    card.appendChild(mk("span", "quiz-type", QUIZ_TYPE_LABELS[q.type] || q.type));
    var changed = function () {
      setSaveStatus("Есть несохранённые изменения…", "dirty");
      saveQuizzesSoon(lesson);
    };
    var structural = function () { changed(); rerender(); };
    var questionField = function (key, label) {
      card.appendChild(inputEl(q[key], label, function (v) { q[key] = v; changed(); }, "q-field"));
    };

    if (q.type === "matching") {
      q.pairs = q.pairs || [];
      questionField("question", "Вопрос");
      q.pairs.forEach(function (p, i) {
        var row = mk("div", "q-row");
        row.appendChild(inputEl(p.left, "Понятие", function (v) { p.left = v; changed(); }));
        row.appendChild(inputEl(p.right, "Описание", function (v) { p.right = v; changed(); }));
        row.appendChild(miniBtn("×", "Удалить пару", q.pairs.length <= 2, function () { q.pairs.splice(i, 1); structural(); }));
        card.appendChild(row);
      });
      if (q.pairs.length < 6) card.appendChild(addBtn("Добавить пару", function () { q.pairs.push({ left: "", right: "" }); structural(); }));

    } else if (q.type === "single_choice") {
      q.options = q.options || [];
      questionField("question", "Вопрос");
      q.options.forEach(function (o, i) {
        var row = mk("div", "q-row");
        var radio = document.createElement("input");
        radio.type = "radio";
        radio.name = "ans-" + lesson.id + "-" + qi;
        radio.checked = i === q.correct_index;
        radio.setAttribute("aria-label", "Правильный ответ");
        radio.addEventListener("change", function () { q.correct_index = i; changed(); });
        row.appendChild(radio);
        row.appendChild(inputEl(o, "Вариант ответа", function (v) { q.options[i] = v; changed(); }));
        row.appendChild(miniBtn("×", "Удалить вариант", q.options.length <= 2, function () {
          q.options.splice(i, 1);
          if (i < q.correct_index) q.correct_index -= 1;
          else if (i === q.correct_index) q.correct_index = 0;
          structural();
        }));
        card.appendChild(row);
      });
      if (q.options.length < 6) card.appendChild(addBtn("Добавить вариант", function () { q.options.push(""); structural(); }));
      card.appendChild(mk("p", "q-hint", "Отметьте кружком правильный ответ."));

    } else if (q.type === "true_false") {
      card.appendChild(inputEl(q.statement, "Утверждение", function (v) { q.statement = v; changed(); }, "q-field"));
      var seg = mk("div", "q-seg");
      [[true, "Верно"], [false, "Неверно"]].forEach(function (opt) {
        var b = mk("button", (q.correct === opt[0] ? "active" : ""), opt[1]);
        b.type = "button";
        b.addEventListener("click", function () { q.correct = opt[0]; structural(); });
        seg.appendChild(b);
      });
      card.appendChild(mk("p", "q-hint", "Правильный ответ на это утверждение:"));
      card.appendChild(seg);

    } else if (q.type === "sequence") {
      q.items = q.items || [];
      questionField("question", "Вопрос");
      q.items.forEach(function (it, i) {
        var row = mk("div", "q-row");
        row.appendChild(mk("span", "", String(i + 1) + "."));
        row.appendChild(inputEl(it, "Шаг " + (i + 1), function (v) { q.items[i] = v; changed(); }));
        row.appendChild(miniBtn("↑", "Выше", i === 0, function () { q.items.splice(i - 1, 0, q.items.splice(i, 1)[0]); structural(); }));
        row.appendChild(miniBtn("↓", "Ниже", i === q.items.length - 1, function () { q.items.splice(i + 1, 0, q.items.splice(i, 1)[0]); structural(); }));
        row.appendChild(miniBtn("×", "Удалить шаг", q.items.length <= 2, function () { q.items.splice(i, 1); structural(); }));
        card.appendChild(row);
      });
      if (q.items.length < 6) card.appendChild(addBtn("Добавить шаг", function () { q.items.push(""); structural(); }));
      card.appendChild(mk("p", "q-hint", "Здесь порядок правильный. Ученику шаги покажутся перемешанными."));

    } else {
      card.appendChild(mk("pre", "", JSON.stringify(q, null, 2)));
    }
    return card;
  }

  function renderQuizzesEditor(lesson) {
    var quizzes = lesson.quizzes || [];
    var root = $("#editorQuizzes");
    root.innerHTML = "";
    quizzes.forEach(function (q, qi) {
      root.appendChild(renderQuizCard(lesson, q, qi, function () { renderQuizzesEditor(lesson); }));
    });
    if (!quizzes.length) {
      root.innerHTML = '<div class="muted">Пока без задания. Сгенерируйте урок, и оно появится здесь.</div>';
    }
  }

  // ---------- генерация ----------

  $("#btnGenerateLesson").addEventListener("click", async function () {
    var entry = currentLessonEntry();
    if (!entry) return;
    var btn = this;
    var label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Пишем урок…";
    cancelPending(); // перегенерация заменяет текущий текст урока
    try {
      var lesson = await api("/api/courses/" + state.course.id + "/lessons/" + entry.lesson.id + "/generate", { method: "POST" });
      Object.assign(entry.lesson, lesson);
      selectLesson(entry.lesson.id);
      markGenerated();
      toast("Урок готов");
    } catch (err) {
      btn.textContent = label;
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  function markGenerated() {
    renderTopStatus();
    document.querySelectorAll(".nav-lesson").forEach(function (el) {
      var entry = allLessons().find(function (x) { return x.lesson.id === el.dataset.lessonId; });
      el.classList.toggle("generated", !!(entry && entry.lesson.generated));
      el.classList.toggle("has-podcast", !!(entry && entry.lesson.podcast && entry.lesson.podcast.audio));
    });
  }

  $("#btnGenerateCovers").addEventListener("click", async function () {
    var btn = this;
    btn.disabled = true;
    btn.textContent = "Рисуем обложки…";
    try {
      await flushPending();
      var res = await api("/api/courses/" + state.course.id + "/generate-all-covers", { method: "POST" });
      state.course = res.course;
      renderNav();
      if (res.errors && res.errors.length) toast("Ошибка (" + res.errors.length + "): " + res.errors[0].error, true);
      else toast("Обложки глав готовы");
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = "Обложки глав";
    }
  });

  // ---------- генерация всех уроков в фоне ----------

  var genTimer = null;
  var genLastDone = -1;

  function showGenProgress(st) {
    var running = st.status === "running";
    $("#genProgress").classList.toggle("hidden", !running);
    $("#btnGenerateAll").disabled = running;
    $("#btnSettings").disabled = running;
    $("#btnSettings").title = running ? "Недоступно, пока идёт генерация" : "";
    if (running) {
      var pct = st.total ? Math.round(st.done * 100 / st.total) : 0;
      $("#gpFill").style.width = pct + "%";
      $("#gpText").textContent = "Готово " + st.done + " из " + st.total + (st.current ? ". Пишем: " + st.current : "");
    }
    $("#btnGenerateAll").textContent = running ? "Идёт генерация…" : "Сгенерировать все уроки";
  }

  // Подтягиваем готовые уроки с сервера, не трогая тот, который автор сейчас правит
  async function refreshLessonsFromServer() {
    var fresh = await api("/api/courses/" + state.course.id);
    var freshById = {};
    fresh.chapters.forEach(function (ch) { ch.lessons.forEach(function (ls) { freshById[ls.id] = ls; }); });
    var reselect = false;
    allLessons().forEach(function (x) {
      var f = freshById[x.lesson.id];
      if (!f || !f.generated || x.lesson.generated) return;
      var editing = x.lesson.id === state.currentLessonId && pendingSave;
      if (editing) return;
      x.lesson.content_html = f.content_html;
      x.lesson.quizzes = f.quizzes;
      x.lesson.generated = true;
      if (x.lesson.id === state.currentLessonId) reselect = true;
    });
    markGenerated();
    if (reselect) selectLesson(state.currentLessonId);
  }

  async function pollGeneration() {
    clearTimeout(genTimer);
    var st;
    try {
      st = await api("/api/courses/" + state.course.id + "/generate-all/status");
    } catch (err) {
      genTimer = setTimeout(pollGeneration, 4000); // кратковременный сбой связи не должен прерывать наблюдение
      return;
    }
    showGenProgress(st);
    if (st.done !== genLastDone) {
      genLastDone = st.done;
      try { await refreshLessonsFromServer(); } catch (e) { /* обновим при следующем опросе */ }
    }
    if (st.status === "running") {
      genTimer = setTimeout(pollGeneration, 2000);
      return;
    }
    genLastDone = -1;
    if (st.failed) toast("Не удалось написать уроков: " + st.failed + ". " + (st.errors[0] ? st.errors[0].title + ": " + st.errors[0].error : ""), true);
    else if (st.status === "cancelled") toast("Генерация остановлена. Готовые уроки сохранены.");
    else if (st.total) toast("Все уроки готовы");
  }

  async function resumeGenerationIfRunning() {
    if (!state.course) return;
    try {
      var st = await api("/api/courses/" + state.course.id + "/generate-all/status");
      if (st.status === "running") { genLastDone = -1; pollGeneration(); }
      else showGenProgress(st);
    } catch (e) { /* не критично */ }
  }

  $("#btnGenerateAll").addEventListener("click", async function () {
    try {
      await flushPending();
      var st = await api("/api/courses/" + state.course.id + "/generate-all", { method: "POST" });
      if (!st.total && st.status !== "running") { toast("Все уроки уже написаны"); return; }
      genLastDone = -1;
      showGenProgress(st);
      pollGeneration();
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("#btnCancelGen").addEventListener("click", async function () {
    try {
      await api("/api/courses/" + state.course.id + "/generate-all/cancel", { method: "POST" });
      $("#gpText").textContent = "Останавливаем после текущего урока…";
    } catch (err) { toast(err.message, true); }
  });

  $("#btnBuildScorm").addEventListener("click", async function () {
    var btn = this;
    if (!canEdit()) { window.location.href = "/api/courses/" + state.course.id + "/download"; return; }  // коллега с правом просмотра скачивает уже собранный пакет
    btn.disabled = true;
    btn.textContent = "Собираем…";
    try {
      await flushPending();
      var res = await api("/api/courses/" + state.course.id + "/build", { method: "POST" });
      window.location.href = res.download_url;
      var limit = res.limit_mb || 500;
      if (res.size_mb > limit) toast("SCORM-пакет весит " + res.size_mb + " МБ, а портал принимает до " + limit + " МБ. Уберите видео или подкасты из части уроков.", true);
      else if (res.size_mb >= limit * 0.8) toast("SCORM-пакет готов, он весит " + res.size_mb + " МБ из допустимых " + limit + " МБ.");
      else toast("SCORM-пакет готов, загрузка началась");
      setTopStatus("SCORM собран", "ok");
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = state.mode !== "work";
      btn.textContent = "Собрать SCORM";
    }
  });


  // ---------- подкаст к уроку ----------

  var podInfo = { configured: false };
  var podJob = { status: "idle", total: 0, done: 0, current: null, current_id: null, errors: [], failed: 0 };
  var podTimer = null;
  var podLastDone = -1;
  var podBoxKey = "";
  var podScriptTimer = null;

  function podMinutes(sec) { return Math.max(1, Math.round((sec || 0) / 60)); }

  function lessonHasText(l) {
    var tmp = document.createElement("div");
    tmp.innerHTML = l.content_html || "";
    return (tmp.textContent || "").trim().split(/\s+/).filter(Boolean).length >= 15;
  }

  function syncPodcastFlags() {
    var entry = currentLessonEntry();
    var warn = $("#pbWarn");
    if (!entry || !warn) return;
    var pod = entry.lesson.podcast || {};
    var msg = pod.audio_outdated ? "Вы изменили текст подкаста. Нажмите «Озвучить заново», чтобы аудио совпало с ним."
      : pod.text_changed ? "Текст урока изменился после создания подкаста. Пересоздайте подкаст, чтобы аудио совпадало с уроком." : "";
    warn.textContent = msg;
    warn.classList.toggle("hidden", !msg);
    var re = $("#pbReaudio");
    if (re) re.disabled = !pod.audio_outdated || podJob.status === "running";
  }

  function renderPodcastBox() {
    var box = $("#podcastBox");
    var entry = currentLessonEntry();
    if (!entry) { box.innerHTML = ""; podBoxKey = ""; return; }
    var l = entry.lesson, pod = l.podcast;
    var running = podJob.status === "running";
    var busy = running && podJob.current_id === l.id;
    var hasText = lessonHasText(l);
    var key = JSON.stringify([l.id, podInfo.configured, running, busy, hasText, pod ? pod.audio : "", pod ? pod.seconds : 0]);
    if (key === podBoxKey) { syncPodcastFlags(); return; }
    podBoxKey = key;

    var head = '<div class="pb-head"><b>Подкаст</b>' +
      (pod && pod.audio ? '<span class="pb-chip ok">Готов, около ' + podMinutes(pod.seconds) + " мин</span>" : '<span class="pb-chip">Нет</span>') + "</div>";
    var body;
    if (!podInfo.configured) {
      body = '<p class="pb-text">Озвучка не настроена на сервере. Администратору нужно задать TTS_PROVIDER (см. README).</p>';
    } else if (busy) {
      body = '<div class="pb-busy"><span class="dot pulse"></span>Пишем текст для диктора и озвучиваем. Обычно это 1–3 минуты.</div>';
    } else if (pod && pod.audio) {
      body = '<p id="pbWarn" class="pb-warn hidden"></p>' +
        '<audio class="pb-audio" controls preload="none" src="' + esc(pod.audio) + '"></audio>' +
        '<div class="pb-actions"><button id="pbRecreate" class="btn btn-outline" type="button"' + (running ? " disabled" : "") + '>Пересоздать</button>' +
        '<button id="pbRemove" class="btn btn-ghost" type="button"' + (running ? " disabled" : "") + '>Убрать подкаст</button></div>' +
        '<details class="pb-script"><summary>Текст подкаста</summary>' +
        '<textarea id="pbScript" spellcheck="true"></textarea>' +
        '<div class="pb-actions"><button id="pbReaudio" class="btn btn-outline" type="button" disabled>Озвучить заново</button></div></details>';
    } else {
      body = '<p class="pb-text">Аудиоверсия урока для тех, кто лучше слушает: полный пересказ голосом диктора. Она появится в уроке над текстом.</p>' +
        '<div class="pb-actions"><button id="pbCreate" class="btn btn-outline" type="button"' + (!hasText || running ? " disabled" : "") + '>Создать подкаст</button></div>' +
        (!hasText ? '<p class="pb-text" style="margin-top:10px">Сначала напишите или сгенерируйте текст урока.</p>' : "");
    }
    box.innerHTML = head + body;
    if ($("#pbScript")) $("#pbScript").value = pod.script || "";
    syncPodcastFlags();

    var create = $("#pbCreate"), recreate = $("#pbRecreate"), remove = $("#pbRemove"), reaudio = $("#pbReaudio"), script = $("#pbScript");
    if (create) create.addEventListener("click", function () { startPodcastForLesson(l, "all"); });
    if (recreate) recreate.addEventListener("click", function () {
      if (confirm("Текст подкаста и озвучка будут созданы заново, ваши правки текста подкаста пропадут. Продолжить?")) startPodcastForLesson(l, "all");
    });
    if (reaudio) reaudio.addEventListener("click", async function () {
      clearTimeout(podScriptTimer);
      await saveScript(l);
      startPodcastForLesson(l, "audio");
    });
    if (remove) remove.addEventListener("click", async function () {
      if (!confirm("Убрать подкаст из урока?")) return;
      try {
        var res = await api("/api/courses/" + state.course.id + "/lessons/" + l.id + "/podcast", { method: "DELETE" });
        l.podcast = res.podcast || null;
        markGenerated(); renderPodcastBox(); showPreview(l.id);
      } catch (err) { toast(err.message, true); }
    });
    if (script) script.addEventListener("input", function () {
      l.podcast.script = script.value;
      l.podcast.audio_outdated = true;
      syncPodcastFlags();
      clearTimeout(podScriptTimer);
      podScriptTimer = setTimeout(function () { saveScript(l); }, 1000);
    });
  }

  async function saveScript(l) {
    if (!l.podcast) return;
    try {
      await api("/api/courses/" + state.course.id + "/lessons/" + l.id, {
        method: "PUT", body: JSON.stringify({ podcast_script: l.podcast.script }),
      });
    } catch (err) { toast("Не удалось сохранить текст подкаста: " + err.message, true); }
  }

  function showPodProgress(st) {
    var running = st.status === "running";
    $("#podProgress").classList.toggle("hidden", !running);
    $("#btnGeneratePodcasts").disabled = running || !podInfo.configured;
    $("#btnGeneratePodcasts").textContent = running ? "Идёт озвучка…" : "Подкасты для всех уроков";
    if (running) {
      var pct = st.total ? Math.round(st.done * 100 / st.total) : 0;
      $("#ppFill").style.width = pct + "%";
      $("#ppText").textContent = "Готово " + st.done + " из " + st.total + (st.current ? ". Озвучиваем: " + st.current : "");
    }
  }

  async function refreshPodcastsFromServer() {
    var fresh = await api("/api/courses/" + state.course.id);
    var freshById = {};
    fresh.chapters.forEach(function (ch) { ch.lessons.forEach(function (ls) { freshById[ls.id] = ls; }); });
    allLessons().forEach(function (x) {
      var f = freshById[x.lesson.id];
      if (!f) return;
      var editingScript = x.lesson.id === state.currentLessonId && document.activeElement === $("#pbScript");
      if (editingScript) return;
      x.lesson.podcast = f.podcast || null;
    });
    markGenerated();
    podBoxKey = "";
    renderPodcastBox();
  }

  async function pollPodcasts() {
    clearTimeout(podTimer);
    var st;
    try {
      st = await api("/api/courses/" + state.course.id + "/podcasts/status");
    } catch (err) { podTimer = setTimeout(pollPodcasts, 4000); return; }
    var wasRunning = podJob.status === "running";
    podJob = st;
    showPodProgress(st);
    if (st.done !== podLastDone || (wasRunning && st.status !== "running")) {
      podLastDone = st.done;
      try {
        await refreshPodcastsFromServer();
        var cur = currentLessonEntry();
        if (cur && st.status !== "running") showPreview(cur.lesson.id);
      } catch (e) { /* обновим при следующем опросе */ }
    } else {
      podBoxKey = ""; renderPodcastBox();  // сменился текущий урок в очереди
    }
    if (st.status === "running") { podTimer = setTimeout(pollPodcasts, 2500); return; }
    podLastDone = -1;
    if (st.failed) toast("Не удалось озвучить: " + st.failed + ". " + (st.errors[0] ? st.errors[0].title + ": " + st.errors[0].error : ""), true);
    else if (st.status === "cancelled") toast("Остановлено. Готовые подкасты сохранены.");
    else if (st.total) toast("Подкасты готовы");
  }

  async function startPodcasts(body) {
    try {
      await flushPending();
      var st = await api("/api/courses/" + state.course.id + "/podcasts", { method: "POST", body: JSON.stringify(body) });
      if (!st.total && st.status !== "running") { toast("Подкасты уже есть у всех уроков с текстом"); return; }
      podJob = st; podLastDone = -1;
      showPodProgress(st); podBoxKey = ""; renderPodcastBox();
      pollPodcasts();
    } catch (err) { toast(err.message, true); }
  }

  function startPodcastForLesson(l, stage) { startPodcasts({ lesson_ids: [l.id], stage: stage }); }

  $("#btnGeneratePodcasts").addEventListener("click", function () { startPodcasts({ lesson_ids: null, stage: "all" }); });
  $("#btnCancelPod").addEventListener("click", async function () {
    try {
      await api("/api/courses/" + state.course.id + "/podcasts/cancel", { method: "POST" });
      $("#ppText").textContent = "Останавливаем после текущего урока…";
    } catch (err) { toast(err.message, true); }
  });

  async function initPodcasts() {
    try { podInfo = await api("/api/podcast/info"); } catch (e) { podInfo = { configured: false }; }
    $("#btnGeneratePodcasts").disabled = !podInfo.configured;
    $("#btnGeneratePodcasts").title = podInfo.configured ? "" : "Озвучка не настроена на сервере (TTS_PROVIDER)";
    podBoxKey = ""; renderPodcastBox();
  }
  async function resumePodcastsIfRunning() {
    if (!state.course) return;
    try {
      var st = await api("/api/courses/" + state.course.id + "/podcasts/status");
      podJob = st; showPodProgress(st);
      if (st.status === "running") { podLastDone = -1; pollPodcasts(); }
    } catch (e) { /* не критично */ }
  }


  // ---------- пользователь и права доступа ----------

  function canEdit() { return ["owner", "edit", "admin"].indexOf(state.access) >= 0; }
  function canShare() { return state.access === "owner" || state.access === "admin"; }

  function applyAccess() {
    var has = !!state.course;
    var ro = has && !canEdit();
    document.body.classList.toggle("readonly", ro);
    $("#btnShare").classList.toggle("hidden", !(has && canShare()));
    var badge = $("#accessBadge");
    var note = "";
    if (has && state.access === "view") note = "Только просмотр";
    else if (has && state.access === "edit") note = "Вам открыт доступ на редактирование";
    else if (has && state.access === "admin" && state.course.owner && state.me && state.course.owner !== state.me.email) note = "Курс: " + (state.course.owner_name || state.course.owner);
    badge.textContent = note;
    badge.classList.toggle("hidden", !note);
    $("#courseTitle").disabled = !has || ro;
    if (has) $("#btnBuildScorm").textContent = ro ? "Скачать SCORM" : "Собрать SCORM";
    if (has && ro) $("#btnBuildScorm").disabled = state.course.status !== "built";
    $("#btnSettings").classList.toggle("hidden", !has || ro || state.mode !== "work");
    if (lessonEditor) lessonEditor.setEditable(!ro);
  }

  async function loadMe() {
    try {
      state.me = await api("/api/me");
      $("#userName").textContent = state.me.name + (state.me.is_admin ? " (админ)" : "");
      $("#userName").title = state.me.email;
      $("#userBox").classList.remove("hidden");
      if (!state.me.auth) $("#btnLogout").classList.add("hidden");
      $("#cpAdmin").classList.toggle("hidden", !state.me.is_admin);
    } catch (e) { /* без входа страница не открывается, сюда не попадём */ }
  }

  // доступ к курсу
  var shareDraft = [];

  function renderShareList() {
    var ul = $("#shareList");
    ul.innerHTML = "";
    if (!shareDraft.length) { ul.innerHTML = '<li class="share-empty">Курс пока виден только вам.</li>'; return; }
    shareDraft.forEach(function (sh, i) {
      var li = document.createElement("li");
      li.innerHTML = '<span class="share-mail">' + esc(sh.email) + '</span>' +
        '<select aria-label="Права"><option value="view"' + (sh.role === "view" ? " selected" : "") + '>Просмотр</option>' +
        '<option value="edit"' + (sh.role === "edit" ? " selected" : "") + '>Редактирование</option></select>' +
        '<button type="button" class="icon-btn" aria-label="Закрыть доступ" title="Закрыть доступ">×</button>';
      li.querySelector("select").addEventListener("change", function (e) { sh.role = e.target.value; });
      li.querySelector(".icon-btn").addEventListener("click", function () { shareDraft.splice(i, 1); renderShareList(); });
      ul.appendChild(li);
    });
  }

  function shareError(text) {
    var el = $("#shareError");
    el.textContent = text || "";
    el.classList.toggle("hidden", !text);
  }

  function addShare() {
    var email = $("#shareEmail").value.trim().toLowerCase();
    if (!email) return;
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) { shareError("Введите адрес почты полностью, например ivanov@company.ru"); return; }
    if (state.course.owner && email === state.course.owner.toLowerCase()) { shareError("Этот человек уже владелец курса."); return; }
    shareError("");
    var existing = shareDraft.find(function (x) { return x.email === email; });
    if (existing) existing.role = $("#shareRole").value;
    else shareDraft.push({ email: email, role: $("#shareRole").value });
    $("#shareEmail").value = "";
    renderShareList();
  }

  async function openShareDialog() {
    if (!state.course) return;
    try {
      var data = await api("/api/courses/" + state.course.id + "/shares");
      shareDraft = data.shares || [];
      $("#shareOwner").textContent = data.owner ? "Владелец: " + (data.owner_name ? data.owner_name + ", " : "") + data.owner : "";
    } catch (err) { toast(err.message, true); return; }
    shareError(""); renderShareList();
    $("#shareDialog").classList.remove("hidden");
    $("#shareEmail").focus();
  }

  function closeShareDialog() { $("#shareDialog").classList.add("hidden"); }

  $("#btnShare").addEventListener("click", openShareDialog);
  $("#shareClose").addEventListener("click", closeShareDialog);
  $("#shareCancel").addEventListener("click", closeShareDialog);
  $("#shareAdd").addEventListener("click", addShare);
  $("#shareEmail").addEventListener("input", function () { shareError(""); });
  $("#shareEmail").addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); addShare(); } });
  $("#shareDialog").addEventListener("mousedown", function (e) { if (e.target === $("#shareDialog")) closeShareDialog(); });
  $("#shareSave").addEventListener("click", async function () {
    if ($("#shareEmail").value.trim()) addShare();  // введённый, но не добавленный адрес добавляется при сохранении
    else shareError("");
    if (!$("#shareError").classList.contains("hidden")) return;
    try {
      var res = await api("/api/courses/" + state.course.id + "/shares", { method: "PUT", body: JSON.stringify({ shares: shareDraft }) });
      state.course.shares = res.shares;
      closeShareDialog();
      toast(res.shares.length ? "Доступ сохранён" : "Доступ закрыт для всех, кроме вас");
    } catch (err) { shareError(err.message); }
  });

  // ---------- мои курсы ----------

  var panel = $("#coursesPanel");

  function statusText(c) {
    if (c.status === "built") return "SCORM собран";
    if (c.status === "draft_structure") return "Черновик структуры";
    return "Уроки: " + c.generated + " из " + c.lessons;
  }

  function closeCourses() {
    panel.classList.add("hidden");
    $("#btnMyCourses").setAttribute("aria-expanded", "false");
  }

  async function openCourses() {
    panel.classList.remove("hidden");
    $("#btnMyCourses").setAttribute("aria-expanded", "true");
    var list = $("#coursesList");
    list.innerHTML = '<li class="courses-empty">Загружаем…</li>';
    try {
      var items = await api("/api/courses");
      list.innerHTML = "";
      if (!items.length) { list.innerHTML = '<li class="courses-empty">Курсов пока нет.</li>'; return; }
      items.forEach(function (c) {
        var li = document.createElement("li");
        var d = new Date(c.updated * 1000);
        var mine = c.access === "owner";
        var who = mine ? "" : (c.access === "admin" ? ", владелец: " + (c.owner_name || c.owner || "не назначен")
          : ", " + (c.owner_name || c.owner || "") + (c.access === "view" ? " (просмотр)" : " (редактирование)"));
        li.innerHTML = '<button type="button" class="course-open"><b>' + esc(c.title) + "</b><span>" +
          esc(statusText(c)) + ", " + d.toLocaleDateString("ru-RU") + esc(who) + '</span></button>' +
          ((c.access === "owner" || c.access === "admin") ? '<button type="button" class="icon-btn" aria-label="Удалить курс" title="Удалить курс">×</button>' : "");
        li.querySelector(".course-open").addEventListener("click", function () { openCourse(c.id); });
        var delBtn = li.querySelector(".icon-btn");
        if (delBtn) delBtn.addEventListener("click", async function () {
          if (!confirm("Удалить курс «" + c.title + "»? Это нельзя отменить.")) return;
          try {
            await api("/api/courses/" + c.id, { method: "DELETE" });
            if (state.course && state.course.id === c.id) { window.location.href = "/"; return; }
            openCourses();
          } catch (err) { toast(err.message, true); }
        });
        list.appendChild(li);
      });
    } catch (err) {
      list.innerHTML = '<li class="courses-empty">Не удалось загрузить: ' + esc(err.message) + "</li>";
    }
  }

  async function openCourse(id) {
    closeCourses();
    try {
      await flushPending();
      var course = await api("/api/courses/" + id);
      state.course = course;
      state.access = course.access || "owner";
      state.currentLessonId = null;
      state.uploads = [];
      renderUploads();
      $("#descriptionInput").value = course.setup.description || "";
      $("#audienceInput").value = course.setup.audience || "";
      $("#structureInput").value = course.setup.desired_structure || "";
      $("#languageSelect").value = course.setup.language || "ru";
      setScope(course.setup.scope);
      setDetail(course.setup.detail);
      setCourseTitle(course.title);
      setTopStatus(course.status === "built" ? "SCORM собран" : "Черновик", course.status === "built" ? "ok" : "");
      $("#btnCreateStructure").textContent = "Собрать структуру заново";
      if (course.status === "draft_structure") {
        setMode("start");
        renderStructure();
      } else {
        setMode("work");
      }
      applyAccess();
    } catch (err) { toast(err.message, true); }
  }

  // очистка неиспользуемых файлов (только администратор)
  $("#btnCleanup").addEventListener("click", async function () {
    var btn = this;
    btn.disabled = true;
    try {
      var rep = await api("/api/admin/cleanup");
      if (!rep.files) { toast("Неиспользуемых файлов нет"); return; }
      var lines = rep.by_kind.map(function (k) { return "• " + k.kind + ": " + k.files + " шт., " + k.mb + " МБ"; }).join("\n");
      if (!confirm("Можно удалить " + rep.files + " файлов (" + rep.mb + " МБ), на которые не ссылается ни один курс:\n\n" + lines + "\n\nУдалить? Это нельзя отменить.")) return;
      var done = await api("/api/admin/cleanup", { method: "POST" });
      toast("Удалено файлов: " + done.deleted + " (" + done.mb + " МБ)");
    } catch (err) { toast(err.message, true); }
    finally { btn.disabled = false; }
  });

  $("#btnMyCourses").addEventListener("click", function () {
    if (panel.classList.contains("hidden")) openCourses(); else closeCourses();
  });
  $("#btnNewCourse").addEventListener("click", function () { window.location.href = "/"; });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeCourses(); });
  document.addEventListener("click", function (e) {
    if (!panel.classList.contains("hidden") && !panel.contains(e.target) && !e.target.closest("#btnMyCourses")) closeCourses();
  });

  // ---------- старт ----------
  setMode("start");
  loadMe();
  initPodcasts();
})();
