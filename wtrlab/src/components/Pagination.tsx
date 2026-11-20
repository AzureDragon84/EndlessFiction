interface PaginationProps {
  page: number;
  pages: number;
  onChange: (page: number) => void;
}

export default function Pagination({ page, pages, onChange }: PaginationProps) {
  if (pages <= 1) return null;
  const windowStart = Math.max(1, Math.min(page - 2, pages - 4));
  const items = Array.from({ length: Math.min(5, pages) }, (_, index) => windowStart + index).filter(
    (value) => value >= 1 && value <= pages,
  );

  return (
    <nav className="pagination" aria-label="Pagination">
      <button type="button" onClick={() => onChange(page - 1)} disabled={page === 1} aria-label="Previous page">
        <i className="bi bi-chevron-left" />
      </button>
      {windowStart > 1 ? (
        <>
          <button type="button" onClick={() => onChange(1)}>
            1
          </button>
          <span className="muted" style={{ padding: '0 4px' }}>
            …
          </span>
        </>
      ) : null}
      {items.map((item) => (
        <button
          key={item}
          type="button"
          className={item === page ? 'is-active' : undefined}
          onClick={() => onChange(item)}
          aria-current={item === page ? 'page' : undefined}
        >
          {item}
        </button>
      ))}
      {windowStart + items.length - 1 < pages ? (
        <>
          <span className="muted" style={{ padding: '0 4px' }}>
            …
          </span>
          <button type="button" onClick={() => onChange(pages)}>
            {pages}
          </button>
        </>
      ) : null}
      <button type="button" onClick={() => onChange(page + 1)} disabled={page === pages} aria-label="Next page">
        <i className="bi bi-chevron-right" />
      </button>
    </nav>
  );
}
