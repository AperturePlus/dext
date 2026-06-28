// ==UserScript==
// @name         dext spike — alive reader
// @namespace    https://github.com/AperturePlus/dext
// @version      0.0.1
// @description  SPIKE (slice-0): reads the extension content script's alive timestamp from localStorage and logs freshness. Throwaway.
// @match        *://*.edu.cn/*
// @match        *://*.github.io/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

// @grant none → runs in the page context, sharing the page's localStorage
// with the extension content script (a separate isolated world). If this
// reader can see the value the CS writes, the Phase 2 fallback channel is
// viable.
(function () {
  const KEY = 'ycl_ext_alive_v1';
  const TTL_MS = 6000;
  const POLL_MS = 1000;

  function read() {
    let raw = null;
    try {
      raw = localStorage.getItem(KEY);
    } catch {
      // ignore
    }
    if (raw === null) {
      console.log('[dext-spike] alive: <absent>');
      return;
    }
    const ts = Number(raw);
    const age = Number.isFinite(ts) ? Date.now() - ts : NaN;
    const fresh = Number.isFinite(age) && age < TTL_MS;
    console.log('[dext-spike] alive: ts=' + raw + ' ageMs=' + age + ' fresh=' + fresh);
  }

  read();
  setInterval(read, POLL_MS);
})();
