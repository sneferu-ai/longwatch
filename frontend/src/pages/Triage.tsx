// Triage — the workbench.  A ranked queue with detection-incomplete spans
// pinned above it, beside a synchronized player, the measured trace strip, the
// evidence table, and the decision record.  Keyboard-first; the key legend is
// printed on the panel (DESIGN § 1, pattern 1).

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, describeError, mediaUrl, post, UnauthorizedError } from '../api';
import { useOnce, usePoll } from '../hooks';
import { Btn, Chip, Empty, Legend, Modal, Notice, Panel, SkeletonRows, StateChip, useToast } from '../ui/kit';
import { IconAlert, IconCheck, IconFlag, IconHelp, IconPause, IconPlay, IconX } from '../ui/icons';
import { TraceStrip, OverviewStrip } from '../ui/TimelineStrip';
import { duration, humanKey, int, milli, timecode, unitFor, valueFor } from '../format';
import {
  countCandidates,
  entryKey,
  mergeEntries,
  mergeTail,
  tailPageOffsets,
  type QueueEntry,
} from './triageQueue';

const POLL_MS = 2000;
const QUEUE_PAGE = 100;

interface CandidateDetail {
  candidate_id: string;
  window_id: string;
  source_id: string;
  source_name: string;
  clip_id: string | null;
  start_s: number;
  end_s: number;
  pattern_slug: string;
  pattern_version: number;
  score: number;
  sentence: string;
  state: string;
  evidence: Record<string, unknown>;
  synthetic: boolean;
  demo: boolean;
  ml_state: string | null;
  detections_summary: Record<string, Record<string, number>> | null;
  decisions: Array<{
    id: string;
    action: string;
    note: string | null;
    reviewer: string;
    src_viewed: boolean;
    created_at: string;
  }>;
}

interface Timeline {
  candidates: Array<{ id: string; score: number; start_s: number; end_s: number; state: string }>;
  events: Array<{ kind: string; t_start: number | null; t_end: number | null; value_milli: number | null }>;
  windows: Array<{ id: string; idx: number; start_s: number; end_s: number; boundary_kind: string; ml_state: string | null }>;
}

interface SourceDetail {
  id: string;
  duration_s: number | null;
  storage_path: string;
  original_name: string;
  origin: string;
}

const KEYS: Array<[React.ReactNode, string]> = [
  ['j / k', 'next / previous (↑ ↓ also work)'],
  ['space', 'play / pause'],
  ['a', 'accept'],
  ['r', 'reject'],
  ['f', 'flag with note'],
  ['n', 'note'],
  ['v', 'source-resolution view'],
  ['x', 'acknowledge a detection failure'],
  ['?', 'this help'],
];

export function Triage({ onUnauthorized }: { onUnauthorized: () => void }) {
  const toast = useToast();
  const [filter, setFilter] = useState<string>('all');
  const queuePath =
    filter === 'all'
      ? `/api/v1/candidates?limit=${QUEUE_PAGE}`
      : `/api/v1/candidates?limit=${QUEUE_PAGE}&state=${filter}`;
  const queue = usePoll<{ total: number; entries: QueueEntry[] }>(queuePath, POLL_MS);

  // Pages past the head are loaded only on the operator's demand ("Load
  // more"): the head page polls so the top of the ranking is always live,
  // while the appended tail is a snapshot — a re-score can re-rank it, but the
  // list never re-orders under the cursor (DESIGN § 7). The server paginates
  // the candidate side by offset; its ml_failed pins ride every page, so the
  // merge drops them from tail pages and dedupes on the row identity.
  //
  // The tail is keyed by identity, never by a stored offset: the cursor for
  // the next page is the count of *distinct candidates already held*, so a
  // head that shrinks or absorbs rows (a re-score, an ml_ack) can never make
  // the next fetch start past rows that were never served.
  const [extra, setExtra] = useState<QueueEntry[]>([]);
  const [extraBusy, setExtraBusy] = useState(false);
  const [extraError, setExtraError] = useState<{ title: string; detail: string } | null>(null);

  const [selected, setSelected] = useState(0);
  const [note, setNote] = useState('');
  const [showNote, setShowNote] = useState(false);
  const [showSource, setShowSource] = useState(false);
  const [srcViewed, setSrcViewed] = useState(false);
  const [sourceError, setSourceError] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [message, setMessage] = useState<{ kind: 'ok' | 'err' | 'warn'; text: string } | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [playhead, setPlayhead] = useState<number | null>(null);
  const noteRef = useRef<HTMLTextAreaElement | null>(null);
  const proxyRef = useRef<HTMLVideoElement | null>(null);
  const sourceRef = useRef<HTMLVideoElement | null>(null);
  // The cursor is anchored to the selected row's identity: a poll that
  // inserts, drops or re-ranks rows above the selection must not silently
  // move the selection onto a different span.
  const selectedKey = useRef<string | null>(null);

  // A filter switch invalidates the tail: it was fetched under the old filter.
  useEffect(() => {
    setExtra([]);
    setExtraError(null);
  }, [filter]);

  const headEntries = queue.data?.entries ?? [];
  const entries = useMemo(() => mergeEntries(headEntries, extra, filter), [headEntries, extra, filter]);

  const headCandidates = useMemo(() => headEntries.filter((entry) => entry.kind === 'candidate').length, [headEntries]);
  const totalCandidates = (queue.data?.total ?? 0) - (headEntries.length - headCandidates);
  // The "shown" count is what the operator can actually see, post-merge, and
  // the cursor the next page continues from.
  const shownCandidates = useMemo(() => countCandidates(entries), [entries]);
  const hasMore = queue.data != null && shownCandidates < totalCandidates;

  const index = Math.min(selected, Math.max(0, entries.length - 1));
  const current = entries[index];
  // The explanation hook must exist as soon as the queue renders — including
  // when the first entry is a pinned detection-incomplete span — so the first
  // candidate carries it until a candidate is selected.
  const explanationIndex = useMemo(() => {
    if (entries[index]?.kind === 'candidate') return index;
    return entries.findIndex((entry) => entry.kind === 'candidate');
  }, [entries, index]);

  // Keep the cursor on its row. The anchor is adopted from the row under the
  // cursor the first time the queue renders, then followed across polls, tail
  // appends and rows falling out of the head; a row that leaves the queue
  // entirely hands the anchor to the row now under the cursor.
  useEffect(() => {
    if (entries.length === 0) return;
    const key = selectedKey.current;
    if (key === null) {
      selectedKey.current = entryKey(entries[index]);
      return;
    }
    const found = entries.findIndex((entry) => entryKey(entry) === key);
    if (found >= 0) {
      if (found !== selected) setSelected(found);
      return;
    }
    selectedKey.current = entries[index] ? entryKey(entries[index]) : null;
  }, [entries, index, selected]);

  // Keep the cursor visible when the operator navigates with j/k — and only
  // then. scrollIntoView moves the browser's sequential-focus starting point
  // to the row, so firing it on mount or on a poll tick silently stole the
  // first Tab stop from the header/nav and re-stole it mid-session every time
  // the queue length changed (SOUL ¶2: nothing moves under your hands).
  const scrolledTo = useRef<number>(index);
  useEffect(() => {
    if (scrolledTo.current === index) return;
    scrolledTo.current = index;
    const row = document.querySelector(`[data-testid="queue-entry-${index}"]`);
    row?.scrollIntoView({ block: 'nearest' });
  }, [index, entries.length]);

  const detailQuery = useOnce<CandidateDetail>(
    current?.kind === 'candidate' && current.candidate_id
      ? `/api/v1/candidates/${current.candidate_id}`
      : null,
  );
  // Never render one candidate's evidence under another's selection, not even
  // for the frame between a queue switch and the detail fetch landing.
  const detail =
    detailQuery.data && current?.kind === 'candidate' && detailQuery.data.candidate_id === current.candidate_id
      ? detailQuery.data
      : null;

  const sourceId = current?.source_id ?? null;
  const sourceQuery = useOnce<SourceDetail>(sourceId ? `/api/v1/sources/${sourceId}` : null);
  const timelineQuery = useOnce<Timeline>(sourceId ? `/api/v1/sources/${sourceId}/timeline` : null);

  const durationS = sourceQuery.data?.duration_s ?? null;
  // A response that is not the documented shape is treated as absent: the
  // workbench degrades to "no overview", it never blanks the page.
  const timeline =
    timelineQuery.data && Array.isArray(timelineQuery.data.windows) && Array.isArray(timelineQuery.data.candidates)
      ? timelineQuery.data
      : null;
  const windowStart = current?.start_s ?? 0;
  const windowEnd = current?.end_s ?? 0;
  const pad = Math.max(5, (windowEnd - windowStart) * 0.35);
  const stripFrom = Math.max(0, Math.floor(windowStart - pad));
  const stripTo = durationS ? Math.min(Math.ceil(durationS), Math.ceil(windowEnd + pad)) : Math.ceil(windowEnd + pad);
  const signalsQuery = useOnce<{ from: number; to: number | null; series: Record<string, number[]> }>(
    sourceId ? `/api/v1/sources/${sourceId}/signals?from=${stripFrom}&to=${stripTo}` : null,
  );

  useEffect(() => {
    setShowSource(false);
    setSrcViewed(false);
    setSourceError(false);
    setShowNote(false);
    setPlayhead(null);
  }, [current?.candidate_id, current?.window_id]);

  const selectRow = useCallback(
    (next: number) => {
      const clamped = Math.max(0, Math.min(entries.length - 1, next));
      selectedKey.current = entries[clamped] ? entryKey(entries[clamped]) : null;
      setSelected(clamped);
    },
    [entries],
  );

  const move = useCallback(
    (delta: number) => {
      selectRow(index + delta);
    },
    [selectRow, index],
  );

  const loadMore = useCallback(async () => {
    if (extraBusy || !hasMore) return;
    setExtraBusy(true);
    setExtraError(null);
    // Two pages, merged by identity:
    //   * the frontier at the count of distinct candidates held — where the
    //     loaded set actually ends, so a head that shrank (a re-score) or
    //     absorbed rows (an ml_ack) cannot shift the cursor past unserved
    //     rows; and
    //   * the heal page at the live head boundary — the region a re-score
    //     pushes rows into, so a re-rank that inserted rows above the loaded
    //     region cannot leave a row the client will never fetch.
    // Overlap between the pages (and with everything already held) is absorbed
    // by the merge dedupe.
    const offsets = tailPageOffsets(headCandidates, shownCandidates);
    try {
      const pages = await Promise.all(
        offsets.map((offset) => api<{ entries: QueueEntry[] }>(`${queuePath}&offset=${offset}`)),
      );
      const fresh = pages.flatMap((payload) => payload.entries);
      setExtra((currentExtra) => mergeTail(headEntries, currentExtra, fresh, filter));
    } catch (err) {
      if (err instanceof UnauthorizedError) onUnauthorized();
      else setExtraError(describeError(err));
    } finally {
      setExtraBusy(false);
    }
  }, [extraBusy, hasMore, headCandidates, shownCandidates, queuePath, headEntries, filter, onUnauthorized]);

  const handleError = useCallback(
    (err: unknown) => {
      if (err instanceof UnauthorizedError) {
        onUnauthorized();
        return;
      }
      const described = describeError(err);
      const text = described.term
        ? `${described.title}: the term “${described.term}” is not permitted — ${described.detail}`
        : `${described.title}. ${described.detail}`;
      setMessage({ kind: 'err', text });
      toast.push({ kind: 'err', text: described.title, detail: described.detail });
    },
    [onUnauthorized, toast],
  );

  const decide = useCallback(
    async (action: 'accept' | 'reject' | 'flag') => {
      if (!current) {
        setMessage({ kind: 'warn', text: 'nothing is selected — pick an entry from the ranked queue first' });
        return;
      }
      if (current.kind !== 'candidate' || !current.candidate_id) {
        // Never silently drop an operator action (DESIGN § 6): say what this
        // entry is and which verb applies to it.
        setMessage({
          kind: 'warn',
          text: 'this entry is a detection-incomplete span, not a scored candidate — '
            + 'accept / reject / flag apply to candidates; press x to acknowledge it',
        });
        return;
      }
      if (pending) {
        // Never silently drop an operator action: say why it did not land.
        const label =
          pending === 'ack' ? 'acknowledgement' : pending === 'accept' ? 'accept' : pending === 'reject' ? 'reject' : 'flag';
        setMessage({
          kind: 'warn',
          text: `${pending === 'ack' ? 'an' : 'a'} ${label} is still being recorded — this one was not sent`,
        });
        return;
      }
      setPending(action);
      try {
        const result = await post<{ ledger_seq: number; src_viewed: boolean; state: string }>(
          `/api/v1/candidates/${current.candidate_id}/decisions`,
          // A blank draft is never stored as an empty-string note; a typed note
          // rides whichever decision the operator records (the field says so).
          { action, note: note.trim() ? note.trim() : null, src_viewed: srcViewed },
        );
        // The decision is durable the moment the POST returns; clear the
        // in-flight marker before anything else so a fast operator is never
        // told an action is running when it has already landed.
        setPending(null);
        setMessage({
          kind: 'ok',
          text: `${action} recorded · ledger seq ${result.ledger_seq} · src_viewed ${String(result.src_viewed)}`,
        });
        setNote('');
        setShowNote(false);
        // The tail is a snapshot, not a poll target: patch the acted-on row in
        // place so its state chip reflects the decision the operator just made
        // (the head page refreshes itself on the poll).
        setExtra((currentExtra) =>
          currentExtra.map((entry) =>
            entry.candidate_id === current.candidate_id ? { ...entry, state: result.state } : entry,
          ),
        );
        void queue.reload();
        void detailQuery.reload();
      } catch (err) {
        handleError(err);
      } finally {
        setPending(null);
      }
    },
    [current, note, srcViewed, pending, queue, detailQuery, handleError],
  );

  const acknowledge = useCallback(async () => {
    if (!current) {
      setMessage({ kind: 'warn', text: 'nothing is selected — pick an entry from the ranked queue first' });
      return;
    }
    if (current.kind !== 'ml_failed') {
      // Never silently drop an operator action (DESIGN § 6).
      setMessage({
        kind: 'warn',
        text: 'this entry is a scored candidate, not a detection-incomplete span — '
          + 'x acknowledges spans the detector could not look at',
      });
      return;
    }
    if (pending) return;
    setPending('ack');
    try {
      await post(`/api/v1/windows/${current.window_id}/ml_ack`);
      setMessage({ kind: 'ok', text: 'detection failure acknowledged · ledger event ML_FAILURE_ACKED written' });
      await queue.reload();
    } catch (err) {
      handleError(err);
    } finally {
      setPending(null);
    }
  }, [current, pending, queue, handleError]);

  const flagFlow = useCallback(async () => {
    // A note belongs to a candidate decision; on any other entry the press
    // must say why nothing happened instead of opening an invisible field.
    if (!current || current.kind !== 'candidate') {
      await decide('flag');
      return;
    }
    if (!showNote) {
      setShowNote(true);
      window.setTimeout(() => noteRef.current?.focus(), 0);
      return;
    }
    await decide('flag');
  }, [current, showNote, decide]);

  const toggleSource = useCallback(() => {
    if (!current || current.kind !== 'candidate') {
      // The pane streams a scored candidate's source; claiming it was opened
      // for any other entry would record a src_viewed that never happened.
      setMessage({
        kind: 'warn',
        text: 'the source-resolution pane opens on a scored candidate — this entry has no source span to stream',
      });
      return;
    }
    setShowSource((value) => !value);
    setSrcViewed(true);
  }, [current]);

  const togglePlay = useCallback(() => {
    const player = proxyRef.current;
    if (!player) return;
    if (player.paused) void player.play();
    else player.pause();
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
      if (event.key === 'Escape') {
        setHelpOpen(false);
        setShowNote(false);
        return;
      }
      if (typing || event.metaKey || event.ctrlKey || event.altKey) return;
      if (helpOpen) return; // the dialog owns the keyboard while it is open
      // A focused player owns the arrow keys natively (seek and volume); the
      // queue still answers to j/k while the player holds focus.
      if (
        target?.tagName === 'VIDEO' &&
        (event.key === 'ArrowUp' || event.key === 'ArrowDown' || event.key === 'ArrowLeft' || event.key === 'ArrowRight')
      ) {
        return;
      }
      // A focused button, link or video element owns Space natively: without
      // this guard, Space on a focused "Flag" button would ALSO play/pause.
      // Queue rows are option-buttons (role="option"): Space stays the
      // transport key there, so they are deliberately not exempt.
      if (
        event.key === ' ' &&
        target &&
        (target.tagName === 'A' ||
          target.tagName === 'VIDEO' ||
          (target.tagName === 'BUTTON' && target.getAttribute('role') !== 'option'))
      ) {
        return;
      }
      switch (event.key) {
        case 'j':
        case 'ArrowDown':
          event.preventDefault();
          move(1);
          break;
        case 'k':
        case 'ArrowUp':
          event.preventDefault();
          move(-1);
          break;
        case 'a':
          void decide('accept');
          break;
        case 'r':
          void decide('reject');
          break;
        case 'f':
          void flagFlow();
          break;
        case 'n':
          setShowNote((value) => !value);
          window.setTimeout(() => noteRef.current?.focus(), 0);
          break;
        case 'v':
          void toggleSource();
          break;
        case 'x':
          void acknowledge();
          break;
        case '?':
          setHelpOpen((value) => !value);
          break;
        case ' ':
          event.preventDefault();
          togglePlay();
          break;
        default:
          break;
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [move, decide, acknowledge, toggleSource, flagFlow, togglePlay, helpOpen]);

  const seek = useCallback(
    (seconds: number) => {
      const player = proxyRef.current;
      if (!player || !current) return;
      const local = Math.max(0, seconds - current.start_s);
      try {
        player.currentTime = local;
      } catch {
        // metadata has not arrived yet; the value will not stick, so do nothing
      }
      if (player.paused) void player.play();
    },
    [current],
  );

  const stripSeries = useMemo(() => {
    const series = signalsQuery.data?.series;
    if (!series || typeof series !== 'object') return null;
    return { motion: series.motion ?? [], luma: series.luma ?? [] };
  }, [signalsQuery.data]);

  const evidenceRows = useMemo(() => {
    const evidence = detail?.evidence;
    if (!evidence || typeof evidence !== 'object') return [];
    return Object.entries(evidence).filter(([key]) => key !== 'synthetic');
  }, [detail]);

  const total = queue.data?.total ?? 0;

  return (
    <>
      <div className="page-head">
        <h1 className="page-title">Triage</h1>
        <p className="page-lede">
          The strongest verb here is <strong>flag</strong>. Every decision writes a row — including whether the
          source-resolution pane was opened — and a self-contained ledger event.
        </p>
      </div>

      <div className="workbench">
        <Panel
          className="queue-panel"
          title="Ranked queue"
          stamp={`${total} total · ${entries.length} loaded`}
          flush
          enter={0}
        >
          <div className="queue-filter" role="group" aria-label="Filter the queue by decision state">
            {['all', 'open', 'accepted', 'rejected', 'flagged'].map((value) => (
              <button
                key={value}
                type="button"
                className="filter-chip"
                data-testid={`queue-filter-${value}`}
                aria-label={`Show ${value} candidates`}
                aria-pressed={filter === value}
                onClick={() => {
                  setFilter(value);
                  setSelected(0);
                }}
              >
                {value}
              </button>
            ))}
          </div>

          <div className="queue-scroll">
            {queue.loading && entries.length === 0 && <SkeletonRows count={6} />}
            {queue.error && (
              <div style={{ padding: 'var(--space-3)' }}>
                <Notice kind="error" title={queue.error.title} detail={queue.error.detail} />
              </div>
            )}
            {!queue.loading && !queue.error && entries.length === 0 && (
              <div className="queue-empty">
                {filter === 'all'
                  ? 'The queue is empty. Candidates appear here as soon as a source finishes scoring — ingest footage first.'
                  : `No candidates in state “${filter}”. Switch the filter back to all.`}
              </div>
            )}

            <div
              data-testid="queue-table"
              role="listbox"
              aria-label="Ranked candidate queue"
              tabIndex={0}
              aria-activedescendant={current ? `queue-option-${index}` : undefined}
            >
              {entries.map((entry, entryIndex) => {
                const isSelected = entryIndex === index;
                return (
                  <button
                    key={`${entry.kind}-${entry.candidate_id ?? entry.window_id}`}
                    type="button"
                    role="option"
                    id={`queue-option-${entryIndex}`}
                    aria-selected={isSelected}
                    tabIndex={-1}
                    className={`queue-row${isSelected ? ' is-selected' : ''}${entry.kind === 'ml_failed' ? ' is-ml-failed' : ''}`}
                    data-testid={`queue-entry-${entryIndex}`}
                    onClick={() => selectRow(entryIndex)}
                  >
                    {/* Button content is phrasing content: spans, not divs —
                        the grid/flex display classes blockify them exactly as
                        the divs were laid out. */}
                    <span className="queue-rank" aria-hidden="true">
                      {entry.kind === 'ml_failed' ? '!' : entryIndex + 1}
                    </span>
                    <span className="queue-body">
                      {entry.kind === 'ml_failed' ? (
                        <>
                          <span className="bench-chips">
                            <Chip kind="ml-failed" icon={<IconAlert size={11} />}>
                              detection incomplete
                            </Chip>
                          </span>
                          <span className="queue-sentence">{entry.label ?? 'manual review required'}</span>
                          <span className="queue-meta">
                            <span>{entry.source_name}</span>
                            <span>
                              {timecode(entry.start_s, false)}–{timecode(entry.end_s, false)}
                            </span>
                            <span title={entry.reason}>{entry.reason?.slice(0, 60) || 'no reason recorded'}</span>
                          </span>
                        </>
                      ) : (
                        <>
                          <span
                            className="queue-sentence"
                            data-testid={entryIndex === explanationIndex ? 'candidate-explanation' : undefined}
                          >
                            {entry.sentence}
                          </span>
                          <span className="queue-meta">
                            <span className="queue-pattern">{entry.pattern_slug}</span>
                            <span>v{entry.pattern_version}</span>
                            <span>score {entry.score}</span>
                            <span>
                              {timecode(entry.start_s, false)}–{timecode(entry.end_s, false)}
                            </span>
                            {entry.state && entry.state !== 'open' && <StateChip state={entry.state} />}
                            {entry.demo && <Chip kind="demo">demo · synthetic</Chip>}
                            {!entry.demo && entry.synthetic && <Chip kind="demo">synthetic</Chip>}
                          </span>
                        </>
                      )}
                    </span>
                  </button>
                );
              })}
            </div>

            {extraError && (
              <div style={{ padding: 'var(--space-3)' }}>
                <Notice kind="error" title={extraError.title} detail={`${extraError.detail} The loaded rows are untouched; load more to retry.`} />
              </div>
            )}
            {hasMore && (
              <div className="queue-more">
                <Btn
                  variant="ghost"
                  block
                  data-testid="queue-load-more"
                  loading={extraBusy}
                  loadingLabel="Loading…"
                  onClick={() => void loadMore()}
                >
                  Load the next {QUEUE_PAGE} — {shownCandidates} of {totalCandidates} candidates shown
                </Btn>
              </div>
            )}
          </div>

          <div className="panel-note">
            Detection-incomplete spans rank <em>above</em> the machine's candidates: spans it could not look at come
            before spans it ranked.
          </div>
        </Panel>

        <div className="bench">
          {!current && (
            <Panel title="Workbench" enter={0}>
              <Empty title="Nothing selected">
                Pick an entry from the ranked queue. If the queue is empty, ingest footage first — the workbench only
                shows spans the pipeline has scored.
              </Empty>
            </Panel>
          )}

          {current?.kind === 'ml_failed' && (
            <Panel
              title="Detection incomplete — manual review required"
              enter={0}
              stamp={`${current.source_name} · ${timecode(current.start_s, false)}–${timecode(current.end_s, false)}`}
            >
              <div data-testid="ml-failed-detail">
                <Notice kind="warn" title="The detector did not run on this span">
                  {current.reason || 'no reason recorded'}
                </Notice>
                <p className="muted" style={{ fontSize: 'var(--text-sm)' }}>
                  This window is in the queue because the machine could not look at it. Reviewing it means opening the
                  span by hand — the product will not rank what it did not measure. Acknowledging records a
                  <span className="mono"> ML_FAILURE_ACKED </span> ledger event and removes the entry from the queue.
                </p>
                <Btn
                  variant="primary"
                  data-testid="ml-ack"
                  loading={pending === 'ack'}
                  loadingLabel="Acknowledging…"
                  onClick={() => void acknowledge()}
                  icon={<IconCheck size={14} />}
                >
                  Acknowledge (x)
                </Btn>
              </div>
              {message && (
                <p className="action-status" data-testid="triage-message" role="status">
                  {message.text}
                </p>
              )}
            </Panel>
          )}

          {current?.kind === 'candidate' && (
            <Panel enter={0} flush className="bench-panel">
              {/* The bench is a companion region, not a child of "Ranked
                  queue": give heading navigation an h2 to land on even though
                  the score header — not a panel head — is the visible title. */}
              <h2 className="sr-only">Workbench</h2>
              {!detail && !detailQuery.error && (
                <div style={{ padding: 'var(--space-4)' }}>
                  <SkeletonRows count={2} height="var(--space-10)" />
                </div>
              )}
              {detailQuery.error && (
                <div style={{ padding: 'var(--space-4)' }}>
                  <Notice kind="error" title={detailQuery.error.title} detail={detailQuery.error.detail} />
                </div>
              )}
              {detail && (
                <div data-testid="candidate-detail">
                  <div className="bench-head">
                    <div className="bench-score">
                      {detail.score}
                      <small>rank score</small>
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="bench-chips">
                        <Chip kind="quiet">{detail.pattern_slug}</Chip>
                        <Chip kind="quiet">v{detail.pattern_version}</Chip>
                        <StateChip state={detail.state} />
                        {detail.demo && <Chip kind="demo">demo · synthetic</Chip>}
                        {!detail.demo && detail.synthetic && <Chip kind="demo">synthetic</Chip>}
                        {detail.ml_state === 'failed' && (
                          <Chip kind="ml-failed" icon={<IconAlert size={11} />}>
                            detection incomplete
                          </Chip>
                        )}
                      </div>
                      <p className="bench-sentence" data-testid="candidate-sentence">
                        {detail.sentence}
                      </p>
                      <div className="queue-meta" style={{ marginTop: 'var(--space-2)' }}>
                        <span>{detail.source_name}</span>
                        <span>
                          {timecode(detail.start_s, false)}–{timecode(detail.end_s, false)} ·{' '}
                          {duration(detail.end_s - detail.start_s)}
                        </span>
                        <span>window {detail.window_id.slice(0, 8)}</span>
                      </div>
                    </div>
                  </div>

                  <div className="player">
                    {detail.clip_id ? (
                      <video
                        data-testid="proxy-player"
                        ref={proxyRef}
                        controls
                        preload="metadata"
                        src={mediaUrl(`/api/v1/clips/${detail.clip_id}/proxy`)}
                        onTimeUpdate={(event) => setPlayhead(detail.start_s + event.currentTarget.currentTime)}
                        onSeeked={(event) => setPlayhead(detail.start_s + event.currentTarget.currentTime)}
                      />
                    ) : (
                      <div className="player-missing">
                        no review proxy for this window — the proxy stage did not produce one
                      </div>
                    )}
                    <div className="player-transport">
                      <span className="tc">
                        {playhead != null ? timecode(playhead) : timecode(detail.start_s)}
                      </span>
                      <span>/ {timecode(detail.end_s)}</span>
                      <span className="dim">· working copy (lossy) — decisions render on this, not on the excerpt</span>
                      <span className="spacer" />
                      <span className="dim">space plays</span>
                    </div>
                  </div>

                  <div className="strip-wrap">
                    <div className="strip-legend">
                      <span>
                        <span className="swatch swatch-motion" aria-hidden="true" />
                        motion energy
                      </span>
                      <span>
                        <span className="swatch swatch-luma" aria-hidden="true" />
                        luma
                      </span>
                      <span>
                        <span className="swatch swatch-audio" aria-hidden="true" />
                        audio events
                      </span>
                      <span>
                        <span className="swatch swatch-presence" aria-hidden="true" />
                        this window
                      </span>
                      <span className="dim">click or ←/→ to seek</span>
                    </div>
                    {stripSeries && (stripSeries.motion.length > 0 || stripSeries.luma.length > 0) ? (
                      <TraceStrip
                        from={stripFrom}
                        to={stripTo}
                        window={{ start_s: detail.start_s, end_s: detail.end_s }}
                        series={stripSeries}
                        events={timeline?.events ?? []}
                        playhead={playhead}
                        onSeek={seek}
                        label={`Signal trace for ${detail.source_name}, ${timecode(stripFrom, false)} to ${timecode(stripTo, false)}`}
                      />
                    ) : (
                      <p className="strip-hint">
                        {signalsQuery.loading
                          ? 'loading the signal series…'
                          : 'no signal series is stored for this span — the signals stage has not run on this source.'}
                      </p>
                    )}
                    {timeline && durationS != null && (
                      <>
                        <div style={{ marginTop: 'var(--space-2)' }}>
                          <OverviewStrip
                            duration={durationS}
                            windows={timeline.windows}
                            candidates={timeline.candidates}
                            current={{ start_s: detail.start_s, end_s: detail.end_s }}
                            onSeek={seek}
                          />
                        </div>
                        <p className="strip-hint">
                          Whole source: {timeline.windows.length} windows · {timeline.candidates.length} candidates ·{' '}
                          {duration(durationS)}. Red windows are detection-incomplete.
                        </p>
                      </>
                    )}
                  </div>

                  {showSource && (
                    <div className="source-pane" data-testid="source-pane">
                      <h3 style={{ marginBottom: 'var(--space-2)' }}>
                        Source-resolution view — the pixels that will ship
                      </h3>
                      {sourceError ? (
                        <p className="muted" data-testid="source-unplayable">
                          The original's codec is not browser-playable here. Open it locally from{' '}
                          <span className="mono">{sourceQuery.data?.storage_path ?? 'the storage path in Settings'}</span>.
                          The decision still records that this pane was opened.
                        </p>
                      ) : (
                        <video
                          data-testid="source-player"
                          ref={sourceRef}
                          controls
                          preload="metadata"
                          src={mediaUrl(`/api/v1/sources/${detail.source_id}/original`)}
                          onError={() => setSourceError(true)}
                        />
                      )}
                      <p className="source-pane-note">
                        This pane is the mitigation for the lossy working copy: the reviewer can judge the original
                        itself whenever its codec is browser-playable. Whether it was opened travels into the decision
                        row, the ledger event, the CSV and the manifest as <span className="mono">src_viewed</span>.
                      </p>
                    </div>
                  )}

                  <div style={{ padding: 'var(--space-4)' }}>
                    <h3 style={{ marginBottom: 'var(--space-2)' }}>Measured evidence</h3>
                    <table className="evidence-table" data-testid="candidate-evidence">
                      <thead>
                        <tr>
                          <th>measure</th>
                          <th className="num">value</th>
                          <th>unit</th>
                        </tr>
                      </thead>
                      <tbody>
                        {evidenceRows.map(([key, value]) => (
                          <tr key={key}>
                            <td className="mono">{humanKey(key)}</td>
                            <td className="value">{valueFor(key, value)}</td>
                            <td className="unit">{unitFor(key)}</td>
                          </tr>
                        ))}
                        {evidenceRows.length === 0 && (
                          <tr>
                            <td colSpan={3} className="dim">
                              this candidate carries no evidence block
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                    <details style={{ marginTop: 'var(--space-2)' }}>
                      <summary className="dim" style={{ fontSize: 'var(--text-xs)', cursor: 'pointer' }}>
                        raw evidence object
                      </summary>
                      <pre className="evidence mono" style={{ marginTop: 'var(--space-2)' }}>
                        {JSON.stringify(detail.evidence ?? {}, null, 2)}
                      </pre>
                    </details>

                    {detail.detections_summary && Object.keys(detail.detections_summary).length > 0 && (
                      <>
                        <h3 style={{ marginTop: 'var(--space-4)', marginBottom: 'var(--space-2)' }}>
                          Detections in this window
                        </h3>
                        <div className="presence-chips">
                          {Object.entries(detail.detections_summary).map(([name, stats]) => (
                            <Chip
                              key={name}
                              kind={stats.positive_frames > 0 ? 'running' : 'quiet'}
                              title={`${stats.positive_frames} of ${stats.samples} sampled frames; max confidence ${milli(
                                stats.conf_max_milli,
                              )}; sampled every ${stats.sample_interval_ms} ms`}
                            >
                              {name} · {int(stats.positive_frames)}/{int(stats.samples)} frames · conf{' '}
                              {milli(stats.conf_max_milli)}
                            </Chip>
                          ))}
                        </div>
                        <p className="strip-hint">
                          Per-window detection summary (sampled frames with a positive detection of each retained
                          class). The pipeline also stores merged presence intervals; this API does not expose them as
                          time ranges, so none are drawn here.
                        </p>
                      </>
                    )}

                    <h3 style={{ marginTop: 'var(--space-4)', marginBottom: 'var(--space-2)' }}>Decision record</h3>
                    <ul className="decisions" data-testid="decision-history">
                      {(detail.decisions ?? []).map((decision) => (
                        <li key={decision.id}>
                          <span>
                            <StateChip state={decision.action === 'accept' ? 'accepted' : decision.action === 'reject' ? 'rejected' : 'flagged'} />
                          </span>
                          <span>
                            <span className="mono">
                              {decision.reviewer} · src_viewed {String(decision.src_viewed)} ·{' '}
                              {decision.created_at}
                            </span>
                            {decision.note && <span className="decision-note">“{decision.note}”</span>}
                          </span>
                        </li>
                      ))}
                      {(detail.decisions ?? []).length === 0 && (
                        <li>
                          <span />
                          <span className="dim">No decision recorded for this candidate yet.</span>
                        </li>
                      )}
                    </ul>
                  </div>

                  <div className="action-bar">
                    {showNote && (
                      <div className="action-note">
                        <label className="field-label" htmlFor="triage-note">
                          Note (recorded with the decision; vocabulary-guarded)
                        </label>
                        <textarea
                          id="triage-note"
                          className="textarea"
                          data-testid="note-input"
                          ref={noteRef}
                          placeholder="Describe observables — durations, class presences, motion magnitudes, loudness deltas. Enter records the flag; Shift+Enter starts a new line."
                          value={note}
                          onChange={(event) => setNote(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' && !event.shiftKey) {
                              event.preventDefault();
                              void decide('flag');
                            }
                          }}
                        />
                      </div>
                    )}

                    <div className="action-row">
                      <Btn
                        variant="default"
                        data-testid="decision-accept"
                        loading={pending === 'accept'}
                        loadingLabel="Recording…"
                        onClick={() => void decide('accept')}
                        icon={<IconCheck size={14} />}
                        disabled={Boolean(pending)}
                      >
                        Accept (a)
                      </Btn>
                      <Btn
                        data-testid="decision-reject"
                        loading={pending === 'reject'}
                        loadingLabel="Recording…"
                        onClick={() => void decide('reject')}
                        icon={<IconX size={14} />}
                        disabled={Boolean(pending)}
                      >
                        Reject (r)
                      </Btn>
                      <Btn
                        variant="primary"
                        data-testid="decision-flag"
                        loading={pending === 'flag'}
                        loadingLabel="Recording…"
                        onClick={() => void flagFlow()}
                        icon={<IconFlag size={14} />}
                        disabled={Boolean(pending)}
                      >
                        Flag with note (f)
                      </Btn>
                      <Btn
                        data-testid="toggle-note"
                        onClick={() => {
                          setShowNote((value) => !value);
                          window.setTimeout(() => noteRef.current?.focus(), 0);
                        }}
                        aria-pressed={showNote}
                      >
                        Note (n)
                      </Btn>
                      <Btn
                        data-testid="toggle-source"
                        onClick={toggleSource}
                        aria-pressed={showSource}
                        icon={showSource ? <IconPause size={14} /> : <IconPlay size={14} />}
                      >
                        Source view (v)
                      </Btn>
                      <span className="toolbar-end" />
                      <span
                        className={`src-viewed${srcViewed ? ' is-true' : ''}`}
                        data-testid="src-viewed-indicator"
                        title="recorded with the next decision"
                      >
                        source viewed: {String(srcViewed)}
                      </span>
                      <Btn
                        variant="ghost"
                        data-testid="help-open"
                        aria-label="Keyboard shortcuts"
                        onClick={() => setHelpOpen(true)}
                        icon={<IconHelp size={14} />}
                      >
                        ?
                      </Btn>
                    </div>

                    {message && (
                      <div className="action-status" data-testid="triage-message" role="status">
                        {message.text}
                      </div>
                    )}

                    <Legend items={KEYS} />
                  </div>
                </div>
              )}
            </Panel>
          )}
        </div>
      </div>

      <Modal
        open={helpOpen}
        onClose={() => setHelpOpen(false)}
        title="Keyboard"
        footer={
          <Btn variant="primary" data-testid="help-close" onClick={() => setHelpOpen(false)}>
            Close (Esc)
          </Btn>
        }
      >
        <p className="dim" style={{ fontSize: 'var(--text-sm)' }}>
          The workbench is keyboard-first. Keys act on the selected queue entry; typing in the note field never
          triggers an action.
        </p>
        <table className="table">
          <tbody>
            {KEYS.map(([key, label]) => (
              <tr key={label}>
                <td style={{ width: 'var(--space-16)' }}>
                  <span className="keycap">{key}</span>
                </td>
                <td>{label}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Modal>
    </>
  );
}
