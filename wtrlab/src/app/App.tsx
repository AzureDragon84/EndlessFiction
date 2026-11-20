import { useEffect } from 'react';
import { Route, Routes, useLocation } from 'react-router-dom';
import Layout from './Layout';
import HomePage from '../pages/HomePage';
import NovelDetailPage from '../pages/NovelDetailPage';
import ReaderPage from '../pages/ReaderPage';
import LibraryPage from '../pages/LibraryPage';
import CatalogPage from '../pages/CatalogPage';
import FinderPage from '../pages/FinderPage';
import RankingPage from '../pages/RankingPage';
import LeaderboardPage from '../pages/LeaderboardPage';
import SearchPage from '../pages/SearchPage';
import NotFoundPage from '../pages/NotFoundPage';

function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [pathname]);
  return null;
}

export default function App() {
  return (
    <>
      <ScrollToTop />
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<HomePage />} />
          <Route path="/novels" element={<CatalogPage />} />
          <Route path="/novel-finder" element={<FinderPage />} />
          <Route path="/ranking" element={<RankingPage />} />
          <Route path="/ranking/:period" element={<RankingPage />} />
          <Route path="/leaderboard" element={<LeaderboardPage />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/novel/:id/:slug" element={<NovelDetailPage />} />
          <Route path="/novel/:id/:slug/:chapter" element={<ReaderPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </>
  );
}
