export type NovelStatus = 'Ongoing' | 'Completed' | 'Hiatus' | 'Dropped';

export type RankingPeriod = 'daily' | 'weekly' | 'monthly' | 'all_time';

export interface TagGroup {
  name: string;
  tags: string[];
}

export interface Novel {
  id: number;
  slug: string;
  title: string;
  altTitles: string[];
  author: string;
  status: NovelStatus;
  /** Optional artwork; novels without artwork fall back to a generated gradient cover. */
  cover?: string;
  /** Gradient stops used by the fallback cover. */
  gradient: [string, string];
  views: number;
  readers: number;
  chars: number;
  rating: number;
  votes: number;
  reviewCount: number;
  chapterCount: number;
  genres: string[];
  tags: string[];
  tagGroups: TagGroup[];
  addedAt: string;
  summary: string;
  /** Chip row shown above the tabs on the detail page. */
  audience: { male: number; female: number };
  unlocked: number;
  requestedBy?: string;
  releasedBy?: string;
  rankings?: Partial<Record<RankingPeriod, number>>;
}

export interface ChapterMeta {
  number: number;
  title: string;
  model: string;
  publishedAt: string;
}

export interface Review {
  id: string;
  novelId: number;
  user: string;
  rating: number;
  date: string;
  body: string;
  likes: number;
  replies: number;
}

export interface Thread {
  id: string;
  novelId: number;
  user: string;
  title: string;
  replies: number;
  date: string;
}

export interface Poll {
  id: string;
  question: string;
  options: { label: string; votes: number }[];
  voters: number;
}

export interface LeaderEntry {
  rank: number;
  user: string;
  novelsRead: number;
  chapters: number;
  points: number;
}

export interface LibraryFolder {
  id: number;
  name: string;
  builtin: boolean;
}

export interface ReadingProgress {
  chapter: number;
  percent: number;
  updatedAt: string;
}

export interface LibraryEntry {
  novelId: number;
  folders: number[];
  addedAt: string;
  progress?: ReadingProgress;
}

export interface HistoryItem {
  novelId: number;
  chapter: number;
  percent: number;
  at: string;
}

export interface LibraryState {
  folders: LibraryFolder[];
  entries: Record<number, LibraryEntry>;
  history: HistoryItem[];
}

export interface CatalogFilters {
  query?: string;
  descriptionOnly?: boolean;
  genre?: string;
  status?: string;
  minChapters?: number;
  minRating?: number;
  minReviews?: number;
  orderBy?: 'addition' | 'update' | 'name' | 'views' | 'reader' | 'chapter' | 'rating' | 'reviews' | 'votes';
  direction?: 'desc' | 'asc';
  genres?: string[];
  excludedGenres?: string[];
  genreMode?: 'and' | 'or';
}

export interface ReaderSettings {
  fontSize: number;
  font: 'sans' | 'serif';
  width: 'wide' | 'narrow';
  theme: 'light' | 'sepia' | 'dark';
}
