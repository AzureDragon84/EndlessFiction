import { useMemo } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import NovelCard from '../components/NovelCard';
import { searchNovels } from '../data/catalog';

export default function SearchPage() {
  const [params] = useSearchParams();
  const query = params.get('q') ?? '';
  const results = useMemo(() => searchNovels(query), [query]);

  return (
    <div className="container">
      <div className="page-head">
        <div>
          <h1>Search</h1>
          <p>
            {query ? (
              <>
                {results.length} result{results.length === 1 ? '' : 's'} for “{query}”
              </>
            ) : (
              'Enter a title, author or genre in the search bar above.'
            )}
          </p>
        </div>
        <Link className="btn" to="/novel-finder">
          <i className="bi bi-sliders" />
          Advanced search
        </Link>
      </div>

      {results.length ? (
        <div className="catalog-grid">
          {results.map((novel) => (
            <NovelCard key={novel.id} novel={novel} showAdded={false} />
          ))}
        </div>
      ) : (
        <div className="panel">
          <div className="empty">
            <i className="bi bi-search" />
            {query ? 'No novels matched that search.' : 'Search the catalogue from the header.'}
          </div>
        </div>
      )}
    </div>
  );
}
