import { Link } from 'react-router-dom';

export default function NotFoundPage() {
  return (
    <div className="container">
      <div className="panel">
        <div className="empty" style={{ padding: '64px 20px' }}>
          <i className="bi bi-compass" />
          <h2 style={{ marginBottom: 8 }}>Page not found</h2>
          <p className="muted" style={{ marginBottom: 18 }}>
            The novel or chapter you were looking for is not in this catalogue.
          </p>
          <Link className="btn btn--filled" to="/">
            Back to home
          </Link>
        </div>
      </div>
    </div>
  );
}
