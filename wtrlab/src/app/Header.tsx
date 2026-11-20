import { useState } from 'react';
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom';
import { isShelved, useLibrary } from '../data/library';

const NAV = [
  { to: '/library', label: 'Library', icon: 'bi-book' },
  { to: '/novels', label: 'Novels', icon: 'bi-bar-chart' },
  { to: '/ranking', label: 'Ranking', icon: 'bi-list' },
  { to: '/leaderboard', label: 'Leaderboard', icon: 'bi-trophy' },
];

const DRAWER_LINKS = [
  { to: '/', label: 'Home', icon: 'bi-house' },
  { to: '/library', label: 'Library', icon: 'bi-book' },
  { to: '/novels', label: 'Novels', icon: 'bi-collection' },
  { to: '/novel-finder', label: 'Novel Finder', icon: 'bi-search' },
  { to: '/ranking/daily', label: 'Ranking', icon: 'bi-list' },
  { to: '/leaderboard', label: 'Leaderboard', icon: 'bi-trophy' },
];

function Brand() {
  return (
    <Link to="/" className="brand" aria-label="WTR-LAB home">
      <svg viewBox="0 0 26 30" aria-hidden="true">
        <path
          d="M13 1c2.6 3.1 8.4 5.6 8.4 12.2C21.4 21.4 17.6 29 13 29S4.6 21.4 4.6 13.2C4.6 6.6 10.4 4.1 13 1Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
        />
        <path d="M13 9.4c1.5 2 3.6 3.6 3.6 6.6 0 2.6-1.6 4.6-3.6 4.6s-3.6-2-3.6-4.6c0-3 2.1-4.6 3.6-6.6Z" fill="currentColor" />
      </svg>
      <span>WTR-LAB</span>
    </Link>
  );
}

export default function Header() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [query, setQuery] = useState('');
  const navigate = useNavigate();
  const location = useLocation();
  const library = useLibrary();
  const shelvedCount = Object.values(library.entries).filter(isShelved).length;

  const submitSearch = (event: React.FormEvent) => {
    event.preventDefault();
    if (!query.trim()) return;
    navigate(`/search?q=${encodeURIComponent(query.trim())}`);
    setDrawerOpen(false);
  };

  const isActive = (to: string) =>
    to === '/ranking' ? location.pathname.startsWith('/ranking') : location.pathname.startsWith(to);

  return (
    <>
      <header className="site-header">
        <div className="container site-header__inner">
          <button className="burger" type="button" aria-label="Open menu" onClick={() => setDrawerOpen(true)}>
            <i className="bi bi-list" />
          </button>
          <Brand />

          <form className="header-search" onSubmit={submitSearch} role="search">
            <i className="bi bi-search" />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search..."
              aria-label="Search novels"
            />
          </form>

          <nav className="header-nav" aria-label="Primary">
            {NAV.map((item) => (
              <NavLink key={item.to} to={item.to} className={() => (isActive(item.to) ? 'is-active' : undefined)}>
                <i className={`bi ${item.icon}`} />
                <span>{item.label}</span>
                {item.to === '/library' && shelvedCount > 0 ? (
                  <span className="chip chip--muted" style={{ height: 20, fontSize: 12 }}>
                    {shelvedCount}
                  </span>
                ) : null}
              </NavLink>
            ))}
          </nav>

          <div className="header-actions">
            <button className="icon-btn" type="button" aria-label="Log in">
              <i className="bi bi-box-arrow-in-right" />
            </button>
            <button className="icon-btn" type="button" aria-label="Profile">
              <i className="bi bi-person" />
            </button>
          </div>
        </div>
      </header>

      {drawerOpen ? (
        <>
          <button className="drawer-scrim" type="button" aria-label="Close menu" onClick={() => setDrawerOpen(false)} />
          <aside className="drawer" aria-label="Menu">
            <div className="drawer__head">
              <strong>Menu</strong>
              <button className="btn btn--ghost btn--sm" type="button" onClick={() => setDrawerOpen(false)}>
                <i className="bi bi-x-lg" />
              </button>
            </div>
            <div className="drawer__section">
              <form onSubmit={submitSearch} className="search-wrap">
                <i className="bi bi-search" />
                <input
                  className="input input--search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search novels..."
                  aria-label="Search novels"
                />
              </form>
            </div>
            <div className="drawer__section">
              <div className="drawer__label">Browse</div>
              {DRAWER_LINKS.map((link) => (
                <Link key={link.to} to={link.to} className="drawer__link" onClick={() => setDrawerOpen(false)}>
                  <i className={`bi ${link.icon}`} />
                  {link.label}
                </Link>
              ))}
            </div>
            <div className="drawer__section">
              <div className="drawer__label">Community</div>
              <a className="drawer__link" href="#community">
                <i className="bi bi-discord" />
                Discord
              </a>
              <a className="drawer__link" href="#support">
                <i className="bi bi-heart" />
                Patreon
              </a>
            </div>
          </aside>
        </>
      ) : null}
    </>
  );
}
