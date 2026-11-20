import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import NovelCard from '../components/NovelCard';
import Pagination from '../components/Pagination';
import { filterNovels } from '../data/catalog';
import { GENRES, SORT_OPTIONS, STATUS_OPTIONS, TAG_GROUPS } from '../data/taxonomy';
import type { NovelStatus } from '../domain/types';

const PAGE_SIZE = 16;

const AGE_OPTIONS = [
  { value: 'all', label: 'All' },
  { value: '7', label: 'Last 7 days' },
  { value: '30', label: 'Last 30 days' },
  { value: '365', label: 'Last year' },
];

const RATING_OPTIONS = [0, 1, 2, 3, 4, 4.5];
const REVIEW_OPTIONS = [0, 5, 20, 50, 100];
const CHAPTER_OPTIONS = [0, 50, 100, 250, 500];

export default function FinderPage() {
  const [params] = useSearchParams();
  const activeTag = params.get('tag') ?? '';
  const [query, setQuery] = useState('');
  const [descriptionOnly, setDescriptionOnly] = useState(false);
  const [orderBy, setOrderBy] = useState('addition');
  const [direction, setDirection] = useState<'desc' | 'asc'>('desc');
  const [status, setStatus] = useState<string>('All');
  const [minChapters, setMinChapters] = useState(0);
  const [minRating, setMinRating] = useState(0);
  const [minReviews, setMinReviews] = useState(0);
  const [age, setAge] = useState('all');
  const [genres, setGenres] = useState<string[]>([]);
  const [excluded, setExcluded] = useState<string[]>([]);
  const [genreMode, setGenreMode] = useState<'and' | 'or'>('or');
  const [page, setPage] = useState(1);

  const toggle = (list: string[], value: string): string[] =>
    list.includes(value) ? list.filter((item) => item !== value) : [...list, value];

  const results = useMemo(() => {
    const ageDays = age === 'all' ? null : Number(age);
    const cutoff = ageDays ? Date.now() - ageDays * 86_400_000 : null;
    return filterNovels({
      query: query || undefined,
      descriptionOnly,
      orderBy: orderBy as 'addition',
      direction,
      status,
      minChapters,
      minRating,
      minReviews,
      genres,
      excludedGenres: excluded,
      genreMode,
    }).filter((novel) => {
      if (cutoff && Date.parse(novel.addedAt) < cutoff) return false;
      if (activeTag && !novel.tags.includes(activeTag)) return false;
      return true;
    });
  }, [
    query,
    descriptionOnly,
    orderBy,
    direction,
    status,
    minChapters,
    minRating,
    minReviews,
    genres,
    excluded,
    genreMode,
    age,
    activeTag,
  ]);

  const pages = Math.max(1, Math.ceil(results.length / PAGE_SIZE));
  const currentPage = Math.min(page, pages);
  const visible = results.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  const reset = () => {
    setQuery('');
    setDescriptionOnly(false);
    setOrderBy('addition');
    setDirection('desc');
    setStatus('All');
    setMinChapters(0);
    setMinRating(0);
    setMinReviews(0);
    setAge('all');
    setGenres([]);
    setExcluded([]);
    setGenreMode('or');
    setPage(1);
  };

  return (
    <div className="container">
      <div className="page-head">
        <div>
          <h1>Novel Finder</h1>
          <p>Filter the entire catalogue by status, length, rating, genre and tags.</p>
        </div>
        <div className="row">
          <a className="btn" href="#help">
            <i className="bi bi-question-circle" />
            Guide
          </a>
        </div>
      </div>

      <section className="panel" style={{ marginBottom: 12 }}>
        <div className="finder-block">
          <div className="finder-grid">
            <div className="field">
              <label htmlFor="finder-query">Search</label>
              <div className="search-wrap">
                <i className="bi bi-search" />
                <input
                  id="finder-query"
                  className="input input--search"
                  placeholder="Title or description..."
                  value={query}
                  onChange={(event) => {
                    setQuery(event.target.value);
                    setPage(1);
                  }}
                />
              </div>
              <label className="check" style={{ marginTop: 4 }}>
                <input
                  type="checkbox"
                  checked={descriptionOnly}
                  onChange={(event) => setDescriptionOnly(event.target.checked)}
                />
                Description Search Only
              </label>
            </div>

            <div className="field">
              <label htmlFor="finder-order">Order by</label>
              <select
                id="finder-order"
                className="select"
                value={orderBy}
                onChange={(event) => setOrderBy(event.target.value)}
              >
                {SORT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <div className="segmented" style={{ marginTop: 4 }}>
                <button
                  type="button"
                  className={direction === 'desc' ? 'is-active' : undefined}
                  onClick={() => setDirection('desc')}
                >
                  Descending
                </button>
                <button
                  type="button"
                  className={direction === 'asc' ? 'is-active' : undefined}
                  onClick={() => setDirection('asc')}
                >
                  Ascending
                </button>
              </div>
            </div>

            <div className="field">
              <label htmlFor="finder-status">Status</label>
              <select
                id="finder-status"
                className="select"
                value={status}
                onChange={(event) => setStatus(event.target.value)}
              >
                <option value="All">All</option>
                {STATUS_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
              <label htmlFor="finder-age" style={{ marginTop: 6 }}>
                Addition age
              </label>
              <select id="finder-age" className="select" value={age} onChange={(event) => setAge(event.target.value)}>
                {AGE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="field">
              <label htmlFor="finder-chapters">Chapter minimum</label>
              <select
                id="finder-chapters"
                className="select"
                value={minChapters}
                onChange={(event) => setMinChapters(Number(event.target.value))}
              >
                {CHAPTER_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option === 0 ? 'Any chapters' : `${option}+ chapters`}
                  </option>
                ))}
              </select>

              <label htmlFor="finder-rating" style={{ marginTop: 6 }}>
                Minimum rating
              </label>
              <select
                id="finder-rating"
                className="select"
                value={minRating}
                onChange={(event) => setMinRating(Number(event.target.value))}
              >
                {RATING_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option === 0 ? 'Any rating' : `${option}+`}
                  </option>
                ))}
              </select>

              <label htmlFor="finder-reviews" style={{ marginTop: 6 }}>
                Minimum review count
              </label>
              <select
                id="finder-reviews"
                className="select"
                value={minReviews}
                onChange={(event) => setMinReviews(Number(event.target.value))}
              >
                {REVIEW_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option === 0 ? 'Any reviews' : `${option}+`}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>

        <div className="finder-block">
          <div className="finder-block__title">
            <span>
              Genre <span className="muted small">{genres.length} selected</span>
            </span>
            <div className="segmented" style={{ width: 128 }}>
              <button
                type="button"
                className={genreMode === 'and' ? 'is-active' : undefined}
                onClick={() => setGenreMode('and')}
              >
                And
              </button>
              <button
                type="button"
                className={genreMode === 'or' ? 'is-active' : undefined}
                onClick={() => setGenreMode('or')}
              >
                Or
              </button>
            </div>
          </div>
          <div className="genre-check-grid">
            {GENRES.map((genre) => (
              <label key={genre.slug} className="check">
                <input
                  type="checkbox"
                  checked={genres.includes(genre.slug)}
                  onChange={() => {
                    setGenres((current) => toggle(current, genre.slug));
                    setPage(1);
                  }}
                />
                {genre.name}
              </label>
            ))}
          </div>
        </div>

        <div className="finder-block">
          <div className="finder-block__title">
            <span>Tags exclude</span>
            {activeTag ? <span className="chip chip--muted">Filtering tag: {activeTag}</span> : null}
          </div>
          <div className="genre-check-grid">
            {TAG_GROUPS.flatMap((group) => group.tags).map((tag) => (
              <label key={tag} className="check">
                <input
                  type="checkbox"
                  checked={excluded.includes(tag)}
                  onChange={() => {
                    setExcluded((current) => toggle(current, tag));
                    setPage(1);
                  }}
                />
                {tag}
              </label>
            ))}
          </div>
        </div>

        <div className="finder-actions">
          <button className="btn" type="button" onClick={reset}>
            Clear
          </button>
          <button className="btn btn--filled" type="button" onClick={() => setPage(1)}>
            Apply Filters
          </button>
        </div>
      </section>

      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 10 }}>
        <span className="results-count">{results.length} novels found</span>
      </div>

      {visible.length ? (
        <>
          <div className="catalog-grid">
            {visible.map((novel) => (
              <NovelCard key={novel.id} novel={novel} showAdded={false} />
            ))}
          </div>
          <Pagination page={currentPage} pages={pages} onChange={setPage} />
        </>
      ) : (
        <div className="panel">
          <div className="empty">
            <i className="bi bi-funnel" />
            No novels match those filters.
          </div>
        </div>
      )}
    </div>
  );
}
