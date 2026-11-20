import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import Cover from '../components/Cover';
import LibraryModal from '../components/LibraryModal';
import { chapterBody, chapterMeta, chaptersOf } from '../data/chapters';
import { getNovel, progressPercent } from '../data/catalog';
import { setProgress, useLibraryEntry } from '../data/library';
import type { ReaderSettings } from '../domain/types';
import { useLocalStorageState } from '../lib/hooks';
import { formatShortDate } from '../lib/format';
import NotFoundPage from './NotFoundPage';

type Sheet = 'display' | 'settings' | 'more' | null;
type Source = 'web' | 'webplus' | 'ai';

const DEFAULT_SETTINGS: ReaderSettings = {
  fontSize: 18,
  font: 'sans',
  width: 'wide',
  theme: 'light',
};

const SOURCES: { key: Source; label: string }[] = [
  { key: 'web', label: 'Web' },
  { key: 'webplus', label: 'Web+' },
  { key: 'ai', label: 'AI' },
];

export default function ReaderPage() {
  const { id, chapter } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const novel = getNovel(id ?? '');
  const chapterNumber = Number((chapter ?? '').replace(/^chapter-/, ''));
  const entry = useLibraryEntry(novel?.id ?? 0);

  const sourceParam = params.get('service');
  const source: Source = sourceParam === 'web' || sourceParam === 'webplus' ? sourceParam : 'ai';

  const [settings, setSettings] = useLocalStorageState<ReaderSettings>('wtrlab.reader.v1', DEFAULT_SETTINGS);
  const [tocOpen, setTocOpen] = useState(false);
  const [sheet, setSheet] = useState<Sheet>(null);
  const [tocQuery, setTocQuery] = useState('');
  const [modalOpen, setModalOpen] = useState(false);

  const chapters = useMemo(() => (novel ? chaptersOf(novel) : []), [novel]);
  const meta = novel ? chapterMeta(novel, chapterNumber) : undefined;
  const paragraphs = useMemo(
    () => (novel && meta ? chapterBody(novel, chapterNumber) : []),
    [novel, meta, chapterNumber],
  );

  const percent = novel && meta ? progressPercent(novel, chapterNumber) : 0;

  useEffect(() => {
    if (!novel || !meta) return;
    setProgress(novel.id, chapterNumber, percent);
  }, [novel, meta, chapterNumber, percent]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement) return;
      if (event.key === 'ArrowLeft' && chapterNumber > 1) {
        navigate(`/novel/${id}/${novel?.slug}/chapter-${chapterNumber - 1}`);
      }
      if (event.key === 'ArrowRight' && novel && chapterNumber < novel.chapterCount) {
        navigate(`/novel/${id}/${novel.slug}/chapter-${chapterNumber + 1}`);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [chapterNumber, id, navigate, novel]);

  if (!novel || !meta) return <NotFoundPage />;

  const prevHref = chapterNumber > 1 ? `/novel/${novel.id}/${novel.slug}/chapter-${chapterNumber - 1}` : null;
  const nextHref =
    chapterNumber < novel.chapterCount ? `/novel/${novel.id}/${novel.slug}/chapter-${chapterNumber + 1}` : null;
  const tocChapters = tocQuery.trim()
    ? chapters.filter((item) => item.title.toLowerCase().includes(tocQuery.trim().toLowerCase()))
    : chapters;

  const changeSource = (next: Source) => {
    const nextParams = new URLSearchParams(params);
    nextParams.set('service', next);
    setParams(nextParams, { replace: true });
  };

  return (
    <>
      <div
        className="container reader"
        data-font={settings.font}
        data-width={settings.width}
        data-theme={settings.theme}
      >
        <div className="crumbs">
          <Link to={`/novel/${novel.id}/${novel.slug}`}>{novel.title}</Link>
        </div>

        <section className="panel" style={{ marginBottom: 12 }}>
          <div className="reader-nav">
            {prevHref ? (
              <Link className="btn" to={prevHref}>
                <i className="bi bi-chevron-left" />
                Prev
              </Link>
            ) : (
              <button className="btn" type="button" disabled>
                <i className="bi bi-chevron-left" />
                Prev
              </button>
            )}
            <div className="reader-nav__mid">
              Ch. {chapterNumber} / {novel.chapterCount}
              <span className="reader-nav__pct">{percent}%</span>
            </div>
            {nextHref ? (
              <Link className="btn" to={nextHref}>
                Next
                <i className="bi bi-chevron-right" />
              </Link>
            ) : (
              <button className="btn" type="button" disabled>
                Next
                <i className="bi bi-chevron-right" />
              </button>
            )}
          </div>
          <div className="source-tabs">
            {SOURCES.map((item) => (
              <button
                key={item.key}
                type="button"
                className={source === item.key ? 'is-active' : undefined}
                onClick={() => changeSource(item.key)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </section>

        <article className="panel">
          <div className="chapter-card">
            <h1>
              <span>#{chapterNumber}</span> Chapter {chapterNumber}, Section {(chapterNumber % 3) + 1}: {meta.title}
            </h1>
            <div className="chapter-meta">
              <span className="chip">{meta.model}</span>
              <span className="chip">AI translation</span>
              <span>{formatShortDate(meta.publishedAt)}</span>
              <span className="spacer" />
              <span>{paragraphs.join(' ').split(' ').length.toLocaleString('en-US')} words</span>
            </div>
            <div className="chapter-body" style={{ fontSize: settings.fontSize }}>
              {paragraphs.map((paragraph, index) => (
                <p key={index}>{paragraph}</p>
              ))}
            </div>
            <div className="row" style={{ justifyContent: 'space-between', marginTop: 24 }}>
              {prevHref ? (
                <Link className="btn" to={prevHref}>
                  <i className="bi bi-chevron-left" />
                  Previous chapter
                </Link>
              ) : (
                <span />
              )}
              {nextHref ? (
                <Link className="btn btn--primary" to={nextHref}>
                  Next chapter
                  <i className="bi bi-chevron-right" />
                </Link>
              ) : null}
            </div>
          </div>
        </article>
      </div>

      <div className="reader-bar">
        <div className="container reader-bar__inner">
          <div className="reader-nav" style={{ padding: '4px 0 8px' }}>
            {prevHref ? (
              <Link className="btn btn--sm" to={prevHref}>
                <i className="bi bi-chevron-left" />
                Prev
              </Link>
            ) : (
              <button className="btn btn--sm" type="button" disabled>
                <i className="bi bi-chevron-left" />
                Prev
              </button>
            )}
            <div className="reader-nav__mid" style={{ fontSize: 14 }}>
              Ch. {chapterNumber} / {novel.chapterCount}
              <span className="reader-nav__pct">{percent}%</span>
            </div>
            {nextHref ? (
              <Link className="btn btn--sm" to={nextHref}>
                Next
                <i className="bi bi-chevron-right" />
              </Link>
            ) : (
              <button className="btn btn--sm" type="button" disabled>
                Next
                <i className="bi bi-chevron-right" />
              </button>
            )}
          </div>

          <div className="reader-bar__grid">
            <div className="row" style={{ gap: 8, minWidth: 0 }}>
              <button className="btn" type="button" onClick={() => setTocOpen(true)}>
                <i className="bi bi-list-ul" />
                Contents
              </button>
              <Link className="reader-bar__novel" to={`/novel/${novel.id}/${novel.slug}`}>
                <span className="reader-bar__thumb">
                  <Cover novel={novel} />
                </span>
                <span style={{ minWidth: 0 }}>
                  <span className="small muted" style={{ display: 'block' }}>
                    NOVEL
                  </span>
                  <span
                    style={{
                      display: 'block',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      fontSize: 14,
                      fontWeight: 600,
                    }}
                  >
                    {novel.title}
                  </span>
                </span>
                <i className="bi bi-chevron-right muted" style={{ marginLeft: 'auto' }} />
              </Link>
            </div>
            <div className="row" style={{ gap: 8 }}>
              <button className="btn" type="button" onClick={() => setSheet('more')}>
                <i className="bi bi-pencil-square" />
                Edit Terms
              </button>
              <button className="btn" type="button" onClick={() => setModalOpen(true)}>
                <i className="bi bi-plus-lg" />
                {entry ? 'In Library' : 'Add to Library'}
              </button>
            </div>
          </div>

          <div className="reader-bar__tabs">
            <button type="button" className={sheet === null ? 'is-active' : undefined} onClick={() => setSheet(null)}>
              <i className="bi bi-book" />
              Read
            </button>
            <button
              type="button"
              className={sheet === 'display' ? 'is-active' : undefined}
              onClick={() => setSheet(sheet === 'display' ? null : 'display')}
            >
              <i className="bi bi-fonts" />
              Display
            </button>
            <button
              type="button"
              className={sheet === 'settings' ? 'is-active' : undefined}
              onClick={() => setSheet(sheet === 'settings' ? null : 'settings')}
            >
              <i className="bi bi-gear" />
              Settings
            </button>
            <button
              type="button"
              className={sheet === 'more' ? 'is-active' : undefined}
              onClick={() => setSheet(sheet === 'more' ? null : 'more')}
            >
              <i className="bi bi-list-ul" />
              More
            </button>
          </div>

          {sheet ? (
            <div className="reader-sheet">
              {sheet === 'display' ? (
                <>
                  <div className="setting-row">
                    <span className="setting-row__label">Font size</span>
                    <span className="stepper">
                      <button
                        type="button"
                        aria-label="Decrease font size"
                        onClick={() =>
                          setSettings({ ...settings, fontSize: Math.max(14, settings.fontSize - 1) })
                        }
                      >
                        −
                      </button>
                      <span>{settings.fontSize}px</span>
                      <button
                        type="button"
                        aria-label="Increase font size"
                        onClick={() =>
                          setSettings({ ...settings, fontSize: Math.min(26, settings.fontSize + 1) })
                        }
                      >
                        +
                      </button>
                    </span>
                  </div>
                  <div className="setting-row">
                    <span className="setting-row__label">Typeface</span>
                    <div className="segmented" style={{ width: 190 }}>
                      <button
                        type="button"
                        className={settings.font === 'sans' ? 'is-active' : undefined}
                        onClick={() => setSettings({ ...settings, font: 'sans' })}
                      >
                        Sans
                      </button>
                      <button
                        type="button"
                        className={settings.font === 'serif' ? 'is-active' : undefined}
                        onClick={() => setSettings({ ...settings, font: 'serif' })}
                      >
                        Serif
                      </button>
                    </div>
                  </div>
                  <div className="setting-row">
                    <span className="setting-row__label">Theme</span>
                    <div className="theme-swatches">
                      {(['light', 'sepia', 'dark'] as const).map((theme) => (
                        <button
                          key={theme}
                          type="button"
                          aria-label={theme}
                          className={`theme-swatch ${settings.theme === theme ? 'is-active' : ''}`}
                          style={{
                            background:
                              theme === 'light' ? '#ffffff' : theme === 'sepia' ? '#f7f0e1' : '#1f2327',
                          }}
                          onClick={() => setSettings({ ...settings, theme })}
                        />
                      ))}
                    </div>
                  </div>
                </>
              ) : null}

              {sheet === 'settings' ? (
                <>
                  <div className="setting-row">
                    <span className="setting-row__label">Reading width</span>
                    <div className="segmented" style={{ width: 190 }}>
                      <button
                        type="button"
                        className={settings.width === 'wide' ? 'is-active' : undefined}
                        onClick={() => setSettings({ ...settings, width: 'wide' })}
                      >
                        Wide
                      </button>
                      <button
                        type="button"
                        className={settings.width === 'narrow' ? 'is-active' : undefined}
                        onClick={() => setSettings({ ...settings, width: 'narrow' })}
                      >
                        Narrow
                      </button>
                    </div>
                  </div>
                  <div className="setting-row">
                    <span className="setting-row__label">Source</span>
                    <span className="chip chip--outline">{SOURCES.find((item) => item.key === source)?.label}</span>
                  </div>
                  <div className="setting-row">
                    <span className="setting-row__label">Progress saved</span>
                    <Link className="small" style={{ color: 'var(--primary)' }} to="/library">
                      View in library
                    </Link>
                  </div>
                </>
              ) : null}

              {sheet === 'more' ? (
                <div className="stack">
                  <Link className="btn btn--block" to={`/novel/${novel.id}/${novel.slug}`}>
                    <i className="bi bi-info-circle" />
                    Novel details
                  </Link>
                  <button className="btn btn--block" type="button" onClick={() => setTocOpen(true)}>
                    <i className="bi bi-list-ul" />
                    Table of contents
                  </button>
                  <button className="btn btn--block" type="button" onClick={() => setModalOpen(true)}>
                    <i className="bi bi-plus-lg" />
                    Manage library folders
                  </button>
                  <a className="btn btn--block" href="#report">
                    <i className="bi bi-flag" />
                    Report this chapter
                  </a>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>

      {tocOpen ? (
        <>
          <button className="drawer-scrim" type="button" aria-label="Close contents" onClick={() => setTocOpen(false)} />
          <aside className="toc-drawer" aria-label="Table of contents">
            <div className="toc-drawer__head">
              <strong>Table of Contents</strong>
              <button className="btn btn--ghost btn--sm" type="button" onClick={() => setTocOpen(false)}>
                <i className="bi bi-x-lg" />
              </button>
            </div>
            <div className="toc-drawer__search">
              <div className="search-wrap">
                <i className="bi bi-search" />
                <input
                  className="input input--search"
                  placeholder="Search chapter..."
                  value={tocQuery}
                  onChange={(event) => setTocQuery(event.target.value)}
                />
              </div>
            </div>
            <div className="toc-drawer__body">
              {tocChapters.map((item) => (
                <Link
                  key={item.number}
                  to={`/novel/${novel.id}/${novel.slug}/chapter-${item.number}`}
                  className={`toc-row ${item.number === chapterNumber ? 'is-read' : ''}`}
                  onClick={() => setTocOpen(false)}
                  style={item.number === chapterNumber ? { background: 'var(--primary-soft)' } : undefined}
                >
                  <span className="toc-row__num">#{item.number}</span>
                  <span className="toc-row__title">{item.title}</span>
                </Link>
              ))}
            </div>
          </aside>
        </>
      ) : null}

      {modalOpen ? <LibraryModal novel={novel} onClose={() => setModalOpen(false)} /> : null}
    </>
  );
}
