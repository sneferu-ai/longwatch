// Ingest — two ingest paths (streamed upload, operator-invoked inbox scan)
// with identical downstream behavior, the labeled demo fixture, and the
// source register with live job progress.

import React, { useCallback, useMemo, useRef, useState } from 'react';
import type { SystemStatus } from '../App';
import { post, uploadFile, describeError, UnauthorizedError } from '../api';
import { usePoll } from '../hooks';
import { Btn, Chip, Empty, Notice, Panel, Progress, SkeletonRows, useToast } from '../ui/kit';
import { IconAlert, IconCheck, IconOpen, IconRefresh } from '../ui/icons';
import { ago, bytes, duration, shortId } from '../format';

interface SourceRow {
  id: string;
  origin: string;
  original_name: string;
  state: string;
  sha256: string;
  size_bytes: number;
  duration_s: number | null;
  has_audio: boolean;
  created_at: string;
  failure_reason: string | null;
  signal_summary: Record<string, Record<string, number>> | null;
  window_count: number;
  pending_jobs: number;
}

interface SourceDetail extends SourceRow {
  storage_path: string;
  video_codec: string | null;
  audio_codec: string | null;
  fps: number | null;
  width: number | null;
  height: number | null;
  ml_failed_count: number;
  job: { id: string; kind: string; state: string; stage: string | null; pct: number; detail: string | null; error: string | null } | null;
}

const STATE_ICON: Record<string, React.ReactNode> = {
  registered: <IconOpen size={11} />,
  windowed: <IconOpen size={11} />,
  proxied: <IconOpen size={11} />,
  analyzed: <IconOpen size={11} />,
  scored: <IconOpen size={11} />,
  ready: <IconCheck size={11} />,
  unreadable: <IconAlert size={11} />,
  missing: <IconAlert size={11} />,
  failed: <IconAlert size={11} />,
};

const STATE_KIND: Record<string, string> = {
  ready: 'complete',
  unreadable: 'failed',
  missing: 'failed',
  failed: 'failed',
  registered: 'open',
  windowed: 'open',
  proxied: 'open',
  analyzed: 'open',
  scored: 'running',
};

/** The state chip carries a glyph and the bare state word; the e2e reads the
 *  word verbatim, so no other text may live inside this element. */
function SourceState({ id, state }: { id: string; state: string }) {
  return (
    <span className={`chip chip-${STATE_KIND[state] ?? 'quiet'}`} data-testid={`source-state-${id}`}>
      <span className="chip-glyph" aria-hidden="true">
        {STATE_ICON[state] ?? <IconOpen size={11} />}
      </span>
      {state}
    </span>
  );
}

export function Ingest({
  status,
  onUnauthorized,
  onGoToTriage,
}: {
  status: SystemStatus | null;
  onUnauthorized: () => void;
  onGoToTriage: () => void;
}) {
  const toast = useToast();
  const sources = usePoll<{ sources: SourceRow[] }>('/api/v1/sources', 5000);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState<'upload' | 'scan' | 'demo' | null>(null);
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [message, setMessage] = useState<{ kind: 'ok' | 'err' | 'warn'; text: string } | null>(null);
  const [openRow, setOpenRow] = useState<string | null>(null);
  // The expanded detail polls at the job cadence while it is open: an ingest
  // takes minutes, and a frozen pct beside a live row is a lie (SPEC §10).
  const detailQuery = usePoll<SourceDetail>(openRow ? `/api/v1/sources/${openRow}` : null, 2000);
  // And it never renders one source's detail under another's expanded row.
  const detail = detailQuery.data && detailQuery.data.id === openRow ? detailQuery.data : null;
  const detailError = detailQuery.error;
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const rows = sources.data?.sources ?? [];
  const isEmpty = !sources.loading && rows.length === 0;

  const fail = useCallback(
    (err: unknown) => {
      if (err instanceof UnauthorizedError) {
        onUnauthorized();
        return;
      }
      const described = describeError(err);
      setMessage({ kind: 'err', text: `${described.title}. ${described.detail}` });
      toast.push({ kind: 'err', text: described.title, detail: described.detail });
    },
    [onUnauthorized, toast],
  );

  const upload = useCallback(async () => {
    if (!file) return;
    setBusy('upload');
    setUploadPct(0);
    setMessage(null);
    try {
      const result = await uploadFile('/api/v1/uploads', file, (loaded, total) => {
        setUploadPct(total > 0 ? Math.round((loaded / total) * 100) : 0);
      });
      const body = result.body as { source_id?: string; job_id?: string; duplicate?: boolean };
      if (result.status === 401) {
        onUnauthorized();
        setMessage({ kind: 'err', text: 'unauthorized' });
      } else if (result.status === 200) {
        setMessage({
          kind: 'warn',
          text: `Duplicate digest — this file is already registered as source ${shortId(body.source_id)}. Nothing was copied.`,
        });
      } else if (result.status === 202) {
        setMessage({
          kind: 'ok',
          text: `Hashing complete; ingest queued (job ${shortId(body.job_id)}). The pipeline runs probe → windows → proxies → signals → detect → score.`,
        });
        setFile(null);
        if (inputRef.current) inputRef.current.value = '';
      } else {
        // A mid-stream 413 (size ceiling) or another refusal arrives here; name
        // the server's reason, never a raw JSON dump.
        const detailText = (result.body as { detail?: unknown })?.detail;
        setMessage({
          kind: 'err',
          text:
            typeof detailText === 'string' && detailText
              ? `Upload refused (${result.status}): ${detailText}`
              : `Upload failed (${result.status}) — the server refused the file without a reason.`,
        });
      }
      await sources.reload();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
      setUploadPct(null);
    }
  }, [file, onUnauthorized, sources, fail]);

  const scan = useCallback(async () => {
    setBusy('scan');
    setMessage(null);
    try {
      const result = await post<Array<{ source_id: string; job_id?: string; duplicate?: boolean }>>('/api/v1/ingest/scan');
      const fresh = result.filter((entry) => entry.job_id).length;
      setMessage({
        kind: 'ok',
        text: `Inbox scan registered ${result.length} file(s) — ${fresh} new ingest job(s), ${result.length - fresh} already known.`,
      });
      await sources.reload();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  }, [sources, fail]);

  const seedDemo = useCallback(async () => {
    setBusy('demo');
    setMessage(null);
    try {
      const result = await post<{ source_id: string; job_id?: string }>('/api/v1/demo/seed');
      setMessage({
        kind: 'ok',
        text: result.job_id
          ? `Demo fixture queued (job ${shortId(result.job_id)}). Every surface labels it “demo · synthetic”.`
          : 'An intact demo source already exists; nothing new was created.',
      });
      await sources.reload();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  }, [sources, fail]);

  const openDetail = useCallback(
    (row: SourceRow) => {
      setOpenRow((current) => (current === row.id ? null : row.id));
    },
    [],
  );

  const totals = useMemo(() => {
    const windows = rows.reduce((sum, row) => sum + row.window_count, 0);
    const pending = rows.reduce((sum, row) => sum + row.pending_jobs, 0);
    return { windows, pending };
  }, [rows]);

  return (
    <>
      <div className="page-head">
        <h1 className="page-title">Ingest</h1>
        <p className="page-lede">
          Originals are <strong>read-only</strong>. Uploaded files are hashed while streaming and referenced by path;
          nothing renames, moves, re-encodes, truncates or deletes an original. The only deletions in this product are
          LongWatch's own derived artifacts, under the operator-invoked retention purge.
        </p>
      </div>

      <div className="cols cols-main">
        <div>
          <Panel
            title="Intake"
            enter={0}
            stamp={totals.pending > 0 ? `${totals.pending} job(s) queued or running` : 'idle'}
            note={
              <>
                One inbox directory, scanned only when you press the button — unless the operator set{' '}
                <span className="mono">LONGWATCH_INBOX_POLL_S</span>, in which case it also polls on that interval.
              </>
            }
          >
            {/* A label wrapping the file input: clicking anywhere in the
                dropzone opens the picker, and the input itself stays
                keyboard-reachable (Tab to it, Enter opens the picker). */}
            <label
              className={`dropzone${dragging ? ' is-over' : ''}`}
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                const dropped = event.dataTransfer.files?.[0];
                if (dropped) setFile(dropped);
              }}
            >
              <input
                data-testid="upload-input"
                ref={inputRef}
                type="file"
                className="dropzone-input"
                aria-label="Choose a video file to ingest"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
              <span className="dropzone-title">Drop a video file here, or choose one</span>
              <span className="dropzone-hint">
                The file is streamed through a SHA-256 hasher; a known digest is reported as a duplicate and nothing is
                copied twice.
              </span>
            </label>

            <div className="filepick" style={{ marginTop: 'var(--space-3)' }}>
              <Btn
                variant="primary"
                data-testid="upload-submit"
                loading={busy === 'upload'}
                loadingLabel={uploadPct != null ? `Uploading ${uploadPct}%` : 'Uploading…'}
                disabled={!file || busy !== null}
                disabledReason={file ? 'another intake action is running' : 'choose a file first'}
                onClick={() => void upload()}
              >
                Upload and hash
              </Btn>
              <Btn
                data-testid="scan-inbox"
                loading={busy === 'scan'}
                loadingLabel="Scanning…"
                disabled={busy !== null}
                onClick={() => void scan()}
                icon={<IconRefresh size={14} />}
                title="Register every file in the configured inbox directory (LONGWATCH_INBOX_DIR)"
              >
                Scan inbox
              </Btn>
              <span className="spacer" />
              {file ? (
                <span className="filepick-name" title={file.name}>
                  {file.name} · {bytes(file.size)}
                </span>
              ) : (
                <span className="dim" style={{ fontSize: 'var(--text-xs)' }}>
                  no file chosen
                </span>
              )}
            </div>

            {uploadPct != null && (
              <div className="source-progress" style={{ marginTop: 'var(--space-3)' }}>
                <Progress pct={uploadPct} />
                <span className="mono">{uploadPct}%</span>
              </div>
            )}

            {message && (
              <div style={{ marginTop: 'var(--space-3)' }} data-testid="ingest-message">
                <Notice kind={message.kind === 'ok' ? 'ok' : message.kind === 'warn' ? 'warn' : 'error'}>
                  {message.text}
                </Notice>
              </div>
            )}


          </Panel>

          <Panel
            title="Sources"
            enter={1}
            stamp={`${rows.length} registered · ${totals.windows} windows`}
            flush
          >
            {sources.loading && rows.length === 0 && <SkeletonRows count={3} height="var(--space-16)" />}

            {sources.error && (
              <div style={{ padding: 'var(--space-4)' }}>
                <Notice kind="error" title={sources.error.title} detail={sources.error.detail} />
              </div>
            )}

            {isEmpty && !sources.error && (
              <Empty
                title="No footage registered yet"
                action={
                  <div className="inline" style={{ justifyContent: 'center' }}>
                    <Btn
                      data-testid="demo-seed"
                      loading={busy === 'demo'}
                      loadingLabel="Creating fixture…"
                      disabled={busy !== null}
                      onClick={() => void seedDemo()}
                      icon={<IconOpen size={14} />}
                    >
                      Create demo fixture (labeled synthetic)
                    </Btn>
                  </div>
                }
              >
                Upload a file, scan the inbox directory, or create the labeled demo fixture to see the pipeline end to
                end. The fixture is a generated scene with known content — it is measured by the same pipeline as real
                footage and labeled “demo · synthetic” on every surface.
              </Empty>
            )}

            {rows.map((row) => (
              <div key={row.id} data-testid={`source-row-${row.id}`}>
                <div className="source-row">
                  <div>
                    <div className="source-name">{row.original_name}</div>
                    <div className="source-meta">
                      <span title={row.sha256}>sha256 {shortId(row.sha256, 12)}</span>
                      <span>{bytes(row.size_bytes)}</span>
                      <span>{row.duration_s != null ? duration(row.duration_s) : 'duration unknown'}</span>
                      <span>{row.window_count} windows</span>
                      <span>{ago(row.created_at)}</span>
                      {row.has_audio && <span>audio</span>}
                    </div>
                    {row.failure_reason && (
                      <div className="source-meta error-text">{row.failure_reason}</div>
                    )}
                  </div>
                  <div className="source-facts">
                    {row.origin === 'demo' && <Chip kind="demo">demo · synthetic</Chip>}
                    {row.origin !== 'demo' && <Chip kind="quiet">{row.origin}</Chip>}
                    <SourceState id={row.id} state={row.state} />
                    <Btn
                      size="sm"
                      variant="ghost"
                      data-testid={`source-detail-${row.id}`}
                      aria-expanded={openRow === row.id}
                      onClick={() => openDetail(row)}
                    >
                      {openRow === row.id ? 'Hide' : 'Detail'}
                    </Btn>
                  </div>
                  {row.pending_jobs > 0 && (
                    <div className="source-progress">
                      <Progress pct={null} state="running" />
                      <span>{row.pending_jobs} job(s) queued or running</span>
                    </div>
                  )}
                </div>

                {openRow === row.id && (
                  <div className="source-row" style={{ background: 'var(--bg-overlay)' }}>
                    <div className="source-detail">
                      {detailError && (
                        <Notice kind="error" title={detailError.title} detail={detailError.detail} />
                      )}
                      {!detail && !detailError && <SkeletonRows count={1} height="var(--space-16)" />}
                      {detail && (
                        <>
                          {detail.job && (
                            <div className="source-progress" style={{ marginBottom: 'var(--space-3)' }}>
                              <span className="mono">
                                {detail.job.kind} · {detail.job.state} · {detail.job.stage ?? '—'}
                              </span>
                              <Progress pct={detail.job.pct} state={detail.job.state} />
                              <span className="mono">{detail.job.pct}%</span>
                              {detail.job.error && <span className="error-text">{detail.job.error}</span>}
                            </div>
                          )}
                          <dl className="kv">
                            <dt>storage path</dt>
                            <dd>{detail.storage_path}</dd>
                            <dt>sha256</dt>
                            <dd>{detail.sha256}</dd>
                            <dt>probe</dt>
                            <dd>
                              {detail.video_codec ?? '—'}
                              {detail.width && detail.height ? ` · ${detail.width}×${detail.height}` : ''}
                              {detail.fps ? ` · ${detail.fps.toFixed(3)} fps` : ''}
                              {detail.audio_codec ? ` · ${detail.audio_codec}` : ' · no audio'}
                            </dd>
                            <dt>windows with failed detection</dt>
                            <dd>{detail.ml_failed_count}</dd>
                          </dl>
                          {detail.signal_summary && (
                            <div style={{ marginTop: 'var(--space-3)' }}>
                              {/* A caption for the table, not a section heading:
                                  keeping it as an h4 skipped h3 in the outline
                                  (WCAG 2.2 SC 1.3.1 / H42). */}
                              <span className="field-label">Signal percentiles (milli-units, whole source)</span>
                              <div className="table-wrap">
                              <table className="table">
                                <thead>
                                  <tr>
                                    <th>series</th>
                                    <th className="num">p10</th>
                                    <th className="num">p25</th>
                                    <th className="num">p50</th>
                                    <th className="num">p75</th>
                                    <th className="num">p85</th>
                                    <th className="num">p90</th>
                                    <th className="num">p95</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {Object.entries(detail.signal_summary).map(([name, values]) => (
                                    <tr key={name}>
                                      <td className="mono">{name}</td>
                                      {['p10', 'p25', 'p50', 'p75', 'p85', 'p90', 'p95'].map((key) => (
                                        <td className="num" key={key}>
                                          {values?.[key] ?? '—'}
                                        </td>
                                      ))}
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                              </div>
                            </div>
                          )}
                          <div className="inline" style={{ marginTop: 'var(--space-3)' }}>
                            <Btn size="sm" data-testid="open-workbench" onClick={onGoToTriage}>
                              Open the workbench
                            </Btn>
                          </div>
                        </>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </Panel>
        </div>

        <div>
          <Panel title="System" enter={2} stamp={status ? 'as of just now' : 'connecting'}>
            <dl className="kv">
              <dt>database</dt>
              <dd>{status?.database ?? '—'}</dd>
              <dt>detector</dt>
              <dd>
                {status ? (status.model.ok ? `ok · ${status.model.classes ?? '?'} classes` : `down · ${status.model.error ?? ''}`) : '—'}
              </dd>
              <dt>detector provenance</dt>
              <dd>{status?.model.provenance ?? '—'}</dd>
              <dt>class map</dt>
              <dd>{status ? (status.class_map.ok ? `ok · ${status.class_map.classes ?? '?'} names` : `invalid · ${status.class_map.error ?? ''}`) : '—'}</dd>
              <dt>offline guard</dt>
              <dd>
                {status ? (status.offline_guard.installed ? 'installed' : 'not installed') : '—'}
              </dd>
              <dt>sanctioned egress</dt>
              <dd>{status?.offline_guard.sanctioned_egress ?? '—'}</dd>
              <dt>windows awaiting review</dt>
              <dd>{status?.ml_failed_count ?? 0}</dd>
            </dl>
            <div style={{ marginTop: 'var(--space-3)' }}>
              {Object.entries(status?.workers ?? {}).map(([name, worker]) => (
                <div className="inline" key={name} style={{ marginBottom: 'var(--space-1)' }}>
                  <Chip kind={worker.alive ? 'complete' : 'failed'}>
                    {name} {worker.alive ? 'alive' : 'down'}
                  </Chip>
                  <span className="dim mono">{worker.jobs_processed ?? 0} jobs</span>
                  {worker.last_error && (
                    <span className="error-text">{worker.last_error}</span>
                  )}
                </div>
              ))}
              {!status && <span className="dim">workers unknown until the status endpoint answers</span>}
            </div>
          </Panel>

          <Panel title="Why two workers" enter={3}>
            <p className="dim" style={{ fontSize: 'var(--text-sm)' }}>
              The media worker runs the five pipeline stages. The utility worker runs exports, purges and re-scores, so
              a multi-day corpus ingest never blocks a bundle.
            </p>
          </Panel>
        </div>
      </div>
    </>
  );
}
