import { Link } from 'react-router-dom';

export default function Footer() {
  return (
    <footer className="site-footer">
      <div className="container site-footer__inner">
        <Link to="/" style={{ fontWeight: 700, color: 'var(--heading)' }}>
          WTR-LAB
        </Link>
        <span>Read translated novels and track your reading progress.</span>
        <span className="spacer" />
        <a href="#discord">Discord</a>
        <a href="#patreon">Patreon</a>
        <a href="#changelog">v1.13.17</a>
      </div>
    </footer>
  );
}
