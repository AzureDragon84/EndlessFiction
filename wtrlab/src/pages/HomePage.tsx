import { useState } from 'react';
import { Link } from 'react-router-dom';
import { RankingWidget, CommunityTiles, PollsWidget } from '../components/Widgets';
import NovelRail from '../components/NovelRail';
import Cover from '../components/Cover';
import { latestUpdates, newNovels, rankedNovels, recommendedNovels, trendingNovels } from '../data/catalog';
import { isShelved, useLibrary } from '../data/library';
import type { Novel } from '../domain/types';
import { formatCount, timeAgo, formatShortDate } from '../lib/format';

const CHOICE_TABS = ['Overall', 'Comedic Undertone', 'Hard-Working Protagonist', 'Harem-seeking', 'Revenge', 'Ninja World'];

function ReadersChoice() {
  const [tab, setTab] = useState(0);
  const [sort, setSort] = useState<'points' | 'bayesian'>('points');
  const novels = rankedNovels('weekly', 24);
  const ordered =
    sort === 'points'
      ? novels.slice(0, 6)
      : [...novels].sort((a, b) => b.rating * Math.log(b.votes + 2) - a.rating * Math.log(a.votes + 2)).slice(0, 6);

  return (
    <section className="panel">
      <header className="panel__head">
        <h2>Reader&apos;s Choice: {new Date().toLocaleString('en-US', { month: 'long' })}</h2>
        <div className="row">
          <div className="segmented" style={{ width: 190 }}>
            <button
              type="button"
              className={sort === 'points' ? 'is-active' : undefined}
              onClick={() => setSort('points')}
            >
              Points
            </button>
            <button
              type="button"
              className={sort === 'bayesian' ? 'is-active' : undefined}
              onClick={() => setSort('bayesian')}
            >
              Bayesian
            </button>
          </div>
          <Link className="btn btn--sm" to="/ranking/weekly">
            See More
          </Link>
        </div>
      </header>
      <div style={{ padding: '0 14px' }}>
        <div className="line-tabs">
          {CHOICE_TABS.map((label, index) => (
            <button
              key={label}
              type="button"
              className={index === tab ? 'is-active' : undefined}
              onClick={() => setTab(index)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="choice-row">
        {ordered.map((novel, index) => (
          <Link key={novel.id} to={`/novel/${novel.id}/${novel.slug}`} className="choice-item">
            <span className="choice-item__num">#{index + 1}</span>
            <span className="choice-item__thumb">
              <Cover novel={novel} />
            </span>
            <span className="choice-item__text">
              <span className="choice-item__title">{novel.title}</span>
              <span className="choice-item__meta">
                <i className="bi bi-star-fill" style={{ color: 'var(--star)', marginRight: 4 }} />
                {novel.rating.toFixed(1)} · {formatCount(novel.views)} views
              </span>
            </span>
          </Link>
        ))}
      </div>
    </section>
  );
}

function LatestUpdates() {
  const updates = latestUpdates(6);
  return (
    <section className="panel">
      <header className="panel__head">
        <h2>Latest Updates</h2>
        <Link className="btn btn--sm" to="/novels">
          See More
        </Link>
      </header>
      <div>
        {updates.map(({ novel, chapter }) => (
          <Link
            key={`${novel.id}-${chapter.number}`}
            to={`/novel/${novel.id}/${novel.slug}/chapter-${chapter.number}`}
            className="update-row"
          >
            <span className="update-row__thumb">
              <Cover novel={novel} />
            </span>
            <div style={{ minWidth: 0 }}>
              <div className="update-row__novel">{novel.title}</div>
              <div className="update-row__chapter">
                #{chapter.number} {chapter.title}
              </div>
            </div>
            <span className="small muted">{formatShortDate(chapter.publishedAt)}</span>
          </Link>
        ))}
      </div>
    </section>
  );
}

function FeatureTiles() {
  return (
    <div className="home-actions">
      <Link to="/novels" className="feature-tile feature-tile--tall feature-tile--vote">
        <i className="bi bi-box-arrow-in-down" />
        <span>Vote Novels</span>
      </Link>
      <Link to="/novel-finder" className="feature-tile feature-tile--tall feature-tile--request">
        <i className="bi bi-plus-circle-dotted" />
        <span>Request Novels</span>
      </Link>
    </div>
  );
}

function SignedOutBar() {
  return (
    <div className="signed-out">
      <i className="bi bi-person-circle" />
      <span>
        <Link to="/library">Login</Link> to keep track of where you left off in the novel.
      </span>
    </div>
  );
}

export default function HomePage() {
  const library = useLibrary();
  const hasLibrary = Object.values(library.entries).some(isShelved);
  const newList: Novel[] = newNovels(8);

  return (
    <div className="container">
      <div className="with-rail">
        <div className="main-col">
          {hasLibrary ? null : <SignedOutBar />}
          <NovelRail title="New Novels" href="/novels" novels={newList} />
          <FeatureTiles />
          <ReadersChoice />
          <NovelRail title="Trending" href="/ranking/daily" novels={trendingNovels(8)} showAdded={false} />
          <LatestUpdates />
          <NovelRail title="Recommendations" href="/novels" novels={recommendedNovels(11, 8)} showAdded={false} />
        </div>
        <aside className="rail">
          <RankingWidget />
          <CommunityTiles />
          <PollsWidget />
          <div className="panel">
            <div className="panel__body small muted">
              <strong style={{ color: 'var(--heading)' }}>WTR-LAB</strong>
              <p style={{ marginTop: 6 }}>
                A community-driven reading platform for machine-translated novels. Demo catalogue, last updated{' '}
                {timeAgo(newList[0]?.addedAt ?? new Date().toISOString())}.
              </p>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
