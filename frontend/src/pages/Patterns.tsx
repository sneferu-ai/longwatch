// Patterns — versioned rows, edited through the validated API.  An edit
// creates version N+1, is ledgered with the counts of decided candidates
// carried over, and queues a re-score job; the request itself never evaluates
// rules.  Decided candidates keep their version, decision and export
// eligibility forever.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, describeError, put, UnauthorizedError } from '../api';
import { useOnce, usePoll } from '../hooks';
import { grammarFrom, validateDocument, type JsonSchema } from '../patternSchema';
import { Btn, Chip, Empty, Notice, Panel, Progress, SkeletonRows, useToast } from '../ui/kit';
import { percent } from '../format';

interface PatternRow {
  slug: string;
  version: number;
  title: string;
  sentence_template: string;
  rule: unknown;
  weight: number;
  enabled: boolean;
  builtin: boolean;
  is_current: boolean;
  updated_at?: string;
  updated_by?: string;
}

interface StatRow {
  slug: string;
  version: number;
  fired: number;
  accepted: number;
  rejected: number;
  flagged: number;
  precision_estimate: number | null;
}

const POLL_MS = 2000;

export function Patterns({ onUnauthorized }: { onUnauthorized: () => void }) {
  const toast = useToast();
  const patterns = usePoll<{ patterns: PatternRow[] }>('/api/v1/patterns', 5000);
  const stats = usePoll<{ stats: StatRow[] }>('/api/v1/patterns/stats', 5000);
  const [slug, setSlug] = useState<string>('');
  const [draft, setDraft] = useState('');
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ kind: 'ok' | 'err' | 'warn'; text: string } | null>(null);
  const [jobId, setJobId] = useState<string>('');
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const schemaQuery = useOnce<JsonSchema>('/api/v1/patterns/schema');

  const rows = patterns.data?.patterns ?? [];
  const slugs = useMemo(() => Array.from(new Set(rows.map((row) => row.slug))), [rows]);
  const current = useMemo(() => rows.find((row) => row.slug === slug && row.is_current), [rows, slug]);
  const versions = useMemo(() => rows.filter((row) => row.slug === slug).sort((a, b) => b.version - a.version), [rows, slug]);

  useEffect(() => {
    if (!slug && slugs.length) setSlug(slugs[0]);
  }, [slug, slugs]);

  const serialize = useCallback(
    (row: PatternRow) =>
      JSON.stringify(
        {
          title: row.title,
          sentence_template: row.sentence_template,
          rule: row.rule,
          weight: row.weight,
          enabled: row.enabled,
        },
        null,
        2,
      ),
    [],
  );

  // Validate the draft against the *fetched* schema whenever either changes.
  useEffect(() => {
    if (!draft) {
      setProblems([]);
      return;
    }
    try {
      setProblems(validateDocument(JSON.parse(draft), schemaQuery.data));
    } catch {
      setProblems([]); // a parse error is reported by jsonError instead
    }
  }, [draft, schemaQuery.data]);

  useEffect(() => {
    if (current) {
      setDraft(serialize(current));
      setDirty(false);
      setJsonError(null);
    }
    // only re-seed when the selected slug/version changes, never on a poll tick
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current?.slug, current?.version]);

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

  const save = useCallback(async () => {
    let body: unknown;
    try {
      body = JSON.parse(draft);
    } catch (err) {
      setJsonError(err instanceof Error ? err.message : String(err));
      return;
    }
    setJsonError(null);
    const againstSchema = validateDocument(body, schemaQuery.data);
    if (againstSchema.length > 0) {
      setProblems(againstSchema);
      setMessage({
        kind: 'err',
        text: `the document does not satisfy the fetched pattern schema — ${againstSchema.length} problem(s) below; nothing was sent`,
      });
      return;
    }
    setSaving(true);
    setMessage(null);
    try {
      const result = await put<{ version: number; rescore_job_id: string }>(`/api/v1/patterns/${slug}`, body);
      setMessage({
        kind: 'ok',
        text: `version ${result.version} saved and ledgered; re-score job ${result.rescore_job_id.slice(0, 8)} queued. Decided candidates keep their version.`,
      });
      setJobId(result.rescore_job_id);
      await patterns.reload();
    } catch (err) {
      handleError(err);
    } finally {
      setSaving(false);
    }
  }, [draft, slug, patterns, handleError, schemaQuery.data]);

  const [rescore, setRescore] = useState<{ state: string; pct: number; stage: string | null; error?: string | null } | null>(
    null,
  );
  useEffect(() => {
    if (!jobId) return undefined;
    let cancelled = false;
    const tick = async () => {
      try {
        const payload = await api<{ state: string; pct: number; stage: string | null; error?: string | null }>(
          `/api/v1/jobs/${jobId}`,
        );
        if (cancelled) return;
        setRescore(payload);
        // The job is done: stop polling. A terminal state never changes, so
        // the 2 s cadence would only re-set identical state for the life of
        // the page (a new job restarts the effect with its own id).
        if (payload.state === 'succeeded' || payload.state === 'failed') {
          window.clearInterval(timer);
        }
      } catch {
        /* a job lookup is best-effort; the message above already recorded the queueing */
      }
    };
    const timer = window.setInterval(tick, POLL_MS);
    void tick();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [jobId]);

  return (
    <>
      <div className="page-head">
        <h1 className="page-title">Patterns</h1>
        <p className="page-lede">
          Patterns are <strong>versioned rows</strong>. Saving creates version N+1, writes a ledger event with the
          counts of decided candidates carried over, and queues a re-score — the request never evaluates rules. Decided
          candidates keep their version, their decision and their export eligibility forever.
        </p>
      </div>

      <div className="cols cols-main">
        <div>
          <Panel
            title="Editor"
            enter={0}
            stamp={current ? `current v${current.version}${current.builtin ? ' · builtin' : ''}` : 'no pattern selected'}
            note="Templates may name only placeholders this pattern's rule measures; a template naming anything else is refused at write time, and a rendering that would breach the vocabulary is refused before a candidate is persisted."
            actions={
              <label className="inline" style={{ fontSize: 'var(--text-xs)' }}>
                <span className="field-label" style={{ margin: 0 }}>
                  slug
                </span>
                <select
                  className="select"
                  data-testid="pattern-select"
                  value={slug}
                  onChange={(event) => setSlug(event.target.value)}
                  aria-label="Pattern slug"
                >
                  {slugs.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
            }
          >
            {patterns.loading && rows.length === 0 && <SkeletonRows count={4} height="var(--space-8)" />}
            {patterns.error && <Notice kind="error" title={patterns.error.title} detail={patterns.error.detail} />}

            {current && (
              <>
                <div className="inline" style={{ marginBottom: 'var(--space-2)' }}>
                  <Chip kind={current.enabled ? 'complete' : 'quiet'}>
                    {current.enabled ? 'enabled' : 'disabled'}
                  </Chip>
                  <Chip kind="quiet">weight {current.weight}</Chip>
                  {current.builtin && <Chip kind="quiet">builtin</Chip>}
                  <span className="dim" style={{ fontSize: 'var(--text-xs)' }}>
                    {current.title}
                  </span>
                </div>

                <label className="field-label" htmlFor="pattern-rule">
                  Rule document (validated against the fetched schema)
                </label>
                <textarea
                  id="pattern-rule"
                  className="textarea"
                  data-testid="pattern-editor"
                  style={{ minHeight: 'calc(var(--space-16) * 4)' }}
                  spellCheck={false}
                  aria-invalid={jsonError ? true : undefined}
                  value={draft}
                  onChange={(event) => {
                    setDraft(event.target.value);
                    setDirty(true);
                    setJsonError(null);
                  }}
                />
                {jsonError && (
                  <p className="field-error" role="alert">
                    invalid JSON — {jsonError}
                  </p>
                )}
                {!jsonError && problems.length > 0 && (
                  <ul className="field-error" role="alert" style={{ paddingLeft: 'var(--space-4)' }}>
                    {problems.map((problem) => (
                      <li key={problem}>{problem}</li>
                    ))}
                  </ul>
                )}
                {!jsonError && problems.length === 0 && draft && (
                  <p className="field-help">
                    the draft satisfies the fetched schema ({schemaQuery.data ? 'live from /api/v1/patterns/schema' : 'schema unavailable — the service still validates'})
                  </p>
                )}

                <div className="inline" style={{ marginTop: 'var(--space-3)' }}>
                  <Btn
                    variant="primary"
                    data-testid="pattern-save"
                    loading={saving}
                    loadingLabel="Saving…"
                    disabled={!dirty || problems.length > 0}
                    disabledReason={
                      problems.length > 0
                        ? 'the document does not satisfy the fetched pattern schema'
                        : 'the document is unchanged since it was loaded'
                    }
                    onClick={() => void save()}
                  >
                    Save as v{(current.version ?? 0) + 1} and re-score
                  </Btn>
                  <Btn
                    data-testid="pattern-format"
                    onClick={() => {
                      try {
                        setDraft(JSON.stringify(JSON.parse(draft), null, 2));
                        setJsonError(null);
                        setMessage({ kind: 'ok', text: 'document reformatted locally; nothing was sent' });
                      } catch (err) {
                        setJsonError(err instanceof Error ? err.message : String(err));
                      }
                    }}
                  >
                    Format JSON
                  </Btn>
                  <Btn
                    data-testid="pattern-revert"
                    onClick={() => {
                      setDraft(serialize(current));
                      setDirty(false);
                      setJsonError(null);
                      setMessage({ kind: 'warn', text: 'editor reset to the stored version; nothing was sent' });
                    }}
                  >
                    Revert to stored
                  </Btn>
                  <span className="toolbar-end" />
                  {rescore && (
                    <span className="inline" data-testid="rescore-progress">
                      <Progress pct={rescore.pct} state={rescore.state} />
                      <span className="mono">
                        re-score {rescore.state} {rescore.pct}%
                      </span>
                    </span>
                  )}
                </div>

                {message && (
                  <div style={{ marginTop: 'var(--space-3)' }} data-testid="pattern-message">
                    <Notice kind={message.kind === 'ok' ? 'ok' : message.kind === 'warn' ? 'warn' : 'error'}>
                      {message.text}
                    </Notice>
                  </div>
                )}


              </>
            )}
            {!current && !patterns.loading && !patterns.error && (
              <Empty title="No patterns returned">
                The service seeds three builtin patterns idempotently on boot. If this list is empty, the boot sequence
                did not reach the seeding step.
              </Empty>
            )}
          </Panel>

          <Panel title="Version history" enter={1} flush stamp={slug ? `${versions.length} version(s)` : ''}>
            <div className="table-wrap">
            <table className="table" data-testid="pattern-versions">
              <thead>
                <tr>
                  <th>version</th>
                  <th>title</th>
                  <th className="num">weight</th>
                  <th>enabled</th>
                  <th>current</th>
                  <th>updated</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((row) => (
                  <tr key={row.version}>
                    <td className="mono">v{row.version}</td>
                    <td>{row.title}</td>
                    <td className="num">{row.weight}</td>
                    <td>{row.enabled ? 'yes' : 'no'}</td>
                    <td>{row.is_current ? <Chip kind="complete">current</Chip> : <span className="dim">—</span>}</td>
                    <td className="mono dim">{row.updated_at ? `${row.updated_at} · ${row.updated_by ?? ''}` : '—'}</td>
                  </tr>
                ))}
                {versions.length === 0 && (
                  <tr className="table-empty">
                    <td colSpan={6}>select a pattern to see its versions</td>
                  </tr>
                )}
              </tbody>
            </table>
            </div>
          </Panel>
        </div>

        <div>
          <Panel title="Fired and decided" enter={2} stamp="precision estimate, not recall">
            <p className="dim" style={{ fontSize: 'var(--text-sm)' }}>
              precision estimate = accepted ÷ decided. It measures reviewer agreement with the pattern on the spans the
              pattern fired on. It is <strong>not</strong> recall: recall needs hand-labeled negatives, which are v1.
            </p>
            <div className="table-wrap">
            <table className="table" data-testid="pattern-stats">
              <thead>
                <tr>
                  <th>slug</th>
                  <th className="num">v</th>
                  <th className="num">fired</th>
                  <th className="num">acc</th>
                  <th className="num">rej</th>
                  <th className="num">flag</th>
                  <th className="num">prec</th>
                </tr>
              </thead>
              <tbody>
                {(stats.data?.stats ?? []).map((row) => (
                  <tr key={`${row.slug}-${row.version}`}>
                    <td className="mono">{row.slug}</td>
                    <td className="num">{row.version}</td>
                    <td className="num">{row.fired}</td>
                    <td className="num">{row.accepted}</td>
                    <td className="num">{row.rejected}</td>
                    <td className="num">{row.flagged}</td>
                    <td className="num">{row.precision_estimate != null ? percent(row.precision_estimate * 100) : '—'}</td>
                  </tr>
                ))}
                {(stats.data?.stats ?? []).length === 0 && (
                  <tr className="table-empty">
                    <td colSpan={7}>no pattern has fired yet</td>
                  </tr>
                )}
              </tbody>
            </table>
            </div>
          </Panel>

          <Panel title="Rule grammar" enter={3} stamp="from /api/v1/patterns/schema">
            {schemaQuery.data ? (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>condition</th>
                      <th>required</th>
                      <th>fields</th>
                    </tr>
                  </thead>
                  <tbody>
                    {grammarFrom(schemaQuery.data).map((entry) => (
                      <tr key={entry.kind}>
                        <td className="mono">{entry.kind}</td>
                        <td className="mono">{entry.required.filter((name) => name !== 'type').join(', ') || '—'}</td>
                        <td className="dim" style={{ fontSize: 'var(--text-xs)' }}>
                          {entry.fields.map((field) => `${field.name}: ${field.values}`).join(' · ') || '—'}
                        </td>
                      </tr>
                    ))}
                    <tr>
                      <td className="mono">all / any</td>
                      <td className="mono">of</td>
                      <td className="dim" style={{ fontSize: 'var(--text-xs)' }}>
                        of: a non-empty array of conditions
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="dim" style={{ fontSize: 'var(--text-sm)' }}>
                {schemaQuery.loading ? 'loading the schema…' : 'the schema is unavailable — the service still validates every write.'}
              </p>
            )}
            <p className="dim" style={{ fontSize: 'var(--text-xs)', marginTop: 'var(--space-2)' }}>
              The editor checks the draft against this fetched schema before it sends anything; the service validates
              again, and it is the authority.
            </p>
          </Panel>

          <Panel title="How a version change lands" enter={3}>
            <ol className="dim" style={{ fontSize: 'var(--text-sm)', paddingLeft: 'var(--space-4)' }}>
              <li>The API validates the document against the pattern schema and the vocabulary guard.</li>
              <li>
                A new row is inserted at version N+1 with <span className="mono">is_current</span> moved to it; the old
                version stays readable.
              </li>
              <li>A <span className="mono">rescore</span> job re-runs scoring over undecided candidates only.</li>
              <li>
                Every decided candidate keeps its original <span className="mono">pattern_version</span> — which is why
                an accepted decision can never vanish because a rule was tuned.
              </li>
            </ol>
          </Panel>
        </div>
      </div>
    </>
  );
}
