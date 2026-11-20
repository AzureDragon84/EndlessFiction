import { Link, useParams } from 'react-router-dom';
import Cover from '../components/Cover';
import { periodViews, rankedNovels } from '../data/catalog';
import { genreName } from '../data/taxonomy';
import type { RankingPeriod } from '../domain/types';
import { formatCount } from '../lib/format';
import Stars from '../components/Stars';

const TABS: { key: RankingPeriod; label: string }[] = [
  { key: 'daily', label: 'Daily' },
  { key: 'weekly', label: 'Weekly' },
  { key: 'monthly', label: 'Monthly' },
  { key: 'all_time', label: 'All Time' },
];

const PERIOD_LABEL: Record<RankingPeriod, string> = {
  daily: 'today',
  weekly: 'this week',
  monthly: 'this month',
  all_time: 'all time',
};

export default function RankingPage() {
  const { period } = useParams();
  const active = (TABS.find((tab) => tab.key === period)?.key ?? 'daily') as RankingPeriod;
  const novels = rankedNovels(active, 20);

  return (
    <div className="container">
      <div className="page-head">
        <div>
          <h1>Novel Ranking</h1>
          <p>Ranked by views {PERIOD_LABEL[active]}.</p>
        </div>
        <Link className="btn" to="/leaderboard">
          <i className="bi bi-trophy" />
          Leaderboard
        </Link>
      </div>

      <div className="panel" style={{ marginBottom: 12 }}>
        <div style={{ padding: 10 }}>
          <div className="segmented segmented--wide">
            {TABS.map((tab) => (
              <Link
                key={tab.key}
                to={`/ranking/${tab.key}`}
                className={tab.key === active ? 'is-active' : undefined}
                style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 36, fontWeight: 600 }}
              >
                {tab.label}
              </Link>
            ))}
          </div>
        </div>
      </div>

      <section className="panel rank-table">
        {novels.map((novel, index) => (
          <Link
            key={novel.id}
            to={`/novel/${novel.id}/${novel.slug}`}
            className="rank-table__row"
            style={{ color: 'inherit' }}
          >
            <span className={`rank-table__num ${index < 3 ? 'rank-table__num--top' : ''}`}>{index + 1}</span>
            <span className="rank-table__cover">
              <Cover novel={novel} />
            </span>
            <div style={{ minWidth: 0 }}>
              <div className="rank-table__title">{novel.title}</div>
              <div className="rank-table__sub">
                {novel.genres.slice(0, 3).map((genre) => (
                  <span key={genre} className="chip chip--muted" style={{ height: 20, fontSize: 11.5 }}>
                    {genreName(genre)}
                  </span>
                ))}
                <span className="small muted">{formatCount(novel.readers)} readers</span>
              </div>
            </div>
            <span className="rank-table__stat">
              <i className="bi bi-eye" />
              {formatCount(periodViews(novel, active))}
            </span>
            <span className="rank-table__stat">{novel.chapterCount} ch</span>
            <span className="rank-table__stat">
              <Stars rating={novel.rating} />
            </span>
          </Link>
        ))}
      </section>
    </div>
  );
}
