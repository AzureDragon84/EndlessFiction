import { useState } from 'react';
import type { Novel } from '../domain/types';
import { createFolder, folderCount, toggleFolder, useLibrary } from '../data/library';

interface LibraryModalProps {
  novel: Novel;
  onClose: () => void;
}

export default function LibraryModal({ novel, onClose }: LibraryModalProps) {
  const library = useLibrary();
  const entry = library.entries[novel.id];
  const [newFolder, setNewFolder] = useState('');

  const addFolder = () => {
    const name = newFolder.trim();
    if (!name) return;
    const id = createFolder(name);
    toggleFolder(novel.id, id);
    setNewFolder('');
  };

  return (
    <div className="modal-scrim" role="dialog" aria-modal="true" aria-label="Add to library">
      <div className="modal">
        <header className="modal__head">
          <h3>Add to Library</h3>
          <button className="btn btn--ghost btn--sm" type="button" onClick={onClose} aria-label="Close">
            <i className="bi bi-x-lg" />
          </button>
        </header>
        <div className="modal__body">
          <p className="small muted" style={{ marginBottom: 8 }}>
            {novel.title}
          </p>
          {library.folders
            .filter((folder) => folder.id !== 5)
            .map((folder) => (
              <label key={folder.id} className="folder-option">
                <input
                  type="checkbox"
                  checked={entry?.folders.includes(folder.id) ?? false}
                  onChange={() => toggleFolder(novel.id, folder.id)}
                  style={{ accentColor: 'var(--primary)', width: 16, height: 16 }}
                />
                <span style={{ fontWeight: 600 }}>{folder.name}</span>
                <span className="folder-option__count">{folderCount(library, folder.id)}</span>
              </label>
            ))}

          <div className="row" style={{ marginTop: 14 }}>
            <input
              className="input"
              placeholder="New folder name"
              value={newFolder}
              onChange={(event) => setNewFolder(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') addFolder();
              }}
            />
            <button className="btn" type="button" onClick={addFolder}>
              <i className="bi bi-plus-lg" />
              Add
            </button>
          </div>
        </div>
        <footer className="modal__foot">
          <button className="btn btn--filled" type="button" onClick={onClose}>
            Done
          </button>
        </footer>
      </div>
    </div>
  );
}
