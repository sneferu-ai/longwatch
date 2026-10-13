// About — the product's conscience.  The narrow claim, verbatim; the four
// refusals; the review-medium disclosure and its mitigation; the time basis;
// the detector's limits; and what this product deliberately does not do.

import type { SystemStatus } from '../App';
import { Chip, Panel } from '../ui/kit';

export function About({ status }: { status: SystemStatus | null }) {
  const d = status?.disclosures;
  return (
    <>
      <div className="page-head">
        <h1 className="page-title">About</h1>
        <p className="page-lede">
          What this instrument claims, what it refuses, and the exact places where its evidence is weaker than it
          looks.
        </p>
      </div>

      <div className="about-grid">
        <div>
          <Panel title="The claim, verbatim" enter={0}>
            <blockquote className="quote" data-testid="integrity-statement">
              {d?.integrity_statement ??
                'The SHA-256 digests in this bundle show that each listed excerpt file is byte-identical to material cut from a source file whose digest LongWatch recorded when it read that file, and that the accompanying custody record has not changed since each entry was written. They show nothing about what happened to the footage before LongWatch received it, nothing about when it was recorded, and nothing about whether depicted conduct was lawful, humane, or otherwise. LongWatch makes exactly one claim: the listed time ranges deserve examination by a qualified person, and the measured reasons are attached.'}
            </blockquote>
            <p className="dim" style={{ fontSize: 'var(--text-xs)', marginTop: 'var(--space-3)' }}>
              The same text ships in every export manifest and in the README.
            </p>
          </Panel>

          <Panel title="Four refusals, enforced by code" enter={1}>
            <ol className="refusal-list">
              <li>
                <span>
                  <span className="refusal-title">No verdict channel.</span>
                  The scoring layer emits an integer rank and one sentence. No output field anywhere can carry a welfare
                  or legal judgment: the forbidden lexicon lives in one module and is enforced at runtime on every text
                  the system accepts or generates — a pattern template, a decision note, and the rendered sentence
                  before a candidate is persisted.
                </span>
              </li>
              <li>
                <span>
                  <span className="refusal-title">Originals are read-only.</span>
                  Ingested files are opened for reading, streamed through a hasher, and referenced by path. No code path
                  renames, moves, re-encodes, truncates or deletes an original; there is no API or CLI path that deletes
                  a source row.
                </span>
              </li>
              <li>
                <span>
                  <span className="refusal-title">No egress except the database host's DNS.</span>
                  The running container initiates zero outbound connections other than resolving the configured
                  database hostname — the single sanctioned egress. A startup socket guard turns any other connection
                  attempt into a loud crash before a packet leaves the machine.
                </span>
              </li>
              <li>
                <span>
                  <span className="refusal-title">No capture path.</span>
                  No RTSP listener, no device control, no always-on watcher. The only inputs are a browser upload and an
                  operator-invoked scan of one configured directory.
                </span>
              </li>
            </ol>
          </Panel>

          <Panel title="Review medium — disclosed and mitigated" enter={2}>
            <p data-testid="review-medium">
              {d?.review_medium ??
                'Triage decisions are rendered on a lossy working copy, not on the evidence excerpt.'}
            </p>
            <p className="dim" style={{ fontSize: 'var(--text-sm)' }}>
              The gap between the medium you judge on and the medium that ships is real, so it is disclosed in every
              manifest and CSV row <em>and</em> mitigated: press <span className="mono">v</span> in Triage to open the
              source-resolution pane, which streams the original file itself by byte-range. Whether you opened it travels
              into the decision row, the ledger event, the CSV and the manifest as{' '}
              <span className="mono">src_viewed</span>. Recipients should spot-check accepted decisions against the
              excerpts.
            </p>
          </Panel>

          <Panel title="Time basis" enter={2}>
            <p data-testid="time-basis">
              {d?.time_basis ??
                'server-local clock at processing time; not synchronized to any external time authority'}
            </p>
            <p className="dim" style={{ fontSize: 'var(--text-sm)' }}>
              Processing time is never capture time, and the manifest says so. RFC 3161 anchoring requires an online
              authority and is deliberately out of scope for an offline tool.
            </p>
          </Panel>
        </div>

        <div>
          <Panel title="Detector limits" enter={1}>
            <p data-testid="model-limits">
              {d?.model_limits ??
                'The bundled detector was trained on general COCO imagery; detections are coarse ranking inputs, not findings.'}
            </p>
            <dl className="kv" style={{ marginTop: 'var(--space-3)' }}>
              <dt>provenance</dt>
              <dd data-testid="model-provenance">{status?.model.provenance ?? 'unknown'}</dd>
              <dt>classes retained</dt>
              <dd>person, truck, bird, cat, dog, horse, sheep, cow</dd>
              <dt>self-test</dt>
              <dd>{status ? (status.model.ok ? `passed · ${status.model.classes ?? '?'} classes` : 'failed') : '—'}</dd>
            </dl>
            <p className="dim" style={{ fontSize: 'var(--text-xs)', marginTop: 'var(--space-2)' }}>
              The filter set is name-based: a model swap that does not declare a matching class map fails readiness
              closed rather than shifting the ids.
            </p>
          </Panel>

          <Panel title="Non-detection is not absence" enter={2}>
            <p data-testid="person-absent">
              {d?.person_absent ??
                'A window with no person detected is not evidence that no person was present; it is a window the detector did not flag.'}
            </p>
            <p className="dim" style={{ fontSize: 'var(--text-sm)' }}>
              Windows where detection failed are queued explicitly as detection-incomplete and pinned above the ranked
              candidates — the machine never silently skips a span it could not look at.
            </p>
          </Panel>

          <Panel title="Trust boundary" enter={2}>
            <p data-testid="trust-boundary">
              {d?.trust_boundary ?? 'Single-operator offline appliance. No login system; one reviewer identity from configuration.'}
            </p>
            <p className="dim" style={{ fontSize: 'var(--text-sm)' }}>
              Attribution, not access control. The optional bearer token is an accident guard for the case where an
              operator binds to a shared network; it is not an account system.
            </p>
          </Panel>

          <Panel title="What this is not" enter={3}>
            <div className="inline" style={{ gap: 'var(--space-2)' }}>
              <Chip kind="quiet">not case management</Chip>
              <Chip kind="quiet">not a classifier of conduct</Chip>
              <Chip kind="quiet">not a surveillance product</Chip>
              <Chip kind="quiet">no live capture</Chip>
              <Chip kind="quiet">no cloud processing</Chip>
              <Chip kind="quiet">no GPU required</Chip>
            </div>
            <p className="dim" style={{ fontSize: 'var(--text-sm)', marginTop: 'var(--space-3)' }}>
              Named for v1, deliberately not built: independent reviewer accounts with inter-rater statistics,
              object-store ingest, a graphical pattern builder, free-text class naming, push progress, recall reporting
              against hand-labeled sets, and source deletion as an explicit ledgered operation. The schema admits each
              additively.
            </p>
          </Panel>

          <Panel title="Demo data" enter={3}>
            <p className="dim" style={{ fontSize: 'var(--text-sm)' }}>
              The demo fixture is an explicit operator action on the Ingest page — never an environment default. It is
              measured by the same pipeline as real footage and labeled <strong>demo · synthetic</strong> on every
              surface, in the decision ledger, in the CSV and in the manifest.
            </p>
          </Panel>
        </div>
      </div>
    </>
  );
}
