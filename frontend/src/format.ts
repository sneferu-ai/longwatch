// Formatting.  Every value in this product is an integer in milli-units or
// milliseconds (SPEC §6/§7.1); the UI renders the human unit and keeps the raw
// integer available wherever the raw integer is the evidence.

/** Seconds → `M:SS.d` (or `H:MM:SS.d` past an hour). */
export function timecode(seconds: number | null | undefined, tenths = true): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—';
  const total = Math.max(0, seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = Math.floor(total % 60);
  const frac = tenths ? `.${Math.floor((total * 10) % 10)}` : '';
  const mm = String(minutes).padStart(2, '0');
  const ss = String(secs).padStart(2, '0');
  return hours > 0 ? `${hours}:${mm}:${ss}${frac}` : `${mm}:${ss}${frac}`;
}

/** Seconds → a compact duration phrase with its unit. */
export function duration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—';
  const total = Math.max(0, seconds);
  if (total < 60) return `${total < 10 ? total.toFixed(1) : Math.round(total)}s`;
  if (total < 3600) {
    const minutes = Math.floor(total / 60);
    const secs = Math.round(total % 60);
    return secs ? `${minutes}m ${secs}s` : `${minutes}m`;
  }
  const hours = Math.floor(total / 3600);
  const minutes = Math.round((total % 3600) / 60);
  return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
}

/** An integer in milli-units → its fractional value. */
export function milli(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  const sign = value < 0 ? '-' : '';
  const abs = Math.abs(Math.round(value));
  return `${sign}${Math.floor(abs / 1000)}.${String(abs % 1000).padStart(3, '0')}`;
}

/** Milliseconds → seconds with three decimals (the raw evidence form). */
export function ms(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${(value / 1000).toFixed(3)}`;
}

export function int(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return String(Math.round(value));
}

export function percent(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${Math.round(value)}%`;
}

export function bytes(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size < 10 && index > 0 ? size.toFixed(1) : Math.round(size)} ${units[index]}`;
}

/** An ISO timestamp → the server-local clock reading it records. */
export function clock(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString(undefined, { hour12: false });
}

export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const delta = Math.max(0, Math.round((now - date.getTime()) / 1000));
  if (delta < 5) return 'just now';
  if (delta < 60) return `${delta}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

export function shortId(id: string | null | undefined, length = 8): string {
  if (!id) return '—';
  return id.length <= length ? id : id.slice(0, length);
}

const UNITS: Record<string, string> = {
  _ms: 'ms',
  _milli: 'milli',
  _s: 's',
  _pct: '%',
  _frames: 'frames',
  _bytes: 'B',
};

/** The unit a measured key carries, named explicitly (DESIGN § 9, rule 9). */
export function unitFor(key: string): string {
  for (const [suffix, unit] of Object.entries(UNITS)) {
    if (key.endsWith(suffix)) return unit;
  }
  if (key === 'percentile') return 'percentile';
  if (key === 'samples') return 'samples';
  if (key === 'score') return 'score';
  return '';
}

/** The display value for a measured key, in its human unit. */
export function valueFor(key: string, value: unknown): string {
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (value == null) return '—';
  if (typeof value === 'string') return value;
  if (typeof value !== 'number') return JSON.stringify(value);
  if (key.endsWith('_ms')) return ms(value);
  if (key.endsWith('_milli')) return milli(value);
  if (key.endsWith('_s')) return value.toFixed(3);
  return int(value);
}

/** `motion_mean_milli` → `motion mean`; `p85_milli` → `p85`. */
export function humanKey(key: string): string {
  return key
    .replace(/_(ms|milli|s|pct|frames|bytes)$/, '')
    .replace(/_/g, ' ')
    .trim();
}
