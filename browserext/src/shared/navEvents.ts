/** Pure navigation-event types + the NavController forwarding interface the
 *  thin navMonitor adapter (amend §3.2) delivers into. NO chrome, NO DOM, NO
 *  fetch — only types + the one isMainFrame filter helper, so both the
 *  background tsconfig (navMonitor, controller, chrome) and the content
 *  tsconfig see them. The Controller owns all requestId/documentId/job/time/
 *  phase correlation under its mutex (amend §2.1–§2.2); navMonitor only
 *  scope-filters (main frame + bound tab) and forwards raw events + outcome.
 *
 *  NOTE: webNavigation.onCommitted does NOT carry requestId — commit correlation
 *  is by bound-tab + main-frame + job + time-window, NOT requestId (amend §2.2).
 *  CommittedEvent therefore has no requestId field. */

import type { NavOutcome, PageDetection } from './state.js';

/** Lock-free scope snapshot the thin navMonitor reads to filter bound-tab
 *  events without taking the Controller mutex (the Controller may be mid-tick). */
export interface NavScope {
  boundTabId: number | null;
}

export interface BeforeRequestEvent {
  tabId: number;
  frameId: number;
  type: string;        // webRequest resource type, e.g. 'main_frame'
  url: string;
  requestId: string;
  timeStamp: number;
}

export interface BeforeRedirectEvent {
  tabId: number;
  frameId: number;
  type: string;
  url: string;           // pre-redirect URL
  redirectUrl: string;  // target of the redirect (same-crawl-site gate target)
  requestId: string;
  timeStamp: number;
}

export interface CommittedEvent {
  tabId: number;
  frameId: number;
  documentId: string;
  url: string;
  timeStamp: number;
  // NO requestId — webNavigation.onCommitted does not carry it (amend §2.2).
}

export interface NavCompletedEvent {
  tabId: number;
  url: string;
  statusCode: number;
  frameId: number;
  requestId: string;
  documentId?: string;
  timeStamp: number;
}

export interface NavErrorEvent {
  tabId: number;
  url: string;
  error: string;
  frameId: number;
  requestId: string;
  timeStamp: number;
}

export interface PageReadyEvent {
  documentId: string;
  url: string;
  detection: PageDetection;
  timeStamp: number;
}

/** Canonical classifier input (status.ts/classifyNavigation consumes this). */
export type NavInput =
  | { kind: 'completed'; statusCode: number }
  | { kind: 'error'; error: string };

/** The forwarding target navMonitor calls after scope-filtering. Each method
 *  re-acquires the Controller mutex internally; navMonitor must NOT hold any
 *  lock when calling these (it has none). */
export interface NavController {
  getNavScope(): NavScope;
  deliverBeforeRequest(e: BeforeRequestEvent): Promise<void>;
  deliverBeforeRedirect(e: BeforeRedirectEvent): Promise<void>;
  deliverCommitted(e: CommittedEvent): Promise<void>;
  deliverHttpEvent(e: NavCompletedEvent, outcome: NavOutcome): Promise<void>;
  deliverError(e: NavErrorEvent, outcome: NavOutcome): Promise<void>;
  deliverPageReady(e: PageReadyEvent): Promise<void>;
}

/** Main-frame filter: frameId === 0 for both webNavigation and main-frame
 *  webRequest events. Sub-frame errors/commits are noise (spec §3.4, §1.4). */
export function isMainFrame(e: { frameId?: number }): boolean {
  return e.frameId === 0;
}
