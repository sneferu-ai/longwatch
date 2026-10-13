#!/usr/bin/env node
// The closed-token audit (DESIGN § 10).
//
// Every colour, size and duration in this application must come from
// frontend/src/styles/tokens.css.  This walks frontend/src, skips the token
// file itself, and fails on any hex colour, rgb()/rgba() literal, px value or
// time duration found anywhere else.
//
// It also enforces the motion contract (DESIGN § 7): transitions and keyframes
// animate `transform` and `opacity` (and paint-only properties such as colour
// and background) — never geometry (`width`, `height`, `top`, `left`, `all`,
// and their min/max/right/bottom variants).  The progress fill is the worked
// example: its fraction lives in `transform: scaleX()`.
//
// One documented exception: media-query conditions, because CSS custom
// properties cannot be used inside them.  Those lines are reported as notes.

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const SRC = join(ROOT, 'src');
const TOKENS = join(SRC, 'styles', 'tokens.css');

const ALLOWED = [
  '0.01ms', // the prefers-reduced-motion collapse
];

const GEOMETRY = new Set([
  'all',
  'width',
  'height',
  'top',
  'left',
  'right',
  'bottom',
  'min-width',
  'max-width',
  'min-height',
  'max-height',
]);

function walk(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else out.push(full);
  }
  return out;
}

/** Forbidden geometry property animated by a `transition`/`transition-property` declaration. */
function transitionViolations(text) {
  const hits = [];
  const re = /transition(?:-property|Property)?\s*:\s*([^;}]*)/g;
  for (const match of text.matchAll(re)) {
    const value = match[1].replace(/!important/g, '');
    for (const part of value.split(',')) {
      const raw = part.trim().replace(/^['"`]/, '');
      const property = (raw.match(/^[a-z-]+/i) ?? [''])[0].toLowerCase();
      if (GEOMETRY.has(property)) {
        hits.push(`transition animates ${property} — geometry never animates (DESIGN § 7)`);
      }
    }
  }
  return hits;
}

/** Forbidden geometry declarations inside a @keyframes block. */
function keyframeViolations(text) {
  const hits = [];
  const re = /@keyframes\s+[A-Za-z0-9_-]+\s*\{/g;
  for (const match of text.matchAll(re)) {
    let depth = 1;
    let i = match.index + match[0].length;
    while (i < text.length && depth > 0) {
      if (text[i] === '{') depth += 1;
      else if (text[i] === '}') depth -= 1;
      i += 1;
    }
    const block = text.slice(match.index, i);
    for (const decl of block.matchAll(/(?:^|[;{])\s*([a-z-]+)\s*:/g)) {
      const property = decl[1].toLowerCase();
      if (GEOMETRY.has(property)) {
        hits.push(`@keyframes animates ${property} — geometry never animates (DESIGN § 7)`);
      }
    }
  }
  return hits;
}

const RULES = [
  { name: 'hex colour', pattern: /#[0-9a-fA-F]{3,8}\b/g, files: ['.css', '.ts', '.tsx', '.html'] },
  { name: 'rgb()/rgba() literal', pattern: /\brgba?\(/g, files: ['.css', '.ts', '.tsx'] },
  { name: 'px value', pattern: /(?<![\w.-])\d+(?:\.\d+)?px\b/g, files: ['.css', '.ts', '.tsx'] },
  { name: 'time duration', pattern: /(?<![\w.-])\d+(?:\.\d+)?m?s\b/g, files: ['.css'] },
];

const problems = [];
const notes = [];

for (const file of walk(SRC)) {
  const rel = relative(ROOT, file);
  if (file === TOKENS) continue;
  const ext = file.slice(file.lastIndexOf('.'));
  const text = readFileSync(file, 'utf8');
  const lines = text.split('\n');
  lines.forEach((line, index) => {
    for (const rule of RULES) {
      if (!rule.files.includes(ext)) continue;
      const matches = line.match(rule.pattern);
      if (!matches) continue;
      for (const match of matches) {
        if (ALLOWED.includes(match)) continue;
        const inMediaQuery = /@media/.test(line) && rule.name === 'px value';
        if (inMediaQuery) {
          notes.push(`${rel}:${index + 1} ${rule.name} in a media-query condition: ${match}`);
        } else {
          problems.push(`${rel}:${index + 1} ${rule.name} outside tokens.css: ${match}  →  ${line.trim()}`);
        }
      }
    }
  });
  if (['.css', '.ts', '.tsx'].includes(ext)) {
    for (const [finder, label] of [
      [transitionViolations, 'transition'],
      [keyframeViolations, 'keyframes'],
    ]) {
      for (const hit of finder(text)) {
        problems.push(`${rel} motion ${label}: ${hit}`);
      }
    }
  }
}

for (const note of notes) console.log(`note: ${note}`);
if (problems.length) {
  console.log(`\nFAIL — ${problems.length} raw value(s) outside tokens.css`);
  for (const problem of problems) console.log(`  - ${problem}`);
  process.exit(1);
}
console.log(
  `PASS — no raw colour, size or duration outside tokens.css, motion contract holds ` +
    `(transitions/keyframes animate transform/opacity/colour only) (${notes.length} media-query exception(s))`,
);
