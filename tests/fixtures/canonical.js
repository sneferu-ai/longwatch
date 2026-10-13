/* Independent canonicalizer — the CANON scenario's cross-language leg.
 *
 * Deliberately written from the specification's rule (§7.1) rather than ported
 * from the Python implementation: object keys sorted by code point, no
 * whitespace between tokens, `,` and `:` separators, and whatever the host
 * language's default JSON string escaping produces.  If the Python and
 * JavaScript implementations disagree by one byte, the CANON scenario fails.
 *
 *   node tests/fixtures/canonical.js tests/fixtures/canonical_vectors.json
 */
'use strict';

function canonicalize(value) {
  if (value === null || typeof value !== 'object') {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return '[' + value.map(canonicalize).join(',') + ']';
  }
  const keys = Object.keys(value).sort();
  return '{' + keys.map((key) => JSON.stringify(key) + ':' + canonicalize(value[key])).join(',') + '}';
}

function main(argv) {
  const fs = require('fs');
  const path = argv[2];
  if (!path) {
    console.error('usage: node canonical.js <vectors.json>');
    process.exit(2);
  }
  const document = JSON.parse(fs.readFileSync(path, 'utf8'));
  let failures = 0;
  for (const vector of document.vectors) {
    const produced = canonicalize(vector.value);
    if (produced !== vector.canonical) {
      failures += 1;
      console.error(`FAIL ${vector.name}`);
      console.error(`  expected ${JSON.stringify(vector.canonical)}`);
      console.error(`  produced ${JSON.stringify(produced)}`);
    } else {
      console.log(`ok ${vector.name}`);
    }
  }
  if (failures) {
    console.error(`${failures} of ${document.vectors.length} vectors diverged`);
    process.exit(1);
  }
  console.log(`all ${document.vectors.length} vectors reproduce byte-exactly under Node`);
  return 0;
}

if (require.main === module) {
  process.exit(main(process.argv));
}

module.exports = { canonicalize };
