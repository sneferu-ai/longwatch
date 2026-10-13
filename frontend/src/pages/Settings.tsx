// Settings — the optional bearer token, the signing key, the ledger, and the
// deployment's live system readout.

import { useCallback, useState } from 'react';
import type { SystemStatus } from '../App';
import { api, describeError, getToken, post, setToken, UnauthorizedError } from '../api';
import { useOnce } from '../hooks';
import { Btn, Chip, CopyButton, Modal, Notice, Panel, useToast } from '../ui/kit';
import { IconAlert, IconKey, IconShield } from '../ui/icons';
import { clock } from '../format';

interface VerifyResult {
  ok: boolean;
  length: number;
  head_hash: string | null;
  first_divergence: number | null;
}

export function Settings({
  status,
  onUnauthorized,
}: {
  status: SystemStatus | null;
  onUnauthorized: () => void;
}) {
  const toast = useToast();
  const [tokenDraft, setTokenDraft] = useState('');
  const [message, setMessage] = useState<{ kind: 'ok' | 'err' | 'warn'; text: string } | null>(null);
  const [verify, setVerify] = useState<VerifyResult | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [confirmPurge, setConfirmPurge] = useState(false);
  const [purging, setPurging] = useState(false);
  const [tokenStored, setTokenStored] = useState(() => Boolean(getToken()));

  // The chain verification is an on-demand walk; the panel reads it once and
  // then only when the operator asks.  It is never polled in the background.
  const ledger = useOnce<VerifyResult>('/api/v1/ledger/verify');

  const handleError = useCallback(
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

  const saveToken = useCallback(() => {
    setToken(tokenDraft.trim());
    setTokenStored(Boolean(tokenDraft.trim()));
    setTokenDraft('');
    setMessage({
      kind: 'ok',
      text: tokenDraft.trim()
        ? 'Token stored in this browser only. It is attached as a header on API calls and as ?token= on the two media routes.'
        : 'Stored token cleared. Media routes fall back to the unset-token deployment behavior.',
    });
  }, [tokenDraft]);

  const runVerify = useCallback(async () => {
    setVerifying(true);
    try {
      const result = await api<VerifyResult>('/api/v1/ledger/verify');
      setVerify(result);
      setMessage(
        result.ok
          ? { kind: 'ok', text: `Chain verified: ${result.length} rows, head ${String(result.head_hash).slice(0, 16)}…` }
          : { kind: 'err', text: `Chain verification FAILED at seq ${result.first_divergence}. Treat every artifact as suspect.` },
      );
    } catch (err) {
      handleError(err);
    } finally {
      setVerifying(false);
    }
  }, [handleError]);

  const purge = useCallback(async () => {
    setPurging(true);
    try {
      const result = await post<{ job_id: string }>('/api/v1/retention/purge');
      setMessage({
        kind: 'ok',
        text: `Purge queued (job ${result.job_id.slice(0, 8)}). Derived artifacts only; each deletion writes a DERIVED_DESTROYED ledger event.`,
      });
      setConfirmPurge(false);
    } catch (err) {
      handleError(err);
    } finally {
      setPurging(false);
    }
  }, [handleError]);

  const chain = verify ?? ledger.data;

  return (
    <>
      <div className="page-head">
        <h1 className="page-title">Settings</h1>
        <p className="page-lede">
          The token is an accident guard, not access control: with it unset, anyone who can reach the port is the
          operator. The signing key and the ledger are the deployment's two durable truths.
        </p>
      </div>

      <div className="cols cols-split">
        <div>
          <Panel title="Optional bearer token" enter={0} stamp={status?.auth.token_required ? 'required' : 'not required'}>
            <p className="dim" style={{ fontSize: 'var(--text-sm)' }}>
              {status?.auth.trust_boundary ??
                'With the token unset, anyone who can reach the port is the operator. It is compared in constant time and never written into the source tree.'}
            </p>
            <div className="field" style={{ marginTop: 'var(--space-3)' }}>
              <label className="field-label" htmlFor="settings-token">
                Token
              </label>
              <div className="inline">
                <input
                  id="settings-token"
                  className="input grow"
                  data-testid="settings-token"
                  type="password"
                  autoComplete="off"
                  placeholder="LONGWATCH_AUTH_TOKEN"
                  value={tokenDraft}
                  onChange={(event) => setTokenDraft(event.target.value)}
                />
                <Btn data-testid="settings-token-save" onClick={saveToken} icon={<IconKey size={14} />}>
                  Save token
                </Btn>
              </div>
              <span className="field-help">
                Stored in this browser's localStorage only. Because a <span className="mono">&lt;video&gt;</span> element
                cannot send headers, the two byte-range media routes also accept{' '}
                <span className="mono">?token=</span>; every response carries{' '}
                <span className="mono">Referrer-Policy: no-referrer</span> so it cannot leak through a referrer.
              </span>
            </div>
            <div className="inline">
              <Chip kind={tokenStored ? 'complete' : 'quiet'}>
                {tokenStored ? 'a token is stored in this browser' : 'no token stored in this browser'}
              </Chip>
            </div>
          </Panel>

          <Panel title="Signing key" enter={1}>
            <p className="dim" style={{ fontSize: 'var(--text-sm)' }}>
              The Ed25519 keypair lives on the data volume (mode 0600) and is generated on first start. Losing it
              invalidates future signatures, never past ones — every past bundle carries its own public key. Compare this
              fingerprint with recipients out of band, before they trust a bundle.
            </p>
            <div className="field" style={{ marginTop: 'var(--space-3)' }}>
              <span className="field-label">SHA-256 fingerprint</span>
              <div className="inline">
                <span className="mono grow" data-testid="key-fingerprint" style={{ overflowWrap: 'anywhere' }}>
                  {status?.key.fingerprint ?? status?.key.error ?? '—'}
                </span>
                <CopyButton value={status?.key.fingerprint ?? ''} label="Copy" size="sm" testId="copy-fingerprint" />
              </div>
            </div>
            {status?.key.public_pem && (
              <details>
                <summary className="dim" style={{ fontSize: 'var(--text-xs)', cursor: 'pointer' }}>
                  public key (PEM)
                </summary>
                <pre className="mono codeblock">{status.key.public_pem}</pre>
              </details>
            )}
          </Panel>
        </div>

        <div>
          <Panel
            title="Ledger"
            enter={1}
            stamp={chain ? `head seq ${chain.length} · ${chain.ok ? 'verified' : 'DIVERGED'}` : 'not yet checked'}
          >
            {chain && !chain.ok && (
              <Notice kind="error" title={`Chain verification failed at seq ${chain.first_divergence}`}>
                Silent ledger corruption is the one failure this product may never have. Every artifact derived after the
                divergence is suspect; restore the data volume from a verified backup.
              </Notice>
            )}
            <div className="inline">
              <Btn
                data-testid="ledger-verify"
                loading={verifying}
                loadingLabel="Verifying…"
                onClick={() => void runVerify()}
                icon={<IconShield size={14} />}
              >
                Verify chain now
              </Btn>
              <Btn
                variant="danger"
                data-testid="retention-purge"
                onClick={() => setConfirmPurge(true)}
                icon={<IconAlert size={14} />}
              >
                Purge derived artifacts
              </Btn>
            </div>
            {chain && (
              <dl className="kv" style={{ marginTop: 'var(--space-3)' }} data-testid="ledger-verify-result">
                <dt>ok</dt>
                <dd>{String(chain.ok)}</dd>
                <dt>rows</dt>
                <dd>{chain.length}</dd>
                <dt>head hash</dt>
                <dd>{String(chain.head_hash).slice(0, 24)}…</dd>
                <dt>first divergence</dt>
                <dd>{chain.first_divergence ?? 'none'}</dd>
                {ledger.updatedAt && (
                  <>
                    <dt>checked</dt>
                    <dd>{clock(new Date(ledger.updatedAt).toISOString())}</dd>
                  </>
                )}
              </dl>
            )}
            <p className="dim" style={{ fontSize: 'var(--text-xs)', marginTop: 'var(--space-2)' }}>
              The chain is re-verified incrementally in the background every{' '}
              <span className="mono">LONGWATCH_LEDGER_VERIFY_S</span> seconds, from a persisted checkpoint. Appends are
              serialized by a transaction-scoped advisory lock; the ledger itself is never trimmed in v0.
            </p>
          </Panel>

          <Panel title="Deployment readout" enter={2}>
            <dl className="kv">
              <dt>database</dt>
              <dd>{status?.database ?? '—'}</dd>
              <dt>detector</dt>
              <dd>
                {status ? (status.model.ok ? `ok · ${status.model.classes ?? '?'} classes` : `down · ${status.model.error ?? ''}`) : '—'}
              </dd>
              <dt>provenance</dt>
              <dd>{status?.model.provenance ?? '—'}</dd>
              <dt>class map</dt>
              <dd>{status ? (status.class_map.ok ? 'valid' : `invalid · ${status.class_map.error ?? ''}`) : '—'}</dd>
              <dt>offline guard</dt>
              <dd>{status ? (status.offline_guard.installed ? 'installed' : 'not installed') : '—'}</dd>
              <dt>sanctioned egress</dt>
              <dd>{status?.offline_guard.sanctioned_egress ?? '—'}</dd>
              <dt>reviewer identity</dt>
              <dd>stamped by configuration on every human action</dd>
            </dl>
            <div style={{ marginTop: 'var(--space-3)' }}>
              {Object.entries(status?.workers ?? {}).map(([name, worker]) => (
                <div className="inline" key={name} style={{ marginBottom: 'var(--space-1)' }}>
                  <Chip kind={worker.alive ? 'complete' : 'failed'}>{name}</Chip>
                  <span className="dim mono">{worker.jobs_processed ?? 0} jobs processed</span>
                </div>
              ))}
            </div>
          </Panel>

          {message && (
            <div data-testid="settings-message">
              <Notice kind={message.kind === 'ok' ? 'ok' : message.kind === 'warn' ? 'warn' : 'error'}>
                {message.text}
              </Notice>
            </div>
          )}
        </div>
      </div>

      <Modal
        open={confirmPurge}
        onClose={() => setConfirmPurge(false)}
        title="Purge derived artifacts?"
        footer={
          <>
            <Btn data-testid="purge-cancel" onClick={() => setConfirmPurge(false)}>Cancel</Btn>
            <Btn
              variant="danger"
              data-testid="purge-confirm"
              loading={purging}
              loadingLabel="Queueing…"
              onClick={() => void purge()}
            >
              Purge derived artifacts
            </Btn>
          </>
        }
      >
        <p>
          This queues a purge job that deletes <strong>LongWatch's own derived artifacts</strong>: review proxies, export
          excerpts and export bundles.
        </p>
        <p className="dim" style={{ fontSize: 'var(--text-sm)' }}>
          It never deletes an original file and never deletes a source row — there is no API or CLI path in v0 that
          deletes a source. Each deletion writes a <span className="mono">DERIVED_DESTROYED</span> ledger event naming
          the artifact, and the ledger itself is never trimmed.
        </p>
      </Modal>
    </>
  );
}
