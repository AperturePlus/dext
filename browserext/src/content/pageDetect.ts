/** Pure page detection — a dependency-injected port of userscripts/src/utils.ts
 *  isErrorPage / terminalUnavailableReason (spec §4.1). Reads the same fields the
 *  userscript reads off `document`, but from an injected input struct so it is
 *  unit-testable in node without a DOM. The regexes and length thresholds are
 *  byte-aligned with the userscript; the empty_page guard mirrors the userscript's
 *  `document.body !== null` check via the `bodyNull` field. Slice 4's content
 *  script calls this on PAGE_READY (first detection) and again before CAPTURE_RESULT
 *  (second detection — amend §2.3). */

import type { PageDetection, TerminalUnavailableReason } from '../shared/state.js';

export interface PageDetectInput {
  title: string;
  bodyText: string;
  readyState: 'loading' | 'interactive' | 'complete';
  linksLength: number;
  bodyNull: boolean;        // true when document.body === null (userscript guards on this)
}

export const ERROR_PATTERNS = /502 bad gateway|503 service|504 gateway|500 internal|error occurred|server error|nginx/i;
export const NOT_FOUND_PATTERNS = /\b404\b|not found|page not found|页面不存在|网页不存在|未找到页面|找不到页面|访问的页面不存在|您访问的页面不存在|信息不存在|该信息不存在|文章不存在/i;
export const REMOVED_PATTERNS = /内容已撤销|内容被撤销|该内容已被删除|内容已被删除|文章已被删除|信息已被删除|该信息已删除|已下线|页面已下线|内容已失效/i;

/** Byte-aligned with userscript isErrorPage + terminalUnavailableReason. Returns
 *  { errorPage, terminalReason }. errorPage is true only for short (<1500 char)
 *  bodies matching an error keyword. terminalReason precedence mirrors the
 *  userscript: not_found → content_removed → empty_page → null. */
export function detectPage(input: PageDetectInput): PageDetection {
  const title = input.title || '';
  const bodyText = input.bodyText || '';

  const errorPage = bodyText.length < 1500 && ERROR_PATTERNS.test(`${title} ${bodyText}`);

  let terminalReason: TerminalUnavailableReason | null = null;
  const haystack = `${title} ${bodyText}`;
  if (bodyText.length < 4000 && NOT_FOUND_PATTERNS.test(haystack)) {
    terminalReason = 'not_found';
  } else if (bodyText.length < 4000 && REMOVED_PATTERNS.test(haystack)) {
    terminalReason = 'content_removed';
  } else if (
    input.readyState === 'complete'
    && input.bodyNull === false
    && bodyText.trim().length === 0
    && input.linksLength === 0
  ) {
    terminalReason = 'empty_page';
  }

  return { errorPage, terminalReason };
}
