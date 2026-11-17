(function () {
  "use strict";

  var entries = [];       // [{type:'cover', chapter} | {type:'lesson', chapter, lesson}]
  var currentIndex = 0;
  var doneSet = new Set();
  var lessonCount = 0;

  function flatten(course) {
    var out = [];
    course.chapters.forEach(function (ch) {
      if (ch.cover_image) {
        out.push({ type: "cover", chapter: ch });
      }
      ch.lessons.forEach(function (ls) {
        out.push({ type: "lesson", chapter: ch, lesson: ls });
      });
    });
    return out;
  }

  // Расчётное время прохождения: слов / 180 в минуту + около 40 секунд на каждое задание
  function estimateMinutes(course) {
    var total = 0;
    course.chapters.forEach(function (ch) {
      ch.lessons.forEach(function (ls) {
        var doc = new DOMParser().parseFromString(ls.content_html || "", "text/html");
        var t = (doc.body.textContent || "").trim();
        total += (t ? t.split(/\s+/).length : 0) / 180 + (ls.quizzes || []).length * 0.7;
      });
    });
    return total <= 15 ? Math.max(1, Math.round(total)) : Math.round(total / 5) * 5;
  }

  function buildSidebar(course) {
    var root = document.getElementById("sidebar");
    var html = '<h1>' + escapeHtml(course.title) + "</h1>" +
      '<div class="course-meta">Время прохождения: около ' + estimateMinutes(course) + " мин</div>";
    course.chapters.forEach(function (ch) {
      var coverIdx = entries.findIndex(function (e) { return e.type === "cover" && e.chapter.id === ch.id; });
      html += '<div class="chapter-title"' + (coverIdx >= 0 ? ' data-index="' + coverIdx + '" style="cursor:pointer"' : "") + '>' + escapeHtml(ch.title) + "</div>";
      ch.lessons.forEach(function (ls) {
        var idx = entries.findIndex(function (e) { return e.type === "lesson" && e.lesson.id === ls.id; });
        html += '<div class="lesson-link" data-index="' + idx + '">' + escapeHtml(ls.title) + "</div>";
      });
    });
    root.innerHTML = html;
    root.querySelectorAll(".lesson-link, .chapter-title[data-index]").forEach(function (el) {
      el.addEventListener("click", function () {
        setMenu(false);
        renderEntry(parseInt(el.getAttribute("data-index"), 10));
      });
    });
  }

  // Мобильное меню-шторка (на широких экранах сайдбар виден всегда)
  function setMenu(open) {
    document.body.classList.toggle("menu-open", open);
    var btn = document.getElementById("menuBtn");
    if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
  }

  // Картинка на весь экран: скриншоты на телефоне иначе не прочитать
  function openLightbox(src, alt) {
    var box = document.createElement("div");
    box.className = "lightbox";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", "Увеличенная картинка");
    var img = document.createElement("img");
    img.src = src;
    img.alt = alt || "";
    box.appendChild(img);
    var close = function () { box.remove(); document.removeEventListener("keydown", onKey); };
    var onKey = function (e) { if (e.key === "Escape") close(); };
    box.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    document.body.appendChild(box);
  }

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.innerText = s == null ? "" : s;
    return d.innerHTML;
  }

  function shuffle(arr) {
    for (var i = arr.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    }
    return arr;
  }

  function markLessonDone(index) {
    if (doneSet.has(index)) return;
    doneSet.add(index);
    updateSidebarState();
    if (doneSet.size >= lessonCount) {
      window.ScormAPI && window.ScormAPI.complete(true);
    }
    window.ScormAPI && window.ScormAPI.setProgress(doneSet.size / Math.max(lessonCount, 1));
  }

  function updateSidebarState() {
    document.querySelectorAll(".lesson-link").forEach(function (el) {
      var idx = parseInt(el.getAttribute("data-index"), 10);
      el.classList.toggle("active", idx === currentIndex);
      el.classList.toggle("done", doneSet.has(idx));
    });
    document.querySelectorAll(".chapter-title[data-index]").forEach(function (el) {
      var idx = parseInt(el.getAttribute("data-index"), 10);
      el.classList.toggle("active", idx === currentIndex);
    });
  }

  function shakeEl(el) {
    el.classList.remove("shake");
    // reflow, чтобы анимация перезапустилась при повторной ошибке подряд
    void el.offsetWidth;
    el.classList.add("shake");
  }

  function quizShell(question) {
    var box = document.createElement("div");
    box.className = "quiz-box";
    var h3 = document.createElement("h3");
    h3.textContent = question || "Проверь себя";
    box.appendChild(h3);
    return box;
  }

  // ---------- Тип: сопоставление (matching) — с анимированной соединительной линией ----------

  function renderMatchingQuiz(quiz, container, onSolved) {
    var box = quizShell(quiz.question);
    var row = document.createElement("div");
    row.className = "matching-row";
    row.style.position = "relative";

    var svgNs = "http://www.w3.org/2000/svg";
    var connectorSvg = document.createElementNS(svgNs, "svg");
    connectorSvg.setAttribute("class", "connector-overlay");

    var leftCol = document.createElement("div");
    leftCol.className = "matching-col";
    var rightCol = document.createElement("div");
    rightCol.className = "matching-col";

    var pairs = quiz.pairs || [];
    var rightShuffled = shuffle(pairs.map(function (p, i) { return { text: p.right, correctIndex: i }; }));
    var selectedLeft = null;
    var solved = 0;

    function drawConnector(elA, elB, ok) {
      var contRect = row.getBoundingClientRect();
      var ra = elA.getBoundingClientRect();
      var rb = elB.getBoundingClientRect();
      var x1 = ra.right - contRect.left, y1 = ra.top + ra.height / 2 - contRect.top;
      var x2 = rb.left - contRect.left, y2 = rb.top + rb.height / 2 - contRect.top;
      var line = document.createElementNS(svgNs, "line");
      line.setAttribute("x1", x1); line.setAttribute("y1", y1);
      line.setAttribute("x2", x1); line.setAttribute("y2", y1);
      line.setAttribute("class", "connector-line" + (ok ? "" : " bad"));
      connectorSvg.appendChild(line);
      requestAnimationFrame(function () {
        line.setAttribute("x2", x2);
        line.setAttribute("y2", y2);
      });
      if (!ok) {
        setTimeout(function () { connectorSvg.removeChild(line); }, 650);
      }
    }

    pairs.forEach(function (p, i) {
      var leftEl = document.createElement("div");
      leftEl.className = "match-item";
      leftEl.textContent = p.left;
      leftEl.dataset.index = i;
      leftEl.addEventListener("click", function () {
        if (leftEl.classList.contains("paired")) return;
        leftCol.querySelectorAll(".match-item").forEach(function (x) { x.classList.remove("selected"); });
        leftEl.classList.add("selected");
        selectedLeft = i;
      });
      leftCol.appendChild(leftEl);
    });

    rightShuffled.forEach(function (r) {
      var rightEl = document.createElement("div");
      rightEl.className = "match-item";
      rightEl.textContent = r.text;
      rightEl.dataset.correctIndex = r.correctIndex;
      rightEl.addEventListener("click", function () {
        if (selectedLeft === null || rightEl.classList.contains("paired")) return;
        var leftEl = leftCol.querySelector('.match-item[data-index="' + selectedLeft + '"]');
        var isCorrect = parseInt(rightEl.dataset.correctIndex, 10) === selectedLeft;
        drawConnector(leftEl, rightEl, isCorrect);
        if (isCorrect) {
          leftEl.classList.add("correct", "paired", "pop");
          rightEl.classList.add("correct", "paired", "pop");
          solved++;
          if (solved === pairs.length && onSolved) onSolved();
        } else {
          shakeEl(leftEl);
          shakeEl(rightEl);
        }
        leftEl.classList.remove("selected");
        selectedLeft = null;
      });
      rightCol.appendChild(rightEl);
    });

    row.appendChild(connectorSvg);
    row.appendChild(leftCol);
    row.appendChild(rightCol);
    box.appendChild(row);
    container.appendChild(box);
  }

  // ---------- Тип: один правильный ответ (single_choice) ----------

  function renderSingleChoiceQuiz(quiz, container, onSolved) {
    var box = quizShell(quiz.question);
    var list = document.createElement("div");
    list.className = "choice-list";

    var options = (quiz.options || []).map(function (text, i) { return { text: text, i: i }; });
    shuffle(options);
    var solved = false;

    options.forEach(function (opt) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "choice-item";
      btn.textContent = opt.text;
      btn.addEventListener("click", function () {
        if (solved) return;
        var isCorrect = opt.i === quiz.correct_index;
        if (isCorrect) {
          solved = true;
          btn.classList.add("correct", "pop");
          list.querySelectorAll(".choice-item").forEach(function (b) { if (b !== btn) b.disabled = true; });
          onSolved && onSolved();
        } else {
          btn.classList.add("incorrect");
          shakeEl(btn);
          setTimeout(function () { btn.classList.remove("incorrect"); }, 500);
        }
      });
      list.appendChild(btn);
    });

    box.appendChild(list);
    container.appendChild(box);
  }

  // ---------- Тип: верно/неверно (true_false) ----------

  function renderTrueFalseQuiz(quiz, container, onSolved) {
    var box = quizShell(quiz.statement);
    box.querySelector("h3").className = "tf-statement";
    var row = document.createElement("div");
    row.className = "tf-row";
    var solved = false;

    [{ label: "Верно", value: true }, { label: "Неверно", value: false }].forEach(function (opt) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "tf-btn";
      btn.textContent = opt.label;
      btn.addEventListener("click", function () {
        if (solved) return;
        var isCorrect = opt.value === quiz.correct;
        if (isCorrect) {
          solved = true;
          btn.classList.add("correct", "pop");
          row.querySelectorAll(".tf-btn").forEach(function (b) { if (b !== btn) b.disabled = true; });
          onSolved && onSolved();
        } else {
          btn.classList.add("incorrect");
          shakeEl(btn);
          setTimeout(function () { btn.classList.remove("incorrect"); }, 500);
        }
      });
      row.appendChild(btn);
    });

    box.appendChild(row);
    container.appendChild(box);
  }

  // ---------- Тип: последовательность (sequence, drag&drop) ----------

  function renderSequenceQuiz(quiz, container, onSolved) {
    var box = quizShell(quiz.question);
    var list = document.createElement("div");
    list.className = "sequence-list";

    var correctOrder = quiz.items || [];
    var current = shuffle(correctOrder.slice());
    var dragIndex = null;
    var solved = false;

    function renderItems() {
      list.innerHTML = "";
      current.forEach(function (text, i) {
        var item = document.createElement("div");
        item.className = "sequence-item";
        item.draggable = !solved;
        item.dataset.index = i;
        item.innerHTML = '<span class="sequence-handle">\u2630</span><span class="sequence-num">' + (i + 1) + "</span><span>" + escapeHtml(text) + "</span>";

        item.addEventListener("dragstart", function () { dragIndex = i; item.classList.add("dragging"); });
        item.addEventListener("dragend", function () { item.classList.remove("dragging"); });
        item.addEventListener("dragover", function (e) { e.preventDefault(); item.classList.add("drag-over"); });
        item.addEventListener("dragleave", function () { item.classList.remove("drag-over"); });
        item.addEventListener("drop", function (e) {
          e.preventDefault();
          item.classList.remove("drag-over");
          if (dragIndex === null || dragIndex === i) return;
          var moved = current.splice(dragIndex, 1)[0];
          current.splice(i, 0, moved);
          dragIndex = null;
          renderItems();
        });
        list.appendChild(item);
      });
    }
    renderItems();

    var checkBtn = document.createElement("button");
    checkBtn.type = "button";
    checkBtn.className = "nav-btn secondary sequence-check";
    checkBtn.textContent = "Проверить порядок";
    checkBtn.addEventListener("click", function () {
      if (solved) return;
      var isCorrect = current.every(function (v, i) { return v === correctOrder[i]; });
      if (isCorrect) {
        solved = true;
        list.querySelectorAll(".sequence-item").forEach(function (el) {
          el.classList.add("correct", "pop");
          el.draggable = false;
        });
        checkBtn.disabled = true;
        checkBtn.textContent = "Верно!";
        onSolved && onSolved();
      } else {
        shakeEl(list);
      }
    });

    box.appendChild(list);
    box.appendChild(checkBtn);
    container.appendChild(box);
  }

  var QUIZ_RENDERERS = {
    matching: renderMatchingQuiz,
    single_choice: renderSingleChoiceQuiz,
    true_false: renderTrueFalseQuiz,
    sequence: renderSequenceQuiz,
  };

  function renderNav(index, hasQuiz, markCurrentDone) {
    var content = document.getElementById("content");
    var nav = document.createElement("div");
    nav.className = "nav-buttons";
    var prevBtn = document.createElement("button");
    prevBtn.className = "nav-btn secondary";
    prevBtn.textContent = "\u2190 Назад";
    prevBtn.disabled = index === 0;
    prevBtn.addEventListener("click", function () { renderEntry(index - 1); });

    var nextBtn = document.createElement("button");
    nextBtn.className = "nav-btn";
    nextBtn.textContent = index === entries.length - 1 ? "Завершить" : "Далее \u2192";
    nextBtn.addEventListener("click", function () {
      if (!hasQuiz && markCurrentDone) markCurrentDone();
      if (index < entries.length - 1) {
        renderEntry(index + 1);
      } else {
        window.ScormAPI && window.ScormAPI.complete(true);
        window.ScormAPI && window.ScormAPI.finish();
      }
    });

    nav.appendChild(prevBtn);
    nav.appendChild(nextBtn);
    content.appendChild(nav);
  }

  function renderCoverEntry(index, entry) {
    var content = document.getElementById("content");
    content.innerHTML = "";
    content.classList.add("cover-mode");

    var cover = document.createElement("div");
    cover.className = "chapter-cover";
    cover.style.backgroundImage = "url('" + entry.chapter.cover_image + "')";

    var overlay = document.createElement("div");
    overlay.className = "chapter-cover-title";
    overlay.textContent = entry.chapter.title;
    cover.appendChild(overlay);
    content.appendChild(cover);

    renderNav(index, false, null);
  }

  function renderPodcast(pod) {
    var box = document.createElement("div");
    box.className = "podcast";
    var mins = Math.max(1, Math.round((pod.seconds || 0) / 60));
    var title = document.createElement("div");
    title.className = "podcast-title";
    title.textContent = "Послушать урок" + (pod.seconds ? " · " + mins + " мин" : "");
    var audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "none";
    audio.src = pod.audio;
    var speeds = document.createElement("div");
    speeds.className = "podcast-speeds";
    [1, 1.25, 1.5].forEach(function (v) {
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = v + "×";
      if (v === 1) b.className = "active";
      b.addEventListener("click", function () {
        audio.playbackRate = v;
        speeds.querySelectorAll("button").forEach(function (x) { x.className = x === b ? "active" : ""; });
      });
      speeds.appendChild(b);
    });
    box.appendChild(title);
    box.appendChild(audio);
    box.appendChild(speeds);
    return box;
  }

  function renderLessonEntry(index, entry) {
    var content = document.getElementById("content");
    content.innerHTML = "";
    content.classList.remove("cover-mode");

    var h2 = document.createElement("h2");
    h2.className = "lesson-title";
    h2.textContent = entry.lesson.title;
    content.appendChild(h2);

    if (entry.lesson.podcast && entry.lesson.podcast.audio) content.appendChild(renderPodcast(entry.lesson.podcast));

    var body = document.createElement("div");
    body.className = "lesson-fade-in lesson-content";
    body.innerHTML = entry.lesson.content_html || "";
    content.appendChild(body);
    body.querySelectorAll("figure.lesson-image img").forEach(function (img) {
      img.addEventListener("click", function () { openLightbox(img.src, img.alt); });
    });

    var quizzes = entry.lesson.quizzes || [];
    var hasQuiz = quizzes.length > 0;
    var solvedCount = 0;

    quizzes.forEach(function (q) {
      var renderer = QUIZ_RENDERERS[q.type] || renderMatchingQuiz;
      renderer(q, content, function () {
        solvedCount++;
        if (solvedCount === quizzes.length) markLessonDone(index);
      });
    });

    renderNav(index, hasQuiz, function () { markLessonDone(index); });

    if (!hasQuiz) markLessonDone(index); else updateSidebarState();
  }

  function renderEntry(index) {
    currentIndex = index;
    var entry = entries[index];
    if (!entry) return;
    if (entry.type === "cover") {
      renderCoverEntry(index, entry);
    } else {
      renderLessonEntry(index, entry);
    }
    updateSidebarState();
    var topTitle = document.getElementById("topTitle");
    if (topTitle) topTitle.textContent = entry.type === "cover" ? entry.chapter.title : entry.lesson.title;
    var contentEl = document.getElementById("content");
    if (contentEl) contentEl.scrollTop = 0;
  }

  window.addEventListener("DOMContentLoaded", function () {
    var menuBtn = document.getElementById("menuBtn");
    if (menuBtn) menuBtn.addEventListener("click", function () { setMenu(!document.body.classList.contains("menu-open")); });
    var scrim = document.getElementById("scrim");
    if (scrim) scrim.addEventListener("click", function () { setMenu(false); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") setMenu(false); });
    window.ScormAPI && window.ScormAPI.init();
    var course = window.COURSE_DATA;
    entries = flatten(course);
    lessonCount = entries.filter(function (e) { return e.type === "lesson"; }).length;
    buildSidebar(course);
    renderEntry(0);
  });
})();
