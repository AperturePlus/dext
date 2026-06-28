// SPIKE (slice-0): validates that a Tampermonkey userscript can read a value
// an extension content script writes to page localStorage. Throwaway — not
// part of the production build; lives on the spike branch only. See
// docs/superpowers/plans/2026-06-28-browserext-slice0-spike.md and spec §5.1.
//
// A content script shares the page's localStorage (and DOM) across its
// isolated world. This probe exploits that to publish an "alive" timestamp
// every 2s — which the reader userscript (a different world) must be able to
// see for the Phase 2 fallback model to hold.
(function () {
  const KEY = 'ycl_ext_alive_v1';
  const INTERVAL_MS = 2000;

  function writeAlive() {
    try {
      localStorage.setItem(KEY, String(Date.now()));
    } catch {
      // ignore quota / access errors — spike is best-effort
    }
  }

  writeAlive();
  setInterval(writeAlive, INTERVAL_MS);
})();
