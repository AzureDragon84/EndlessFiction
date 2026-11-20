import { LEADERBOARD } from '../data/catalog';
import { formatCompact } from '../lib/format';
import { initials } from '../lib/format';

export default function LeaderboardPage() {
  return (
    <div className="container">
      <div className="page-head">
        <div>
          <h1>Leaderboard</h1>
          <p>Top readers by chapters read and points earned this season.</p>
        </div>
      </div>

      <section className="panel">
        <div className="leader-row" style={{ fontWeight: 800, color: 'var(--muted)', fontSize: 13 }}>
          <span>#</span>
          <span />
          <span>Reader</span>
          <span className="leader-row__stat">Novels</span>
          <span className="leader-row__stat">Chapters</span>
          <span className="leader-row__stat">Points</span>
        </div>
        {LEADERBOARD.map((entry) => (
          <div key={entry.user} className="leader-row">
            <span className="leader-row__num">{entry.rank}</span>
            <span className="avatar">{initials(entry.user)}</span>
            <span className="leader-row__name">{entry.user}</span>
            <span className="leader-row__stat">{formatCompact(entry.novelsRead)}</span>
            <span className="leader-row__stat">{formatCompact(entry.chapters)}</span>
            <span className="leader-row__stat">{formatCompact(entry.points)}</span>
          </div>
        ))}
      </section>
    </div>
  );
}
