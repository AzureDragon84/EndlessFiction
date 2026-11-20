import { useState } from 'react';
import { Link } from 'react-router-dom';
import { POLLS, rankedNovels } from '../data/catalog';
import type { Novel, RankingPeriod } from '../domain/types';
import { formatCount } from '../lib/format';
import Cover from './Cover';

const PERIODS: { key: RankingPeriod; label: string }[] = [
  { key: 'daily', label: 'Daily' },
  { key: 'weekly', label: 'Weekly' },
  { key: 'monthly', label: 'Monthly' },
];

const PER_PAGE = 5;

export function RankingWidget() {
  const [period, setPeriod] = useState<RankingPeriod>('daily');
  const [page, setPage] = useState(0);
  const novels = rankedNovels(period, 25);
  const pages = Math.ceil(novels.length / PER_PAGE);
  const visible = novels.slice(page * PER_PAGE, page * PER_PAGE + PER_PAGE);

  return (
    <section className="panel">
      <header className="panel__head">
        <h2>Novel Ranking</h2>
        <Link className="btn btn--sm" to={`/ranking/${period}`}>
          See More
        </Link>
      </header>
      <div className="rank-widget__tabs">
        <div className="line-tabs" role="tablist">
          {PERIODS.map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={period === item.key}
              className={period === item.key ? 'is-active' : undefined}
              onClick={() => {
                setPeriod(item.key);
                setPage(0);
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>
      <div>
        {visible.map((novel, index) => (
          <RankRow key={novel.id} novel={novel} rank={page * PER_PAGE + index + 1} />
        ))}
      </div>
      <div className="widget-pager">
        <button type="button" aria-label="Previous page" onClick={() => setPage((current) => Math.max(0, current - 1))}>
          <i className="bi bi-chevron-left" />
        </button>
        <div className="widget-pager__dots">
          {Array.from({ length: pages }, (_, index) => (
            <button
              key={index}
              type="button"
              aria-label={`Page ${index + 1}`}
              className={`widget-pager__dot ${index === page ? 'is-active' : ''}`}
              onClick={() => setPage(index)}
            />
          ))}
        </div>
        <button
          type="button"
          aria-label="Next page"
          onClick={() => setPage((current) => Math.min(pages - 1, current + 1))}
        >
          <i className="bi bi-chevron-right" />
        </button>
      </div>
    </section>
  );
}

function RankRow({ novel, rank }: { novel: Novel; rank: number }) {
  return (
    <Link className="rank-row" to={`/novel/${novel.id}/${novel.slug}`}>
      <span className="rank-row__num">{rank}</span>
      <span className="rank-row__thumb">
        <Cover novel={novel} />
      </span>
      <div style={{ minWidth: 0 }}>
        <div className="rank-row__title">{novel.title}</div>
        <div className="rank-row__meta">
          <span>
            <i className="bi bi-eye" style={{ marginRight: 4 }} />
            {formatCount(novel.views)} Views
          </span>
          <span className="rating">
            <i className="bi bi-star-fill" />
            {novel.rating.toFixed(1)}
          </span>
        </div>
      </div>
    </Link>
  );
}

export function CommunityTiles() {
  return (
    <div className="tile-grid">
      <Link to="/novels" className="feature-tile">
        <i className="bi bi-infinity" />
        <span>Community</span>
      </Link>
      <Link to="/novels" className="feature-tile">
        <i className="bi bi-folder2-open" />
        <span>Community Folders</span>
      </Link>
    </div>
  );
}

export function PollsWidget() {
  const [tab, setTab] = useState<'recent' | 'popular'>('recent');
  const poll = tab === 'recent' ? POLLS[0] : [...POLLS].sort((a, b) => b.voters - a.voters)[0];
  if (!poll) return null;
  const max = Math.max(...poll.options.map((option) => option.votes));

  return (
    <section className="panel">
      <header className="panel__head">
        <h2>Polls</h2>
        <button className="btn btn--sm btn--filled" type="button">
          See More
        </button>
      </header>
      <div className="line-tabs" style={{ padding: '0 14px' }}>
        <button type="button" className={tab === 'recent' ? 'is-active' : ''} onClick={() => setTab('recent')}>
          Most Recent
        </button>
        <button type="button" className={tab === 'popular' ? 'is-active' : ''} onClick={() => setTab('popular')}>
          Most Popular
        </button>
      </div>
      <div style={{ padding: 14 }}>
        <div style={{ fontWeight: 700, marginBottom: 10 }}>{poll.question}</div>
        <div className="stack">
          {poll.options.map((option) => (
            <div key={option.label}>
              <div className="row" style={{ justifyContent: 'space-between', fontSize: 13.5 }}>
                <span>{option.label}</span>
                <span className="muted">{Math.round((option.votes / poll.voters) * 100)}%</span>
              </div>
              <div className="progress" style={{ marginTop: 4 }}>
                <div
                  className="progress__bar"
                  style={{ width: `${Math.round((option.votes / max) * 100)}%`, background: 'var(--primary)' }}
                />
              </div>
            </div>
          ))}
        </div>
        <div className="small muted" style={{ marginTop: 10 }}>
          {formatCount(poll.voters)} votes
        </div>
      </div>
    </section>
  );
}
