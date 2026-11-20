import { Outlet } from 'react-router-dom';
import Header from './Header';
import Footer from './Footer';

export default function Layout() {
  return (
    <div className="app-shell">
      <Header />
      <main className="page">
        <Outlet />
      </main>
      <Footer />
    </div>
  );
}
