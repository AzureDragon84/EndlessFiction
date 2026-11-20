import { useState } from 'react';
import type { Novel } from '../domain/types';
import { genreName } from '../data/taxonomy';

interface CoverProps {
  novel: Novel;
  className?: string;
}

/**
 * Renders the novel artwork, falling back to a deterministic typographic cover
 * for catalogue entries that have no artwork of their own.
 */
export default function Cover({ novel, className }: CoverProps) {
  const [failed, setFailed] = useState(false);

  if (novel.cover && !failed) {
    return (
      <img
        src={novel.cover}
        alt={`${novel.title} cover`}
        loading="lazy"
        className={className}
        onError={() => setFailed(true)}
      />
    );
  }

  return (
    <div
      className={`cover-art ${className ?? ''}`}
      style={{ background: `linear-gradient(155deg, ${novel.gradient[0]}, ${novel.gradient[1]})` }}
      role="img"
      aria-label={`${novel.title} placeholder cover`}
    >
      <span className="cover-art__genre">{genreName(novel.genres[0] ?? 'fiction')}</span>
      <span className="cover-art__title">{novel.title}</span>
      <span className="cover-art__genre">{novel.author}</span>
    </div>
  );
}
