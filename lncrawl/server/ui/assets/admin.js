/* Endless Fiction — Admin Dashboard. */
(() => {
  "use strict";

  const { api, gate, logout, escapeHtml, escapeAttr, num, jobStatus, jobType, toast } = EF;
  const $ = (id) => document.getElementById(id);

  const TITLES = {
    overview: "Overview",
    keys: "Keys & models",
    extract: "Glossary extraction",
    glossary: "Glossary translation",
    chapters: "Chapter translation",
    settings: "Settings",
  };
  // The translator's working set, in the order the classifier prompt lists it;
  // the review sheet's dropdown and filter come from here, so they cannot drift
  // from the enum.
  const TERM_CATEGORIES = [
    "character",
    "location",
    "sect_org",
    "rank_realm",
    "technique",
    "artifact",
    "special",
    "honorific",
    "creature",
    "race",
    "formation",
    "constitution",
    "currency",
    "concept",
    "idiom",
  ];
  const TRANSLATION_JOB_TYPES = new Set([2, 3, 7, 8, 12, 13]);
  // The Gemini catalogue, with the free-tier limits of one key. Numbers here are
  // only defaults for a new row: the pool trusts whatever the row ends up saying.
  const MODELS = [
    { id: "gemini-4-hydrogen", name: "Gemini 4 Hydrogen", rpm: 5, tpm: 250000, rpd: 20,
      note: "Latest Gemini 4 mid-tier — 20 requests/day" },
    { id: "gemini-3.8-flash", name: "Gemini 3.8 Flash", rpm: 5, tpm: 250000, rpd: 20,
      note: "High quality — 20 requests/day" },
    { id: "gemini-3.6-flash", name: "Gemini 3.6 Flash", rpm: 5, tpm: 250000, rpd: 20,
      note: "Active Flash series — 20 requests/day" },
    { id: "gemini-3.5-flash-lite", name: "Gemini 3.5 Flash Lite", rpm: 15, tpm: 250000, rpd: 500,
      note: "Best Gemini option for bulk — 500 requests/day" },
    { id: "gemini-3.1-flash-lite", name: "Gemini 3.1 Flash Lite", rpm: 15, tpm: 250000, rpd: 500,
      note: "Best Gemini option for bulk — 500 requests/day" },
    { id: "gemma-4-26b-a4b-it", name: "Gemma 4 26B", rpm: 30, tpm: 16000, rpd: 14400,
      note: "Highest daily count — keep chunks under 16K TPM" },
    { id: "gemma-4-31b-it", name: "Gemma 4 31B", rpm: 30, tpm: 16000, rpd: 14400,
      note: "Highest daily count — keep chunks under 16K TPM" },
  ];
  const MODEL_MAP = new Map(MODELS.map((model) => [model.id, model]));
  const CHAIN_TASKS = [
    { key: "translate", label: "translate" },
    { key: "extract", label: "extract" },
  ];
  // Tasks an admin can pin for one run: extraction and the glossary decision
  // pass share the `extract` chain, so they share one picker.
  const RUN_TASKS = ["extract"];
  // What a key health probe can report. `tag` is the pill colour, and the label
  // is the plain-English meaning of the state the pool stored.
  const HEALTH = {
    ok: { label: "valid", tag: "ok" },
    expired: { label: "expired", tag: "bad" },
    invalid: { label: "invalid key", tag: "bad" },
    blocked: { label: "blocked", tag: "bad" },
    billing: { label: "billing off", tag: "bad" },
    quota_spent: { label: "daily quota spent", tag: "warn" },
    rate_limited: { label: "rate limited", tag: "warn" },
    no_model: { label: "model not available", tag: "warn" },
    overloaded: { label: "model overloaded", tag: "warn" },
    unreachable: { label: "unreachable", tag: "bad" },
    unknown: { label: "error", tag: "bad" },
    unchecked: { label: "not checked", tag: "" },
  };

  function healthTag(name, detail) {
    const meta = HEALTH[name] || HEALTH.unknown;
    const title = detail ? ` title="${escapeAttr(detail)}"` : "";
    return `<span class="tag ${meta.tag}"${title}>${escapeHtml(meta.label)}</span>`;
  }

  const state = {
    novels: [],
    novelId: "",
    language: "en",
    panel: "overview",
    mode: "ai",
    chapterOffset: 0,
    chapterLimit: 50,
    chapterTotal: 0,
    termOffset: 0,
    termLimit: 50,
    // Whether the novel's profile produces a reading at all; an English novel
    // hides the Reading column instead of showing twelve empty cells.
    reading: true,
    jobTimer: 0,
  };

  // ------------------------------------------------------------- helpers

  function note(id, text, failure = false) {
    const node = $(id);
    if (!node) return;
    node.textContent = text || "";
    node.className = failure ? "error" : "muted";
  }

  function statusTag(job) {
    const map = { 2: "ok", 3: "bad", 5: "warn", 4: "bad" };
    return `<span class="tag ${map[job.status] || ""}">${jobStatus(job.status)}</span>`;
  }

  function coverage(ok, label) {
    return `<span class="tag ${ok ? "ok" : ""}">${label}</span>`;
  }

  function stat(label, value, footer = "") {
    return `<div class="stat"><div class="label">${escapeHtml(label)}</div>
      <div class="value">${typeof value === "number" ? num(value) : escapeHtml(value)}</div>
      ${footer ? `<div class="note">${escapeHtml(footer)}</div>` : ""}</div>`;
  }

  function translationJobs(limit) {
    return api(`/api/jobs?limit=${Math.min(100, limit)}`).then((page) =>
      (page.items || [])
        .filter((job) => TRANSLATION_JOB_TYPES.has(Number(job.type)))
        .sort((a, b) => Number(b.created_at || 0) - Number(a.created_at || 0))
        .slice(0, limit),
    );
  }

  function renderJobs(host, jobs) {
    if (!host) return;
    if (!jobs.length) {
      host.innerHTML = '<div class="empty">No translation jobs yet.</div>';
      return;
    }
    const rows = jobs
      .map((job) => {
        const done = Number(job.done || 0);
        const total = Number(job.total || 0);
        const pct = total > 0 ? Math.min(100, Math.round((done * 100) / total)) : 0;
        return `<tr>
          <td class="mono">${escapeHtml(String(job.id).slice(0, 8))}</td>
          <td>${escapeHtml(jobType(job.type))}</td>
          <td>${statusTag(job)}</td>
          <td>${done}/${total}<div class="bar"><span style="width:${pct}%"></span></div></td>
          <td>${escapeHtml(EF.timeAgo(job.created_at))}</td>
          <td>${job.is_done ? `<span class="muted">${escapeHtml(job.error || "")}</span>` : `<button class="btn small" data-cancel="${escapeAttr(job.id)}">Cancel</button>`}</td>
        </tr>`;
      })
      .join("");
    host.innerHTML = `<table><thead><tr><th>Job</th><th>Type</th><th>Status</th><th>Progress</th><th>Created</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
    host.querySelectorAll("[data-cancel]").forEach((button) => {
      button.addEventListener("click", () => cancelJob(button.dataset.cancel));
    });
  }

  async function cancelJob(id) {
    try {
      await api(`/api/job/${id}/cancel`, { method: "POST" });
      toast("Job cancelled", "ok");
      if (state.panel === "chapters") await refreshJobs();
      else await refreshOverview();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function stopTimers() {
    clearTimeout(state.jobTimer);
    state.jobTimer = 0;
  }

  // ------------------------------------------------------------- panels

  const refresh = {
    overview: refreshOverview,
    keys: refreshKeys,
    extract: refreshExtract,
    glossary: refreshGlossary,
    chapters: refreshChapters,
    settings: refreshSettings,
  };

  function showPanel(name) {
    if (!refresh[name]) name = "overview";
    state.panel = name;
    stopTimers();
    document.querySelectorAll(".nav-item").forEach((node) => {
      node.classList.toggle("active", node.dataset.panel === name);
    });
    document.querySelectorAll(".panel").forEach((node) => {
      node.classList.toggle("active", node.id === `panel-${name}`);
    });
    EF.setText("panelTitle", TITLES[name]);
    history.replaceState(null, "", `#${name}`);
    refresh[name]().catch((error) => toast(error.message, "error"));
  }

  // ----------------------------------------------------------- overview

  async function refreshOverview() {
    const query = new URLSearchParams({ language: state.language });
    if (state.novelId) query.set("novel_id", state.novelId);
    const data = await api(`/api/translation/overview?${query.toString()}`);
    const pool = data.pool || {};
    const rawRatio = data.terms ? (100 * data.decided) / data.terms : 0;
    const ratio = data.decided > 0 && rawRatio < 1 ? "<1" : String(Math.round(rawRatio));
    $("overviewStats").innerHTML = [
      stat("Novels", data.novels),
      stat("Chapters here", data.chapters),
      stat("AI translations", data.translated),
      stat("Glossary terms", data.terms),
      stat("Decided", data.decided, `${ratio}% of terms`),
      stat("Calls left today", pool.free_today || 0),
    ].join("");

    $("overviewPool").innerHTML = `<dl class="kv">
      <dt>Keys</dt><dd>${pool.configured ? `${num(pool.keys)} key(s) · ${num(pool.units)} units` : '<span class="tag bad">none saved</span>'}</dd>
      <dt>Ready units</dt><dd>${num(pool.ready || 0)} / ${num(pool.units || 0)}</dd>
      <dt>Daily state</dt><dd>${
        !pool.configured
          ? '<span class="tag bad">no keys</span>'
          : pool.paused
            ? '<span class="tag bad">pool spent</span>'
            : '<span class="tag ok">has quota</span>'
      }</dd>
      <dt>Resets</dt><dd>${pool.reset_at ? escapeHtml(new Date(pool.reset_at).toLocaleString()) : "—"}</dd>
      <dt>Translation</dt><dd>${pool.enabled ? '<span class="tag ok">enabled</span>' : '<span class="tag bad">disabled</span>'}</dd>
    </dl>`;

    renderJobs($("overviewJobs"), await translationJobs(8));
  }

  // --------------------------------------------------------------- keys

  function knownModels(blocks) {
    const ids = MODELS.map((model) => model.id);
    for (const block of blocks || []) {
      const id = block && block.model;
      if (id && !ids.includes(id)) ids.push(id);
    }
    return ids;
  }

  function modelName(id) {
    const found = MODEL_MAP.get(id);
    return found ? found.name : id;
  }

  function modelOption(value, label) {
    return `<option value="${escapeAttr(value)}">${escapeHtml(label)}</option>`;
  }

  function fillModelPicker() {
    $("modelAdd").innerHTML = MODELS.map((model) =>
      modelOption(model.id, `${model.name} — ${num(model.rpd)} req/day`),
    ).join("");
  }

  function fillChains(chains, blocks) {
    const inPool = new Set((blocks || []).map((block) => block.model));
    const options = knownModels(blocks)
      .map((model) => {
        const suffix = inPool.has(model) ? "" : " — not in pool";
        return modelOption(model, `${modelName(model)}${suffix}`);
      })
      .join("");
    for (const task of CHAIN_TASKS) {
      const select = $(`chain-${task.key}`);
      if (!select) continue;
      const chosen = new Set((chains || {})[task.key] || []);
      select.innerHTML = options;
      select.querySelectorAll("option").forEach((option) => {
        option.selected = chosen.has(option.value);
      });
    }
    fillTaskModels(chains, blocks);
  }

  // The per-run model picker: pool models only, because a model outside the
  // pool has no key behind it and the endpoint would reject the pin. The
  // current choice wins if it is still in the pool, otherwise the first model
  // of that task's saved chain that the pool actually has.
  function fillTaskModels(chains, blocks) {
    const inPool = (blocks || []).map((block) => block.model).filter(Boolean);
    const options = inPool.map((model) => modelOption(model, modelName(model))).join("");
    for (const task of RUN_TASKS) {
      const select = $(`model-${task}`);
      if (!select) continue;
      const current = select.value;
      select.innerHTML = options;
      const chain = (chains || {})[task] || [];
      const preferred = [current, ...chain].find(
        (model) => model && inPool.includes(model),
      );
      if (preferred) select.value = preferred;
      if (!select.value && select.options.length) select.selectedIndex = 0;
    }
  }

  function renderModels(blocks) {
    const host = $("modelRows");
    const models = (blocks || []).filter((block) => block && block.model);
    if (!models.length) {
      host.innerHTML = '<div class="empty">No model in the pool yet — pick one below and add it.</div>';
      return;
    }
    host.innerHTML = models
      .map((block) => {
        const catalog = MODEL_MAP.get(String(block.model));
        const fallback = catalog || { rpm: 15, tpm: 250000, rpd: 500 };
        return `<div class="model-row" data-model-row>
          <select data-role="model" aria-label="Model">
            ${knownModels(models).map((model) => modelOption(model, `${modelName(model)} · ${model}`)).join("")}
          </select>
          <input data-role="rpm" type="number" min="0" step="1" placeholder="RPM" value="${escapeAttr(block.rpm ?? fallback.rpm)}" aria-label="Requests per minute" />
          <input data-role="tpm" type="number" min="0" step="1000" placeholder="TPM" value="${escapeAttr(block.tpm ?? fallback.tpm)}" aria-label="Tokens per minute" />
          <input data-role="rpd" type="number" min="0" step="1" placeholder="RPD" value="${escapeAttr(block.rpd ?? fallback.rpd)}" aria-label="Requests per day" />
          <button class="btn small" type="button" data-role="remove">Remove</button>
        </div>`;
      })
      .join("");
    host.querySelectorAll("[data-model-row]").forEach((row, index) => {
      const select = row.querySelector('[data-role="model"]');
      select.value = String(models[index].model);
      const catalog = MODEL_MAP.get(select.value);
      if (catalog) row.title = catalog.note;
      select.addEventListener("change", () => {
        const next = MODEL_MAP.get(select.value);
        if (!next) return;
        row.querySelector('[data-role="rpm"]').value = next.rpm;
        row.querySelector('[data-role="tpm"]').value = next.tpm;
        row.querySelector('[data-role="rpd"]').value = next.rpd;
        row.title = next.note;
      });
      row.querySelector('[data-role="remove"]').addEventListener("click", () => row.remove());
    });
  }

  function readModels() {
    const blocks = [];
    $("modelRows")
      .querySelectorAll("[data-model-row]")
      .forEach((row) => {
        const model = (row.querySelector('[data-role="model"]').value || "").trim();
        if (!model) return;
        const value = (role) =>
          Number(row.querySelector(`[data-role="${role}"]`).value || 0) || undefined;
        blocks.push({ model, rpm: value("rpm"), tpm: value("tpm"), rpd: value("rpd") });
      });
    return blocks;
  }

  function renderKeyHealth(status) {
    const host = $("keyHealthTable");
    if (!host) return;
    const keys = status.key_health || [];
    if (!keys.length) {
      host.innerHTML = '<div class="empty">No key saved yet.</div>';
      return;
    }
    const rows = keys
      .map((entry) => {
        const models = (entry.units || [])
          .map((unit) => {
            const quota =
              unit.quota_state === "exhausted"
                ? '<span class="tag bad">quota spent</span>'
                : unit.quota_state === "cooling"
                  ? '<span class="tag warn">cooling</span>'
                  : "";
            return `<div class="health-model">${healthTag(unit.state, unit.detail)}
              <span class="mono">${escapeHtml(unit.model)}</span>${quota}</div>`;
          })
          .join("");
        const checked = entry.checked_at ? EF.timeAgo(entry.checked_at * 1000) : "never";
        const left = `${num(entry.free_today)} / ${num(entry.rpd)}`;
        return `<tr>
          <td class="mono">${escapeHtml(entry.label)}</td>
          <td>${healthTag(entry.state, entry.detail)}</td>
          <td class="health-models">${models}</td>
          <td class="num">${left}</td>
          <td class="detail">${escapeHtml(entry.detail || "—")}</td>
          <td class="muted">${escapeHtml(checked)}</td>
          <td><button class="btn small" type="button" data-probe-key="${escapeAttr(entry.label)}">Check</button></td>
        </tr>`;
      })
      .join("");
    host.innerHTML = `<table><thead><tr><th>Key</th><th>State</th><th>Models</th><th class="num">Left today</th><th>Detail</th><th>Checked</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  async function probeKeys(label) {
    const button = $("probeKeys");
    const target = label ? `key ${label}` : "every key";
    try {
      note("probeNote", `Checking ${target}… this takes a few seconds.`);
      if (!label && button) button.disabled = true;
      const query = label ? `?key=${encodeURIComponent(label)}` : "";
      await api(`/api/translation/pool/probe${query}`, { method: "POST" });
      await refreshKeys();
      note("probeNote", `Checked ${target}.`);
      toast("Key health updated", "ok");
    } catch (error) {
      note("probeNote", error.message, true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function refreshKeys() {
    const status = await api("/api/translation/pool");
    const models = status.models || [];
    note("keysNote", status.configured ? `${status.units.length} key/model unit(s) saved` : "No key saved yet.");
    renderModels(models);
    note("modelNote", models.length ? `${models.length} model(s) in the pool` : "");
    fillChains(status.chains || {}, models);
    renderKeyHealth(status);
    const settings = status.settings || {};
    $("setEnabled").checked = Boolean(settings.enabled);
    $("setAutoPause").checked = Boolean(settings.auto_pause_on_daily);
    $("setResetTz").value = settings.reset_tz || "";
    $("setMaxTokens").value = settings.max_request_tokens || "";
    $("setEngines").value = (settings.web_engine_order || []).join(", ");

    const host = $("poolTable");
    if (!status.units.length) {
      host.innerHTML = '<div class="empty">Paste keys above to build the pool.</div>';
    } else {
      const rows = status.units
        .map((unit) => {
          const tag = unit.state === "ok" ? "ok" : unit.state === "cooling" ? "warn" : "bad";
          return `<tr>
            <td>${escapeHtml(unit.model)}</td>
            <td>${escapeHtml(unit.project)}</td>
            <td><span class="tag ${tag}">${escapeHtml(unit.state)}</span></td>
            <td>${healthTag((unit.health || {}).state, (unit.health || {}).detail)}</td>
            <td class="num">${num(unit.used_today)} / ${num(unit.rpd)}</td>
            <td class="num">${num(unit.free_rpm)} rpm · ${num(unit.free_tpm)} tpm</td>
            <td class="num">${num(unit.tokens_today)}</td>
            <td class="num">${num(unit.errors)}</td>
          </tr>`;
        })
        .join("");
      host.innerHTML = `<table><thead><tr><th>Model</th><th>Key</th><th>State</th><th>Health</th><th class="num">Today</th><th class="num">Free</th><th class="num">Tokens</th><th class="num">Errors</th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    const log = (status.log || []).join("\n");
    $("poolLog").textContent = log || "No pool calls yet.";
  }

  async function saveKeys() {
    const api_keys = $("keysInput").value;
    if (!api_keys.trim()) {
      note("keysNote", "Paste at least one key first.", true);
      return;
    }
    try {
      note("keysNote", "Saving…");
      const status = await api("/api/translation/pool", {
        method: "PUT",
        body: JSON.stringify({ api_keys }),
      });
      $("keysInput").value = "";
      note("keysNote", `${status.units.length} unit(s) ready`);
      toast("Key pool updated", "ok");
      await refreshKeys();
    } catch (error) {
      note("keysNote", error.message, true);
    }
  }

  async function clearKeys() {
    if (
      !confirm(
        "Remove every saved key, plus today's usage ledger and key health? Models, chains and translations are kept.",
      )
    )
      return;
    try {
      note("keysNote", "Removing…");
      await api("/api/translation/pool/clear", { method: "POST" });
      $("keysInput").value = "";
      await refreshKeys();
      note("keysNote", "All keys removed — paste new ones above and save.");
      toast("Keys cleared", "ok");
    } catch (error) {
      note("keysNote", error.message, true);
    }
  }

  function addModel() {
    const id = $("modelAdd").value;
    const catalog = MODEL_MAP.get(id);
    if (!catalog) return;
    const models = readModels();
    if (models.some((block) => block.model === id)) {
      note("modelNote", `${catalog.name} is already in the pool.`, true);
      return;
    }
    models.push({ model: id, rpm: catalog.rpm, tpm: catalog.tpm, rpd: catalog.rpd });
    renderModels(models);
    note("modelNote", `${catalog.name} added — save to apply.`);
  }

  async function saveModels() {
    const models = readModels();
    if (!models.length) {
      note("modelNote", "Add at least one model first.", true);
      return;
    }
    try {
      note("modelNote", "Saving…");
      await api("/api/translation/pool", {
        method: "PUT",
        body: JSON.stringify({ pool_models: models }),
      });
      toast("Models saved", "ok");
      await refreshKeys();
    } catch (error) {
      note("modelNote", error.message, true);
    }
  }

  async function saveChains() {
    const chosen = (key) =>
      Array.from($(`chain-${key}`).selectedOptions).map((option) => option.value);
    try {
      await api("/api/translation/pool", {
        method: "PUT",
        body: JSON.stringify({
          pool_tasks: {
            translate: chosen("translate"),
            glossary: chosen("glossary"),
            classify: chosen("classify"),
          },
        }),
      });
      note("chainNote", "Saved");
      toast("Task chains saved", "ok");
      await refreshKeys();
    } catch (error) {
      note("chainNote", error.message, true);
    }
  }

  async function savePoolSettings() {
    const engines = $("setEngines")
      .value.split(",")
      .map((part) => part.trim())
      .filter(Boolean);
    try {
      note("settingsNote", "Saving…");
      await api("/api/translation/pool", {
        method: "PUT",
        body: JSON.stringify({
          enabled: $("setEnabled").checked,
          auto_pause_on_daily: $("setAutoPause").checked,
          reset_tz: $("setResetTz").value.trim() || undefined,
          max_request_tokens: Number($("setMaxTokens").value || 0) || undefined,
          web_engine_order: engines,
        }),
      });
      note("settingsNote", "Saved");
      toast("Run settings saved", "ok");
      await refreshKeys();
    } catch (error) {
      note("settingsNote", error.message, true);
    }
  }

  async function estimate() {
    try {
      const chapters = Number($("estimateChapters").value || 0);
      const chars = Number($("estimateChars").value || 4000);
      const data = await api(
        `/api/translation/pool/estimate?chapters=${chapters}&chars_per_chapter=${chars}`,
        { method: "POST" },
      );
      note(
        "estimateNote",
        `${num(data.calls)} calls · ${num(data.remaining_calls_today)} left today · ${
          data.enough ? "enough for this run" : "not enough before the reset"
        } · resets ${new Date(data.resume_at).toLocaleString()}`,
      );
    } catch (error) {
      note("estimateNote", error.message, true);
    }
  }

  // ------------------------------------------------------------ extract

  async function extractBatch(offset, announce = true) {
    const limit = Number($("extractLimit").value || 40);
    const minFreq = Number($("extractMinFreq").value || 2);
    const data = await api(
      `/api/translation/${state.novelId}/glossary/extract?offset=${offset}&limit=${limit}&min_freq=${minFreq}`,
      { method: "POST" },
    );
    $("extractOffset").value = data.next_offset;
    if (announce) {
      note(
        "extractNote",
        `${data.scanned} chapter(s) scanned · ${data.found} found · ${data.new} new · ${data.updated} updated` +
          (data.hidden ? ` · ${data.hidden} hidden` : "") +
          (data.blocked ? ` · ${data.blocked} blocked` : "") +
          (data.capped ? ` · capped: ${data.found} of ${data.found_total}` : "") +
          ` · next ${data.next_offset}/${data.total_chapters}`,
      );
    }
    return data;
  }

  async function extractLlmBatch(offset) {
    const model = $("model-extract").value;
    const pinned = model ? `&model=${encodeURIComponent(model)}` : "";
    const limit = Number($("extractLimit").value || 40);
    note("extractNote", "Reading the batch and asking the pool…");
    const data = await api(
      `/api/translation/${state.novelId}/glossary/extract/llm?offset=${offset}&limit=${limit}${pinned}`,
      { method: "POST" },
    );
    $("extractOffset").value = data.next_offset;
    const line =
      `${data.stored} of ${data.asked} surface(s) stored via ${data.model || "?"}` +
      (data.local ? ` · +${data.local} from the local scan` : "") +
      ` · ${data.new} new · ${data.updated} updated` +
      (data.blocked ? ` · ${data.blocked} blocked` : "") +
      (data.invalid ? ` · ${data.invalid} invalid` : "") +
      (data.capped ? ` · capped: ${data.found} of ${data.found_total}` : "") +
      ` · ${data.calls} call(s) · next ${data.next_offset}/${data.total_chapters}`;
    note("extractNote", data.error ? `${line} · ${data.error}` : line, Boolean(data.error && !data.stored));
    await refreshPlan();
    return data;
  }

  // Preflight for the paid mode: the call count shown here is the one the run
  // re-checks, and the server refuses the run when the pool cannot cover it.
  async function refreshPlan() {
    if (!state.novelId) return;
    const mode = $("extractMode").value;
    $("scanAll").disabled = mode === "llm";
    if (mode !== "llm") {
      note("llmNote", "Local scan: free, deterministic, no key needed.");
      return;
    }
    try {
      const offset = Number($("extractOffset").value || 0);
      const limit = Number($("extractLimit").value || 40);
      const data = await api(
        `/api/translation/${state.novelId}/glossary/extract/plan?offset=${offset}&limit=${limit}`,
      );
      note(
        "llmNote",
        `~${data.calls} call(s) for ${num(data.chapters)} chapter(s), ${num(data.chars)} chars` +
          ` · ${num(data.remaining_calls_today)} left today` +
          (data.enough ? "" : " · not enough quota, this batch will be refused"),
        !data.enough,
      );
    } catch (error) {
      note("llmNote", error.message, true);
    }
  }

  async function refreshExtract() {
    if (!state.novelId) return;
    const data = await api(`/api/translation/${state.novelId}/chapters?language=${state.language}&limit=1`);
    note(
      "extractNote",
      `${num(data.total)} chapter(s) in this novel. Next batch starts at offset ${$("extractOffset").value}.`,
    );
    await Promise.all([loadTopTerms(), refreshTaskModels().catch(() => {}), refreshPlan()]);
  }

  async function loadTopTerms() {
    try {
      const data = await api(
        `/api/translation/${state.novelId}/glossary?language=${state.language}&limit=20`,
      );
      const items = data.items || [];
      if (!items.length) {
        $("extractResults").innerHTML = '<div class="empty">No terms extracted yet.</div>';
        return;
      }
      const reading = state.reading ? "<th>Reading</th>" : "";
      const rows = items
        .map(
          (term) => `<tr>
            <td>${escapeHtml(term.source)}</td>
            ${state.reading ? `<td>${escapeHtml(term.reading || "—")}</td>` : ""}
            <td>${escapeHtml(term.rendering || "—")}</td>
            <td>${escapeHtml(categoryName(term.category))}</td>
            <td class="num">${firstChapter(term.first_chapter_serial)}</td>
            <td class="num">${num(term.freq)}</td>
          </tr>`,
        )
        .join("");
      $("extractResults").innerHTML = `<table><thead><tr><th>Source</th>${reading}<th>Rendering</th><th>Category</th><th class="num">First</th><th class="num">Freq</th></tr></thead><tbody>${rows}</tbody></table>`;
    } catch (error) {
      $("extractResults").innerHTML = `<div class="empty error">${escapeHtml(error.message)}</div>`;
    }
  }

  async function runExtract() {
    if (!state.novelId) return;
    const button = $("runExtract");
    button.disabled = true;
    try {
      const offset = Number($("extractOffset").value || 0);
      if ($("extractMode").value === "llm") {
        await extractLlmBatch(offset);
      } else {
        await extractBatch(offset);
      }
      await loadTopTerms();
    } catch (error) {
      note("extractNote", error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function scanAll() {
    // The paid mode is per batch: a whole novel is hundreds of calls, and the
    // plan says it must never run away in one click.
    if (!state.novelId || $("extractMode").value === "llm") return;
    const batchButton = $("runExtract");
    const allButton = $("scanAll");
    const bar = $("extractBar");
    batchButton.disabled = true;
    allButton.disabled = true;
    bar.hidden = false;
    let offset = Number($("extractOffset").value || 0);
    let batches = 0;
    let found = 0;
    let changed = 0;
    try {
      for (;;) {
        const data = await extractBatch(offset, false);
        found += data.found;
        changed += data.new;
        offset = data.next_offset;
        batches += 1;
        const pct = data.total_chapters ? Math.min(100, Math.round((offset * 100) / data.total_chapters)) : 100;
        bar.firstElementChild.style.width = `${pct}%`;
        note(
          "extractNote",
          `${num(offset)}/${num(data.total_chapters)} chapters · ${num(found)} found · ${num(changed)} new · batch ${batches}`,
        );
        if (offset >= data.total_chapters || !data.scanned || batches >= 40) break;
      }
      toast(`Extraction finished: ${num(changed)} new term(s)`, "ok");
      await loadTopTerms();
    } catch (error) {
      note("extractNote", error.message, true);
    } finally {
      batchButton.disabled = false;
      allButton.disabled = false;
    }
  }

  async function runDecide() {
    if (!state.novelId) return;
    const button = $("runDecide");
    const model = $("model-extract").value;
    button.disabled = true;
    note("decideNote", "Asking the pool for decisions…");
    try {
      const pinned = model ? `&model=${encodeURIComponent(model)}` : "";
      const data = await api(
        `/api/translation/${state.novelId}/glossary/decide?language=${state.language}${pinned}`,
        { method: "POST", body: JSON.stringify({ limit: 50 }) },
      );
      if (!data.model) {
        note(
          "decideNote",
          data.asked ? "Nothing was decided." : "No open terms left.",
          true,
        );
      } else {
        // A partial batch still decided terms: the counters come first and the
        // error explains the shortfall, instead of replacing the result.
        const line =
          `${data.stored} of ${data.asked} decided via ${data.model}` +
          (data.rendered ? ` · ${data.rendered} rendered` : "") +
          (data.categorized ? ` · ${data.categorized} categorised` : "") +
          (data.merged ? ` · ${data.merged} merged` : "") +
          (data.invalid ? ` · ${data.invalid} invalid` : "") +
          (data.skipped ? ` · ${data.skipped} skipped` : "");
        note(
          "decideNote",
          data.error ? `${line} · ${data.error}` : line,
          Boolean(data.error && !data.stored),
        );
        if (data.stored) toast("Decisions stored", "ok");
      }
      await Promise.all([loadTerms(), loadGlossaryStats()]);
    } catch (error) {
      note("decideNote", error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  // ----------------------------------------------------------- glossary

  async function refreshGlossary() {
    if (!state.novelId) return;
    // The style decides whether this novel has a Reading at all, so it is read
    // before the table is built.
    await loadStyle();
    // The pool choices are read here, not only in Keys & models: the run model
    // pickers live in this panel, and an empty select is what an admin sees
    // when they open the glossary first.
    await Promise.all([loadTerms(), loadGlossaryStats(), refreshTaskModels()]);
  }

  async function loadStyle() {
    try {
      const data = await api(`/api/translation/${state.novelId}/style`);
      const profile = data.profile || {};
      state.reading = Boolean(profile.reading);
      fillStyleControls(data);
      const degraded = data.requested_code && profile.code !== data.requested_code;
      note(
        "styleNote",
        degraded
          ? `${data.requested_code} needs ${profile.requires || "an optional dependency"}, which is missing; extraction uses the fallback profile.`
          : `${profile.label || "Fallback"} profile for ${data.language || "an unknown language"}` +
            (profile.requires ? ` · ${profile.requires}` : ""),
        Boolean(degraded),
      );
    } catch (error) {
      note("styleNote", error.message, true);
    }
  }

  function fillStyleControls(data) {
    const style = data.style || {};
    const profiles = (data.profiles || []).filter((profile) => profile.code !== "fallback");
    const stored = data.stored_language || "";
    const options = [
      `<option value="">Auto${data.detected_language ? ` (${escapeHtml(data.detected_language)})` : ""}</option>`,
    ].concat(
      profiles.map(
        (profile) =>
          `<option value="${escapeAttr(profile.code)}">${escapeHtml(profile.label)} (${escapeHtml(profile.code)})</option>`,
      ),
    );
    if (stored && !profiles.some((profile) => profile.code === stored)) {
      options.push(`<option value="${escapeAttr(stored)}">${escapeHtml(stored)}</option>`);
    }
    $("styleLanguage").innerHTML = options.join("");
    $("styleLanguage").value = stored;

    $("styleHonorifics").innerHTML = (data.honorific_policies || [])
      .map(
        (value) =>
          `<option value="${value}">${value === "keep" ? "Keep with the name" : "Drop from renderings"}</option>`,
      )
      .join("");
    $("styleHonorifics").value = style.honorifics || "strip";

    const enabled = new Set(style.categories || []);
    $("styleCategories").innerHTML = (data.categories || [])
      .map(
        (name) =>
          `<option value="${name}" ${enabled.size === 0 || enabled.has(name) ? "selected" : ""}>${escapeHtml(name.replace(/_/g, " "))}</option>`,
      )
      .join("");
  }

  async function saveStyle() {
    if (!state.novelId) return;
    const button = $("saveStyle");
    button.disabled = true;
    try {
      const select = $("styleCategories");
      const picked = Array.from(select.selectedOptions).map((option) => option.value);
      const all = Array.from(select.options).map((option) => option.value);
      const data = await api(`/api/translation/${state.novelId}/style`, {
        method: "PUT",
        body: JSON.stringify({
          language: $("styleLanguage").value,
          honorifics: $("styleHonorifics").value,
          // "every category selected" is stored as "no restriction".
          categories: picked.length === all.length ? [] : picked,
        }),
      });
      state.reading = Boolean(data.profile && data.profile.reading);
      fillStyleControls(data);
      note("styleNote", `Saved · ${(data.profile || {}).label || "fallback"} profile`);
      toast("Translation style saved", "ok");
      await loadTerms();
    } catch (error) {
      note("styleNote", error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function refreshTaskModels() {
    const status = await api("/api/translation/pool");
    fillTaskModels(status.chains || {}, status.models || []);
  }

  async function loadGlossaryStats() {
    try {
      const data = await api(
        `/api/translation/overview?language=${state.language}&novel_id=${state.novelId}`,
      );
      const pending = Math.max(0, data.terms - data.decided);
      $("glossStats").innerHTML = `<dt>Terms</dt><dd>${num(data.terms)}</dd>
        <dt>Decided</dt><dd>${num(data.decided)}</dd>
        <dt>Pending</dt><dd>${num(pending)}</dd>`;
    } catch (_) {
      /* the review table already reports failures */
    }
  }

  async function loadTerms() {
    if (!state.novelId) return;
    const host = $("termTable");
    host.innerHTML = '<div class="empty">Loading…</div>';
    const params = new URLSearchParams({
      language: state.language,
      limit: String(state.termLimit),
      offset: String(state.termOffset),
    });
    if ($("termHidden").checked) params.set("hidden", "1");
    const search = $("termSearch").value.trim();
    if (search) params.set("query", search);
    const category = $("termCategory").value;
    if (category) params.set("category", category);
    const source = $("termSource").value;
    if (source) params.set("source", source);

    try {
      const data = await api(`/api/translation/${state.novelId}/glossary?${params.toString()}`);
      fillTermSources(data.sources);
      const items = data.items || [];
      if (!items.length) {
        host.innerHTML = '<div class="empty">No terms match this filter.</div>';
        $("termPage").textContent = "0";
        $("termNext").disabled = true;
        return;
      }
      const rows = items
        .map((term) => {
          const chips = [
            term.verdict ? `<span class="tag">${escapeHtml(term.verdict)}</span>` : "",
            term.merged_into ? `<span class="tag">→ ${escapeHtml(term.merged_into)}</span>` : "",
            term.blocked ? '<span class="tag bad">blocked</span>' : "",
            term.found_by && term.found_by !== "local"
              ? `<span class="tag" title="Found by a model">${escapeHtml(term.found_by)}</span>`
              : "",
            term.locked ? '<span class="tag">locked</span>' : "",
          ]
            .filter(Boolean)
            .join(" ");
          const actions = term.blocked
            ? '<button class="btn small" data-action="unblock">Unblock</button>'
            : `<button class="btn small" data-action="block">Block</button>
               <button class="btn small" data-action="merge">Merge</button>
               <button class="btn small" data-action="verdict" data-value="${term.verdict === "skip" ? "keep" : "skip"}">${term.verdict === "skip" ? "Unhide" : "Hide"}</button>`;
          return `<tr data-handle="${escapeAttr(term.handle)}">
            <td>${escapeHtml(term.source)}${
              state.reading && term.reading
                ? `<div class="muted">${escapeHtml(term.reading)}</div>`
                : ""
            }</td>
            <td><input data-field="rendering" value="${escapeAttr(term.rendering || "")}" placeholder="undecided" /></td>
            <td>${categorySelect(term.category)}</td>
            <td class="num">${firstChapter(term.first_chapter_serial)}</td>
            <td class="num">${num(term.freq)}</td>
            <td class="num">${num(term.chapters || 0)}</td>
            <td>${chips}</td>
            <td class="actions">${actions}</td>
          </tr>`;
        })
        .join("");
      host.innerHTML = `<table><thead><tr><th>Source</th><th>Rendering</th><th>Category</th><th class="num">First</th><th class="num">Freq</th><th class="num">Ch.</th><th>State</th><th>Actions</th></tr></thead><tbody>${rows}</tbody></table>`;
      host.querySelectorAll("tr[data-handle]").forEach((row) => {
        const handle = row.dataset.handle;
        row.querySelector('[data-field="rendering"]').addEventListener("change", (event) => {
          saveTerm(handle, { rendering: event.target.value }, event.target);
        });
        row.querySelector("select[aria-label='Category']").addEventListener("change", (event) => {
          saveTerm(handle, { category: event.target.value }, event.target);
        });
        row.querySelectorAll("[data-action]").forEach((button) => {
          button.addEventListener("click", () =>
            termAction(handle, button.dataset.action, button.dataset.value, button),
          );
        });
      });
      $("termPage").textContent = state.termLimit
        ? `${state.termOffset + 1}–${state.termOffset + items.length}`
        : `${num(state.termOffset + items.length)} shown`;
      $("termPrev").disabled = state.termOffset === 0;
      $("termNext").disabled = !state.termLimit || items.length < state.termLimit;
    } catch (error) {
      host.innerHTML = `<div class="empty error">${escapeHtml(error.message)}</div>`;
    }
  }

  function fillTermSources(sources) {
    const select = $("termSource");
    const current = select.value;
    const names = sources || [];
    select.innerHTML = ['<option value="">Any source</option>']
      .concat(
        names.map(
          (source) =>
            `<option value="${escapeAttr(source.name)}">${escapeHtml(
              source.name === "local" ? "Local scan" : source.name,
            )} (${num(source.count)})</option>`,
        ),
      )
      .join("");
    select.value = names.some((source) => source.name === current) ? current : "";
  }

  function categorySelect(value) {
    const options = [`<option value="">—</option>`]
      .concat(
        TERM_CATEGORIES.map(
          (name) =>
            `<option value="${name}" ${value === name ? "selected" : ""}>${name.replace(/_/g, " ")}</option>`,
        ),
      )
      .join("");
    return `<select aria-label="Category">${options}</select>`;
  }

  // "First appears in": the chapter the scan first met the term in. Serials are
  // 1-based, and a term with no scan row has none.
  function firstChapter(serial) {
    return serial ? `ch. ${num(serial)}` : "—";
  }

  function categoryName(value) {
    return value ? value.replace(/_/g, " ") : "—";
  }

  async function saveTerm(handle, body, input) {
    try {
      await api(
        `/api/translation/${state.novelId}/glossary/terms/${encodeURIComponent(handle)}?language=${state.language}`,
        { method: "PUT", body: JSON.stringify(body) },
      );
      input.style.borderColor = "var(--ok)";
      await loadGlossaryStats();
    } catch (error) {
      input.style.borderColor = "var(--danger)";
      toast(error.message, "error");
    }
  }

  async function termAction(handle, action, value, button) {
    button.disabled = true;
    const base = `/api/translation/${state.novelId}/glossary/terms/${encodeURIComponent(handle)}`;
    try {
      if (action === "block") {
        await api(`${base}/block`, { method: "PUT", body: JSON.stringify({}) });
        toast("Blocked — a rescan will not bring this back", "ok");
      } else if (action === "unblock") {
        await api(`${base}/block`, { method: "DELETE" });
        toast("Unblocked", "ok");
      } else if (action === "merge") {
        const into = window.prompt("Merge into which term? Type its source text or handle.");
        if (!into) {
          button.disabled = false;
          return;
        }
        const data = await api(`${base}/merge`, {
          method: "PUT",
          body: JSON.stringify({ into }),
        });
        if (!data.ok) throw new Error(`No term matches ${into}.`);
        toast(`Merged into ${into}`, "ok");
      } else {
        await api(`${base}?language=${state.language}`, {
          method: "PUT",
          body: JSON.stringify({ verdict: value }),
        });
      }
      await Promise.all([loadTerms(), loadGlossaryStats()]);
    } catch (error) {
      toast(error.message, "error");
      button.disabled = false;
    }
  }

  async function clearGlossary() {
    const confirmed = confirm(
      "Delete the glossary for every novel — terms, renderings, proposals, revisions and reader overrides? Chapters and their translations are kept.",
    );
    if (!confirmed) return;
    const button = $("clearGlossary");
    button.disabled = true;
    note("glossNote", "Clearing the glossary…");
    try {
      const counts = await api("/api/translation/glossary", { method: "DELETE" });
      note(
        "glossNote",
        `Cleared ${num(counts.terms)} terms, ${num(counts.entries)} renderings, ` +
          `${num(counts.candidates)} proposals, ${num(counts.revisions)} revisions, ` +
          `${num(counts.user_entries)} reader overrides, ${num(counts.overrides)} rejected surfaces.`,
      );
      toast("Glossary cleared", "ok");
      state.termOffset = 0;
      await refreshGlossary();
    } catch (error) {
      note("glossNote", error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  // ----------------------------------------------------------- chapters

  async function refreshChapters() {
    if (!state.novelId) return;
    const host = $("chapterTable");
    host.innerHTML = '<div class="empty">Loading…</div>';
    try {
      const data = await api(
        `/api/translation/${state.novelId}/chapters?language=${state.language}&offset=${state.chapterOffset}&limit=${state.chapterLimit}`,
      );
      state.chapterTotal = data.total || 0;
      const items = data.items || [];
      if (!items.length) {
        host.innerHTML = '<div class="empty">No chapters here.</div>';
      } else {
        const rows = items
          .map(
            (chapter) => `<tr>
              <td><input type="checkbox" data-chapter="${escapeAttr(chapter.id)}" ${chapter.has_source ? "" : "disabled"} /></td>
              <td class="num">${num(chapter.serial)}</td>
              <td>${escapeHtml(chapter.title || "")}</td>
              <td>${coverage(chapter.has_source, "raw")}</td>
              <td>${coverage(chapter.has_web, "web")}</td>
              <td>${coverage(chapter.has_web_plus, "web+")}</td>
              <td>${coverage(chapter.has_ai, "ai")}</td>
            </tr>`,
          )
          .join("");
        host.innerHTML = `<table><thead><tr><th></th><th class="num">#</th><th>Chapter</th><th>Raw</th><th>Web</th><th>Web+</th><th>AI</th></tr></thead><tbody>${rows}</tbody></table>`;
      }
      note(
        "chapterNote",
        `${num(state.chapterOffset + 1)}–${num(state.chapterOffset + items.length)} of ${num(state.chapterTotal)} · pass ${state.mode === "ai" ? "AI (key pool)" : state.mode === "web" ? "Web (keyless)" : "Web+ (keyless + glossary)"}`,
      );
      $("chapterPage").textContent = `${num(state.chapterOffset + 1)}–${num(state.chapterOffset + items.length)} of ${num(state.chapterTotal)}`;
      $("chapterPrev").disabled = state.chapterOffset === 0;
      $("chapterNext").disabled = state.chapterOffset + items.length >= state.chapterTotal;
    } catch (error) {
      host.innerHTML = `<div class="empty error">${escapeHtml(error.message)}</div>`;
    }
    await refreshJobs();
  }

  async function refreshJobs() {
    if (state.panel !== "chapters") return;
    try {
      renderJobs($("jobTable"), await translationJobs(20));
    } catch (_) {
      /* keep the last table on a transient failure */
    }
    clearTimeout(state.jobTimer);
    state.jobTimer = setTimeout(() => refreshJobs().catch(() => {}), 5000);
  }

  async function translateSelected() {
    if (!state.novelId) return;
    const ids = Array.from(document.querySelectorAll("[data-chapter]:checked")).map(
      (node) => node.dataset.chapter,
    );
    if (!ids.length) {
      toast("Select at least one chapter", "error");
      return;
    }
    const button = $("translateSelected");
    button.disabled = true;
    try {
      const job = await api("/api/job/create/translate-chapters", {
        method: "POST",
        body: JSON.stringify({ chapters: ids, language: state.language, mode: state.mode }),
      });
      toast(`Queued ${ids.length} chapter(s) as ${jobType(job.type)}`, "ok");
      await refreshJobs();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  function selectPage() {
    document.querySelectorAll("[data-chapter]:not(:disabled)").forEach((box) => {
      box.checked = true;
    });
  }

  // ----------------------------------------------------------- settings

  async function refreshSettings() {
    const sections = await api("/api/admin/configs");
    const host = $("configSections");
    host.innerHTML = "";
    let shown = 0;
    for (const section of sections) {
      const properties = (section.properties || []).filter((property) => !property.sensitive);
      if (!properties.length) continue;
      shown += 1;
      const card = document.createElement("div");
      card.className = "card";
      const title = document.createElement("h2");
      title.textContent = section.display_name || section.key;
      card.appendChild(title);
      if (section.description) {
        const hint = document.createElement("p");
        hint.className = "hint";
        hint.textContent = section.description;
        card.appendChild(hint);
      }
      const grid = document.createElement("div");
      grid.className = "form-grid";
      for (const property of properties) grid.appendChild(configField(section.key, property));
      card.appendChild(grid);
      const actions = document.createElement("div");
      actions.className = "row end";
      actions.style.marginTop = "14px";
      const feedback = document.createElement("span");
      feedback.className = "muted";
      const save = document.createElement("button");
      save.className = "btn primary small";
      save.textContent = "Save section";
      save.addEventListener("click", () => saveSection(section.key, grid, feedback));
      actions.append(feedback, save);
      card.appendChild(actions);
      host.appendChild(card);
    }
    if (!shown) host.innerHTML = '<div class="empty">No editable settings.</div>';
  }

  function configField(sectionKey, property) {
    const label = document.createElement("label");
    label.className = "field";
    label.dataset.key = property.key;
    if (property.description) label.title = property.description;

    const value = property.value;
    let input;
    if (property.value_kind === "boolean" || typeof value === "boolean") {
      label.className = "check";
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = Boolean(value);
      label.appendChild(input);
      label.appendChild(document.createTextNode(` ${property.display_name || property.key}`));
      input.dataset.kind = "boolean";
    } else if (property.value_kind === "integer" || typeof value === "number") {
      input = document.createElement("input");
      input.type = "number";
      input.value = value ?? "";
      input.dataset.kind = "number";
    } else if (value !== null && typeof value === "object") {
      input = document.createElement("textarea");
      input.rows = 4;
      input.value = JSON.stringify(value, null, 2);
      input.dataset.kind = "json";
      label.appendChild(document.createTextNode(property.display_name || property.key));
    } else {
      input = document.createElement("input");
      input.value = value ?? "";
      input.dataset.kind = "string";
      label.appendChild(document.createTextNode(property.display_name || property.key));
    }
    input.dataset.section = sectionKey;
    input.dataset.original = JSON.stringify(value === undefined ? null : value);
    if (!input.parentElement) label.appendChild(input);
    return label;
  }

  async function saveSection(sectionKey, grid, feedback) {
    const changes = [];
    for (const input of grid.querySelectorAll("input, textarea")) {
      const label = input.closest("label");
      if (!label) continue;
      let value;
      try {
        if (input.dataset.kind === "boolean") value = input.checked;
        else if (input.dataset.kind === "number")
          value = input.value === "" ? null : Number(input.value);
        else if (input.dataset.kind === "json") value = JSON.parse(input.value || "null");
        else value = input.value;
      } catch (error) {
        feedback.className = "error";
        feedback.textContent = `${label.dataset.key}: ${error.message}`;
        return;
      }
      const original = JSON.parse(input.dataset.original || "null");
      if (JSON.stringify(value) !== JSON.stringify(original)) {
        changes.push({ section: sectionKey, key: label.dataset.key, value });
      }
    }
    if (!changes.length) {
      feedback.className = "muted";
      feedback.textContent = "Nothing changed.";
      return;
    }
    try {
      await api("/api/admin/configs", { method: "PATCH", body: JSON.stringify(changes) });
      feedback.className = "muted";
      feedback.textContent = `Saved ${changes.length} value(s).`;
      toast("Settings saved", "ok");
    } catch (error) {
      feedback.className = "error";
      feedback.textContent = error.message;
    }
  }

  async function maintenance(kind) {
    const note = $("maintenanceNote");
    const button = kind === "sources" ? $("updateSources") : $("softRestart");
    if (kind === "restart" && !confirm("Restart the background services now?")) return;
    button.disabled = true;
    note.textContent = kind === "sources" ? "Updating sources…" : "Restarting…";
    try {
      await api(kind === "sources" ? "/api/admin/update-sources" : "/api/admin/soft-restart", {
        method: "POST",
      });
      note.textContent = kind === "sources" ? "Sources updated." : "Services restarted.";
      toast(note.textContent, "ok");
    } catch (error) {
      note.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  // --------------------------------------------------------------- boot

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
    const remembered = localStorage.getItem("ef_admin_novel");
    const found = state.novels.find((novel) => novel.id === remembered);
    state.novelId = (found && found.id) || (state.novels[0] && state.novels[0].id) || "";
    if (state.novelId) select.value = state.novelId;
    else select.innerHTML = '<option value="">No novels</option>';
  }

  function buildCategoryFilter() {
    const select = $("termCategory");
    for (const name of TERM_CATEGORIES) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name.replace(/_/g, " ");
      select.appendChild(option);
    }
  }

  async function boot(user) {
    if (!EF.isAdmin(user)) {
      document.querySelector(".dash-body").innerHTML =
        '<div class="card"><h2>Administrator only</h2><p class="hint">This account cannot open the Admin Dashboard.</p></div>';
      document.querySelector(".rail").hidden = true;
      document.querySelector(".dash-bar").hidden = true;
      return;
    }
    buildCategoryFilter();
    fillModelPicker();
    await loadNovels();
    showPanel((location.hash || "#overview").slice(1));
  }

  // ------------------------------------------------------------- events

  document.querySelectorAll(".nav-item").forEach((node) => {
    node.addEventListener("click", () => showPanel(node.dataset.panel));
  });

  $("novelSelect").addEventListener("change", (event) => {
    state.novelId = event.target.value;
    state.chapterOffset = 0;
    state.termOffset = 0;
    try {
      localStorage.setItem("ef_admin_novel", state.novelId);
    } catch (_) {
      /* ignore */
    }
    showPanel(state.panel);
  });

  $("signOut").addEventListener("click", logout);

  $("saveKeys").addEventListener("click", saveKeys);
  $("clearKeys").addEventListener("click", clearKeys);
  $("probeKeys").addEventListener("click", () => probeKeys(""));
  $("keyHealthTable").addEventListener("click", (event) => {
    const button = event.target.closest("[data-probe-key]");
    if (button) probeKeys(button.dataset.probeKey);
  });
  $("addModel").addEventListener("click", addModel);
  $("saveModels").addEventListener("click", saveModels);
  $("saveChains").addEventListener("click", saveChains);
  $("saveSettings").addEventListener("click", savePoolSettings);
  $("estimateButton").addEventListener("click", estimate);

  $("runExtract").addEventListener("click", runExtract);
  $("scanAll").addEventListener("click", scanAll);
  $("runDecide").addEventListener("click", runDecide);
  $("extractMode").addEventListener("change", () => {
    refreshPlan().catch((error) => toast(error.message, "error"));
  });
  ["extractOffset", "extractLimit"].forEach((id) => {
    $(id).addEventListener("change", () => {
      refreshPlan().catch((error) => toast(error.message, "error"));
    });
  });

  $("saveStyle").addEventListener("click", saveStyle);
  $("clearGlossary").addEventListener("click", clearGlossary);
  $("reloadTerms").addEventListener("click", () => {
    state.termOffset = 0;
    loadTerms().catch((error) => toast(error.message, "error"));
  });
  $("termSearch").addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    state.termOffset = 0;
    loadTerms().catch((error) => toast(error.message, "error"));
  });
  $("termCategory").addEventListener("change", () => {
    state.termOffset = 0;
    loadTerms().catch((error) => toast(error.message, "error"));
  });
  $("termSource").addEventListener("change", () => {
    state.termOffset = 0;
    loadTerms().catch((error) => toast(error.message, "error"));
  });
  $("termLimit").addEventListener("change", () => {
    state.termLimit = Number($("termLimit").value || 0);
    state.termOffset = 0;
    loadTerms().catch((error) => toast(error.message, "error"));
  });
  $("termHidden").addEventListener("change", () => {
    state.termOffset = 0;
    loadTerms().catch((error) => toast(error.message, "error"));
  });
  $("termPrev").addEventListener("click", () => {
    state.termOffset = Math.max(0, state.termOffset - state.termLimit);
    loadTerms().catch((error) => toast(error.message, "error"));
  });
  $("termNext").addEventListener("click", () => {
    state.termOffset += state.termLimit;
    loadTerms().catch((error) => toast(error.message, "error"));
  });

  document.querySelectorAll("#chapterModes .mode").forEach((button) => {
    button.addEventListener("click", () => {
      state.mode = button.dataset.mode;
      document.querySelectorAll("#chapterModes .mode").forEach((node) => {
        node.classList.toggle("active", node === button);
      });
      note(
        "chapterNote",
        `Pass set to ${EF.modeName(state.mode)}. Select chapters, then translate.`,
      );
    });
  });
  $("pageSelect").addEventListener("click", selectPage);
  $("translateSelected").addEventListener("click", translateSelected);
  $("chapterPrev").addEventListener("click", () => {
    state.chapterOffset = Math.max(0, state.chapterOffset - state.chapterLimit);
    refreshChapters().catch((error) => toast(error.message, "error"));
  });
  $("chapterNext").addEventListener("click", () => {
    state.chapterOffset += state.chapterLimit;
    refreshChapters().catch((error) => toast(error.message, "error"));
  });

  $("updateSources").addEventListener("click", () => maintenance("sources"));
  $("softRestart").addEventListener("click", () => maintenance("restart"));

  window.addEventListener("hashchange", () => {
    const name = (location.hash || "#overview").slice(1);
    if (name !== state.panel) showPanel(name);
  });
  window.addEventListener("beforeunload", stopTimers);

  gate(boot).catch((error) => toast(error.message, "error"));
})();
