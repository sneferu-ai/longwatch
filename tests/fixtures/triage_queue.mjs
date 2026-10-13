#!/usr/bin/env node
// The triage queue's pagination invariants, checked against the *shipped*
// frontend module (frontend/src/pages/triageQueue.ts) under Node — the same
// cross-language discipline as the CANON leg.  The bug this pins: a stored
// positional cursor (headCandidates + extraFetched) drifted when a re-score
// shrank, grew or re-ranked the head page, and rows that were never served
// could be skipped silently.
//
// Usage:  node --experimental-strip-types tests/fixtures/triage_queue.mjs
// Exit 0 = every invariant holds; exit 1 = a divergence, named on stderr.

const MODULE = new URL('../../frontend/src/pages/triageQueue.ts', import.meta.url).href;
const { countCandidates, entryKey, mergeEntries, mergeTail, tailPageOffsets } =
  await import(MODULE);

// With this set, the client pages with the *removed* positional cursor
// (headCandidates + extraFetched) so the harness can be proven to detect the
// exact defect the fix removed — a check that cannot fail proves nothing.
const LEGACY_CURSOR = process.env.TRIAGE_QUEUE_LEGACY_CURSOR === '1';

let failures = 0;
function check(name, condition, detail = '') {
  if (condition) {
    console.log(`  ok   ${name}`);
    return;
  }
  failures += 1;
  console.error(`  FAIL ${name}${detail ? ` — ${detail}` : ''}`);
}

const PAGE = 100;

function candidate(index) {
  return {
    kind: 'candidate',
    candidate_id: `cand-${index}`,
    window_id: `win-${index}`,
    source_id: 'src-1',
    source_name: 'evidence.mp4',
    start_s: index,
    end_s: index + 1,
    pattern_slug: 'stationary_animal_with_person',
    pattern_version: 1,
    score: 100 - (index % 100),
    state: 'open',
  };
}

/** The client as the component drives it: a live head page + a keyed tail. */
function client(ranking) {
  let extra = [];
  let extraFetched = 0; // only the legacy cursor counts this
  const state = {
    get head() {
      return ranking().slice(0, PAGE);
    },
    get extra() {
      return extra;
    },
    get entries() {
      return mergeEntries(this.head, extra, 'all');
    },
    get shown() {
      return countCandidates(this.entries);
    },
    get total() {
      return ranking().length;
    },
    /** One Load-more click against a server that pages the *current* ranking. */
    click() {
      const headCandidates = this.head.filter((e) => e.kind === 'candidate').length;
      const offsets = LEGACY_CURSOR
        ? [headCandidates + extraFetched]
        : tailPageOffsets(headCandidates, this.shown);
      const fresh = offsets.flatMap((offset) => ranking().slice(offset, offset + PAGE));
      extraFetched += fresh.filter((entry) => entry.kind === 'candidate').length;
      extra = mergeTail(this.head, extra, fresh, 'all');
      return offsets;
    },
  };
  return state;
}

function covered(client) {
  const held = new Set(client.entries.map(entryKey));
  return client.rankingAll.every((entry) => held.has(entryKey(entry)));
}

function runScenario(name, rankingFactory, mutate) {
  const ranking = rankingFactory();
  const c = client(() => ranking);
  c.rankingAll = ranking;
  let clicks = 0;
  while (c.shown < c.total && clicks < 20) {
    c.click();
    clicks += 1;
    if (clicks === 2 && mutate) mutate(ranking);
  }
  const keys = c.entries.map(entryKey);
  check(`${name}: every ranked row is reachable`,
        covered(c) || c.shown >= c.total,
        `held ${new Set(keys).size} of ${c.total} after ${clicks} clicks`);
  check(`${name}: the merged view holds no duplicate row`,
        new Set(keys).size === keys.length, `${keys.length} rows, ${new Set(keys).size} distinct`);
  check(`${name}: the head stays first and live`,
        c.entries.slice(0, c.head.length).every((entry, index) => entryKey(entry) === entryKey(c.head[index])));
}

console.log('triage queue pagination');

// --- pure arithmetic -------------------------------------------------------
check('nothing beyond the head → one page at the head boundary',
      JSON.stringify(tailPageOffsets(100, 100)) === '[100]');
check('a loaded tail → heal page + frontier page',
      JSON.stringify(tailPageOffsets(100, 200)) === '[100,200]');
check('a shrunken head → the heal page follows it down',
      JSON.stringify(tailPageOffsets(40, 200)) === '[40,200]');
check('a head at or past what is held → one page at the boundary',
      JSON.stringify(tailPageOffsets(120, 100)) === '[120]');

// --- merge identity --------------------------------------------------------
const head = [candidate(0), candidate(1)];
const tail = [candidate(1), candidate(2)];
check('countCandidates counts distinct rows only', countCandidates([...head, ...tail]) === 3);
check('mergeEntries keeps the head first and dedupes by identity',
      mergeEntries(head, tail, 'all').map(entryKey).join(',') ===
      [candidate(0), candidate(1), candidate(2)].map(entryKey).join(','));
check('mergeTail drops rows the head already serves',
      mergeTail(head, [], [candidate(1), candidate(3)], 'all').map(entryKey).join(',') ===
      [candidate(3)].map(entryKey).join(','));
check('mergeTail drops rows the tail already holds',
      mergeTail(head, [candidate(2)], [candidate(2)], 'all').length === 1);
check('mergeTail honours the state filter',
      mergeTail(head, [], [{ ...candidate(3), state: 'accepted' }], 'open').length === 0);

// --- scenarios (the reviewer's drift cases) --------------------------------
runScenario('stable ranking', () => Array.from({ length: 350 }, (_v, i) => candidate(i)));
runScenario('a re-score shrinks the head',
            () => Array.from({ length: 350 }, (_v, i) => candidate(i)),
            (ranking) => { ranking.splice(0, 60); });
runScenario('a re-score inserts rows above the loaded region',
            () => Array.from({ length: 350 }, (_v, i) => candidate(i)),
            (ranking) => { ranking.unshift(...Array.from({ length: 30 }, (_v, i) => candidate(1000 + i))); });
runScenario('an ml_ack grows the head by a candidate',
            () => Array.from({ length: 350 }, (_v, i) => candidate(i)),
            (ranking) => { ranking.splice(0, 0, candidate(2000)); });

if (failures) {
  console.error(`\n${failures} invariant(s) diverged`);
  process.exit(1);
}
console.log('\nall triage queue pagination invariants hold');
