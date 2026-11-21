/* Endless Fiction — reader: four modes, chapter shelf, glossary meanings. */
(() => {
  "use strict";

  const { api, gate, logout, escapeHtml, modeName, jobStatus, toast } = EF;
  const $ = (id) => document.getElementById(id);

  const MODES = ["raw", "web", "web_plus", "ai"];
  const state = {
    novels: [],
    chapters: [],
    novelId: "",
    chapterId: "",
    // First visit opens the source text: an empty "not translated yet" page is
    // a worse landing than the chapter itself. The last used mode is remembered.
    mode: MODES.includes(localStorage.getItem("ef_mode")) ? localStorage.getItem("ef_mode") : "raw",
    language: new URLSearchParams(location.search).get("language") || "en",
    admin: false,
    term: null,
    poll: 0,
    loading: false,
  };

  function pathChapter() {
    const parts = location.pathname.split("/").filter(Boolean);
    if (parts[0] === "read" && parts[1]) return decodeURIComponent(parts[1]);
    return new URLSearchParams(location.search).get("chapter") || "";
  }

  function chapterUrl(chapterId) {
    const params = new URLSearchParams();
    if (state.mode !== "ai") params.set("mode", state.mode);
    const query = params.toString();
    return `/read/${encodeURIComponent(chapterId)}${query ? `?${query}` : ""}`;
  }

  function remember() {
    try {
      localStorage.setItem("ef_mode", state.mode);
      localStorage.setItem("ef_language", state.language);
      if (state.novelId) localStorage.setItem("ef_novel", state.novelId);
      if (state.chapterId) localStorage.setItem("ef_chapter", state.chapterId);
    } catch (_) {
      /* ignore */
    }
  }

  function status(text) {
    EF.setText("statusPill", text);
  }

  function paintModes() {
    document.querySelectorAll("#modes .mode").forEach((button) => {
      button.classList.toggle("active", button.dataset.mode === state.mode);
    });
  }

  function paintChapters() {
    document.querySelectorAll(".chap").forEach((node) => {
      node.classList.toggle("active", node.dataset.id === state.chapterId);
    });
    const active = document.querySelector(".chap.active");
    if (active) active.scrollIntoView({ block: "nearest" });
  }

  async function boot(user) {
    state.admin = EF.isAdmin(user);
    $("translateButton").hidden = !state.admin;
    $("adminLink").hidden = !state.admin;
    paintModes();
    await loadNovels();

    const fromPath = pathChapter();
    const wanted = fromPath || localStorage.getItem("ef_chapter") || "";
    if (wanted) {
      await openChapter(wanted, { push: false });
    } else if (state.chapters.length) {
      go(state.chapters[0].id, { replace: true });
    } else {
      $("content").innerHTML = '<div class="empty">This novel has no chapters yet.</div>';
    }
  }

  async function loadNovels() {
    const page = await api("/api/novels?limit=100");
    state.novels = page.items || [];
    const select = $("novelSelect");
    select.innerHTML = "";
    for (const novel of state.novels) {
      const option = document.createElement("option");
      option.value = novel.id;
      option.textContent = novel.title;
      select.appendChild(option);
    }
    const remembered = localStorage.getItem("ef_novel");
    const found = state.novels.find((novel) => novel.id === remembered);
    state.novelId = (found && found.id) || (state.novels[0] && state.novels[0].id) || "";
    if (state.novelId) {
      select.value = state.novelId;
      await loadChapters();
    } else {
      $("chapterList").innerHTML = '<div class="empty">No novels in the library yet.</div>';
    }
  }

  async function loadChapters() {
    const list = [];
    let offset = 0;
    let total = 0;
    $("chapterList").innerHTML = '<div class="empty">Loading chapters…</div>';
    for (;;) {
      const page = await api(
        `/api/novel/${state.novelId}/chapters?offset=${offset}&limit=100`,
      );
      const items = page.items || [];
      list.push(...items);
      total = Number(page.total || list.length);
      if (!items.length) break;
      offset += items.length;
      if (offset >= total) break;
    }
    state.chapters = list;
    EF.setText("chapterCount", `${total}`);
    const host = $("chapterList");
    const fragment = document.createDocumentFragment();
    list.forEach((chapter) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "chap";
      button.dataset.id = chapter.id;
      button.dataset.serial = String(chapter.serial);
      button.dataset.title = chapter.title || "";
      button.innerHTML = `<span class="serial">${chapter.serial}.</span>${escapeHtml(chapter.title || "")}`;
      button.addEventListener("click", () => {
        $("shelf").classList.remove("open");
        go(chapter.id);
      });
      fragment.appendChild(button);
    });
    host.innerHTML = "";
    host.appendChild(fragment);
    filterChapters();
  }

  function filterChapters() {
    const query = ($("chapterSearch").value || "").trim().toLowerCase();
    document.querySelectorAll(".chap").forEach((node) => {
      const haystack = `${node.dataset.serial} ${node.dataset.title}`.toLowerCase();
      node.hidden = Boolean(query) && !haystack.includes(query);
    });
  }

  function go(chapterId, options = {}) {
    const url = chapterUrl(chapterId);
    if (options.replace) history.replaceState({ chapterId }, "", url);
    else if (options.push !== false) history.pushState({ chapterId }, "", url);
    openChapter(chapterId, options);
  }

  async function openChapter(chapterId, options = {}) {
    clearTimeout(state.poll);
    state.chapterId = chapterId;
    state.loading = true;
    paintChapters();
    remember();
    const content = $("content");
    content.innerHTML = '<div class="empty">Loading…</div>';
    status("loading…");

    // Crawling is free, so Raw may fetch on open. A translated mode must never
    // start a job by itself: spending quota is the admin's Translate button.
    const params = new URLSearchParams({ mode: state.mode });
    if (state.mode === "raw") params.set("auto_fetch", "true");
    else params.set("language", state.language);

    try {
      const data = await api(
        `/api/chapter/${encodeURIComponent(chapterId)}/read?${params.toString()}`,
      );
      render(data);
      if (!options.push && location.pathname !== chapterUrl(chapterId).split("?")[0]) {
        history.replaceState({ chapterId }, "", chapterUrl(chapterId));
      }
    } catch (error) {
      renderFailure(error);
    } finally {
      state.loading = false;
    }
  }

  function renderFailure(error) {
    const message = String(error.message || error);
    const known = state.chapters.find((chapter) => chapter.id === state.chapterId);
    if (known) $("chapterTitle").textContent = known.title || "";
    $("chapterSub").textContent = modeName(state.mode);
    status("unavailable");
    const engine = /keyless|blocked by Google|captcha|Translation Failure|bing/i.test(message);
    if (engine) {
      $("content").innerHTML = `<div class="notice"><strong>The keyless translator is unavailable</strong>${
        state.admin
          ? "Google is refusing this server's address (captcha) and Bing's auth endpoint is down. A saved Gemini key still powers AI mode."
          : "Try Raw mode, or an AI translation if one already exists."
      }</div>`;
      return;
    }
    const unavailable = /not available|not enabled|tier|permission|forbidden/i.test(message);
    if (unavailable) {
      $("content").innerHTML = `<div class="notice"><strong>No ${escapeHtml(modeName(state.mode))} version yet</strong>${
        state.admin
          ? "Use <em>Translate chapter</em> to produce it."
          : "Only the administrator can start new translation work. Raw mode always works."
      }</div>`;
      return;
    }
    $("content").innerHTML = `<div class="notice"><strong>Could not load this chapter</strong>${escapeHtml(message)}</div>`;
  }

  function render(data) {
    const chapter = data.chapter || {};
    const novel = data.novel || {};
    document.title = `${chapter.title || "Chapter"} — ${novel.title || "Endless Fiction"}`;
    $("chapterTitle").textContent = chapter.title || "";
    const words = data.word_count ? `${EF.num(data.word_count)} words` : "";
    const engine = data.translation_engine ? ` · ${data.translation_engine}` : "";
    $("chapterSub").textContent = [novel.title, modeName(data.mode || state.mode), words]
      .filter(Boolean)
      .join(" · ");
    status(`${modeName(data.mode || state.mode).toUpperCase()}${engine}`);

    const content = $("content");
    if (data.content) {
      content.innerHTML = data.content;
      content.querySelectorAll(".glossary-term").forEach((node) => {
        node.addEventListener("click", () => openTerm(node));
      });
    } else if (data.job && !data.job.is_done) {
      content.innerHTML = `<div class="notice"><strong>${escapeHtml(modeName(state.mode))} is being prepared</strong>This page refreshes when the job finishes.</div>`;
    } else {
      content.innerHTML = `<div class="notice"><strong>No ${escapeHtml(modeName(data.mode || state.mode))} content yet</strong>${
        state.admin
          ? "Use <em>Translate chapter</em> to produce it."
          : "Only the administrator can start new translation work. Raw mode always works."
      }</div>`;
    }

    $("prevButton").disabled = !data.previous_id;
    $("nextButton").disabled = !data.next_id;
    $("prevButton").onclick = () => data.previous_id && go(data.previous_id);
    $("nextButton").onclick = () => data.next_id && go(data.next_id);

    if (data.job && !data.job.is_done) pollJob(data.job);
    if (novel.id) {
      state.novelId = novel.id;
      if ($("novelSelect").value !== novel.id) $("novelSelect").value = novel.id;
      remember();
    }
  }

  function pollJob(job) {
    clearTimeout(state.poll);
    const progress = (latest) =>
      latest.total > 1 ? `${latest.done}/${latest.total}` : `${latest.done || 0}`;
    const tick = async () => {
      let latest;
      try {
        latest = await api(`/api/job/${job.id}`);
      } catch (_) {
        return;
      }
      status(`${modeName(state.mode).toUpperCase()} · ${jobStatus(latest.status)} ${progress(latest)}`);
      if (latest.is_done) {
        if (latest.status === 3 || latest.error) {
          toast(latest.error || "Translation failed", "error");
        }
        await openChapter(state.chapterId, { push: false });
        return;
      }
      state.poll = setTimeout(tick, 2500);
    };
    state.poll = setTimeout(tick, 1200);
  }

  // -------------------------------------------------------------- terms

  async function openTerm(node) {
    const handle = node.dataset.g;
    if (!handle || !state.novelId) return;
    state.term = { handle, node };
    try {
      const card = await api(
        `/api/translation/${state.novelId}/glossary/card/${encodeURIComponent(handle)}?language=${encodeURIComponent(state.language)}`,
      );
      $("termSource").textContent = (card && card.source) || node.textContent;
      const meta = [];
      if (card && card.reading) meta.push(card.reading);
      if (card && card.category) meta.push(String(card.category).replace(/_/g, " "));
      if (card && card.freq) meta.push(`${card.freq}×`);
      if (card && card.locked) meta.push("locked");
      $("termMeta").textContent = meta.join(" · ") || "No meaning decided yet";
      $("termGlossary").value = (card && card.admin_rendering) || "";
      $("termOverride").value = (card && card.override) || "";
      $("termDialog").showModal();
      $("termOverride").focus();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function closeTerm() {
    $("termDialog").close();
    state.term = null;
  }

  async function saveGlossary() {
    if (!state.term) return;
    const value = $("termGlossary").value.trim();
    try {
      await api(
        `/api/translation/${state.novelId}/glossary/terms/${encodeURIComponent(state.term.handle)}?language=${encodeURIComponent(state.language)}`,
        { method: "PUT", body: JSON.stringify({ rendering: value }) },
      );
      toast(value ? "Glossary meaning saved" : "Glossary meaning cleared", "ok");
      closeTerm();
      await openChapter(state.chapterId, { push: false });
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function saveOverride() {
    if (!state.term) return;
    const value = $("termOverride").value.trim();
    try {
      await api(
        `/api/translation/${state.novelId}/glossary/card/${encodeURIComponent(state.term.handle)}?language=${encodeURIComponent(state.language)}`,
        { method: "PUT", body: JSON.stringify({ rendering: value }) },
      );
      if (value && state.term.node) state.term.node.textContent = value;
      toast(value ? "Your meaning saved" : "Back to the glossary meaning", "ok");
      closeTerm();
      if (!value) await openChapter(state.chapterId, { push: false });
    } catch (error) {
      toast(error.message, "error");
    }
  }

  // ------------------------------------------------------------- events

  document.querySelectorAll("#modes .mode").forEach((button) => {
    button.addEventListener("click", async () => {
      const mode = button.dataset.mode;
      if (!MODES.includes(mode) || mode === state.mode) return;
      state.mode = mode;
      remember();
      paintModes();
      if (state.chapterId) {
        history.replaceState({ chapterId: state.chapterId }, "", chapterUrl(state.chapterId));
        await openChapter(state.chapterId, { push: false });
      }
    });
  });

  $("translateButton").addEventListener("click", async () => {
    if (!state.chapterId) return;
    const mode = state.mode === "raw" ? "ai" : state.mode;
    try {
      status("starting…");
      const job = await api(
        `/api/chapter/${encodeURIComponent(state.chapterId)}/translate?language=${encodeURIComponent(state.language)}&mode=${mode}`,
      );
      pollJob(job);
    } catch (error) {
      toast(error.message, "error");
      status("idle");
    }
  });

  $("novelSelect").addEventListener("change", async (event) => {
    state.novelId = event.target.value;
    state.chapterId = "";
    history.replaceState({}, "", "/read");
    remember();
    try {
      await loadChapters();
      if (state.chapters.length) go(state.chapters[0].id, { replace: true });
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $("chapterSearch").addEventListener("input", filterChapters);
  $("menuButton").addEventListener("click", () => $("shelf").classList.toggle("open"));
  $("signOut").addEventListener("click", logout);

  $("termCancel").addEventListener("click", closeTerm);
  $("termSaveGlossary").addEventListener("click", saveGlossary);
  $("termSaveOverride").addEventListener("click", saveOverride);
  $("termUseGlossary").addEventListener("click", async () => {
    $("termOverride").value = "";
    await saveOverride();
  });

  document.addEventListener("keydown", (event) => {
    if (event.target.matches("input, textarea, select")) return;
    if ($("termDialog").open) return;
    if (event.key === "ArrowRight" && !$("nextButton").disabled) $("nextButton").click();
    if (event.key === "ArrowLeft" && !$("prevButton").disabled) $("prevButton").click();
  });

  window.addEventListener("popstate", () => {
    const id = pathChapter();
    if (id && id !== state.chapterId) openChapter(id, { push: false });
  });

  gate(boot).catch((error) => toast(error.message, "error"));
})();
