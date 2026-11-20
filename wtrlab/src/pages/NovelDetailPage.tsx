import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import Cover from '../components/Cover';
import LibraryModal from '../components/LibraryModal';
import Pagination from '../components/Pagination';
import Stars from '../components/Stars';
import { chaptersOf } from '../data/chapters';
import { getNovel, reviewsOf, threadsOf } from '../data/catalog';
import { useLibraryEntry } from '../data/library';
import { genreName } from '../data/taxonomy';
import { formatShortDate, formatCount, formatDate, timeAgo, initials } from '../lib/format';
import NotFoundPage from './NotFoundPage';

const TABS = [
  { key: 'about', label: 'About' },
  { key: 'toc', label: 'Table of Contents' },
  { key: 'reviews', label: 'Reviews' },
  { key: 'community', label: 'Community' },
] as const;

type TabKey = (typeof TABS)[number]['key'];

const PER_PAGE = 50;

export default function NovelDetailPage() {
  const { id } = useParams();
  const novel = getNovel(id ?? '');
  const entry = useLibraryEntry(novel?.id ?? 0);
  const [tab, setTab] = useState<TabKey>('about');
  const [modalOpen, setModalOpen] = useState(false);
  const [tocQuery, setTocQuery] = useState('');
  const [tocPage, setTocPage] = useState(1);
  const [tocAsc, setTocAsc] = useState(true);

  const chapters = useMemo(() => (novel ? chaptersOf(novel) : []), [novel]);
  const filteredChapters = useMemo(() => {
    const list = tocQuery.trim()
      ? chapters.filter((chapter) => chapter.title.toLowerCase().includes(tocQuery.trim().toLowerCase()))
      : chapters;
    return tocAsc ? list : [...list].reverse();
  }, [chapters, tocQuery, tocAsc]);

  if (!novel) return <NotFoundPage />;

  const startChapter = entry?.progress?.chapter ?? 1;
  const reviews = reviewsOf(novel);
  const threads = threadsOf(novel);
  const pageCount = Math.max(1, Math.ceil(filteredChapters.length / PER_PAGE));
  const currentPage = Math.min(tocPage, pageCount);
  const visibleChapters = filteredChapters.slice((currentPage - 1) * PER_PAGE, currentPage * PER_PAGE);
  const ratingCount = novel.votes;

  return (
    <div className="container">
      <div className="crumbs">
        <Link to="/">Home</Link>
        <i className="bi bi-chevron-right" style={{ fontSize: 11 }} />
        <Link to="/novels">Novels</Link>
        <i className="bi bi-chevron-right" style={{ fontSize: 11 }} />
        <span className="muted">{novel.title}</span>
      </div>

      <section className="panel" style={{ marginBottom: 12 }}>
        <div className="novel-title-row">
          <h1>{novel.title}</h1>
          <button className="report-btn" type="button" aria-label="Report this novel">
            <i className="bi bi-exclamation-triangle" />
          </button>
        </div>

        <div className="detail-hero">
          <Link className="detail-cover" to={`/novel/${novel.id}/${novel.slug}/chapter-${startChapter}`}>
            <Cover novel={novel} />
          </Link>
          <div>
            <div className="stat-grid">
              <div className="stat">
                <span className="stat__label">Status</span>
                <span className="stat__value">
                  <span className="badge-dot" />
                  {novel.status}
                </span>
              </div>
              <div className="stat">
                <span className="stat__label">Views</span>
                <span className="stat__value">{formatCount(novel.views)}</span>
              </div>
              <div className="stat">
                <span className="stat__label">Chapters</span>
                <span className="stat__value">{novel.chapterCount}</span>
              </div>
              <div className="stat">
                <span className="stat__label">Rating</span>
                <span className="stat__value">
                  <i className="bi bi-star-fill" style={{ color: 'var(--star)', fontSize: 13 }} />
                  {novel.rating.toFixed(1)} ({ratingCount})
                </span>
              </div>
              <div className="stat">
                <span className="stat__label">Readers</span>
                <span className="stat__value">{formatCount(novel.readers)}</span>
              </div>
              <div className="stat">
                <span className="stat__label">Chars</span>
                <span className="stat__value">{formatCount(novel.chars)}</span>
              </div>
            </div>

            <div className="detail-actions">
              <button className="btn btn--block" type="button" onClick={() => setModalOpen(true)}>
                <i className="bi bi-plus-lg" />
                Add to Library
              </button>
              <Link className="btn btn--block btn--primary" to={`/novel/${novel.id}/${novel.slug}/chapter-${startChapter}`}>
                Start Reading
              </Link>
            </div>
          </div>
        </div>

        <div className="detail-tags">
          <span className="chip chip--warm">♂ {novel.audience.male}</span>
          <span className="chip chip--pink">♀ {novel.audience.female}</span>
          {novel.tags.slice(0, 13).map((tag) => (
            <Link
              key={tag}
              to={`/novel-finder?tag=${encodeURIComponent(tag)}`}
              className="chip chip--outline chip-link"
            >
              {tag}
            </Link>
          ))}
        </div>
      </section>

      <section className="panel ai-unlock" style={{ marginBottom: 12 }}>
        <div className="ai-unlock__head">
          <span>
            <i className="bi bi-translate" />
            AI-Unlock Progress
          </span>
          <span className="ai-unlock__count">
            {novel.unlocked} <small>/ {novel.chapterCount}</small>
          </span>
        </div>
        <div className="progress" style={{ height: 8 }}>
          <div className="progress__bar" style={{ width: `${(novel.unlocked / novel.chapterCount) * 100}%` }} />
        </div>
        <div className="ai-unlock__foot">
          {novel.unlocked >= novel.chapterCount ? 'All chapters unlocked' : `${novel.chapterCount - novel.unlocked} chapters pending`}
        </div>
      </section>

      <section className="panel">
        <div className="detail-tabs">
          <div className="segmented segmented--wide">
            {TABS.map((item) => (
              <button
                key={item.key}
                type="button"
                className={tab === item.key ? 'is-active' : undefined}
                onClick={() => setTab(item.key)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        {tab === 'about' ? (
          <div className="tab-panel">
            <h2 className="section-title">Novel Summary</h2>
            <p className="summary-text">{novel.summary}</p>

            <h2 className="section-title" style={{ marginTop: 26 }}>
              Details
            </h2>
            <div className="detail-grid">
              <div className="detail-grid__item">
                <span className="detail-grid__label">Titles</span>
                <span className="detail-grid__value">{novel.altTitles.join(' / ')}</span>
              </div>
              <div className="detail-grid__item">
                <span className="detail-grid__label">Status</span>
                <span className="detail-grid__value">{novel.status}</span>
              </div>
              <div className="detail-grid__item">
                <span className="detail-grid__label">Date Added</span>
                <span className="detail-grid__value">{formatDate(novel.addedAt)}</span>
              </div>
              <div className="detail-grid__item">
                <span className="detail-grid__label">Author</span>
                <span className="detail-grid__value">{novel.author}</span>
              </div>
              <div className="detail-grid__item">
                <span className="detail-grid__label">Requested</span>
                <span className="detail-grid__value">{novel.requestedBy ?? '—'}</span>
              </div>
              <div className="detail-grid__item">
                <span className="detail-grid__label">Released</span>
                <span className="detail-grid__value">{novel.releasedBy ?? 'WTR-LAB'}</span>
              </div>
              <div className="detail-grid__item">
                <span className="detail-grid__label">Rankings</span>
                <span className="detail-grid__value">
                  {novel.rankings
                    ? Object.entries(novel.rankings)
                        .map(([period, rank]) => `${period.replace('_', ' ')} #${rank}`)
                        .join(' · ')
                    : 'Unranked'}
                </span>
              </div>
              <div className="detail-grid__item">
                <span className="detail-grid__label">Updated</span>
                <span className="detail-grid__value">{timeAgo(novel.addedAt)}</span>
              </div>
            </div>

            <div className="tag-groups">
              <div className="tag-group">
                <div className="tag-group__name">
                  Genre <span>{novel.genres.length}</span>
                </div>
                <div className="chip-row">
                  {novel.genres.map((genre) => (
                    <Link key={genre} to={`/novels?genre=${genre}`} className="chip chip--outline chip-link">
                      {genreName(genre)}
                    </Link>
                  ))}
                </div>
              </div>
              {novel.tagGroups.map((group) => (
                <div key={group.name} className="tag-group">
                  <div className="tag-group__name">
                    {group.name} <span>{group.tags.length}</span>
                  </div>
                  <div className="chip-row">
                    {group.tags.map((tag) => (
                      <Link
                        key={tag}
                        to={`/novel-finder?tag=${encodeURIComponent(tag)}`}
                        className="chip chip--outline chip-link"
                      >
                        {tag}
                      </Link>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {tab === 'toc' ? (
          <div className="tab-panel">
            <div className="toc-toolbar">
              <div className="search-wrap" style={{ flex: 1 }}>
                <i className="bi bi-search" />
                <input
                  className="input input--search"
                  placeholder="Search chapter title..."
                  value={tocQuery}
                  onChange={(event) => {
                    setTocQuery(event.target.value);
                    setTocPage(1);
                  }}
                />
              </div>
              <button className="btn" type="button" onClick={() => setTocAsc((value) => !value)}>
                <i className={`bi bi-sort-${tocAsc ? 'down' : 'up'}`} />
                {tocAsc ? 'Ascending' : 'Descending'}
              </button>
            </div>
            <div className="toc-list">
              {visibleChapters.map((chapter) => {
                const isRead = Boolean(entry?.progress && chapter.number <= entry.progress.chapter);
                return (
                  <Link
                    key={chapter.number}
                    to={`/novel/${novel.id}/${novel.slug}/chapter-${chapter.number}`}
                    className={`toc-row ${isRead ? 'is-read' : ''}`}
                  >
                    <span className="toc-row__num">#{chapter.number}</span>
                    <span className="toc-row__title">{chapter.title}</span>
                    <span className="toc-row__date">{formatShortDate(chapter.publishedAt)}</span>
                  </Link>
                );
              })}
              {visibleChapters.length === 0 ? <div className="empty">No chapters match that search.</div> : null}
            </div>
            <Pagination page={currentPage} pages={pageCount} onChange={setTocPage} />
          </div>
        ) : null}

        {tab === 'reviews' ? (
          <div className="tab-panel">
            <div className="rating-summary">
              <div>
                <div className="rating-summary__score">{novel.rating.toFixed(1)}</div>
                <div className="rating-summary__stars">
                  {[0, 1, 2, 3, 4].map((index) => (
                    <i
                      key={index}
                      className={`bi bi-star${index < Math.round(novel.rating) ? '-fill' : ''}`}
                      style={{ letterSpacing: 0 }}
                    />
                  ))}
                </div>
              </div>
              <div>
                <div style={{ fontWeight: 700 }}>{formatCount(novel.reviewCount)} reviews</div>
                <div className="small muted">{formatCount(novel.votes)} ratings · demo content</div>
              </div>
            </div>
            {reviews.map((review) => (
              <article key={review.id} className="review">
                <div className="review__head">
                  <span className="avatar">{initials(review.user)}</span>
                  <div>
                    <div className="review__name">{review.user}</div>
                    <div className="review__meta">
                      <Stars rating={review.rating} /> · {timeAgo(review.date)}
                    </div>
                  </div>
                </div>
                <p className="review__body">{review.body}</p>
                <div className="review__foot">
                  <span>
                    <i className="bi bi-hand-thumbs-up" /> {review.likes}
                  </span>
                  <span>
                    <i className="bi bi-chat" /> {review.replies}
                  </span>
                </div>
              </article>
            ))}
          </div>
        ) : null}

        {tab === 'community' ? (
          <div className="tab-panel">
            {threads.map((thread) => (
              <div key={thread.id} className="review" style={{ display: 'flex', gap: 12 }}>
                <span className="avatar">{initials(thread.user)}</span>
                <div>
                  <div style={{ fontWeight: 700 }}>{thread.title}</div>
                  <div className="review__meta" style={{ marginTop: 4 }}>
                    {thread.user} · {timeAgo(thread.date)} · {thread.replies} replies
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : null}
      </section>

      {modalOpen ? <LibraryModal novel={novel} onClose={() => setModalOpen(false)} /> : null}
    </div>
  );
}
