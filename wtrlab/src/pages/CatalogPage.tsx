import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import NovelCard from '../components/NovelCard';
import Pagination from '../components/Pagination';
import { filterNovels } from '../data/catalog';
import { GENRES, STATUS_OPTIONS } from '../data/taxonomy';
import type { NovelStatus } from '../domain/types';

const PAGE_SIZE = 16;

const SORTS = [
  { value: 'addition', label: 'Addition Date' },
  { value: 'views', label: 'Views' },
  { value: 'rating', label: 'Rating' },
  { value: 'chapter', label: 'Chapters' },
  { value: 'name', label: 'Name' },
];

export default function CatalogPage() {
  const [params, setParams] = useSearchParams();
  const genre = params.get('genre') ?? '';
  const [status, setStatus] = useState<string>('All');
  const [sort, setSort] = useState('addition');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);

  const results = useMemo(
    () =>
      filterNovels({
        genre: genre || undefined,
        status,
        orderBy: sort as 'addition',
        query: query || undefined,
      }),
    [genre, status, sort, query],
  );

  const pages = Math.max(1, Math.ceil(results.length / PAGE_SIZE));
  const currentPage = Math.min(page, pages);
  const visible = results.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  return (
    <div className="container">
      <div className="page-head">
        <div>
          <h1>Novels</h1>
          <p>Every translated novel in the catalogue, updated as chapters are released.</p>
        </div>
        <Link className="btn" to="/novel-finder">
          <i className="bi bi-sliders" />
          Novel Finder
        </Link>
      </div>

      <section className="panel" style={{ marginBottom: 12 }}>
        <div style={{ padding: 14 }} className="stack">
          <div className="filter-bar">
            <div className="search-wrap" style={{ flex: '1 1 240px' }}>
              <i className="bi bi-search" />
              <input
                className="input input--search"
                placeholder="Search title..."
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setPage(1);
                }}
              />
            </div>
            <select
              className="select"
              style={{ width: 160 }}
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                setPage(1);
              }}
              aria-label="Status"
            >
              <option value="All">All status</option>
              {STATUS_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
            <select
              className="select"
              style={{ width: 170 }}
              value={sort}
              onChange={(event) => setSort(event.target.value)}
              aria-label="Sort by"
            >
              {SORTS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <span className="results-count">{results.length} results</span>
          </div>

          <div className="genre-strip">
            <Link
              to="/novels"
              className={`chip chip-link ${genre ? 'chip--outline' : 'chip--muted'}`}
              onClick={() => {
                const next = new URLSearchParams(params);
                next.delete('genre');
                setParams(next);
                setPage(1);
              }}
            >
              All genres
            </Link>
            {GENRES.map((item) => (
              <Link
                key={item.slug}
                to={`/novels?genre=${item.slug}`}
                className={`chip chip-link ${genre === item.slug ? 'chip--muted' : 'chip--outline'}`}
                onClick={() => setPage(1)}
              >
                {item.name}
              </Link>
            ))}
          </div>
        </div>
      </section>

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
            <i className="bi bi-search" />
            No novels match those filters.
          </div>
        </div>
      )}
    </div>
  );
}
