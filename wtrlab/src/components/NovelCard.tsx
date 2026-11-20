import { Link } from 'react-router-dom';
import type { Novel } from '../domain/types';
import { formatCount, timeAgo } from '../lib/format';
import { genreName } from '../data/taxonomy';
import Cover from './Cover';

interface NovelCardProps {
  novel: Novel;
  showAdded?: boolean;
}

export default function NovelCard({ novel, showAdded = true }: NovelCardProps) {
  return (
    <article className="novel-card">
      <Link to={`/novel/${novel.id}/${novel.slug}`} className="novel-card__cover">
        <Cover novel={novel} />
      </Link>
      <Link to={`/novel/${novel.id}/${novel.slug}`} className="novel-card__title">
        {novel.title}
      </Link>
      <div className="novel-card__chips">
        <span className="novel-card__genre">{genreName(novel.genres[0] ?? 'fiction')}</span>
        <span>{formatCount(novel.chapterCount)} Ch</span>
      </div>
      {showAdded ? (
        <span className="novel-card__added">Added {timeAgo(novel.addedAt)}</span>
      ) : (
        <span className="novel-card__added">
          <i className="bi bi-eye" style={{ marginRight: 4 }} />
          {formatCount(novel.views)} views
        </span>
      )}
    </article>
  );
}
