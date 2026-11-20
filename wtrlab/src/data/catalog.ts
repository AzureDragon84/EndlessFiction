import type {
  CatalogFilters,
  ChapterMeta,
  LeaderEntry,
  LibraryState,
  Novel,
  Poll,
  RankingPeriod,
  Review,
  Thread,
} from '../domain/types';
import { chapterMeta, chaptersOf } from './chapters';
import { NOVELS, novelById } from './novels';

export const allNovels = (): Novel[] => NOVELS;

export function getNovel(idOrSlug: string | number): Novel | undefined {
  const id = typeof idOrSlug === 'number' ? idOrSlug : Number(idOrSlug);
  if (!Number.isNaN(id)) {
    const byId = novelById.get(id);
    if (byId) return byId;
  }
  return NOVELS.find((novel) => novel.slug === String(idOrSlug));
}

export function newNovels(limit = 8): Novel[] {
  return [...NOVELS].sort((a, b) => Date.parse(b.addedAt) - Date.parse(a.addedAt)).slice(0, limit);
}

export function trendingNovels(limit = 8): Novel[] {
  return [...NOVELS].sort((a, b) => b.views - a.views).slice(0, limit);
}

export function recommendedNovels(seed = 7, limit = 8): Novel[] {
  const list = [...NOVELS];
  let state = seed;
  for (let i = list.length - 1; i > 0; i -= 1) {
    state = (state * 1103515245 + 12345) % 2147483648;
    const j = state % (i + 1);
    const a = list[i];
    const b = list[j];
    if (a && b) {
      list[i] = b;
      list[j] = a;
    }
  }
  return list.slice(0, limit);
}

export function byGenre(genre: string, limit?: number): Novel[] {
  const matches = NOVELS.filter((novel) => novel.genres.includes(genre));
  return typeof limit === 'number' ? matches.slice(0, limit) : matches;
}

const PERIOD_WEIGHT: Record<RankingPeriod, number> = {
  daily: 0.021,
  weekly: 0.084,
  monthly: 0.31,
  all_time: 1,
};

export function periodViews(novel: Novel, period: RankingPeriod): number {
  return Math.round(novel.views * PERIOD_WEIGHT[period]);
}

export function rankedNovels(period: RankingPeriod, limit = 20): Novel[] {
  return [...NOVELS].sort((a, b) => periodViews(b, period) - periodViews(a, period)).slice(0, limit);
}

export function latestUpdates(limit = 6): { novel: Novel; chapter: ChapterMeta }[] {
  const rows: { novel: Novel; chapter: ChapterMeta }[] = [];
  for (const novel of [...NOVELS].sort((a, b) => Date.parse(b.addedAt) - Date.parse(a.addedAt)).slice(0, limit)) {
    const chapters = chaptersOf(novel);
    const chapter = chapters[chapters.length - 1];
    if (chapter) rows.push({ novel, chapter });
  }
  return rows;
}

export function searchNovels(query: string, limit = 24): Novel[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return NOVELS.filter(
    (novel) =>
      novel.title.toLowerCase().includes(q) ||
      novel.altTitles.some((alt) => alt.toLowerCase().includes(q)) ||
      novel.author.toLowerCase().includes(q) ||
      novel.genres.some((genre) => genre.includes(q)),
  ).slice(0, limit);
}

function matchesGenreMode(novel: Novel, filters: CatalogFilters): boolean {
  const include = filters.genres ?? [];
  const exclude = filters.excludedGenres ?? [];
  const mode = filters.genreMode ?? 'or';
  if (include.length > 0) {
    const hits = include.filter((genre) => novel.genres.includes(genre));
    if (mode === 'and' ? hits.length !== include.length : hits.length === 0) return false;
  }
  return exclude.every((genre) => !novel.genres.includes(genre));
}

export function filterNovels(filters: CatalogFilters): Novel[] {
  const query = filters.query?.trim().toLowerCase() ?? '';
  const filtered = NOVELS.filter((novel) => {
    if (query) {
      const inTitle = novel.title.toLowerCase().includes(query);
      const inSummary = novel.summary.toLowerCase().includes(query);
      if (filters.descriptionOnly ? !inSummary : !(inTitle || inSummary)) return false;
    }
    if (filters.genre && !novel.genres.includes(filters.genre)) return false;
    if (filters.status && filters.status !== 'All' && novel.status !== filters.status) return false;
    if (filters.minChapters && novel.chapterCount < filters.minChapters) return false;
    if (filters.minRating && novel.rating < filters.minRating) return false;
    if (filters.minReviews && novel.reviewCount < filters.minReviews) return false;
    return matchesGenreMode(novel, filters);
  });

  const orderBy = filters.orderBy ?? 'addition';
  const direction = filters.direction ?? 'desc';
  const sorted = [...filtered].sort((a, b) => {
    switch (orderBy) {
      case 'name':
        return a.title.localeCompare(b.title);
      case 'views':
        return b.views - a.views;
      case 'reader':
        return b.readers - a.readers;
      case 'chapter':
        return b.chapterCount - a.chapterCount;
      case 'rating':
        return b.rating - a.rating;
      case 'reviews':
        return b.reviewCount - a.reviewCount;
      case 'votes':
        return b.votes - a.votes;
      case 'update':
      case 'addition':
      default:
        return Date.parse(b.addedAt) - Date.parse(a.addedAt);
    }
  });
  return direction === 'asc' ? sorted.reverse() : sorted;
}

const REVIEW_USERS = [
  'nightowl_88',
  'DaoSeeker',
  'lin_wei',
  'PaperLantern',
  'kuro_no_kaze',
  'ThousandLi',
  'softboyreads',
  'MiraV',
  'wanderingcrane',
  'fiveelements',
];

const REVIEW_BODIES = [
  'The premise is doing a lot of heavy lifting and the prose mostly keeps up. Chapters are short, which I appreciate more than I expected.',
  'Read to chapter 40 in one sitting. The pacing sags around the sect arc but the dialogue carries it.',
  'Machine translation artefacts are visible but not distracting. Names stay consistent, which is the main thing.',
  'I keep waiting for the protagonist to make a decision that is not immediately reversed two paragraphs later.',
  'Genuinely funny in places. The eavesdropping gimmick is a one-joke engine and somehow it still runs.',
  'Good bones, uneven execution. I would rather read this than most of the genre, which is an endorsement.',
  'Dropped at chapter 60. The power curve flattened out and the antagonists stopped being interesting.',
  'The worldbuilding is the reason to read this. Every chapter adds a rule that matters later.',
  'Rating it for the translation quality more than the story: fast updates, clean formatting, no missing chapters.',
  'If you liked the original run of this kind of story, this is a comfortable version of it with better pacing.',
];

const reviewCache = new Map<number, Review[]>();

export function reviewsOf(novel: Novel): Review[] {
  const cached = reviewCache.get(novel.id);
  if (cached) return cached;
  const count = Math.max(3, Math.min(6, Math.round(novel.reviewCount / 3) || 3));
  const reviews: Review[] = Array.from({ length: count }, (_, index) => {
    const user = REVIEW_USERS[(novel.id + index * 3) % REVIEW_USERS.length] ?? 'reader';
    const body = REVIEW_BODIES[(novel.id + index * 5) % REVIEW_BODIES.length] ?? REVIEW_BODIES[0];
    const rating = Math.max(0.5, Math.min(5, Math.round((novel.rating + (index % 3) - 1) * 2) / 2));
    return {
      id: `${novel.id}-r${index}`,
      novelId: novel.id,
      user,
      rating,
      date: new Date(Date.now() - (index + 1) * 3 * 86_400_000).toISOString(),
      body,
      likes: Math.max(0, 14 - index * 3 + (novel.id % 7)),
      replies: Math.max(0, 5 - index),
    };
  });
  reviewCache.set(novel.id, reviews);
  return reviews;
}

const THREAD_TITLES = [
  'Chapter 61 discussion — that reveal was not earned',
  'Translation quality check: is the new model better?',
  'Raw source vs this version, which should I start with?',
  'Character relationships chart (spoilers up to ch. 40)',
  'Does the pacing improve after the first arc?',
  'Appreciation post for the side characters',
];

export function threadsOf(novel: Novel): Thread[] {
  return Array.from({ length: 4 }, (_, index) => ({
    id: `${novel.id}-t${index}`,
    novelId: novel.id,
    user: REVIEW_USERS[(novel.id + index * 2) % REVIEW_USERS.length] ?? 'reader',
    title: THREAD_TITLES[(novel.id + index) % THREAD_TITLES.length] ?? THREAD_TITLES[0],
    replies: 3 + ((novel.id + index * 7) % 24),
    date: new Date(Date.now() - (index + 1) * 2 * 86_400_000).toISOString(),
  }));
}

export const POLLS: Poll[] = [
  {
    id: 'p1',
    question: 'Which weekly release are you waiting on most?',
    options: [
      { label: 'Soul Land: Numb', votes: 402 },
      { label: 'Ninja World: Ancient Clan', votes: 318 },
      { label: 'Jade Dynasty: Two of Five', votes: 204 },
      { label: 'Battle Through the Heavens', votes: 188 },
    ],
    voters: 1_112,
  },
  {
    id: 'p2',
    question: 'Do you read the Web, Web+ or AI version of a chapter?',
    options: [
      { label: 'AI translation', votes: 812 },
      { label: 'Web+ cleaned', votes: 402 },
      { label: 'Web raw MTL', votes: 214 },
      { label: 'Depends on the novel', votes: 338 },
    ],
    voters: 1_766,
  },
  {
    id: 'p3',
    question: 'What pacing do you prefer for a translation drop?',
    options: [
      { label: 'Daily single chapter', votes: 611 },
      { label: 'Weekly batch', votes: 402 },
      { label: 'Full arc at once', votes: 512 },
    ],
    voters: 1_525,
  },
];

export const LEADERBOARD: LeaderEntry[] = [
  { rank: 1, user: 'lin_wei', novelsRead: 412, chapters: 18_402, points: 92_118 },
  { rank: 2, user: 'DaoSeeker', novelsRead: 388, chapters: 16_884, points: 84_002 },
  { rank: 3, user: 'PaperLantern', novelsRead: 361, chapters: 15_118, points: 78_440 },
  { rank: 4, user: 'nightowl_88', novelsRead: 344, chapters: 14_002, points: 71_880 },
  { rank: 5, user: 'kuro_no_kaze', novelsRead: 302, chapters: 12_884, points: 66_118 },
  { rank: 6, user: 'ThousandLi', novelsRead: 288, chapters: 11_402, points: 60_882 },
  { rank: 7, user: 'wanderingcrane', novelsRead: 254, chapters: 10_118, points: 54_006 },
  { rank: 8, user: 'MiraV', novelsRead: 233, chapters: 9_884, points: 48_772 },
  { rank: 9, user: 'fiveelements', novelsRead: 210, chapters: 8_402, points: 41_118 },
  { rank: 10, user: 'softboyreads', novelsRead: 188, chapters: 7_884, points: 36_004 },
];

export function progressPercent(novel: Novel, chapter: number): number {
  if (novel.chapterCount <= 0) return 0;
  return Math.round((chapter / novel.chapterCount) * 100);
}

export function chapterOrFirst(novel: Novel, chapter: number): ChapterMeta | undefined {
  return chapterMeta(novel, chapter) ?? chaptersOf(novel)[0];
}

export type { LibraryState };
