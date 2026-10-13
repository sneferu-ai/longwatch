// The trace strip — the product's signature data surface.
//
// Two elements: the local strip (the current window plus its context, with the
// motion and luma series, audio events and the playhead) and the overview strip
// (the whole source, with every candidate span marked).  Both render real
// measurements from /sources/{id}/signals and /sources/{id}/timeline; nothing
// here is synthesized.
//
// Both SVG viewBoxes track the measured pixel width of their container, so one
// user unit is one pixel and nothing is stretched — including the axis labels.
//
// The local strip is an accessible slider: it is focusable, arrow keys seek by
// one second, Home/End jump to the ends, and the video element's own controls
// remain the primary path.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { timecode } from '../format';

export interface StripEvent {
  kind: string;
  t_start: number | null;
  t_end: number | null;
}

export interface StripWindow {
  start_s: number;
  end_s: number;
}

/** The measured pixel width of a container, updated on resize. */
function useMeasuredWidth(fallback = 960): [React.MutableRefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    const measure = () => {
      const next = Math.max(120, Math.round(node.getBoundingClientRect().width));
      setWidth((current) => (Math.abs(current - next) > 1 ? next : current));
    };
    measure();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

function seriesPath(values: number[], from: number, to: number, width: number, height: number, maxValue: number): string {
  if (!values.length) return '';
  const span = Math.max(0.001, to - from);
  const top = 4;
  const usable = height - top - 14;
  const points: string[] = [];
  const step = Math.max(1, Math.ceil(values.length / width));
  for (let index = 0; index < values.length; index += step) {
    const t = from + index;
    if (t < from || t > to) continue;
    const x = ((t - from) / span) * width;
    const ratio = maxValue > 0 ? Math.min(1, values[index] / maxValue) : 0;
    const y = height - 14 - ratio * usable;
    points.push(`${x.toFixed(1)},${y.toFixed(1)}`);
  }
  return points.length ? `M${points.join('L')}` : '';
}

function maxOf(values: number[] | undefined): number {
  if (!values || !values.length) return 0;
  let max = 0;
  for (const value of values) if (value > max) max = value;
  return max;
}

function gridTicks(from: number, to: number, width: number): number[] {
  const span = to - from;
  const target = Math.max(2, Math.floor(width / 110));
  const candidates = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 1800];
  const step = candidates.find((value) => span / value <= target) ?? 3600;
  const ticks: number[] = [];
  for (let t = Math.ceil(from / step) * step; t <= to; t += step) ticks.push(t);
  return ticks;
}

export function TraceStrip({ from, to, window, series, events, playhead, onSeek, label }: {
  from: number;
  to: number;
  window: StripWindow;
  series: { motion?: number[]; luma?: number[] } | null;
  events: StripEvent[];
  playhead?: number | null;
  onSeek?: (seconds: number) => void;
  label: string;
}) {
  const height = 88;
  const [wrapRef, width] = useMeasuredWidth();
  const svgRef = useRef<SVGSVGElement | null>(null);

  const motion = series?.motion ?? [];
  const luma = series?.luma ?? [];
  const motionMax = Math.max(1, maxOf(motion));
  const lumaMax = Math.max(1, maxOf(luma));

  const ticks = useMemo(() => gridTicks(from, to, width), [from, to, width]);
  const span = Math.max(0.001, to - from);
  const xOf = useCallback((t: number) => ((t - from) / span) * width, [from, span, width]);

  const audioEvents = events.filter((event) => event.kind === 'silence_gap' || event.kind === 'loudness_spike');

  const seek = useCallback(
    (clientX: number) => {
      if (!onSeek || !svgRef.current) return;
      const box = svgRef.current.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (clientX - box.left) / Math.max(1, box.width)));
      onSeek(from + ratio * span);
    },
    [onSeek, from, span],
  );

  const interactive = Boolean(onSeek);
  const sliderProps = interactive
    ? {
        tabIndex: 0,
        role: 'slider' as const,
        'aria-label': `${label} — seek`,
        'aria-valuemin': Math.round(from),
        'aria-valuemax': Math.round(to),
        'aria-valuenow': Math.round(playhead ?? window.start_s),
        'aria-valuetext': timecode(playhead ?? window.start_s),
        onKeyDown: (event: React.KeyboardEvent) => {
          const current = playhead ?? window.start_s;
          if (event.key === 'ArrowLeft') {
            event.preventDefault();
            onSeek?.(Math.max(from, current - 1));
          } else if (event.key === 'ArrowRight') {
            event.preventDefault();
            onSeek?.(Math.min(to, current + 1));
          } else if (event.key === 'Home') {
            event.preventDefault();
            onSeek?.(from);
          } else if (event.key === 'End') {
            event.preventDefault();
            onSeek?.(to);
          }
        },
        onClick: (event: React.MouseEvent) => seek(event.clientX),
      }
    : { role: 'img' as const, 'aria-label': label };

  return (
    <div className="strip" ref={wrapRef}>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${width} ${height}`}
        {...sliderProps}
        style={interactive ? { cursor: 'crosshair' } : undefined}
      >
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={xOf(tick)} x2={xOf(tick)} y1={0} y2={height - 12} stroke="var(--trace-grid)" strokeWidth={1} />
            <text x={Math.min(width - 34, xOf(tick) + 4)} y={height - 3} fill="var(--fg-tertiary)">
              {timecode(tick, false)}
            </text>
          </g>
        ))}

        <rect
          x={xOf(window.start_s)}
          y={0}
          width={Math.max(1, xOf(window.end_s) - xOf(window.start_s))}
          height={height - 12}
          fill="var(--trace-presence)"
        />
        <line x1={xOf(window.start_s)} x2={xOf(window.start_s)} y1={0} y2={height - 12} stroke="var(--trace-window)" strokeWidth={1} />
        <line x1={xOf(window.end_s)} x2={xOf(window.end_s)} y1={0} y2={height - 12} stroke="var(--trace-window)" strokeWidth={1} />

        {audioEvents.map((event, index) => {
          if (event.t_start == null) return null;
          const start = xOf(event.t_start);
          const end = xOf(event.t_end ?? event.t_start + 1);
          if (event.kind === 'silence_gap') {
            return (
              <rect key={`sil-${index}`} x={start} y={height - 12} width={Math.max(1, end - start)} height={5}
                    fill="var(--trace-audio)" opacity={0.55} />
            );
          }
          return (
            <line key={`spike-${index}`} x1={start} x2={start} y1={height - 12} y2={height - 4}
                  stroke="var(--trace-audio)" strokeWidth={2} />
          );
        })}

        <path d={seriesPath(luma, from, to, width, height, lumaMax)} stroke="var(--trace-luma)" strokeWidth={1} fill="none" />
        <path d={seriesPath(motion, from, to, width, height, motionMax)} stroke="var(--trace-motion)" strokeWidth={1.5} fill="none" />

        {playhead != null && Number.isFinite(playhead) && (
          <line x1={xOf(playhead)} x2={xOf(playhead)} y1={0} y2={height - 12}
                stroke="var(--trace-playhead)" strokeWidth={1} strokeDasharray="3 3" />
        )}
      </svg>
    </div>
  );
}

/* ------------------------------------------------------------- overview */

export interface OverviewWindow {
  id: string;
  start_s: number;
  end_s: number;
  ml_state: string | null;
}

export interface OverviewCandidate {
  id: string;
  score: number;
  start_s: number;
  end_s: number;
}

export function OverviewStrip({ duration, windows, candidates, current, onSeek }: {
  duration: number;
  windows?: OverviewWindow[];
  candidates?: OverviewCandidate[];
  current: StripWindow;
  onSeek?: (seconds: number) => void;
}) {
  const marks = windows ?? [];
  const spans = candidates ?? [];
  const height = 24;
  const [wrapRef, width] = useMeasuredWidth();
  const span = Math.max(0.001, duration);
  const xOf = (t: number) => (t / span) * width;
  const interactive = Boolean(onSeek);
  return (
    <div className="overview" ref={wrapRef}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`Source overview: ${marks.length} windows, ${spans.length} candidates, ${timecode(duration, false)} total`}
        onClick={
          interactive
            ? (event) => {
                const box = (event.currentTarget as SVGSVGElement).getBoundingClientRect();
                const ratio = Math.max(0, Math.min(1, (event.clientX - box.left) / Math.max(1, box.width)));
                onSeek?.(ratio * span);
              }
            : undefined
        }
        style={interactive ? { cursor: 'crosshair' } : undefined}
      >
        <rect x={0} y={0} width={width} height={height} fill="var(--bg-inset)" />
        {marks.map((win) => (
          <rect
            key={win.id}
            x={xOf(win.start_s)}
            y={win.ml_state === 'failed' ? 1 : 4}
            width={Math.max(1, xOf(win.end_s) - xOf(win.start_s))}
            height={win.ml_state === 'failed' ? height - 2 : height - 8}
            fill={win.ml_state === 'failed' ? 'var(--status-ml-failed)' : 'var(--trace-grid)'}
            opacity={win.ml_state === 'failed' ? 0.9 : 0.75}
          />
        ))}
        {spans.map((candidate) => (
          <rect
            key={candidate.id}
            x={xOf(candidate.start_s)}
            y={height - 4 - (Math.max(0, Math.min(100, candidate.score)) / 100) * (height - 8)}
            width={Math.max(1, xOf(candidate.end_s) - xOf(candidate.start_s))}
            height={2}
            fill="var(--accent)"
          />
        ))}
        <rect
          x={xOf(current.start_s)}
          y={0}
          width={Math.max(1, xOf(current.end_s) - xOf(current.start_s))}
          height={height}
          fill="none"
          stroke="var(--fg-primary)"
          strokeWidth={1}
        />
      </svg>
    </div>
  );
}
