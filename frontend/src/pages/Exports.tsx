// Exports — one signed bundle per request, verifiable from its own files.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { SystemStatus } from '../App';
import { describeError, downloadFile, post, UnauthorizedError } from '../api';
import { usePoll } from '../hooks';
import { Btn, Chip, Empty, Notice, Panel, Progress, Segmented, SkeletonRows, useToast } from '../ui/kit';
import { IconDownload, IconShield } from '../ui/icons';
import { clock, shortId } from '../format';

interface ExportRow {
  id: string;
  reviewer: string;
  top_n: number;
  states: string[];
  cut_precision: string;
  blur_faces: boolean;
  job_state?: string | null;
  stage?: string | null;
  pct?: number | null;
  error?: string | null;
  download_ready: boolean;
  created_at: string;
}

const STATES = ['open', 'accepted', 'rejected', 'flagged'] as const;

export function Exports({
  status,
  onUnauthorized,
}: {
  status: SystemStatus | null;
  onUnauthorized: () => void;
}) {
  const toast = useToast();
  const exports = usePoll<{ exports: ExportRow[] }>('/api/v1/exports', 3000);
  const [states, setStates] = useState<string[]>(['accepted', 'flagged']);
  const [cutPrecision, setCutPrecision] = useState<'keyframe' | 'exact'>('keyframe');
  const [blur, setBlur] = useState(false);
  const [topN, setTopN] = useState(200);
  const [creating, setCreating] = useState(false);
  const [message, setMessage] = useState<{ kind: 'ok' | 'err' | 'warn'; text: string } | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);

  // Initialize the form from the deployment's export defaults exactly once —
  // the poll must never clobber an operator's in-progress edits.
  const defaultsApplied = useRef(false);
  const exportDefaults = status?.export_defaults;
  useEffect(() => {
    if (defaultsApplied.current || !exportDefaults) return;
    defaultsApplied.current = true;
    setBlur(Boolean(exportDefaults.blur_faces));
    if (Number.isFinite(exportDefaults.top_n) && exportDefaults.top_n > 0) setTopN(exportDefaults.top_n);
    const valid = (exportDefaults.states ?? []).filter((entry): entry is (typeof STATES)[number] =>
      (STATES as readonly string[]).includes(entry),
    );
    if (valid.length > 0) setStates(valid);
  }, [exportDefaults]);

  const toggleState = (value: string) => {
    setStates((current) => (current.includes(value) ? current.filter((entry) => entry !== value) : [...current, value]));
  };

  const create = useCallback(async () => {
    setCreating(true);
    setMessage(null);
    try {
      const result = await post<{ export_id: string; job_id: string }>('/api/v1/exports', {
        states,
        cut_precision: cutPrecision,
        blur_faces: blur,
        top_n: topN,
      });
      setMessage({
        kind: 'ok',
        text: `Export ${shortId(result.export_id)} queued (job ${shortId(result.job_id)}). Assembly re-hashes every contributing source, cuts the excerpts, then signs the manifest.`,
      });
      await exports.reload();
    } catch (err) {
      if (err instanceof UnauthorizedError) onUnauthorized();
      else {
        const described = describeError(err);
        setMessage({ kind: 'err', text: `${described.title}. ${described.detail}` });
        toast.push({ kind: 'err', text: described.title, detail: described.detail });
      }
    } finally {
      setCreating(false);
    }
  }, [states, cutPrecision, blur, topN, exports, onUnauthorized, toast]);

  const download = useCallback(
    async (id: string, event: React.MouseEvent) => {
      event.preventDefault();
      if (downloading) return; // a bundle fetch is already in flight
      setDownloading(id);
      try {
        await downloadFile(`/api/v1/exports/${id}/download`, `longwatch-export-${id.slice(0, 8)}.zip`);
      } catch (err) {
        if (err instanceof UnauthorizedError) onUnauthorized();
        else {
          const described = describeError(err);
          setMessage({ kind: 'err', text: `${described.title}. ${described.detail}` });
        }
      } finally {
        setDownloading(null);
      }
    },
    [downloading, onUnauthorized],
  );

  const rows = exports.data?.exports ?? [];
  const readyCount = useMemo(() => rows.filter((row) => row.download_ready).length, [rows]);
  const statement = status?.disclosures.integrity_statement;

  return (
    <>
      <div className="page-head">
        <h1 className="page-title">Exports</h1>
        <p className="page-lede">
          One deterministic ZIP per request: the excerpts, the frozen ledger transcript, the public key, the canonical
          manifest bytes and the signature — plus the digests of every other bundle file, including the two verifier
          scripts.
        </p>
      </div>

      <div className="cols cols-main">
        <div>
          <Panel title="Create a bundle" enter={0} stamp={`${readyCount} ready of ${rows.length}`}>
            <div className="field">
              <span className="field-label">Decision states to include</span>
              <div className="inline">
                {STATES.map((value) => (
                  <label className="checkline" key={value}>
                    <input
                      type="checkbox"
                      data-testid={`export-state-${value}`}
                      checked={states.includes(value)}
                      onChange={() => toggleState(value)}
                    />
                    {value}
                  </label>
                ))}
              </div>
              <span className="field-help">
                Default is accepted + flagged. A candidate's decision state is the state at export time; decided
                candidates keep their pattern version forever.
              </span>
            </div>

            <div className="field">
              <span className="field-label">Cut precision</span>
              <Segmented<'keyframe' | 'exact'>
                ariaLabel="Cut precision"
                testId="export-cut-precision"
                value={cutPrecision}
                onChange={setCutPrecision}
                options={[
                  {
                    value: 'keyframe',
                    label: 'keyframe (stream copy)',
                    title: 'Stream copy from the previous keyframe: bit-identical to the original, with lead-in footage.',
                  },
                  {
                    value: 'exact',
                    label: 'exact (re-encode)',
                    title: 'Re-encode for span-precise cuts: no lead-in footage, and the clip is never labeled bit-identical.',
                  },
                ]}
              />
              <span className="field-help">
                {cutPrecision === 'keyframe'
                  ? 'Keyframe-snapped stream copies are bit-identical to the original, and the recorded actual bounds include the lead-in before the requested start.'
                  : 'Exact re-encodes are span-precise and are never labeled bit-identical; the manifest records the re-encode.'}
              </span>
            </div>

            <div className="field">
              <label className="checkline">
                <input
                  type="checkbox"
                  data-testid="export-blur"
                  checked={blur}
                  onChange={(event) => setBlur(event.target.checked)}
                />
                pixelate detected faces in every excerpt
              </label>
              <span className="field-help">
                Off by default. Fails closed with a named error when no face model is present — the export never
                silently ships unblurred. A blurred excerpt is re-encoded, so it is not bit-identical to the source, and
                the manifest says so.
              </span>
            </div>

            <div className="field" style={{ maxWidth: 'var(--space-16)' }}>
              <label className="field-label" htmlFor="export-top-n">
                Top N candidates (rank order)
              </label>
              <input
                id="export-top-n"
                className="input"
                data-testid="export-top-n"
                type="number"
                min={1}
                value={topN}
                onChange={(event) => setTopN(Math.max(1, Number(event.target.value) || 1))}
              />
              <span className="field-help">
                Rank order is by score. ml_failed spans are never exported: they carry no measurement to ship.
                Demo-seeded candidates ship with <span className="mono">synthetic: true</span> in every CSV row and
                manifest entry, so a recipient can always tell the fixture from real footage.
              </span>
            </div>

            <div className="inline" style={{ marginTop: 'var(--space-3)' }}>
              <Btn
                variant="primary"
                data-testid="export-create"
                loading={creating}
                loadingLabel="Queueing…"
                onClick={() => void create()}
                icon={<IconShield size={14} />}
                disabled={states.length === 0}
                disabledReason="select at least one decision state"
              >
                Create signed bundle
              </Btn>
              <span className="dim" style={{ fontSize: 'var(--text-xs)' }}>
                signed with the deployment's Ed25519 key; the key fingerprint is in Settings
              </span>
            </div>

            {message && (
              <div style={{ marginTop: 'var(--space-3)' }} data-testid="export-message">
                <Notice kind={message.kind === 'ok' ? 'ok' : message.kind === 'warn' ? 'warn' : 'error'}>
                  {message.text}
                </Notice>
              </div>
            )}
          </Panel>

          {/* The backend serves the newest 50 exports (api.py list_exports
              LIMIT 50) — a full page says "newest 50 shown", never "50
              created", so the stamp cannot imply these are all of them. */}
          <Panel
            title="Bundles"
            enter={1}
            flush
            stamp={rows.length >= 50 ? `newest ${rows.length} shown` : `${rows.length} created`}
          >
            {exports.loading && rows.length === 0 && <SkeletonRows count={2} height="var(--space-10)" />}
            {exports.error && (
              <div style={{ padding: 'var(--space-4)' }}>
                <Notice kind="error" title={exports.error.title} detail={exports.error.detail} />
              </div>
            )}
            {!exports.loading && !exports.error && rows.length === 0 && (
              <Empty title="No bundles yet">
                Choose the decision states to ship and create the first export; every bundle carries its own key and its
                own frozen ledger transcript, so it verifies without this database.
              </Empty>
            )}
            {rows.length > 0 && (
              <div className="table-wrap">
              <table className="table" data-testid="export-table">
                <thead>
                  <tr>
                    <th>id</th>
                    <th>states</th>
                    <th>precision</th>
                    <th>faces</th>
                    <th>assembly</th>
                    <th>created</th>
                    {/* position:relative scopes the sr-only span's absolute box
                        to this cell; otherwise its containing block is the page
                        and the hidden box extends the mobile scroll width. */}
                    <th style={{ position: 'relative' }}>
                      <span className="sr-only">download</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id}>
                      <td className="mono" title={row.id}>
                        {shortId(row.id)}
                      </td>
                      <td className="mono">{(row.states ?? []).join(' · ') || '—'}</td>
                      <td className="mono">{row.cut_precision}</td>
                      <td className="mono">{row.blur_faces ? 'pixelated' : 'as cut'}</td>
                      <td style={{ minWidth: 'var(--space-16)' }}>
                        {row.download_ready ? (
                          <Chip kind="complete">ready</Chip>
                        ) : row.job_state === 'failed' ? (
                          <Chip kind="failed">failed</Chip>
                        ) : (
                          <div className="inline">
                            <Progress pct={row.pct} state={row.job_state} />
                            <span className="mono dim">
                              {row.job_state ?? 'queued'} {row.stage ? `· ${row.stage}` : ''} {row.pct ?? 0}%
                            </span>
                          </div>
                        )}
                        {row.error && <div className="field-error">{row.error}</div>}
                      </td>
                      <td className="mono dim">{clock(row.created_at)}</td>
                      <td>
                        {row.download_ready ? (
                          <a
                            data-testid={`export-download-${row.id}`}
                            href={`/api/v1/exports/${row.id}/download`}
                            onClick={(event) => void download(row.id, event)}
                            className="inline"
                            style={{ gap: 'var(--space-1)' }}
                          >
                            <IconDownload size={14} />
                            {downloading === row.id ? 'preparing…' : 'download ZIP'}
                          </a>
                        ) : (
                          <span className="dim">building…</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            )}
          </Panel>
        </div>

        <div>
          <Panel title="What the bundle claims" enter={2}>
            <blockquote className="quote" style={{ fontSize: 'var(--text-sm)' }}>
              {statement ??
                'The SHA-256 digests in this bundle show that each listed excerpt file is byte-identical to material cut from a source file whose digest LongWatch recorded when it read that file, and that the accompanying custody record has not changed since each entry was written.'}
            </blockquote>
            <p className="dim" style={{ fontSize: 'var(--text-xs)', marginTop: 'var(--space-3)' }}>
              This text ships verbatim in every manifest, in this interface, and in the README. LongWatch makes exactly
              one claim: the listed time ranges deserve examination by a qualified person, and the measured reasons are
              attached.
            </p>
          </Panel>

          <Panel title="Verifying a bundle" enter={3}>
            <p className="dim" style={{ fontSize: 'var(--text-sm)' }}>
              A recipient needs nothing from this deployment — the bundle carries its own key, canonical manifest,
              signature and ledger transcript.
            </p>
            <pre className="mono codeblock">
{`python3 verify_bundle.py export_<utcstamp>`}
            </pre>
            <p className="dim" style={{ fontSize: 'var(--text-xs)' }}>
              Compare the key fingerprint out of band before trusting a bundle: authenticity of the key is not something
              the bundle can prove about itself.
            </p>
          </Panel>

          <Panel title="Failed exports" enter={3}>
            <p className="dim" style={{ fontSize: 'var(--text-sm)' }}>
              A source whose digest changed or went missing fails the export hard, with{' '}
              <span className="mono">SOURCE_DIGEST_MISMATCH</span> — the bundle is never assembled from material the
              ledger cannot vouch for.
            </p>
          </Panel>
        </div>
      </div>
    </>
  );
}
