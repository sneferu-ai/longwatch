// Shared UI kit: panel, chip, button, notices, empty/loading states, modal,
// toasts, keycaps.  Every interactive element here carries all six states
// (default / hover / focus-visible / active / disabled / loading) — the CSS
// lives in styles/components.css.

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react';
import { IconCheck, IconCopy, IconFlag, IconOpen, IconX } from './icons';

/* ------------------------------------------------------------------ panel */

export function Panel({
  title,
  stamp,
  actions,
  children,
  flush = false,
  note,
  className = '',
  enter,
  id,
}: {
  title?: React.ReactNode;
  stamp?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  flush?: boolean;
  note?: React.ReactNode;
  className?: string;
  enter?: 0 | 1 | 2 | 3;
  id?: string;
}) {
  const enterClass = enter === undefined ? '' : enter === 0 ? 'lw-enter' : `lw-enter lw-enter-${enter}`;
  return (
    <section className={`panel ${enterClass} ${className}`.trim()} id={id}>
      {(title || actions || stamp) && (
        <header className="panel-head">
          {title && <h2 className="panel-title">{title}</h2>}
          {stamp && <span className="panel-stamp">{stamp}</span>}
          {actions && <div className="toolbar toolbar-end">{actions}</div>}
        </header>
      )}
      <div className={flush ? 'panel-body-flush' : 'panel-body'}>{children}</div>
      {note && <div className="panel-note">{note}</div>}
    </section>
  );
}

/* ------------------------------------------------------------------- chip */

export type ChipKind =
  | 'open'
  | 'accepted'
  | 'rejected'
  | 'flagged'
  | 'running'
  | 'complete'
  | 'failed'
  | 'paused'
  | 'halted'
  | 'orphan'
  | 'ml-failed'
  | 'demo'
  | 'quiet';

export function Chip({
  kind = 'quiet',
  icon,
  children,
  title,
  live = false,
  testId,
}: {
  kind?: ChipKind;
  icon?: React.ReactNode;
  children: React.ReactNode;
  title?: string;
  live?: boolean;
  testId?: string;
}) {
  return (
    <span
      className={`chip chip-${kind}${live ? ' chip-live' : ''}`}
      title={title}
      data-testid={testId}
    >
      {icon && <span className="chip-glyph">{icon}</span>}
      {children}
    </span>
  );
}

const DECISION_ICON: Record<string, React.ReactNode> = {
  open: <IconOpen size={11} />,
  accepted: <IconCheck size={11} />,
  rejected: <IconX size={11} />,
  flagged: <IconFlag size={11} />,
};

/** A decision/state chip that always carries a glyph *and* a word. */
export function StateChip({ state, testId }: { state: string; testId?: string }) {
  const kind: ChipKind = (['open', 'accepted', 'rejected', 'flagged'] as string[]).includes(state)
    ? (state as ChipKind)
    : 'quiet';
  return (
    <Chip kind={kind} icon={DECISION_ICON[state] ?? <IconOpen size={11} />} testId={testId}>
      {state}
    </Chip>
  );
}

/* ----------------------------------------------------------------- button */

type BtnVariant = 'default' | 'primary' | 'danger' | 'ghost';

export function Btn({
  variant = 'default',
  size,
  loading = false,
  loadingLabel,
  disabledReason,
  icon,
  children,
  className = '',
  block = false,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: BtnVariant;
  size?: 'sm';
  loading?: boolean;
  loadingLabel?: string;
  disabledReason?: string;
  icon?: React.ReactNode;
  block?: boolean;
}) {
  const variantClass =
    variant === 'primary' ? 'btn-primary' : variant === 'danger' ? 'btn-danger' : variant === 'ghost' ? 'btn-ghost' : '';
  const disabled = rest.disabled || loading;
  return (
    <button
      {...rest}
      type={rest.type ?? 'button'}
      className={`btn ${variantClass} ${size === 'sm' ? 'btn-sm' : ''} ${block ? 'btn-block' : ''} ${className}`.trim()}
      disabled={disabled}
      aria-busy={loading || undefined}
      title={disabled && disabledReason ? disabledReason : rest.title}
    >
      {icon && !loading && <span aria-hidden="true">{icon}</span>}
      {loading ? (loadingLabel ?? 'Working…') : children}
    </button>
  );
}

/* ---------------------------------------------------------------- notices */

export function Notice({
  kind = 'info',
  title,
  detail,
  children,
}: {
  kind?: 'info' | 'error' | 'warn' | 'ok';
  title?: React.ReactNode;
  detail?: React.ReactNode;
  children?: React.ReactNode;
}) {
  const cls = kind === 'error' ? 'notice-error' : kind === 'warn' ? 'notice-warn' : kind === 'ok' ? 'notice-ok' : '';
  return (
    <div className={`notice ${cls}`.trim()} role={kind === 'error' ? 'alert' : undefined}>
      <div>
        {title && <div className="notice-title">{title}</div>}
        {children}
        {detail && <div className="notice-detail">{detail}</div>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ empty/load */

export function Empty({
  title,
  children,
  action,
}: {
  title: React.ReactNode;
  children?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      {children && <div className="empty-body">{children}</div>}
      {action}
    </div>
  );
}

export function SkeletonRows({ count = 4, height = 'var(--queue-row-h)' }: { count?: number; height?: string }) {
  return (
    <div aria-hidden="true">
      {Array.from({ length: count }).map((_, index) => (
        <div key={index} className="skeleton skeleton-row" style={{ height }} />
      ))}
    </div>
  );
}

export function Progress({ pct, state }: { pct: number | null | undefined; state?: string | null }) {
  // pct null/undefined = genuinely unknown progress: ARIA says an
  // indeterminate progressbar omits aria-valuenow, and the fill animates
  // instead of falsely reading "0%".  The bounds stay declared either way —
  // role="progressbar" without valuemin/valuemax announces imprecisely.
  const indeterminate = pct == null || !Number.isFinite(pct);
  const value = Math.max(0, Math.min(100, Math.round(pct ?? 0)));
  const cls = `${state === 'succeeded' ? 'is-complete' : state === 'failed' ? 'is-failed' : ''}${
    indeterminate ? ' is-indeterminate' : ''
  }`.trim();
  return (
    <div
      className="progress"
      role="progressbar"
      aria-valuenow={indeterminate ? undefined : value}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className={`progress-fill ${cls}`.trim()}
        style={indeterminate ? undefined : { transform: `scaleX(${value / 100})` }}
      />
    </div>
  );
}

/* ---------------------------------------------------------------- keycaps */

export function Keycap({ children }: { children: React.ReactNode }) {
  return <kbd className="keycap">{children}</kbd>;
}

export function Legend({ items }: { items: Array<[React.ReactNode, string]> }) {
  return (
    <div className="legend">
      {items.map(([key, label]) => (
        <span className="legend-item" key={label}>
          <Keycap>{key}</Keycap>
          {label}
        </span>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------- copy button */

export function CopyButton({
  value,
  label = 'Copy',
  size,
  testId,
}: {
  value: string;
  label?: string;
  size?: 'sm';
  testId?: string;
}) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // clipboard API unavailable (insecure context) — fall back to a selection
      const area = document.createElement('textarea');
      area.value = value;
      document.body.appendChild(area);
      area.select();
      document.execCommand('copy');
      area.remove();
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  }, [value]);
  return (
    <Btn
      size={size}
      data-testid={testId}
      onClick={onCopy}
      disabled={!value}
      disabledReason="nothing to copy yet"
      icon={copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
    >
      {copied ? 'Copied' : label}
    </Btn>
  );
}

/* ----------------------------------------------------------------- modal */

export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  labelledBy,
}: {
  open: boolean;
  onClose: () => void;
  title: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
  labelledBy?: string;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const titleId = useId();
  const restoreTo = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    restoreTo.current = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    const focusable = panel?.querySelector<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    focusable?.focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== 'Tab' || !panel) return;
      const items = Array.from(
        panel.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((node) => node.offsetParent !== null || node === document.activeElement);
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      restoreTo.current?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="scrim"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy ?? titleId}
        ref={panelRef}
      >
        <header className="modal-head">
          <h2 className="modal-title" id={labelledBy ?? titleId}>
            {title}
          </h2>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- toasts */

export interface Toast {
  id: number;
  kind: 'ok' | 'err' | 'warn';
  text: string;
  detail?: string;
}

interface ToastApi {
  push: (toast: Omit<Toast, 'id'>) => void;
}

const ToastContext = createContext<ToastApi>({ push: () => undefined });
export const useToast = () => useContext(ToastContext);

export function ToastHost({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const push = useCallback((toast: Omit<Toast, 'id'>) => {
    const id = nextId.current++;
    setToasts((current) => [...current.slice(-3), { ...toast, id }]);
    window.setTimeout(
      () => setToasts((current) => current.filter((entry) => entry.id !== id)),
      toast.kind === 'err' ? 12000 : 5000,
    );
  }, []);

  const api = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-stack" aria-live="polite" aria-atomic="false">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast toast-${toast.kind}`} role={toast.kind === 'err' ? 'alert' : 'status'}>
            <div className="toast-text">
              {toast.text}
              {toast.detail && <span className="toast-detail">{toast.detail}</span>}
            </div>
            <button
              type="button"
              className="icon-btn"
              data-testid="toast-dismiss"
              aria-label="Dismiss"
              onClick={() => setToasts((current) => current.filter((entry) => entry.id !== toast.id))}
            >
              <IconX size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/* ------------------------------------------------------------- segmented */

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
  testId,
}: {
  value: T;
  options: Array<{ value: T; label: string; title?: string }>;
  onChange: (value: T) => void;
  ariaLabel: string;
  testId?: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={ariaLabel} data-testid={testId}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          data-testid={testId ? `${testId}-${option.value}` : undefined}
          aria-pressed={value === option.value}
          title={option.title}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
