import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import Cover from '../components/Cover';
import LibraryModal from '../components/LibraryModal';
import { getNovel } from '../data/catalog';
import {
  createFolder,
  deleteEntry,
  folderCount,
  isShelved,
  moveToTrash,
  restoreEntry,
  useLibrary,
} from '../data/library';
import type { LibraryEntry, Novel } from '../domain/types';
import { formatCount, timeAgo } from '../lib/format';

const TABS = [
  { key: 'library', label: 'Library' },
  { key: 'updates', label: 'Updates' },
  { key: 'history', label: 'History' },
  { key: 'followed', label: 'Followed Folders' },
] as const;

type TabKey = (typeof TABS)[number]['key'];

interface Row {
  entry: LibraryEntry;
  novel: Novel;
}

function LibraryRow({
  row,
  menuOpen,
  onMenu,
  onManage,
}: {
  row: Row;
  menuOpen: boolean;
  onMenu: () => void;
  onManage: () => void;
}) {
  const { entry, novel } = row;
  const chapter = entry.progress?.chapter ?? 0;
  const percent = entry.progress?.percent ?? 0;
  const trashed = entry.folders.includes(5);

  return (
    <div className="lib-card">
      <Link className="lib-card__cover" to={`/novel/${novel.id}/${novel.slug}`}>
        <Cover novel={novel} />
      </Link>
      <div style={{ minWidth: 0 }}>
        <Link to={`/novel/${novel.id}/${novel.slug}`} className="lib-card__title">
          {novel.title}
        </Link>
        <div className="lib-card__meta">
          <span className="chip chip--muted" style={{ height: 22, fontSize: 12 }}>
            {novel.status}
          </span>
          <span>{formatCount(novel.chapterCount)} chapters</span>
          <span>{formatCount(novel.views)} views</span>
        </div>
        <div className="lib-card__progress">
          <div className="progress">
            <div className="progress__bar" style={{ width: `${percent}%` }} />
          </div>
          <span>
            {chapter > 0 ? `Chapter ${chapter} of ${novel.chapterCount} · ${percent}%` : 'Not started'}
            {entry.progress ? ` · ${timeAgo(entry.progress.updatedAt)}` : ''}
          </span>
        </div>
      </div>
      <div className="row" style={{ gap: 6 }}>
        <Link className="btn btn--sm btn--primary" to={`/novel/${novel.id}/${novel.slug}/chapter-${chapter || 1}`}>
          Continue
        </Link>
        <div className="menu-wrap">
          <button className="btn btn--sm" type="button" aria-label="More actions" onClick={onMenu}>
            <i className="bi bi-three-dots-vertical" />
          </button>
          {menuOpen ? (
            <div className="menu">
              <button type="button" onClick={onManage}>
                <i className="bi bi-folder" />
                Change folders
              </button>
              {trashed ? (
                <button type="button" onClick={() => restoreEntry(novel.id)}>
                  <i className="bi bi-arrow-counterclockwise" />
                  Restore
                </button>
              ) : (
                <button type="button" onClick={() => moveToTrash(novel.id)}>
                  <i className="bi bi-trash" />
                  Move to trash
                </button>
              )}
              <div className="menu__sep" />
              <button type="button" onClick={() => deleteEntry(novel.id)}>
                <i className="bi bi-x-circle" />
                Remove from library
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default function LibraryPage() {
  const library = useLibrary();
  const [tab, setTab] = useState<TabKey>('library');
  const [folderId, setFolderId] = useState(1);
  const [menuFor, setMenuFor] = useState<number | null>(null);
  const [manage, setManage] = useState<Novel | null>(null);
  const [creating, setCreating] = useState(false);
  const [newFolder, setNewFolder] = useState('');

  const rows = useMemo<Row[]>(
    () =>
      Object.values(library.entries)
        .map((entry) => {
          const novel = getNovel(entry.novelId);
          return novel ? { entry, novel } : null;
        })
        .filter((row): row is Row => row !== null),
    [library.entries],
  );

  const shelved = rows.filter((row) => isShelved(row.entry));
  const folderRows = rows.filter((row) => row.entry.folders.includes(folderId));
  const updateRows = [...shelved]
    .map((row) => ({ row, unread: row.novel.chapterCount - (row.entry.progress?.chapter ?? 0) }))
    .filter((item) => item.unread > 0)
    .sort((a, b) => b.unread - a.unread);
  const historyRows = library.history
    .map((item) => {
      const novel = getNovel(item.novelId);
      return novel ? { item, novel } : null;
    })
    .filter((item): item is { item: (typeof library.history)[number]; novel: Novel } => item !== null);

  const addFolder = () => {
    const name = newFolder.trim();
    if (!name) return;
    const id = createFolder(name);
    setFolderId(id);
    setNewFolder('');
    setCreating(false);
  };

  return (
    <div className="container">
      <div className="page-head">
        <div>
          <h1>My Library</h1>
          <p>{shelved.length} novels shelved · progress tracked on this device</p>
        </div>
        <Link className="btn" to="/novels">
          <i className="bi bi-plus-lg" />
          Browse novels
        </Link>
      </div>

      <div className="panel" style={{ marginBottom: 12 }}>
        <div style={{ padding: 10 }}>
          <div className="segmented segmented--wide">
            {TABS.map((item) => (
              <button
                key={item.key}
                type="button"
                className={tab === item.key ? 'is-active' : undefined}
                onClick={() => setTab(item.key)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="library-layout">
        <aside className="panel folder-nav">
          <div className="drawer__label" style={{ padding: '4px 10px 6px' }}>
            Folders
          </div>
          {library.folders.map((folder) => (
            <button
              key={folder.id}
              type="button"
              className={`folder-nav__item ${folder.id === folderId ? 'is-active' : ''}`}
              onClick={() => {
                setFolderId(folder.id);
                setTab('library');
              }}
            >
              <i
                className={`bi ${
                  folder.id === 1
                    ? 'bi-book'
                    : folder.id === 2
                      ? 'bi-bookmark'
                      : folder.id === 3
                        ? 'bi-check2-circle'
                        : folder.id === 5
                          ? 'bi-trash'
                          : 'bi-folder'
                }`}
              />
              {folder.name}
              <span className="folder-nav__count">{folderCount(library, folder.id)}</span>
            </button>
          ))}
          {creating ? (
            <div className="row" style={{ padding: 8 }}>
              <input
                className="input"
                autoFocus
                placeholder="Folder name"
                value={newFolder}
                onChange={(event) => setNewFolder(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') addFolder();
                  if (event.key === 'Escape') setCreating(false);
                }}
              />
              <button className="btn btn--sm" type="button" onClick={addFolder}>
                Add
              </button>
            </div>
          ) : (
            <button className="folder-nav__item" type="button" onClick={() => setCreating(true)}>
              <i className="bi bi-plus-lg" />
              New folder
            </button>
          )}
        </aside>

        <section className="panel">
          <header className="panel__head">
            <h2>
              {tab === 'library'
                ? library.folders.find((folder) => folder.id === folderId)?.name ?? 'Library'
                : TABS.find((item) => item.key === tab)?.label}
            </h2>
            <span className="small muted">
              {tab === 'library' ? `${folderRows.length} novels` : tab === 'updates' ? `${updateRows.length} with new chapters` : ''}
            </span>
          </header>

          {tab === 'library' ? (
            folderRows.length ? (
              folderRows.map((row) => (
                <LibraryRow
                  key={row.novel.id}
                  row={row}
                  menuOpen={menuFor === row.novel.id}
                  onMenu={() => setMenuFor(menuFor === row.novel.id ? null : row.novel.id)}
                  onManage={() => {
                    setMenuFor(null);
                    setManage(row.novel);
                  }}
                />
              ))
            ) : (
              <div className="empty">
                <i className="bi bi-book" />
                Nothing in this folder yet.
                <div style={{ marginTop: 12 }}>
                  <Link className="btn" to="/novels">
                    Find something to read
                  </Link>
                </div>
              </div>
            )
          ) : null}

          {tab === 'updates' ? (
            updateRows.length ? (
              updateRows.map(({ row, unread }) => (
                <div key={row.novel.id} className="update-row">
                  <span className="update-row__thumb">
                    <Cover novel={row.novel} />
                  </span>
                  <div style={{ minWidth: 0 }}>
                    <Link to={`/novel/${row.novel.id}/${row.novel.slug}`} className="update-row__novel">
                      {row.novel.title}
                    </Link>
                    <div className="update-row__chapter">
                      {unread} new chapter{unread === 1 ? '' : 's'} · last read chapter{' '}
                      {row.entry.progress?.chapter ?? 0}
                    </div>
                  </div>
                  <Link
                    className="btn btn--sm btn--primary"
                    to={`/novel/${row.novel.id}/${row.novel.slug}/chapter-${(row.entry.progress?.chapter ?? 0) + 1}`}
                  >
                    Read
                  </Link>
                </div>
              ))
            ) : (
              <div className="empty">
                <i className="bi bi-inbox" />
                No unread chapters. Everything is caught up.
              </div>
            )
          ) : null}

          {tab === 'history' ? (
            historyRows.length ? (
              historyRows.map(({ item, novel }) => (
                <Link
                  key={`${item.novelId}-${item.chapter}-${item.at}`}
                  to={`/novel/${novel.id}/${novel.slug}/chapter-${item.chapter}`}
                  className="update-row"
                >
                  <span className="update-row__thumb">
                    <Cover novel={novel} />
                  </span>
                  <div style={{ minWidth: 0 }}>
                    <div className="update-row__novel">{novel.title}</div>
                    <div className="update-row__chapter">
                      Chapter {item.chapter} · {item.percent}% of novel
                    </div>
                  </div>
                  <span className="small muted">{timeAgo(item.at)}</span>
                </Link>
              ))
            ) : (
              <div className="empty">
                <i className="bi bi-clock-history" />
                Your reading history will appear here.
              </div>
            )
          ) : null}

          {tab === 'followed' ? (
            <div className="empty">
              <i className="bi bi-people" />
              You are not following any public folders yet.
            </div>
          ) : null}
        </section>
      </div>

      {manage ? <LibraryModal novel={manage} onClose={() => setManage(null)} /> : null}
    </div>
  );
}
