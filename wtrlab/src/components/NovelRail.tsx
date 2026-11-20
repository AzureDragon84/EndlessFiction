import { useRef } from 'react';
import { Link } from 'react-router-dom';
import type { Novel } from '../domain/types';
import NovelCard from './NovelCard';

interface NovelRailProps {
  title: string;
  href?: string;
  novels: Novel[];
  showAdded?: boolean;
}

export default function NovelRail({ title, href, novels, showAdded = true }: NovelRailProps) {
  const railRef = useRef<HTMLDivElement>(null);

  const scrollBy = (direction: 1 | -1) => {
    const node = railRef.current;
    if (!node) return;
    node.scrollBy({ left: direction * Math.max(240, node.clientWidth * 0.7), behavior: 'smooth' });
  };

  return (
    <section className="panel">
      <header className="panel__head">
        <h2>{title}</h2>
        <div className="row">
          {href ? (
            <Link className="btn btn--sm" to={href}>
              See More
            </Link>
          ) : null}
          <button className="btn btn--sm" type="button" aria-label="Scroll left" onClick={() => scrollBy(-1)}>
            <i className="bi bi-chevron-left" />
          </button>
          <button className="btn btn--sm" type="button" aria-label="Scroll right" onClick={() => scrollBy(1)}>
            <i className="bi bi-chevron-right" />
          </button>
        </div>
      </header>
      <div style={{ padding: '0 14px 6px' }}>
        <div className="card-rail card-rail--pages" ref={railRef}>
          {novels.map((novel) => (
            <NovelCard key={novel.id} novel={novel} showAdded={showAdded} />
          ))}
        </div>
      </div>
    </section>
  );
}
