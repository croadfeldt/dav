  // Early-init theme: avoid FOUC by setting data-theme/data-mode on <html>
  // before any styles or body render. Resolves "auto" to current system preference.
  (function() {
    try {
      var t = localStorage.getItem('davTheme') || 'amber';
      var m = localStorage.getItem('davMode')  || 'auto';
      var resolved = m === 'auto'
        ? (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')
        : m;
      document.documentElement.setAttribute('data-theme', t);
      document.documentElement.setAttribute('data-mode', resolved);
    } catch(e) { /* localStorage blocked — stay with defaults */ }
  })();