interface StarsProps {
  rating: number;
  votes?: number;
  strong?: boolean;
}

export default function Stars({ rating, votes, strong }: StarsProps) {
  return (
    <span className={`rating ${strong ? 'rating--strong' : ''}`}>
      <i className="bi bi-star-fill" aria-hidden="true" />
      <span>{rating.toFixed(1)}</span>
      {typeof votes === 'number' ? <span style={{ fontWeight: 500 }}>({votes})</span> : null}
    </span>
  );
}
