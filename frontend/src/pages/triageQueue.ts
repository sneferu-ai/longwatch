// The triage queue's pure pagination logic, kept out of the component so the
// merge and the page-offset arithmetic are directly testable (and pinned by a
// Node check that runs beside the CANON leg).
//
// The queue is the server's ranked list, paginated by `limit`/`offset` over a
// deterministic total order (score DESC, source_id, start_s, pattern_slug).
// The client holds a live head page plus a tail loaded on demand. The tail is
// keyed by row identity, never by a stored offset: every fetch merges by
// identity, and the cursor is derived from what is actually held, so a
// re-score that shrinks, grows or re-ranks the head can never make the next
// fetch start past rows that were never served.

export interface QueueEntry {
  kind: 'candidate' | 'ml_failed';
  candidate_id?: string;
  window_id: string;
  source_id: string;
  source_name: string;
  clip_id?: string | null;
  start_s: number;
  end_s: number;
  pattern_slug?: string;
  pattern_version?: number;
  score?: number;
  sentence?: string;
  state?: string;
  synthetic?: boolean;
  demo?: boolean;
  reason?: string;
  label?: string;
}

/** One row per candidate (or per failed window), whichever side served it. */
export function entryKey(entry: QueueEntry): string {
  return `${entry.kind}-${entry.candidate_id ?? entry.window_id}`;
}

/**
 * The server offsets a Load-more click must fetch, merged by identity.
 *
 * - the frontier, `heldCandidates` = the count of distinct candidates already
 *   held: the page that continues exactly where the loaded set ends;
 * - the heal page, `headCandidates` = the live head boundary: the region a
 *   re-score pushes rows into, so rows that fell out of the head — or that a
 *   re-rank inserted below it — are fetched rather than skipped.
 *
 * When nothing is loaded beyond the head the two coincide and one page is
 * fetched. A cursor is never a stored offset, so head drift cannot skip rows.
 */
export function tailPageOffsets(headCandidates: number, heldCandidates: number): number[] {
  const boundary = Math.max(0, headCandidates);
  // The frontier is never behind the live head boundary: a page below it would
  // only re-serve rows the head already holds.
  const frontier = Math.max(boundary, heldCandidates);
  return frontier === boundary ? [boundary] : [boundary, frontier];
}

/** The distinct candidates the operator is actually holding (head ∪ tail). */
export function countCandidates(entries: QueueEntry[]): number {
  const seen = new Set<string>();
  for (const entry of entries) {
    if (entry.kind === 'candidate') seen.add(entryKey(entry));
  }
  return seen.size;
}

/**
 * Append the freshly fetched candidates to the tail, dropping anything the
 * head already serves and anything already held (the merge is by identity, so
 * overlapping pages and re-serves are absorbed, never duplicated).
 */
export function mergeTail(
  headEntries: QueueEntry[],
  currentExtra: QueueEntry[],
  fresh: QueueEntry[],
  filter: string,
): QueueEntry[] {
  const seen = new Set([...headEntries, ...currentExtra].map(entryKey));
  const appended = fresh.filter(
    (entry) =>
      entry.kind === 'candidate' &&
      !seen.has(entryKey(entry)) &&
      (filter === 'all' || entry.state === filter),
  );
  return appended.length === 0 ? currentExtra : [...currentExtra, ...appended];
}

/** The merged view: the live head, then the tail in first-seen order. */
export function mergeEntries(
  headEntries: QueueEntry[],
  extra: QueueEntry[],
  filter: string,
): QueueEntry[] {
  if (extra.length === 0) return headEntries;
  const seen = new Set(headEntries.map(entryKey));
  const tail = extra.filter(
    (entry) => !seen.has(entryKey(entry)) && (filter === 'all' || entry.state === filter),
  );
  return tail.length === 0 ? headEntries : [...headEntries, ...tail];
}
