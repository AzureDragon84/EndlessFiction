---
name: output-and-translation
description: Binder (e-book output formats) and the Translation service — architecture and recipes for adding an output format or changing how novels are translated. Use when working in services/binder/ or services/translators.py.
---

# Binder (`lncrawl/services/binder/`)

A dispatch table `archive_maker: Dict[OutputFormat, Callable]` in `service.py` maps each
format to a builder. EPUB, JSON, and text have native builders; every other format is
`convert_epub` — Calibre converts **from a previously built EPUB artifact** (it raises
`ServerErrors.no_epub_file` without one). `available_formats` derives from Calibre
availability: without Calibre only the native formats exist. Zip-emitting builders are listed
in `requires_zip`.

**Builder contract** (all builders): `def make_x(working_dir: Path, artifact: Artifact,
signal=Event(), **kwargs) -> None` — write to a temp file inside `working_dir`, then
atomically replace `ctx.files.resolve(artifact.output_file)`. Poll `signal.is_set()` and raise
`AbortedException` for cancellation. `make_artifact` owns the Artifact row, temp-dir lifecycle,
and cleanup.

**Calibre** (`calibre.py`) has two paths: a remote convert API (posted through
`ctx.http.session(signal)`) and the local `ebook-convert` executable, with configurable
fallback. Options are built backend-agnostic as `(flag, value)` tuples and rendered per path.

**Recipe — new output format**:
1. Add the value to `OutputFormat` in `lncrawl/enums.py` (string enum; note name ≠ value is
   allowed, e.g. `text` → `"txt"`). Enum changes need a Postgres enum-sync migration — see the
   `db-migration` skill.
2. Register it in `archive_maker`. If Calibre supports the extension, point it at
   `convert_epub` (one line); otherwise write a builder following the contract (add to
   `requires_zip` if it emits a zip).
3. Nothing else: `available_formats` and the API derive from the enum + table. Per-tier
   format gating is separate (`ctx.tier.enabled_formats`).

**Files**: `ctx.files` (`services/file.py`) resolves artifact-relative paths under the app
data dir, writes atomically, and transparently gzip-compresses text — chapter content on disk
is compressed; always read it via `ctx.files.load_text(...)`.

# Translation (`lncrawl/services/translators.py`)

Translation runs **in-process** via the external `lncrawl-translator` package (imported as
`translator`; sibling repo). `ctx.translator` (`TranslationService`) owns the glossary loop
and persistence; the package owns engines, routing lanes, rate limits, retries, and failover.

- **`ctx.translator.engine`** — a lazily constructed `translator.TranslatorService`: a sync
  facade running the async engine router on its own event-loop thread. Its YAML config path
  comes from `ctx.config.translator.config_file` (app data dir); engines and routing are
  edited through the mounted dashboard. Provider API keys are the one overlap: the admin
  settings page exposes one masked `*_api_key` property per keyed provider, and those
  properties read and write that same YAML (a sparse overlay) so the two UIs cannot
  disagree — a save also swaps the config into a live engine via `reload_config()`.
  `close()` is guarded in `ctx.destroy()`.
- **Dashboard** — the package's web UI is mounted at `/api/translator` by an admin-gated
  ASGI wrapper (`server/api/translator.py`, `TranslatorDashboard`): Bearer/`?token=`/cookie
  auth, token→cookie redirect dance, trailing-slash enforcement. It is a raw ASGI mount —
  app-level exception handlers and router security do NOT apply inside it.
- **Calls** — `_translate_texts`/`_translate_html` build request dicts and go through
  `_invoke`, which maps package errors to `ServerErrors`: `ApiError` 503 →
  `translation_quota_exhausted`, other `ApiError`/`ValidationError` → `translation_failure`,
  facade `AbortedError` → `AbortedException`. The job `signal` and
  `ctx.config.translator.request_timeout` are passed to every engine call.
- **Gemini key pool** — Gemini is configured by *model*, not by key: `translator.gemini_models`
  is an ordered model list and `translator.gemini_api_keys` a list of keys. `sync_gemini_pool()`
  materialises them as one provider per key (`gemini-pool-001`, …) and one engine per
  `(model, key)` (`gemini-pool-001@gemini-3.8-flash`), with both routing lanes model-major so
  every key of the best model is tried before the next model. Rebuilding removes only
  `gemini-pool-*` entries, which is why a dropped key disappears on save. Keys are probed by
  `POST /api/admin/translator/keys/check` (GET `<base>/models` per key) and dead ones dropped
  by `.../keys/prune`; the last check is cached in `translator-keys.json` and shown read-only
  as `gemini_key_status`. Both list settings are **comma separated single lines** on purpose:
  the settings page edits every value in a single-line input, which strips newlines and would
  otherwise glue the entries together. Stored keys are listed as `first4…last4` previews and a
  preview is swapped back for its real key on save, so adding one key to a masked pool works.
- **Glossary loop** — `translate_novel/volume/chapter` load `glossary_entries` for the
  target language (joined to `glossary_terms`) as a plain `source -> rendering` dict, send
  it with each request, and merge returned `new_terms` back (origin `engine`). A chapter's
  *first* translation also runs the scan pre-pass (`scan_chapter_terms`), which writes the
  same coverage rows a review scan would, so the chapter is never counted twice. Terms come
  from extraction methods (`services/glossary_extract.py`: `frequency` n-grams, jieba `pos`,
  imported `seed`), unioned per chapter by **maximum** count — never summed — and stored with
  `freq` derived from `glossary_occurrences`. Editor writes go through `services/glossary.py`
  and are `locked`, so an engine merge never overwrites them; `NovelGlossary.revision` bumps
  once per batch that changed something. Chapter translation dedupes via a content hash and
  stores results through `ctx.files.save_text`.
- **Glossary review screen** — the translator dashboard is the package's own app and knows
  engines, not novels, so the novel side lives at `/api/glossary/ui` (`server/api/glossary.py`
  + a single self-contained page in `server/glossary/index.html`, signed in with an admin
  account). Scans are coverage-based (`scan_novel_terms`, no engine quota): a chapter counts
  as read for a method once its `glossary_scans` row exists, so repeated calls walk the novel
  to the end and `force` re-reads a range. `freq`/`first_chapter_serial` are caches recomputed
  from `glossary_occurrences`; terms carry `tier` (core/tail — the tail is only kept by a
  `min_freq=1` harvest pass) and `fragment_of` (a surface a longer kept term accounts for,
  hidden unless asked for). The page lists terms with frequency/reading/category/candidates/
  tier/origin, saves one term's rendering as a locked decision, rejects false positives into
  `glossary_overrides` so no scan proposes them again, and imports a pasted list
  (`origin=seed`). A method that cannot run (jieba missing, wrong script) is reported with its
  reason instead of being silently dropped.
- **Detection** — `ctx.translator.detect_language(text)` (local, no quota, no event loop)
  returns an ISO 639-1 code or None. `fetch_novel`/`fetch_chapter` use it to fill
  `Novel.language` when the source doesn't provide one; values are normalized via
  `_normalize_language` in `services/crawler.py` (drops `multi`/unknown, maps `zh-cn`→`zh`).

**Changing engines/prompts/chunking** happens in the `lncrawl-translator` package (sibling
repo), not here — bump the dependency version in `pyproject.toml` to pick up a release.
