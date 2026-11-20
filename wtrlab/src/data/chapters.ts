import type { ChapterMeta, Novel } from '../domain/types';

const CHAPTER_PHRASES = [
  'The Flag That Should Not Exist',
  'A Very Public Awakening',
  'The Elder Who Would Not Sit Down',
  'Nine Trials, One Answer',
  'The Invitation',
  'What the River Carried',
  'A Debt Written in Stone',
  'The Quiet Half of the Sect',
  'Two Clauses and a Witness',
  'The Night Market',
  'A Blade Returned',
  'The Second Bell',
  'Everything the Sky Heard',
  'A Cousin From the Capital',
  'The Cost of Being Noticed',
  'Small Miracles, Bad Timing',
  'The Long Way Around the Mountain',
  'What the Elders Decided',
  'A Door Left Open',
  'The First Winter',
  'An Unreasonable Request',
  'The Letter Without a Name',
  'Before the Audience',
  'A Promise Made in Passing',
  'The Weight of a Title',
  'Someone Else\'s Legend',
  'The Last Quiet Day',
  'The Test',
  'A Debt Repaid Badly',
  'The Thing in the Fog',
];

const MODELS = ['gemini-3.1-flash-lite', 'gemini-3.5-flash-lite', 'gemini-2.5-flash'];

const BODY_BANK = [
  'Morning came late over the valley, and the mist refused to lift before the third bell.',
  'He had learned, in the last few weeks, that silence in this place was never empty. It was only waiting.',
  'The courtyard smelled of wet stone and cold iron, the way it always did after a night of rain.',
  'Nobody spoke. In his experience, that was usually the moment before something expensive happened.',
  '"You are early," the old man said, without turning around. "That is either discipline or panic. I have not decided which."',
  'A line of light crossed the floor and stopped at his feet, as though the room itself had taken an interest.',
  'The letter was short, the seal was familiar, and the request inside it was the kind that ended careers.',
  'He counted the exits out of habit. There were two, and one of them was currently standing in the doorway looking displeased.',
  'The technique was simple in the way that a mountain is simple: easy to describe, impossible to move.',
  'Something small and bright drifted past the window, too slow to be a bird and too quiet to be a weapon.',
  'By the time the sun cleared the ridge, the whole compound knew, which meant the whole province would know by evening.',
  '"If you do this," she said, "you will not be able to undo it." He nodded. That had been true of every decision worth making.',
  'The hall had been built for a thousand people and held nine. The emptiness did most of the talking.',
  'He let the silence stretch until it became impolite, then a little longer than that.',
  'There was a rule about the inner courtyard that everyone quoted and no one could source.',
  'The first exchange took less time than the introductions.',
  'Afterwards, he would remember the sound more than the blow: a clean, ringing note, like a bell struck once and never again.',
  'The steward produced a ledger the way other men produced weapons, which was to say: with both hands and visible reluctance.',
  'They walked until the lamps behind them were only a rumour of light through the trees.',
  'It was not fear, exactly. Fear was something you could use. This was the quieter thing that arrived after fear had finished its work.',
  'The answer, when it came, arrived in the least convenient form available to it.',
  '"You misunderstand me," he said. "I am not asking permission."',
  'Somewhere below the terrace, water moved through the dark without any interest in the conversation above it.',
  'The days that followed had the shape of ordinary days, which was how he knew to be careful.',
  'It rained for three days, and on the fourth the road was gone and the visitors arrived anyway.',
];

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const chapterCache = new Map<number, ChapterMeta[]>();
const bodyCache = new Map<string, string[]>();

function buildChapters(novel: Novel): ChapterMeta[] {
  const random = mulberry32(novel.id);
  // One day per chapter, most recent chapter published a few hours ago.
  const newest = Date.now() - 4 * 60 * 60 * 1000;
  const chapters: ChapterMeta[] = [];
  for (let n = 1; n <= novel.chapterCount; n += 1) {
    const title = CHAPTER_PHRASES[(n * 7 + Math.floor(random() * 3)) % CHAPTER_PHRASES.length] ?? 'Untitled';
    const model = MODELS[Math.floor(random() * MODELS.length)] ?? MODELS[0];
    const publishedAt = new Date(newest - (novel.chapterCount - n) * 86_400_000).toISOString();
    chapters.push({ number: n, title, model, publishedAt });
  }
  return chapters;
}

export function chaptersOf(novel: Novel): ChapterMeta[] {
  const cached = chapterCache.get(novel.id);
  if (cached) return cached;
  const built = buildChapters(novel);
  chapterCache.set(novel.id, built);
  return built;
}

export function chapterMeta(novel: Novel, number: number): ChapterMeta | undefined {
  return chaptersOf(novel)[number - 1];
}

/** Deterministic placeholder prose for the reader; a few hand-written paragraphs open each chapter. */
export function chapterBody(novel: Novel, number: number): string[] {
  const key = `${novel.id}:${number}`;
  const cached = bodyCache.get(key);
  if (cached) return cached;

  const meta = chapterMeta(novel, number);
  const random = mulberry32(novel.id * 31 + number);
  const paragraphs: string[] = [];
  paragraphs.push(
    `The morning after the audience, Li Qing woke with the particular clarity of a man who had been promised nothing and given less.`,
  );
  paragraphs.push(meta ? `"${meta.title}." He turned the phrase over once. It did not improve.` : '');
  const count = 9 + Math.floor(random() * 4);
  for (let i = 0; i < count; i += 1) {
    const pick = Math.floor(random() * BODY_BANK.length);
    paragraphs.push(BODY_BANK[(pick + number) % BODY_BANK.length] ?? BODY_BANK[0]);
    if (i === 3 || i === 7) {
      paragraphs.push(
        i === 3
          ? '"You are going to have to explain that again," she said, "slowly, and without the part where you are clever."'
          : 'By the time the lamps were lit, three people had agreed to something and none of them had said it out loud.',
      );
    }
  }
  paragraphs.push(
    'He did not sleep that night. Instead he sat with the window open and listened to the compound settle, one building at a time, until there was nothing left to hear but the water.',
  );

  const cleaned = paragraphs.filter(Boolean);
  bodyCache.set(key, cleaned);
  return cleaned;
}
