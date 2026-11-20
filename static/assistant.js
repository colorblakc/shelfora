// Mira, the Shelfora library assistant. Everything the model returns is built with DOM nodes and
// textContent, never innerHTML, so replies cannot inject markup. The launcher appears only once this
// script has run, so the widget is an enhancement.
(() => {
  const root = document.getElementById("mira");
  if (!root) return;
  const path = location.pathname;
  const launch = root.querySelector(".mira-toggle");
  const panel = root.querySelector(".mira-panel");
  const log = root.querySelector(".mira-messages");
  const chipBox = root.querySelector(".mira-chips");
  const form = root.querySelector(".mira-form");
  const field = form.elements.message;
  const send = form.querySelector(".mira-send");
  const KEY = "shelfora-mira-v1";
  const KEEP = 12;
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)");
  const name = (root.dataset.name || "").trim();
  const signedIn = root.dataset.signedIn === "1";
  let messages = [];      // {role, content, books?, scripted?}
  let busy = false;
  let lastChips = null;

  // ---------- page context ----------
  function pageKind() {
    if (/^\/books\/\d+\/?$/.test(path)) return "book";
    if (path.startsWith("/reading-room")) return "room";
    if (path.startsWith("/shelf")) return "shelf";
    if (path.startsWith("/catalogue")) return "catalogue";
    if (path === "/") return "home";
    return "other";
  }
  const kind = pageKind();
  const CHIPS = {
    home: ["What's available to read?", "How do I book a seat?", "Where is my shelf?"],
    catalogue: ["Suggest something to read", "Is a book available?", "How do I reserve a book?"],
    book: ["Is this book available?", "How do I reserve it?", "Show similar books"],
    room: ["How do I book a seat?", "Which sessions are open?", "Can I release a seat?"],
    shelf: ["When are my books due?", "How do I cancel a request?", "How do fines work?"],
    other: ["Is a book available?", "How do I book a seat?", "How do fines work?"],
  };
  function startChips() {
    const chips = (CHIPS[kind] || CHIPS.other).slice();
    return (signedIn ? chips : chips.filter(c => !/\bmy\b/i.test(c))).slice(0, 3);
  }
  if (kind === "room" || kind === "book") root.classList.add("is-compact");

  // ---------- storage (sessionStorage may be unavailable) ----------
  function load() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(KEY) || "[]");
      if (Array.isArray(saved)) messages = saved.filter(m => m && (m.role === "user" || m.role === "assistant") && typeof m.content === "string").slice(-KEEP);
    } catch (e) { messages = []; }
  }
  function save() {
    try { sessionStorage.setItem(KEY, JSON.stringify(messages.slice(-KEEP))); } catch (e) { /* private mode: carry on */ }
  }

  // ---------- safe markdown-lite: **bold**, - bullets, 1. lists, [label](/path) ----------
  const INLINE = /\*\*(.+?)\*\*|\[([^\]]{1,80})\]\((\/[^)\s]{0,200})\)/g;
  function inline(parent, text) {
    let last = 0, m;
    INLINE.lastIndex = 0;
    while ((m = INLINE.exec(text))) {
      if (m.index > last) parent.append(text.slice(last, m.index));
      if (m[1] !== undefined) {
        const strong = document.createElement("strong");
        strong.textContent = m[1];
        parent.append(strong);
      } else if (!m[3].startsWith("//")) {
        const a = document.createElement("a");
        a.href = m[3];
        a.textContent = m[2];
        parent.append(a);
      } else parent.append(m[0]);
      last = INLINE.lastIndex;
    }
    if (last < text.length) parent.append(text.slice(last));
  }
  function render(container, text) {
    let list = null, para = null;
    String(text).replace(/\r/g, "").split("\n").forEach(raw => {
      const line = raw.trim();
      if (!line) { list = null; para = null; return; }
      const bullet = line.match(/^[-*•]\s+(.*)$/);
      const number = line.match(/^\d+[.)]\s+(.*)$/);
      if (bullet || number) {
        const tag = bullet ? "ul" : "ol";
        if (!list || list.localName !== tag) { list = document.createElement(tag); container.append(list); }
        para = null;
        const li = document.createElement("li");
        inline(li, (bullet || number)[1]);
        list.append(li);
      } else {
        list = null;
        if (!para) { para = document.createElement("p"); container.append(para); }
        else para.append(document.createElement("br"));
        inline(para, line);
      }
    });
  }

  // ---------- book cards ----------
  const safeUrl = url => typeof url === "string" && url.startsWith("/") && !url.startsWith("//") ? url : "#";
  function placeholder() {
    const box = document.createElement("span");
    box.className = "mira-card-img";
    box.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v15H6.5A1.5 1.5 0 0 0 5 19.5v-15ZM5 19.5A1.5 1.5 0 0 0 6.5 21H19v-3"/></svg>'; // static markup only
    return box;
  }
  function cards(books) {
    const list = document.createElement("ul");
    list.className = "mira-cards";
    list.setAttribute("aria-label", "Books mentioned");
    books.slice(0, 4).forEach(b => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.className = "mira-card";
      a.href = safeUrl(b.url);
      let thumb;
      if (typeof b.image === "string" && b.image.startsWith("/") && !b.image.startsWith("//")) {
        thumb = document.createElement("img");
        thumb.className = "mira-card-img";
        thumb.src = b.image; thumb.alt = ""; thumb.width = 42; thumb.height = 56; thumb.loading = "lazy";
        thumb.addEventListener("error", () => thumb.replaceWith(placeholder()));
      } else thumb = placeholder();
      const body = document.createElement("span");
      body.className = "mira-card-body";
      const title = document.createElement("span");
      title.className = "mira-card-title";
      title.textContent = b.title;
      const author = document.createElement("span");
      author.className = "mira-card-author";
      author.textContent = b.author;
      const avail = document.createElement("span");
      const n = Number(b.available) || 0;
      avail.className = "mira-avail" + (n > 0 ? "" : " is-out");
      avail.textContent = n > 0 ? "Available" : "All copies out";
      body.append(title, author, avail);
      a.append(thumb, body);
      li.append(a);
      list.append(li);
    });
    return list;
  }

  // ---------- mascot ----------
  const faceUrl = root.dataset.face || "";
  function botRow() {
    const row = document.createElement("div");
    row.className = "mira-msg mira-msg--bot";
    const img = document.createElement("img");
    img.className = "mira-avatar";
    img.src = faceUrl; img.alt = ""; img.width = 28; img.height = 28;
    const body = document.createElement("div");
    body.className = "mira-msg-body";
    row.append(img, body);
    return {row, body};
  }

  // ---------- message rendering ----------
  function reveal(node, stick) {
    const behavior = calm.matches ? "auto" : "smooth";
    if (node.offsetHeight > log.clientHeight - 24 && !node.classList.contains("mira-msg--user")) log.scrollTo({top: node.offsetTop - 12, behavior});
    else if (stick) log.scrollTo({top: log.scrollHeight, behavior});
  }
  function addMessage(msg, stick = true) {
    const user = msg.role === "user";
    let row, body;
    if (user) {
      row = document.createElement("div");
      row.className = "mira-msg mira-msg--user";
      body = row;
    } else ({row, body} = botRow());
    const bubble = document.createElement("div");
    bubble.className = "mira-bubble";
    if (user) bubble.textContent = msg.content; else render(bubble, msg.content);
    body.append(bubble);
    if (msg.books && msg.books.length) body.append(cards(msg.books));
    log.append(row);
    reveal(row, stick);
    return row;
  }
  function typing() {
    const {row, body} = botRow();
    const dots = document.createElement("div");
    dots.className = "mira-typing";
    dots.setAttribute("role", "status");
    dots.innerHTML = '<span class="visually-hidden">Mira is typing</span><i></i><i></i><i></i>'; // static markup only
    body.append(dots);
    log.append(row);
    log.scrollTo({top: log.scrollHeight});
    return row;
  }
  function showChips(list) {
    chipBox.replaceChildren();
    (list || []).slice(0, 3).forEach(text => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = text;
      b.addEventListener("click", () => ask(text));
      chipBox.append(b);
    });
    chipBox.hidden = !chipBox.children.length;
    chipBox.classList.toggle("is-slim", messages.some(m => m.role === "user"));
  }

  // ---------- greeting (scripted, no API call) ----------
  function greeting() {
    const h = new Date().getHours();
    const part = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
    const hello = name ? part + ", " + name + "." : part + ".";
    const own = signedIn ? ", and check your own shelf and seat bookings" : "";
    return hello + " I'm Mira. I can look up books and availability, explain reservations, fines and the reading room" + own + ".\n\nWhat are you after?";
  }
  function showGreeting() {
    const msg = {role: "assistant", content: greeting(), scripted: true};
    messages = [msg];
    save();
    log.replaceChildren();
    addMessage(msg, false);
    lastChips = null;
    showChips(startChips());
  }
  function paint() {
    log.replaceChildren();
    messages.forEach(m => addMessage(m, false));
    log.scrollTop = log.scrollHeight;
    showChips(lastChips || startChips());
  }

  // ---------- open / close ----------
  function setOpen(open) {
    panel.hidden = !open;
    launch.hidden = open;
    launch.setAttribute("aria-expanded", String(open));
    if (open) {
      if (!messages.length) showGreeting(); else paint();
      keepClear();
      const touch = window.matchMedia("(pointer: coarse)").matches;
      (touch ? panel.querySelector(".mira-icon") : field).focus({preventScroll: true});
    } else launch.focus({preventScroll: true});
  }
  launch.addEventListener("click", () => setOpen(true));
  root.querySelector("[data-mira-close]").addEventListener("click", () => setOpen(false));
  root.querySelector("[data-mira-clear]").addEventListener("click", () => {
    if (busy) return;
    showGreeting();
    field.value = "";
    field.focus({preventScroll: true});
  });
  root.addEventListener("keydown", e => {
    if (e.key === "Escape" && !panel.hidden) { e.stopPropagation(); setOpen(false); }
  });

  // Phones: size the sheet to the visible area so the on-screen keyboard never hides the composer.
  function keepClear() {
    const vv = window.visualViewport;
    if (!vv || panel.hidden) return;
    root.style.setProperty("--mira-kb", Math.max(0, window.innerHeight - vv.height - vv.offsetTop) + "px");
    root.style.setProperty("--mira-sheet-h", vv.height + "px");
  }
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", keepClear);
    window.visualViewport.addEventListener("scroll", keepClear);
  }

  // ---------- asking ----------
  function history(skipLast) {
    let real = messages.filter(m => !m.scripted);
    if (skipLast) real = real.slice(0, -1); // the question being retried is already stored
    return real.slice(-6).map(m => ({role: m.role, content: m.content.slice(0, 500)}));
  }
  function fail(question, text) {
    const {row, body} = botRow();
    const bubble = document.createElement("div");
    bubble.className = "mira-bubble mira-bubble--error";
    const p = document.createElement("p");
    p.textContent = text;
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "mira-retry";
    retry.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.4-5.7M20 4v5h-5"/></svg>'; // static markup only
    retry.append("Retry");
    retry.addEventListener("click", () => { row.remove(); ask(question, true); });
    bubble.append(p, retry);
    body.append(bubble);
    log.append(row);
    reveal(row, true);
    retry.focus({preventScroll: true});
  }
  async function ask(text, isRetry = false) {
    text = (text || "").trim();
    if (busy || text.length < 2) return;
    busy = true;
    send.disabled = true;
    chipBox.hidden = true;
    const prior = history(isRetry);
    if (!isRetry) {
      const mine = {role: "user", content: text};
      messages.push(mine);
      addMessage(mine);
      save();
    }
    const wait = typing();
    const data = new FormData();
    data.append("message", text);
    data.append("page", path);
    data.append("history", JSON.stringify(prior));
    data.append("_token", form.elements._token.value);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(root.dataset.askUrl || form.action, {
        method: "POST", body: data, signal: controller.signal, credentials: "same-origin", headers: {"Accept": "application/json"},
      });
      let result = null;
      try { result = await response.json(); } catch (e) { result = null; }
      wait.remove();
      if (!result || typeof result.reply !== "string") throw new Error("bad response");
      lastChips = Array.isArray(result.suggestions) && result.suggestions.length ? result.suggestions : startChips();
      if (!response.ok) {
        // Server messages (rate limit, not configured, service down) are shown as they are, with a way to retry.
        showChips(lastChips);
        fail(text, result.reply);
        return;
      }
      const msg = {role: "assistant", content: result.reply, books: Array.isArray(result.books) ? result.books.slice(0, 4) : []};
      messages.push(msg);
      save();
      showChips(lastChips); // before scrolling, so the log's final height is settled
      addMessage(msg);
    } catch (e) {
      wait.remove();
      showChips(lastChips || startChips());
      fail(text, controller.signal.aborted
        ? "That took too long. Check your connection and try again."
        : "I couldn't reach the library just now. Your question is saved; try again in a moment.");
    } finally {
      clearTimeout(timer);
      busy = false;
      send.disabled = false;
      if (!panel.hidden && !document.activeElement.classList.contains("mira-retry")) field.focus({preventScroll: true});
    }
  }

  form.addEventListener("submit", e => {
    e.preventDefault();
    const text = field.value;
    if (text.trim().length < 2) { field.focus(); return; }
    field.value = "";
    ask(text);
  });

  load();
  launch.hidden = false;
})();
