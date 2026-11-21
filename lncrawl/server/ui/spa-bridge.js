/* The SPA is generated build output, so its reader and translator routes are
   handed over here instead of being edited in place. Loaded as a classic
   script in the served index.html, before the bundle runs. */
(() => {
  "use strict";

  function owned(url) {
    if (!url) return false;
    let next;
    try {
      next = new URL(url, location.href);
    } catch (_) {
      return false;
    }
    if (next.origin !== location.origin) return false;
    const path = next.pathname;
    return (
      path === "/read" ||
      path.startsWith("/read/") ||
      path === "/reader" ||
      path === "/translator" ||
      path.startsWith("/translator/")
    );
  }

  for (const name of ["pushState", "replaceState"]) {
    const original = history[name].bind(history);
    history[name] = (data, title, url) => {
      if (owned(url)) {
        location.assign(new URL(url, location.href).href);
        return undefined;
      }
      return original(data, title, url);
    };
  }
})();
