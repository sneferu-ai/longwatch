// The shell: header (brand, nav, service readout, theme), the persistent
// model-limits disclosure, the token banner, and the page switch.
//
// Pages are addressed by hash so a reload keeps the operator where they were.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, setToken, previewIdentityOf, UnauthorizedError, describeError } from './api';
import { registerUnauthorizedListener } from './hooks';
import { ToastHost, Btn, Chip } from './ui/kit';
import { MarkLongWatch, IconMoon, IconSun, IconKey } from './ui/icons';
import { Ingest } from './pages/Ingest';
import { Triage } from './pages/Triage';
import { Patterns } from './pages/Patterns';
import { Exports } from './pages/Exports';
import { Settings } from './pages/Settings';
import { About } from './pages/About';

export interface Disclosures {
  integrity_statement: string;
  time_basis: string;
  review_medium: string;
  model_limits: string;
  person_absent: string;
  trust_boundary: string;
}

export interface SystemStatus {
  database: string;
  model: { ok: boolean; classes?: number; error?: string; provenance?: string };
  class_map: { ok: boolean; classes?: number; error?: string };
  workers: Record<string, { alive?: boolean; jobs_processed?: number; last_error?: string | null }>;
  offline_guard: { installed: boolean; database_host: string | null; sanctioned_egress: string };
  ml_failed_count: number;
  key: { fingerprint?: string; public_pem?: string; error?: string };
  auth: { token_required: boolean; trust_boundary: string };
  disclosures: Disclosures;
  export_defaults?: { blur_faces: boolean; top_n: number; states: string[] };
}

const PAGES: Array<{ id: string; label: string }> = [
  { id: 'ingest', label: 'Ingest' },
  { id: 'triage', label: 'Triage' },
  { id: 'patterns', label: 'Patterns' },
  { id: 'exports', label: 'Exports' },
  { id: 'settings', label: 'Settings' },
  { id: 'about', label: 'About' },
];

const THEME_KEY = 'longwatch.theme';

function pageFromHash(): string {
  const raw = window.location.hash.replace(/^#\/?/, '');
  return PAGES.some((page) => page.id === raw) ? raw : 'ingest';
}

function readTheme(): 'dark' | 'light' {
  const stored = document.documentElement.dataset.theme;
  if (stored === 'light' || stored === 'dark') return stored;
  try {
    const saved = window.localStorage.getItem(THEME_KEY);
    if (saved === 'light' || saved === 'dark') return saved;
  } catch {
    /* private mode */
  }
  return 'dark';
}

export default function App() {
  const [page, setPage] = useState<string>(pageFromHash);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [statusError, setStatusError] = useState<{ title: string; detail: string } | null>(null);
  const [unauthorized, setUnauthorized] = useState(false);
  const [tokenDraft, setTokenDraft] = useState('');
  const [theme, setTheme] = useState<'dark' | 'light'>(readTheme);
  const identity = previewIdentityOf();

  const onUnauthorized = useCallback(() => setUnauthorized(true), []);
  // Registration is an effect, not a render side effect: StrictMode double
  // render stays pure, and unmount releases the handler.
  useEffect(() => {
    registerUnauthorizedListener(onUnauthorized);
    return () => registerUnauthorizedListener(null);
  }, [onUnauthorized]);

  // hash routing
  useEffect(() => {
    const onHash = () => setPage(pageFromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const go = useCallback((id: string) => {
    window.location.hash = `#/${id}`;
    setPage(id);
    window.scrollTo({ top: 0 });
  }, []);

  // the service readout: polled every 5 s
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const payload = await api<SystemStatus>('/api/v1/system/status');
        if (!cancelled) {
          setStatus(payload);
          setStatusError(null);
          setUnauthorized(false);
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof UnauthorizedError) setUnauthorized(true);
        else setStatusError(describeError(err));
      }
    };
    void load();
    const timer = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [unauthorized]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      window.localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* private mode: the choice simply does not persist */
    }
  }, [theme]);

  const submitToken = useCallback(() => {
    setToken(tokenDraft.trim());
    setTokenDraft('');
    setUnauthorized(false);
  }, [tokenDraft]);

  const serviceState = useMemo(() => {
    if (!status) return { db: 'unknown', model: 'unknown' };
    return {
      db: status.database === 'ok' ? 'ok' : status.database,
      model: status.model.ok ? 'ok' : 'down',
    };
  }, [status]);

  const pageNode = useMemo(() => {
    switch (page) {
      case 'triage':
        return <Triage onUnauthorized={onUnauthorized} />;
      case 'patterns':
        return <Patterns onUnauthorized={onUnauthorized} />;
      case 'exports':
        return <Exports status={status} onUnauthorized={onUnauthorized} />;
      case 'settings':
        return <Settings status={status} onUnauthorized={onUnauthorized} />;
      case 'about':
        return <About status={status} />;
      case 'ingest':
      default:
        return <Ingest status={status} onUnauthorized={onUnauthorized} onGoToTriage={() => go('triage')} />;
    }
  }, [page, status, onUnauthorized, go]);

  return (
    <ToastHost>
      <div className="shell">
        <header className="app-header">
          <span className="brand" data-testid="brand">
            <MarkLongWatch size={24} className="brand-mark" />
            LongWatch
            <span className="brand-sub">offline footage triage</span>
          </span>
          <nav className="nav" data-testid="nav" aria-label="Sections">
            {PAGES.map((entry) => (
              <button
                key={entry.id}
                type="button"
                data-testid={`nav-${entry.id}`}
                aria-current={page === entry.id ? 'page' : undefined}
                onClick={() => go(entry.id)}
              >
                {entry.label}
                {entry.id === 'triage' && (status?.ml_failed_count ?? 0) > 0 && (
                  <span className="nav-count" title={`${status?.ml_failed_count} windows with failed detection`}>
                    {status?.ml_failed_count}
                  </span>
                )}
              </button>
            ))}
          </nav>
          <div className="header-end">
            <span className="readout" data-testid="service-state" title="polled every 5 s">
              <span className="readout-item">
                <span
                  className={`lamp ${status?.database === 'ok' ? 'lamp-ok' : status ? 'lamp-bad' : ''}`}
                  aria-hidden="true"
                />
                db {serviceState.db}
              </span>
              <span className="readout-item">
                <span className={`lamp ${status?.model.ok ? 'lamp-ok' : status ? 'lamp-bad' : ''}`} aria-hidden="true" />
                model {serviceState.model}
              </span>
              {status && !status.offline_guard.installed && (
                <span className="readout-item" title="the offline guard is not installed in this process">
                  <span className="lamp lamp-warn" aria-hidden="true" />
                  guard off
                </span>
              )}
              {identity?.username && (
                <span className="readout-item" title={identity.role ? `role ${identity.role}` : undefined}>
                  <IconKey size={12} />
                  {identity.username}
                </span>
              )}
            </span>
            <button
              type="button"
              className="icon-btn"
              data-testid="theme-toggle"
              aria-label={theme === 'dark' ? 'Switch to the light theme' : 'Switch to the dark theme'}
              title={theme === 'dark' ? 'Light theme' : 'Dark theme'}
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            >
              {theme === 'dark' ? <IconSun size={16} /> : <IconMoon size={16} />}
            </button>
          </div>
        </header>

        <div className="disclosure" data-testid="model-limits-disclosure">
          <span className="disclosure-glyph" aria-hidden="true">
            ●
          </span>
          <span>
            {status?.disclosures.model_limits ??
              'The bundled detector was trained on general COCO imagery; detections are coarse ranking inputs, not findings.'}
          </span>
        </div>

        {unauthorized && (
          <div className="banner" data-testid="token-banner">
            <span className="banner-text">
              This deployment requires a bearer token. It is stored in this browser only and attached as a header on API
              calls and as <span className="mono">?token=</span> on the two media routes.
            </span>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                submitToken();
              }}
            >
              <input
                data-testid="token-input"
                className="input"
                type="password"
                aria-label="Bearer token"
                placeholder="LONGWATCH_AUTH_TOKEN"
                value={tokenDraft}
                onChange={(event) => setTokenDraft(event.target.value)}
              />
              <Btn type="submit" variant="primary" data-testid="token-submit">
                Use token
              </Btn>
            </form>
          </div>
        )}

        {statusError && !unauthorized && (
          <div className="banner banner-warn" data-testid="error-banner" role="alert">
            <span className="banner-text">
              <strong>{statusError.title}.</strong> {statusError.detail} The interface keeps polling; readings below may
              be stale.
            </span>
            <Chip kind="failed">status unreachable</Chip>
          </div>
        )}

        <main className="page">{pageNode}</main>
      </div>
    </ToastHost>
  );
}
