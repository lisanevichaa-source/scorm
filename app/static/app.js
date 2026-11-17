(function () {
  "use strict";

  var state = {
    uploadIds: [],
    duration: "short",
    course: null,          // текущий объект курса с бэкенда
    currentLessonId: null,
  };

  var $ = function (sel) { return document.querySelector(sel); };
  var screens = {
    setup: $("#screen-setup"),
    structure: $("#screen-structure"),
    editor: $("#screen-editor"),
  };

  function showScreen(name) {
    Object.keys(screens).forEach(function (k) {
      screens[k].classList.toggle("hidden", k !== name);
    });
  }

  function toast(msg, isError) {
    var el = $("#toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    el.classList.toggle("error", !!isError);
    setTimeout(function () { el.classList.add("hidden"); }, 4000);
  }

  async function api(path, options) {
    options = options || {};
    options.headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
    var res = await fetch(path, options);
    if (!res.ok) {
      var body = await res.json().catch(function () { return {}; });
      throw new Error(body.detail || ("Ошибка запроса: " + res.status));
    }
    return res.json();
  }

  // ---------- Экран 1 ----------

  $("#durationGroup").addEventListener("click", function (e) {
    var btn = e.target.closest(".seg-btn");
    if (!btn) return;
    document.querySelectorAll("#durationGroup .seg-btn").forEach(function (b) { b.classList.remove("active"); });
    btn.classList.add("active");
    state.duration = btn.dataset.value;
  });

  $("#fileInput").addEventListener("change", async function (e) {
    var files = Array.from(e.target.files).slice(0, 2);
    var list = $("#uploadList");
    for (var file of files) {
      var li = document.createElement("li");
      li.textContent = "Загрузка: " + file.name + "...";
      list.appendChild(li);
      try {
        var fd = new FormData();
        fd.append("file", file);
        var result = await fetch("/api/upload", { method: "POST", body: fd }).then(function (r) {
          if (!r.ok) throw new Error("Ошибка загрузки файла " + file.name);
          return r.json();
        });
        state.uploadIds.push(result.upload_id);
        li.textContent = "✓ " + file.name + " (" + result.chars_extracted + " символов извлечено)";
      } catch (err) {
        li.textContent = "✗ " + file.name + ": " + err.message;
      }
    }
  });

  $("#btnCreateStructure").addEventListener("click", async function () {
    var description = $("#descriptionInput").value.trim();
    var audience = $("#audienceInput").value.trim();
    if (!description || !audience) {
      toast("Заполните описание курса и целевую аудиторию", true);
      return;
    }
    var btn = this;
    btn.disabled = true;
    btn.textContent = "Генерируем структуру...";
    try {
      var course = await api("/api/courses", {
        method: "POST",
        body: JSON.stringify({
          duration: state.duration,
          language: $("#languageSelect").value,
          description: description,
          audience: audience,
          desired_structure: $("#structureInput").value.trim() || null,
          upload_ids: state.uploadIds,
        }),
      });
      state.course = course;
      renderStructureScreen();
      showScreen("structure");
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = "Создать структуру курса";
    }
  });

  // ---------- Экран 2: структура ----------

  function renderStructureScreen() {
    var root = $("#structureList");
    root.innerHTML = "";
    state.course.chapters.forEach(function (chapter, ci) {
      var chRow = document.createElement("div");
      chRow.className = "chapter-row";
      chRow.draggable = true;
      chRow.dataset.chapterIndex = ci;
      chRow.innerHTML =
        '<span class="drag-handle">≡</span>' +
        '<input class="row-title" value="' + escapeAttr(chapter.title) + '">' +
        '<button class="icon-btn" title="Удалить главу">🗑</button>';
      wireChapterRow(chRow, ci);
      root.appendChild(chRow);

      chapter.lessons.forEach(function (lesson, li) {
        var lsRow = document.createElement("div");
        lsRow.className = "lesson-row";
        lsRow.draggable = true;
        lsRow.dataset.chapterIndex = ci;
        lsRow.dataset.lessonIndex = li;
        lsRow.innerHTML =
          '<span class="drag-handle">≡</span>' +
          '<input class="row-title" value="' + escapeAttr(lesson.title) + '">' +
          '<button class="icon-btn" title="Удалить урок">🗑</button>';
        wireLessonRow(lsRow, ci, li);
        root.appendChild(lsRow);
      });
    });
  }

  function escapeAttr(s) {
    return (s || "").replace(/"/g, "&quot;");
  }

  function wireChapterRow(row, ci) {
    row.querySelector(".row-title").addEventListener("change", function () {
      state.course.chapters[ci].title = this.value;
    });
    row.querySelector(".icon-btn").addEventListener("click", function () {
      state.course.chapters.splice(ci, 1);
      renderStructureScreen();
    });
    wireDrag(row, "chapter");
  }

  function wireLessonRow(row, ci, li) {
    row.querySelector(".row-title").addEventListener("change", function () {
      state.course.chapters[ci].lessons[li].title = this.value;
    });
    row.querySelector(".icon-btn").addEventListener("click", function () {
      state.course.chapters[ci].lessons.splice(li, 1);
      renderStructureScreen();
    });
    wireDrag(row, "lesson");
  }

  // Простой drag&drop: главы можно менять местами друг с другом,
  // уроки — местами внутри списка (перемещаются вместе со своей главой при перетаскивании главы).
  var dragSrc = null;
  function wireDrag(row, kind) {
    row.addEventListener("dragstart", function () {
      dragSrc = { kind: kind, chapterIndex: +row.dataset.chapterIndex, lessonIndex: row.dataset.lessonIndex !== undefined ? +row.dataset.lessonIndex : null };
    });
    row.addEventListener("dragover", function (e) {
      e.preventDefault();
      row.classList.add("drag-over");
    });
    row.addEventListener("dragleave", function () { row.classList.remove("drag-over"); });
    row.addEventListener("drop", function (e) {
      e.preventDefault();
      row.classList.remove("drag-over");
      if (!dragSrc) return;
      var targetKind = kind;
      var targetChapter = +row.dataset.chapterIndex;
      var targetLesson = row.dataset.lessonIndex !== undefined ? +row.dataset.lessonIndex : null;

      if (dragSrc.kind === "chapter" && targetKind === "chapter") {
        var chapters = state.course.chapters;
        var moved = chapters.splice(dragSrc.chapterIndex, 1)[0];
        chapters.splice(targetChapter, 0, moved);
        renderStructureScreen();
      } else if (dragSrc.kind === "lesson" && targetKind === "lesson") {
        var srcLessons = state.course.chapters[dragSrc.chapterIndex].lessons;
        var moved2 = srcLessons.splice(dragSrc.lessonIndex, 1)[0];
        state.course.chapters[targetChapter].lessons.splice(targetLesson, 0, moved2);
        renderStructureScreen();
      }
      dragSrc = null;
    });
  }

  $("#btnConfirmStructure").addEventListener("click", async function () {
    var btn = this;
    btn.disabled = true;
    try {
      await api("/api/courses/" + state.course.id + "/structure", {
        method: "PUT",
        body: JSON.stringify({ chapters: state.course.chapters }),
      });
      var course = await api("/api/courses/" + state.course.id + "/confirm-structure", { method: "POST" });
      state.course = course;
      renderEditorScreen();
      showScreen("editor");
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  // ---------- Экран 3: редактор уроков ----------

  function allLessons() {
    var out = [];
    state.course.chapters.forEach(function (ch) {
      ch.lessons.forEach(function (ls) { out.push({ chapter: ch, lesson: ls }); });
    });
    return out;
  }

  function renderEditorScreen() {
    var nav = $("#editorNav");
    nav.innerHTML = "";
    state.course.chapters.forEach(function (ch) {
      var chTitle = document.createElement("div");
      chTitle.className = "nav-chapter";
      if (ch.cover_image) {
        var thumb = document.createElement("img");
        thumb.className = "nav-chapter-cover";
        thumb.src = ch.cover_image;
        chTitle.appendChild(thumb);
      }
      var chLabel = document.createElement("span");
      chLabel.textContent = ch.title;
      chTitle.appendChild(chLabel);
      nav.appendChild(chTitle);
      ch.lessons.forEach(function (ls) {
        var item = document.createElement("div");
        item.className = "nav-lesson" + (ls.generated ? " generated" : "");
        item.textContent = ls.title;
        item.dataset.lessonId = ls.id;
        item.addEventListener("click", function () { selectLesson(ls.id); });
        nav.appendChild(item);
      });
    });
    var first = allLessons()[0];
    if (first) selectLesson(first.lesson.id);
  }

  function selectLesson(lessonId) {
    state.currentLessonId = lessonId;
    document.querySelectorAll(".nav-lesson").forEach(function (el) {
      el.classList.toggle("active", el.dataset.lessonId === lessonId);
    });
    var entry = allLessons().find(function (x) { return x.lesson.id === lessonId; });
    if (!entry) return;
    $("#editorLessonHeader").textContent = entry.chapter.title + " / " + entry.lesson.title;
    $("#editorContent").value = entry.lesson.content_html || "";
    renderQuizzesEditor(entry.lesson.quizzes || []);
  }

  var QUIZ_TYPE_LABELS = {
    matching: "Квиз-сопоставление",
    single_choice: "Квиз: один правильный ответ",
    true_false: "Квиз: верно / неверно",
    sequence: "Квиз: последовательность",
  };

  function renderQuizzesEditor(quizzes) {
    var root = $("#editorQuizzes");
    root.innerHTML = "";
    quizzes.forEach(function (q) {
      var card = document.createElement("div");
      card.className = "quiz-card";
      var label = QUIZ_TYPE_LABELS[q.type] || q.type;
      var bodyHtml = "";

      if (q.type === "matching") {
        bodyHtml = escapeAttr(q.question || "") + (q.pairs || []).map(function (p) {
          return '<div class="pair"><input value="' + escapeAttr(p.left) + '" data-field="left"><input value="' + escapeAttr(p.right) + '" data-field="right"></div>';
        }).join("");
      } else if (q.type === "single_choice") {
        bodyHtml = escapeAttr(q.question || "") + "<ul>" + (q.options || []).map(function (o, i) {
          return "<li>" + (i === q.correct_index ? "✓ " : "") + escapeAttr(o) + "</li>";
        }).join("") + "</ul>";
      } else if (q.type === "true_false") {
        bodyHtml = escapeAttr(q.statement || "") + " — <b>" + (q.correct ? "Верно" : "Неверно") + "</b>";
      } else if (q.type === "sequence") {
        bodyHtml = escapeAttr(q.question || "") + "<ol>" + (q.items || []).map(function (it) {
          return "<li>" + escapeAttr(it) + "</li>";
        }).join("") + "</ol>";
      } else {
        bodyHtml = JSON.stringify(q);
      }

      card.innerHTML = "<b>" + label + ":</b> " + bodyHtml;
      root.appendChild(card);
    });
    if (!quizzes.length) {
      root.innerHTML = '<div class="muted">Пока без проверочного задания — сгенерируйте урок.</div>';
    }
  }

  function currentLessonEntry() {
    return allLessons().find(function (x) { return x.lesson.id === state.currentLessonId; });
  }

  $("#btnGenerateLesson").addEventListener("click", async function () {
    var entry = currentLessonEntry();
    if (!entry) return;
    var btn = this;
    btn.disabled = true;
    btn.textContent = "Генерируем...";
    try {
      var lesson = await api("/api/courses/" + state.course.id + "/lessons/" + entry.lesson.id + "/generate", { method: "POST" });
      Object.assign(entry.lesson, lesson);
      selectLesson(entry.lesson.id);
      renderEditorScreen_updateNavDotOnly();
      toast("Урок сгенерирован");
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = "Сгенерировать / перегенерировать урок";
    }
  });

  function renderEditorScreen_updateNavDotOnly() {
    document.querySelectorAll(".nav-lesson").forEach(function (el) {
      var entry = allLessons().find(function (x) { return x.lesson.id === el.dataset.lessonId; });
      el.classList.toggle("generated", !!(entry && entry.lesson.generated));
    });
  }

  $("#btnSaveLesson").addEventListener("click", async function () {
    var entry = currentLessonEntry();
    if (!entry) return;
    entry.lesson.content_html = $("#editorContent").value;
    try {
      await api("/api/courses/" + state.course.id + "/lessons/" + entry.lesson.id, {
        method: "PUT",
        body: JSON.stringify({ content_html: entry.lesson.content_html }),
      });
      toast("Изменения сохранены");
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("#btnGenerateCovers").addEventListener("click", async function () {
    var btn = this;
    btn.disabled = true;
    btn.textContent = "Генерируем обложки...";
    try {
      var res = await api("/api/courses/" + state.course.id + "/generate-all-covers", { method: "POST" });
      state.course = res.course;
      renderEditorScreen();
      if (res.errors && res.errors.length) {
        toast("Ошибка (" + res.errors.length + "): " + res.errors[0].error, true);
      } else {
        toast("Обложки глав сгенерированы");
      }
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = "Сгенерировать обложки глав";
    }
  });

  $("#btnGenerateAll").addEventListener("click", async function () {
    var btn = this;
    btn.disabled = true;
    btn.textContent = "Генерируем все уроки...";
    try {
      var res = await api("/api/courses/" + state.course.id + "/generate-all", { method: "POST" });
      state.course = res.course;
      renderEditorScreen();
      if (res.errors && res.errors.length) {
        toast("Ошибка (" + res.errors.length + "): " + res.errors[0].error, true);
      } else {
        toast("Все уроки сгенерированы");
      }
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = "Сгенерировать все уроки";
    }
  });

  $("#btnBuildScorm").addEventListener("click", async function () {
    var btn = this;
    btn.disabled = true;
    btn.textContent = "Собираем пакет...";
    try {
      var res = await api("/api/courses/" + state.course.id + "/build", { method: "POST" });
      window.location.href = res.download_url;
      toast("SCORM-пакет готов, загрузка началась");
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = "Собрать SCORM";
    }
  });
})();
