#!/usr/bin/env node
// WCAG 2.2 contrast audit of the token layer (DESIGN § 3).
//
// Parses frontend/src/styles/tokens.css, resolves the dark (`:root`) and light
// (`[data-theme='light']`) blocks, composites any rgba() value over the
// surface it sits on, and checks the pairs the interface actually renders:
// normal text at 4.5:1, large text and UI boundaries at 3:1.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const TOKENS = fileURLToPath(new URL('../src/styles/tokens.css', import.meta.url));
const css = readFileSync(TOKENS, 'utf8');

function block(selector) {
  const index = css.indexOf(selector);
  if (index < 0) throw new Error(`selector not found: ${selector}`);
  const open = css.indexOf('{', index);
  const close = css.indexOf('}', open);
  const body = css.slice(open + 1, close);
  const values = {};
  for (const line of body.split('\n')) {
    const match = line.match(/^\s*(--[\w-]+):\s*([^;]+);/);
    if (match) values[match[1]] = match[2].trim();
  }
  return values;
}

const themes = {
  dark: block(':root {'),
  light: block("[data-theme='light']"),
};

function parseColor(value, over) {
  if (value.startsWith('#')) {
    const hex = value.slice(1);
    const full = hex.length === 3 ? hex.split('').map((c) => c + c).join('') : hex;
    return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16));
  }
  const match = value.match(/rgba?\(([^)]+)\)/);
  if (!match) throw new Error(`unparsable colour: ${value}`);
  const parts = match[1].split(',').map((part) => parseFloat(part.trim()));
  const [r, g, b] = parts;
  const alpha = parts.length > 3 ? parts[3] : 1;
  if (alpha >= 1) return [r, g, b];
  const base = over ?? [0, 0, 0];
  return [r, g, b].map((channel, index) => Math.round(channel * alpha + base[index] * (1 - alpha)));
}

function luminance([r, g, b]) {
  const channel = (value) => {
    const v = value / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function ratio(foreground, background) {
  const a = luminance(foreground);
  const b = luminance(background);
  const [light, dark] = a > b ? [a, b] : [b, a];
  return (light + 0.05) / (dark + 0.05);
}

const SURFACES = ['--bg-base', '--bg-elevated', '--bg-overlay', '--bg-inset'];
const TEXT = [
  ['--fg-primary', 4.5],
  ['--fg-secondary', 4.5],
  ['--fg-tertiary', 4.5],
  ['--accent', 4.5],
  ['--status-open', 4.5],
  ['--status-running', 4.5],
  ['--status-paused', 4.5],
  ['--status-halted', 4.5],
  ['--status-complete', 4.5],
  ['--status-failed', 4.5],
  ['--status-orphan', 4.5],
  ['--status-accepted', 4.5],
  ['--status-rejected', 4.5],
  ['--status-flagged', 4.5],
  ['--status-ml-failed', 4.5],
  ['--trace-luma', 3.0],
  ['--trace-motion', 3.0],
  ['--trace-audio', 3.0],
];
// SC 1.4.11 applies to the boundary of an interactive control and to the
// focus indicator.  Panel/row separators are structural hairlines, not the
// sole means of identifying a control, so they are reported as notes.
const BORDERS = [
  ['--border-control', 3.0],
  ['--border-focus', 3.0],
];
const SEPARATORS = ['--border-subtle', '--border-default', '--border-strong'];

let failures = 0;
for (const [theme, tokens] of Object.entries(themes)) {
  console.log(`\n${theme.toUpperCase()}`);
  const resolve = (name, over) => parseColor(tokens[name], over);
  const surfaceColors = Object.fromEntries(SURFACES.map((name) => [name, resolve(name)]));

  for (const [name] of TEXT) {
    const row = [];
    for (const surface of SURFACES) {
      const value = ratio(resolve(name, surfaceColors[surface]), surfaceColors[surface]);
      row.push(`${surface.replace('--bg-', '')} ${value.toFixed(2)}`);
    }
    const worst = Math.min(...SURFACES.map((surface) => ratio(resolve(name, surfaceColors[surface]), surfaceColors[surface])));
    const floor = TEXT.find(([n]) => n === name)[1];
    const ok = worst >= floor;
    if (!ok) failures += 1;
    console.log(`  ${ok ? 'ok  ' : 'FAIL'} ${name.padEnd(20)} ${row.join('  ')}   (floor ${floor})`);
  }

  for (const [name, floor] of BORDERS) {
    const value = ratio(resolve(name), surfaceColors['--bg-elevated']);
    const ok = value >= floor;
    if (!ok) failures += 1;
    console.log(`  ${ok ? 'ok  ' : 'FAIL'} ${name.padEnd(20)} on elevated ${value.toFixed(2)}   (floor ${floor})`);
  }

  for (const name of SEPARATORS) {
    const value = ratio(resolve(name), surfaceColors['--bg-elevated']);
    console.log(`  info ${name.padEnd(20)} on elevated ${value.toFixed(2)}   (structural separator, not a control boundary)`);
  }

  // text drawn on a tinted row or chip must still clear 4.5:1
  for (const [fg, bg] of [['--fg-primary', '--accent-quiet'], ['--fg-secondary', '--accent-quiet']]) {
    const surface = parseColor(tokens[bg], surfaceColors['--bg-elevated']);
    const value = ratio(resolve(fg, surface), surface);
    const ok = value >= 4.5;
    if (!ok) failures += 1;
    console.log(`  ${ok ? 'ok  ' : 'FAIL'} ${fg} on ${bg} ${value.toFixed(2)}   (floor 4.5)`);
  }

  const inkOnAccent = ratio(resolve('--accent-ink'), resolve('--accent'));
  const inkOk = inkOnAccent >= 4.5;
  if (!inkOk) failures += 1;
  console.log(`  ${inkOk ? 'ok  ' : 'FAIL'} --accent-ink on --accent ${inkOnAccent.toFixed(2)}   (floor 4.5)`);

  // status text is also drawn on a tinted chip.  The mapping is the one
  // components.css actually renders (`.chip-*`), so the audit checks the pairs
  // the interface ships — and a token the mapping names but tokens.css does
  // not define is a LOUD failure, never a silent skip: claimed coverage that
  // degrades quietly is worse than no coverage.
  const STATUS_TINTS = [
    ['--status-open', '--status-open-quiet'],
    ['--status-running', '--status-running-quiet'],
    ['--status-paused', '--status-paused-quiet'],
    ['--status-halted', '--status-failed-quiet'],
    ['--status-complete', '--status-complete-quiet'],
    ['--status-accepted', '--status-complete-quiet'],
    ['--status-rejected', '--status-open-quiet'],
    ['--status-flagged', '--status-running-quiet'],
    ['--status-failed', '--status-failed-quiet'],
    ['--status-ml-failed', '--status-failed-quiet'],
    ['--status-orphan', '--status-open-quiet'],
  ];
  for (const [name, quiet] of STATUS_TINTS) {
    if (!tokens[quiet] || !tokens[name]) {
      failures += 1;
      const missing = [!tokens[name] && name, !tokens[quiet] && quiet].filter(Boolean).join(', ');
      console.log(`  FAIL ${name} on ${quiet} — missing token(s): ${missing}`);
      continue;
    }
    const bg = parseColor(tokens[quiet], surfaceColors['--bg-elevated']);
    const value = ratio(resolve(name, bg), bg);
    const ok = value >= 4.5;
    if (!ok) failures += 1;
    console.log(`  ${ok ? 'ok  ' : 'FAIL'} ${name} on ${quiet} ${value.toFixed(2)}   (floor 4.5)`);
  }
}

console.log(`\n${failures === 0 ? 'PASS' : `FAIL — ${failures} pair(s) below floor`}`);
process.exit(failures === 0 ? 0 : 1);
