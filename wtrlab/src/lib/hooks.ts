import { useCallback, useEffect, useState } from 'react';

export function useLocalStorageState<T>(key: string, initial: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = window.localStorage.getItem(key);
      return raw ? (JSON.parse(raw) as T) : initial;
    } catch {
      return initial;
    }
  });

  const update = useCallback(
    (next: T) => {
      setValue(next);
      try {
        window.localStorage.setItem(key, JSON.stringify(next));
      } catch {
        /* ignore quota/permission errors */
      }
    },
    [key],
  );

  return [value, update];
}

export function useScrollTop(dependency: unknown): void {
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'auto' });
  }, [dependency]);
}
