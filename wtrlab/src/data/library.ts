import { useSyncExternalStore } from 'react';
import type { LibraryEntry, LibraryFolder, LibraryState } from '../domain/types';

const STORAGE_KEY = 'wtrlab.library.v1';

const DEFAULT_FOLDERS: LibraryFolder[] = [
  { id: 1, name: 'Reading', builtin: true },
  { id: 2, name: 'Read Later', builtin: true },
  { id: 3, name: 'Completed', builtin: true },
  { id: 5, name: 'Trash', builtin: true },
];

const EMPTY: LibraryState = { folders: DEFAULT_FOLDERS, entries: {}, history: [] };

function read(): LibraryState {
  if (typeof window === 'undefined') return EMPTY;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return EMPTY;
    const parsed = JSON.parse(raw) as Partial<LibraryState>;
    return {
      folders: parsed.folders?.length ? parsed.folders : DEFAULT_FOLDERS,
      entries: parsed.entries ?? {},
      history: parsed.history ?? [],
    };
  } catch {
    return EMPTY;
  }
}

let state: LibraryState = read();
const listeners = new Set<() => void>();

function commit(next: LibraryState) {
  state = next;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* storage may be unavailable; the session still works */
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function snapshot(): LibraryState {
  return state;
}

export function useLibrary(): LibraryState {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

export function useLibraryEntry(novelId: number): LibraryEntry | undefined {
  const library = useLibrary();
  return library.entries[novelId];
}

export function isShelved(entry: LibraryEntry | undefined): boolean {
  return Boolean(entry && entry.folders.some((folder) => folder !== 5));
}

export function folderCount(library: LibraryState, folderId: number): number {
  return Object.values(library.entries).filter((entry) => entry.folders.includes(folderId)).length;
}

export function toggleFolder(novelId: number, folderId: number): void {
  const entry = state.entries[novelId];
  if (!entry) {
    commit({
      ...state,
      entries: {
        ...state.entries,
        [novelId]: { novelId, folders: [folderId], addedAt: new Date().toISOString() },
      },
    });
    return;
  }
  const has = entry.folders.includes(folderId);
  const folders = has ? entry.folders.filter((id) => id !== folderId) : [...entry.folders, folderId];
  const entries = { ...state.entries };
  if (folders.length === 0) {
    delete entries[novelId];
  } else {
    entries[novelId] = { ...entry, folders };
  }
  commit({ ...state, entries });
}

export function moveToTrash(novelId: number): void {
  const entry = state.entries[novelId];
  if (!entry) return;
  commit({
    ...state,
    entries: { ...state.entries, [novelId]: { ...entry, folders: [5] } },
  });
}

export function restoreEntry(novelId: number): void {
  const entry = state.entries[novelId];
  if (!entry) return;
  const folders = entry.folders.filter((id) => id !== 5);
  commit({
    ...state,
    entries: { ...state.entries, [novelId]: { ...entry, folders: folders.length ? folders : [1] } },
  });
}

export function deleteEntry(novelId: number): void {
  const entries = { ...state.entries };
  delete entries[novelId];
  commit({ ...state, entries });
}

export function createFolder(name: string): number {
  const id = Math.max(5, ...state.folders.map((folder) => folder.id)) + 1;
  commit({ ...state, folders: [...state.folders, { id, name, builtin: false }] });
  return id;
}

export function setProgress(novelId: number, chapter: number, percent: number): void {
  const entry = state.entries[novelId];
  const now = new Date().toISOString();
  const history = [
    { novelId, chapter, percent, at: now },
    ...state.history.filter((item) => !(item.novelId === novelId && item.chapter === chapter)),
  ].slice(0, 40);

  if (!entry) {
    commit({
      ...state,
      history,
      entries: {
        ...state.entries,
        [novelId]: {
          novelId,
          folders: [1],
          addedAt: now,
          progress: { chapter, percent, updatedAt: now },
        },
      },
    });
    return;
  }

  commit({
    ...state,
    history,
    entries: {
      ...state.entries,
      [novelId]: { ...entry, progress: { chapter, percent, updatedAt: now } },
    },
  });
}
