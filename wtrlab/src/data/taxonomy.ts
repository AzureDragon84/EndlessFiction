export interface Genre {
  id: number;
  slug: string;
  name: string;
}

export const GENRES: Genre[] = [
  { id: 1, slug: 'action', name: 'Action' },
  { id: 2, slug: 'adult', name: 'Adult' },
  { id: 3, slug: 'adventure', name: 'Adventure' },
  { id: 4, slug: 'comedy', name: 'Comedy' },
  { id: 5, slug: 'drama', name: 'Drama' },
  { id: 6, slug: 'ecchi', name: 'Ecchi' },
  { id: 7, slug: 'erciyuan', name: 'Erciyuan' },
  { id: 8, slug: 'fan-fiction', name: 'Fan-fiction' },
  { id: 9, slug: 'fantasy', name: 'Fantasy' },
  { id: 10, slug: 'game', name: 'Game' },
  { id: 11, slug: 'gender-bender', name: 'Gender Bender' },
  { id: 12, slug: 'harem', name: 'Harem' },
  { id: 13, slug: 'historical', name: 'Historical' },
  { id: 14, slug: 'horror', name: 'Horror' },
  { id: 15, slug: 'josei', name: 'Josei' },
  { id: 16, slug: 'martial-arts', name: 'Martial Arts' },
  { id: 17, slug: 'mature', name: 'Mature' },
  { id: 18, slug: 'mecha', name: 'Mecha' },
  { id: 19, slug: 'military', name: 'Military' },
  { id: 20, slug: 'mystery', name: 'Mystery' },
  { id: 21, slug: 'psychological', name: 'Psychological' },
  { id: 22, slug: 'romance', name: 'Romance' },
  { id: 23, slug: 'school-life', name: 'School Life' },
  { id: 24, slug: 'sci-fi', name: 'Sci-Fi' },
  { id: 25, slug: 'seinen', name: 'Seinen' },
  { id: 26, slug: 'shoujo', name: 'Shoujo' },
  { id: 27, slug: 'shoujo-ai', name: 'Shoujo Ai' },
  { id: 28, slug: 'shounen', name: 'Shounen' },
  { id: 29, slug: 'shounen-ai', name: 'Shounen Ai' },
  { id: 30, slug: 'slice-of-life', name: 'Slice of Life' },
  { id: 31, slug: 'smut', name: 'Smut' },
  { id: 32, slug: 'sports', name: 'Sports' },
  { id: 33, slug: 'supernatural', name: 'Supernatural' },
  { id: 34, slug: 'tragedy', name: 'Tragedy' },
  { id: 35, slug: 'urban-life', name: 'Urban Life' },
  { id: 36, slug: 'wuxia', name: 'Wuxia' },
  { id: 37, slug: 'xianxia', name: 'Xianxia' },
  { id: 38, slug: 'xuanhuan', name: 'Xuanhuan' },
  { id: 39, slug: 'yaoi', name: 'Yaoi' },
  { id: 40, slug: 'yuri', name: 'Yuri' },
];

const genreById = new Map(GENRES.map((g) => [g.slug, g]));

export function genreName(slug: string): string {
  return genreById.get(slug)?.name ?? slug;
}

export function genreId(slug: string): number {
  return genreById.get(slug)?.id ?? 0;
}

export const TAG_GROUPS: { name: string; tags: string[] }[] = [
  {
    name: 'Protagonist Archetypes',
    tags: ['Male', 'Behind-the-Scenes Mastermind', 'Low-key', 'Mature', 'Transmigration', 'Transported to Another World', 'Weak to Strong', 'Anti-hero'],
  },
  {
    name: 'Power Systems',
    tags: ['Cheats', 'Cultivation', 'Martial Spirits', 'Special Abilities', 'Leveling System', 'Bloodlines'],
  },
  {
    name: 'Worldbuilding',
    tags: ['Fantasy World', 'Academy', 'Urban Setting', 'Ancient Era', 'Post-Apocalyptic'],
  },
  {
    name: 'Relationship Tropes',
    tags: ['Engagement', 'Older Love Interests', 'Childhood Friends', 'Rivals to Lovers', 'Contract Marriage'],
  },
  {
    name: 'Narrative',
    tags: ['Eavesdropping', 'Schemes And Conspiracies', 'Secrets', 'Reincarnation', 'Time Skip'],
  },
  {
    name: 'Beings & Factions',
    tags: ['Gods', 'Demons', 'Sects', 'Clans', 'Beast Companions'],
  },
  { name: 'Tone & Atmosphere', tags: ['Comedic Undertone', 'Dark', 'Heartwarming', 'Tense'] },
  { name: 'Adaptations', tags: ['Soul Land', 'Naruto', 'Battle Through the Heavens', 'Jade Dynasty'] },
  { name: 'Miscellaneous Narrative Elements', tags: ['Artifacts', 'System Interface', 'Virtual Reality', 'Guilds'] },
];

export const SORT_OPTIONS: { value: string; label: string }[] = [
  { value: 'addition', label: 'Addition Date' },
  { value: 'update', label: 'Update Date' },
  { value: 'views', label: 'Daily Views' },
  { value: 'reader', label: 'Reader' },
  { value: 'chapter', label: 'Chapter' },
  { value: 'name', label: 'Name' },
  { value: 'rating', label: 'Rating' },
  { value: 'reviews', label: 'Review Count' },
  { value: 'votes', label: 'Vote Count' },
];

export const STATUS_OPTIONS = ['Ongoing', 'Completed', 'Hiatus', 'Dropped'] as const;
