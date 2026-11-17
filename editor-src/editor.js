import { Editor, Node, mergeAttributes } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import Underline from "@tiptap/extension-underline";
import Link from "@tiptap/extension-link";
import TextAlign from "@tiptap/extension-text-align";
import Placeholder from "@tiptap/extension-placeholder";

/* ---------- Кастомный блок: врезка (callout) ----------
   HTML: <div class="callout" data-callout data-variant="info|tip|warning">...</div>
   Стили для .callout лежат в lesson_content.css и общие для редактора и плеера SCORM. */
const Callout = Node.create({
  name: "callout",
  group: "block",
  content: "block+",
  defining: true,

  addAttributes() {
    return {
      variant: {
        default: "info",
        parseHTML: (el) => el.getAttribute("data-variant") || "info",
        renderHTML: (attrs) => ({ "data-variant": attrs.variant }),
      },
    };
  },

  parseHTML() {
    return [{ tag: "div[data-callout]" }, { tag: "div.callout" }];
  },

  renderHTML({ HTMLAttributes }) {
    return ["div", mergeAttributes(HTMLAttributes, { "data-callout": "", class: "callout" }), 0];
  },

  addCommands() {
    return {
      toggleCallout:
        (variant = "info") =>
        ({ commands, editor }) => {
          if (editor.isActive("callout", { variant })) return commands.lift("callout");
          if (editor.isActive("callout")) return commands.updateAttributes("callout", { variant });
          return commands.wrapIn("callout", { variant });
        },
    };
  },
});


/* ---------- Видео ----------
   Своё видео:  <div class="video-block" data-video data-src="/media/<id>.mp4"><video src controls></video></div>
   По ссылке:   <div class="video-block" data-video-embed data-src="https://..."><iframe src allowfullscreen></iframe></div>
   В пакет SCORM файлы видео копируются при сборке (см. scorm_builder.py). */
// Встраивать можно любой https-адрес: «код для вставки» из корпоративного портала, YouTube, RuTube и т.д.
// Тексты уроков пишут сами сотрудники, поэтому белого списка доменов нет.
function safeEmbedSrc(src) {
  try {
    const u = new URL(src);
    return u.protocol === "https:" ? u.href : "";
  } catch (e) {
    return "";
  }
}

function videoNodeView(isEmbed) {
  return ({ node, getPos, editor }) => {
    const dom = document.createElement("div");
    dom.className = "video-block";
    dom.setAttribute("contenteditable", "false");
    const media = document.createElement(isEmbed ? "iframe" : "video");
    media.src = node.attrs.src;
    if (isEmbed) {
      media.setAttribute("allowfullscreen", "");
      media.setAttribute("allow", "fullscreen; picture-in-picture");
      media.setAttribute("loading", "lazy");
    } else {
      media.controls = true;
      media.preload = "metadata";
      media.setAttribute("playsinline", "");
    }
    const rm = document.createElement("button");
    rm.type = "button";
    rm.className = "video-remove";
    rm.title = "Убрать видео из урока";
    rm.textContent = "Убрать видео";
    rm.addEventListener("mousedown", (e) => e.preventDefault());
    rm.addEventListener("click", () => {
      const pos = getPos();
      if (typeof pos === "number") editor.chain().focus().deleteRange({ from: pos, to: pos + node.nodeSize }).run();
    });
    dom.appendChild(media);
    dom.appendChild(rm);
    return { dom, stopEvent: () => true, ignoreMutation: () => true };
  };
}

function makeVideoNode(name, isEmbed) {
  return Node.create({
    name,
    group: "block",
    atom: true,
    selectable: true,
    draggable: true,

    addAttributes() {
      return { src: { default: "" } };
    },

    parseHTML() {
      return [{
        tag: isEmbed ? "div[data-video-embed]" : "div[data-video]",
        getAttrs: (el) => {
          const src = el.getAttribute("data-src") || el.querySelector(isEmbed ? "iframe" : "video")?.getAttribute("src") || "";
          if (isEmbed) return safeEmbedSrc(src) ? { src } : false;
          return /^\/media\/[0-9a-f]{12,32}\.(mp4|webm)$/.test(src) ? { src } : false;
        },
      }];
    },

    renderHTML({ node }) {
      const src = node.attrs.src;
      const wrap = { class: "video-block", [isEmbed ? "data-video-embed" : "data-video"]: "", "data-src": src };
      return isEmbed
        ? ["div", wrap, ["iframe", { src, allowfullscreen: "", allow: "fullscreen; picture-in-picture", loading: "lazy" }]]
        : ["div", wrap, ["video", { src, controls: "", preload: "metadata", playsinline: "" }]];
    },

    addNodeView() {
      return videoNodeView(isEmbed);
    },
  });
}

const Video = makeVideoNode("video", false);
const VideoEmbed = makeVideoNode("videoEmbed", true);


/* ---------- Картинка из документа ----------
   <figure class="lesson-image"><img src="/media/<id>.jpg" alt><figcaption>подпись</figcaption></figure>
   Без этого узла редактор выбросил бы картинку при первом же сохранении. */
const LessonImage = Node.create({
  name: "lessonImage",
  group: "block",
  atom: true,
  selectable: true,

  addAttributes() {
    // manual: картинку вставил автор, а не взяли из документа; такие картинки не пропадают при перегенерации урока
    return { src: { default: "" }, alt: { default: "" }, caption: { default: "" }, manual: { default: false } };
  },

  parseHTML() {
    return [{
      tag: "figure.lesson-image",
      getAttrs: (el) => {
        const img = el.querySelector("img");
        const src = img ? img.getAttribute("src") || "" : "";
        if (!/^\/media\/[0-9a-f]{12,32}\.(jpg|png)$/.test(src)) return false;
        const cap = el.querySelector("figcaption");
        return { src, alt: img.getAttribute("alt") || "", caption: cap ? cap.textContent : "", manual: el.hasAttribute("data-manual") };
      },
    }];
  },

  renderHTML({ node }) {
    const { src, alt, caption, manual } = node.attrs;
    const out = ["figure", manual ? { class: "lesson-image", "data-manual": "" } : { class: "lesson-image" },
      ["img", { src, alt: alt || caption || "Иллюстрация" }]];
    if (caption) out.push(["figcaption", {}, caption]);
    return out;
  },

  addNodeView() {
    return ({ node, getPos, editor }) => {
      const dom = document.createElement("figure");
      dom.className = "lesson-image";
      dom.setAttribute("contenteditable", "false");
      const img = document.createElement("img");
      img.src = node.attrs.src;
      img.alt = node.attrs.alt || node.attrs.caption || "Иллюстрация";
      dom.appendChild(img);
      if (node.attrs.caption) {
        const cap = document.createElement("figcaption");
        cap.textContent = node.attrs.caption;
        dom.appendChild(cap);
      }
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "video-remove";
      rm.textContent = "Убрать картинку";
      rm.addEventListener("mousedown", (e) => e.preventDefault());
      rm.addEventListener("click", () => {
        const pos = getPos();
        if (typeof pos === "number") editor.chain().focus().deleteRange({ from: pos, to: pos + node.nodeSize }).run();
      });
      dom.appendChild(rm);
      return { dom, stopEvent: () => true, ignoreMutation: () => true };
    };
  },
});

/* Вставка своей картинки: файл с диска, из буфера (скриншот) или перетаскиванием.
   uploadImage(file) -> Promise<адрес /media/...> | null показывает приложение (app.js). */
function insertImageFile(editor, uploadImage, file, pos) {
  if (!uploadImage) return;
  Promise.resolve(uploadImage(file)).then((src) => {
    if (!src) return;
    const node = { type: "lessonImage", attrs: { src, manual: true } };
    const chain = editor.chain().focus();
    (typeof pos === "number" ? chain.insertContentAt(pos, node) : chain.insertContent(node)).run();
    ensureTrailingParagraph(editor);
  });
}

function pickImage(editor, uploadImage) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "image/png,image/jpeg,image/webp,image/gif";
  input.addEventListener("change", () => {
    if (input.files && input.files[0]) insertImageFile(editor, uploadImage, input.files[0]);
  });
  input.click();
}

/* После видео в конце урока должен остаться пустой абзац: иначе под блоком нельзя поставить курсор и продолжить текст */
function ensureTrailingParagraph(editor) {
  const last = editor.state.doc.lastChild;
  if (last && ["video", "videoEmbed", "lessonImage"].includes(last.type.name)) {
    editor.chain().insertContentAt(editor.state.doc.content.size, { type: "paragraph" }).run();
  }
}

/* Диалог выбора видео показывает приложение (app.js): редактору он возвращает {embed: bool, src} или null */
function insertVideo(editor, requestVideo) {
  if (!requestVideo) return;
  Promise.resolve(requestVideo()).then((v) => {
    if (!v || !v.src) return;
    editor.chain().focus().insertContent({ type: v.embed ? "videoEmbed" : "video", attrs: { src: v.src } }).run();
    ensureTrailingParagraph(editor);
  });
}

/* ---------- Иконки (простые линейные SVG) ---------- */
const svg = (inner) =>
  `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;

const ICONS = {
  undo: svg('<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>'),
  redo: svg('<path d="m15 14 5-5-5-5"/><path d="M20 9H10a6 6 0 0 0 0 12h3"/>'),
  bold: svg('<path d="M7 5h6a3.5 3.5 0 0 1 0 7H7z"/><path d="M7 12h7a3.5 3.5 0 0 1 0 7H7z"/>'),
  italic: svg('<path d="M19 4h-9M14 20H5M15 4 9 20"/>'),
  underline: svg('<path d="M6 4v7a6 6 0 0 0 12 0V4M4 21h16"/>'),
  ul: svg('<path d="M9 6h12M9 12h12M9 18h12"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>'),
  ol: svg('<path d="M10 6h11M10 12h11M10 18h11"/><path d="M4 5.5 5.5 4.5V9M3.5 14.5c.3-.8 2.5-1 2.5.3 0 1.2-2.6 2-2.6 3.2H6.5"/>'),
  quote: svg('<path d="M4 18v-5a5 5 0 0 1 5-5M13 18v-5a5 5 0 0 1 5-5"/>'),
  info: svg('<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>'),
  link: svg('<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>'),
  alignLeft: svg('<path d="M4 6h16M4 10h10M4 14h16M4 18h10"/>'),
  alignCenter: svg('<path d="M4 6h16M7 10h10M4 14h16M7 18h10"/>'),
  alignJustify: svg('<path d="M4 6h16M4 10h16M4 14h16M4 18h16"/>'),
  hr: svg('<path d="M4 12h16"/>'),
  image: svg('<rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="10" r="1.6"/><path d="m21 16-5-5-8 8"/>'),
  video: svg('<rect x="3" y="5" width="14" height="14" rx="3"/><path d="m17 10 4-2.5v9L17 14"/>'),
  clear: svg('<path d="m7 21-4-4 11-11 7 7-6 6z"/><path d="M14 21h7"/>'),
};

/* ---------- Пункты меню блоков по "/" ---------- */
const SLASH_ITEMS = (requestVideo, uploadImage) => [
  { title: "Заголовок", hint: "Крупный заголовок раздела", keys: "заголовок h2 heading", run: (c) => c.setHeading({ level: 2 }) },
  { title: "Подзаголовок", hint: "Заголовок поменьше", keys: "подзаголовок h3 heading", run: (c) => c.setHeading({ level: 3 }) },
  { title: "Маркированный список", hint: "Список с точками", keys: "список маркированный bullet", run: (c) => c.toggleBulletList() },
  { title: "Нумерованный список", hint: "Шаги с цифрами в кружках", keys: "список нумерованный шаги ordered", run: (c) => c.toggleOrderedList() },
  { title: "Важный факт", hint: "Оранжевая врезка", keys: "врезка важный факт callout", run: (c) => c.toggleCallout("info") },
  { title: "Совет", hint: "Зелёная врезка", keys: "врезка совет tip", run: (c) => c.toggleCallout("tip") },
  { title: "Предупреждение", hint: "Красная врезка", keys: "врезка предупреждение внимание warning", run: (c) => c.toggleCallout("warning") },
  { title: "Цитата", hint: "Выделенная цитата", keys: "цитата quote", run: (c) => c.toggleBlockquote() },
  { title: "Картинка", hint: "Загрузить картинку или скриншот", keys: "картинка изображение фото скриншот image",
    run: (c, editor) => { c.run(); pickImage(editor, uploadImage); }, manual: true },
  { title: "Видео", hint: "Загрузить своё видео или вставить ссылку", keys: "видео ролик video youtube",
    run: (c, editor) => { c.run(); insertVideo(editor, requestVideo); }, manual: true },
  { title: "Разделитель", hint: "Горизонтальная линия", keys: "разделитель линия hr", run: (c) => c.setHorizontalRule() },
];

function createSlashMenu(editor, requestVideo, uploadImage) {
  const ITEMS = SLASH_ITEMS(requestVideo, uploadImage);
  const el = document.createElement("div");
  el.className = "slash-menu";
  el.style.display = "none";
  document.body.appendChild(el);

  let open = false;
  let items = [];
  let index = 0;
  let range = null;

  function close() {
    open = false;
    el.style.display = "none";
  }

  function render() {
    el.innerHTML = "";
    items.forEach((it, i) => {
      const row = document.createElement("div");
      row.className = "slash-item" + (i === index ? " active" : "");
      row.innerHTML = `<div class="slash-title"></div><div class="slash-hint"></div>`;
      row.querySelector(".slash-title").textContent = it.title;
      row.querySelector(".slash-hint").textContent = it.hint;
      row.addEventListener("mousedown", (e) => {
        e.preventDefault();
        choose(i);
      });
      el.appendChild(row);
    });
  }

  function choose(i) {
    const it = items[i];
    if (!it || !range) return;
    const r = range;
    close();
    const chain = editor.chain().focus().deleteRange(r);
    if (it.manual) it.run(chain, editor);  // пункт сам запускает цепочку (видео открывает диалог)
    else it.run(chain).run();
  }

  function update() {
    const { selection } = editor.state;
    if (!selection.empty) return close();
    const $from = selection.$from;
    if ($from.parent.type.name !== "paragraph") return close();
    const before = $from.parent.textBetween(0, $from.parentOffset, "\0", "\0");
    const m = before.match(/^\/([^\s/]*)$/);
    if (!m) return close();

    const q = m[1].toLowerCase();
    const found = ITEMS.filter((it) => !q || (it.title + " " + it.keys).toLowerCase().includes(q));
    if (!found.length) return close();

    const wasOpen = open;
    items = found;
    if (!wasOpen || index >= items.length) index = 0;
    range = { from: $from.start(), to: $from.pos };
    open = true;
    render();

    let coords = null;
    try { coords = editor.view.coordsAtPos($from.pos); } catch (e) { /* jsdom */ }
    el.style.display = "block";
    if (coords) {
      el.style.left = Math.min(coords.left, window.innerWidth - 300) + "px";
      el.style.top = coords.bottom + 6 + "px";
    }
  }

  editor.view.dom.addEventListener(
    "keydown",
    (e) => {
      if (!open) return;
      if (e.key === "ArrowDown") { e.preventDefault(); e.stopPropagation(); index = (index + 1) % items.length; render(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); e.stopPropagation(); index = (index - 1 + items.length) % items.length; render(); }
      else if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); e.stopPropagation(); choose(index); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    },
    true
  );
  editor.view.dom.addEventListener("blur", () => setTimeout(close, 120));

  return { update, close, destroy: () => el.remove() };
}

/* ---------- Панель форматирования ---------- */
function buildToolbar(host, editor, requestVideo, uploadImage) {
  host.innerHTML = "";
  host.classList.add("le-toolbar");
  const refreshers = [];

  function sep() {
    const s = document.createElement("span");
    s.className = "le-sep";
    host.appendChild(s);
  }

  function btn(icon, title, onClick, isActive) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "le-btn";
    b.title = title;
    b.setAttribute("aria-label", title);
    b.innerHTML = icon;
    b.addEventListener("mousedown", (e) => e.preventDefault());
    b.addEventListener("click", () => onClick(editor.chain().focus()));
    host.appendChild(b);
    if (isActive) refreshers.push(() => b.classList.toggle("active", !!isActive()));
    return b;
  }

  btn(ICONS.undo, "Отменить (Ctrl+Z)", (c) => c.undo().run());
  btn(ICONS.redo, "Повторить (Ctrl+Y)", (c) => c.redo().run());
  sep();

  const sel = document.createElement("select");
  sel.className = "le-select";
  sel.title = "Стиль текста";
  [["p", "Обычный текст"], ["h2", "Заголовок"], ["h3", "Подзаголовок"]].forEach(([v, t]) => {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = t;
    sel.appendChild(o);
  });
  sel.addEventListener("change", () => {
    const c = editor.chain().focus();
    if (sel.value === "p") c.setParagraph().run();
    else c.setHeading({ level: sel.value === "h2" ? 2 : 3 }).run();
  });
  host.appendChild(sel);
  refreshers.push(() => {
    sel.value = editor.isActive("heading", { level: 2 }) ? "h2" : editor.isActive("heading", { level: 3 }) ? "h3" : "p";
  });
  sep();

  btn(ICONS.bold, "Жирный (Ctrl+B)", (c) => c.toggleBold().run(), () => editor.isActive("bold"));
  btn(ICONS.italic, "Курсив (Ctrl+I)", (c) => c.toggleItalic().run(), () => editor.isActive("italic"));
  btn(ICONS.underline, "Подчёркнутый (Ctrl+U)", (c) => c.toggleUnderline().run(), () => editor.isActive("underline"));
  btn(ICONS.link, "Ссылка", () => {
    const prev = editor.getAttributes("link").href || "";
    const url = window.prompt("Адрес ссылки (оставьте пустым, чтобы убрать):", prev);
    if (url === null) return;
    if (url.trim() === "") editor.chain().focus().extendMarkRange("link").unsetLink().run();
    else editor.chain().focus().extendMarkRange("link").setLink({ href: url.trim() }).run();
  }, () => editor.isActive("link"));
  sep();

  btn(ICONS.ul, "Маркированный список", (c) => c.toggleBulletList().run(), () => editor.isActive("bulletList"));
  btn(ICONS.ol, "Нумерованный список (шаги)", (c) => c.toggleOrderedList().run(), () => editor.isActive("orderedList"));
  btn(ICONS.quote, "Цитата", (c) => c.toggleBlockquote().run(), () => editor.isActive("blockquote"));
  btn(ICONS.info, "Врезка «Важный факт»", (c) => c.toggleCallout("info").run(), () => editor.isActive("callout"));
  sep();

  btn(ICONS.alignLeft, "По левому краю", (c) => c.setTextAlign("left").run(), () => editor.isActive({ textAlign: "left" }));
  btn(ICONS.alignCenter, "По центру", (c) => c.setTextAlign("center").run(), () => editor.isActive({ textAlign: "center" }));
  btn(ICONS.alignJustify, "По ширине", (c) => c.setTextAlign("justify").run(), () => editor.isActive({ textAlign: "justify" }));
  sep();

  if (uploadImage) btn(ICONS.image, "Картинка: загрузить файл (или вставьте скриншот через Ctrl+V)", () => pickImage(editor, uploadImage));
  if (requestVideo) btn(ICONS.video, "Видео: загрузить своё или вставить ссылку", () => insertVideo(editor, requestVideo));
  btn(ICONS.hr, "Разделитель", (c) => c.setHorizontalRule().run());
  btn(ICONS.clear, "Очистить форматирование", (c) => c.clearNodes().unsetAllMarks().run());

  const refresh = () => refreshers.forEach((f) => f());
  refresh();
  return refresh;
}

/* ---------- Публичный API ---------- */
function create({ toolbar, mount, html, onChange, requestVideo, uploadImage }) {
  let slash = null;
  let refreshToolbar = () => {};

  const editor = new Editor({
    element: mount,
    content: html || "",
    extensions: [
      StarterKit.configure({ heading: { levels: [2, 3] } }),
      Underline,
      Link.configure({ openOnClick: false, autolink: true, HTMLAttributes: { rel: "noopener noreferrer", target: "_blank" } }),
      TextAlign.configure({ types: ["heading", "paragraph"] }),
      Placeholder.configure({ placeholder: "Начните писать или нажмите «/» для вставки блоков…" }),
      Callout,
      Video,
      VideoEmbed,
      LessonImage,
    ],
    editorProps: {
      attributes: { class: "lesson-content le-content", spellcheck: "true" },
      // скриншот из буфера обмена (только если вместе с ним нет текста: иначе это обычная вставка из Word / Excel)
      handlePaste: (view, event) => {
        const cd = event.clipboardData;
        if (!uploadImage || !cd || cd.getData("text/plain")) return false;
        const files = Array.from(cd.files || []).filter((f) => f.type.startsWith("image/"));
        if (!files.length) return false;
        event.preventDefault();
        files.forEach((f) => insertImageFile(editor, uploadImage, f));
        return true;
      },
      handleDrop: (view, event) => {
        const files = Array.from((event.dataTransfer && event.dataTransfer.files) || []).filter((f) => f.type.startsWith("image/"));
        if (!uploadImage || !files.length) return false;
        event.preventDefault();
        const at = view.posAtCoords({ left: event.clientX, top: event.clientY });
        files.forEach((f) => insertImageFile(editor, uploadImage, f, at ? at.pos : undefined));
        return true;
      },
    },
    onUpdate: ({ editor }) => {
      ensureTrailingParagraph(editor);
      onChange && onChange(editor.getHTML());
    },
    onTransaction: () => {
      refreshToolbar();
      slash && slash.update();
    },
  });

  refreshToolbar = buildToolbar(toolbar, editor, requestVideo, uploadImage);
  slash = createSlashMenu(editor, requestVideo, uploadImage);

  return {
    setHTML(value) {
      editor.commands.setContent(value || "", false);
      ensureTrailingParagraph(editor);
      refreshToolbar();
    },
    getHTML() {
      return editor.getHTML();
    },
    focus() {
      editor.commands.focus();
    },
    setEditable(on) {
      editor.setEditable(!!on);
      toolbar.classList.toggle("le-disabled", !on);
    },
    destroy() {
      slash && slash.destroy();
      editor.destroy();
    },
  };
}

window.LessonEditor = { create };
