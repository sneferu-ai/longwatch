// A small, honest validator for the pattern editor.
//
// The service publishes the authoritative JSON Schema at GET /api/v1/patterns/schema
// and re-validates every write; this module walks the *fetched* schema so the
// editor can name a mistake before it is sent, without hardcoding a second copy
// of the rule grammar.  Anything it cannot judge is left to the server.

export interface JsonSchema {
  $ref?: string;
  type?: string;
  title?: string;
  required?: string[];
  properties?: Record<string, JsonSchema>;
  definitions?: Record<string, JsonSchema>;
  items?: JsonSchema;
  enum?: unknown[];
  const?: unknown;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  minItems?: number;
  oneOf?: JsonSchema[];
  pattern?: string;
}

export interface Grammar {
  kind: string;
  required: string[];
  fields: Array<{ name: string; values: string }>;
}

function resolve(schema: JsonSchema | undefined, root: JsonSchema): JsonSchema | undefined {
  if (!schema) return undefined;
  if (schema.$ref?.startsWith('#/definitions/')) {
    return root.definitions?.[schema.$ref.slice('#/definitions/'.length)];
  }
  return schema;
}

function describe(spec: JsonSchema): string {
  if (spec.enum) return spec.enum.map((value) => String(value)).join(' · ');
  if (spec.const !== undefined) return String(spec.const);
  if (spec.type === 'number') {
    const bounds = [spec.minimum != null ? `≥ ${spec.minimum}` : '', spec.maximum != null ? `≤ ${spec.maximum}` : '']
      .filter(Boolean)
      .join(' ');
    return bounds ? `number (${bounds})` : 'number';
  }
  if (spec.type === 'array') return `array of ${spec.items ? describe(spec.items) : 'values'}`;
  return spec.type ?? 'value';
}

/** The rule grammar as the service declares it — for the editor's reference pane. */
export function grammarFrom(schema: JsonSchema | null): Grammar[] {
  if (!schema) return [];
  const condition = resolve(schema.properties?.rule, schema);
  const branches = condition?.oneOf ?? [];
  const out: Grammar[] = [];
  for (const branch of branches) {
    const props = branch.properties ?? {};
    const typeSpec = props.type;
    if (typeSpec?.const !== undefined) {
      out.push({
        kind: String(typeSpec.const),
        required: branch.required ?? [],
        fields: Object.entries(props)
          .filter(([name]) => name !== 'type')
          .map(([name, spec]) => ({ name, values: describe(spec) })),
      });
    } else if (typeSpec?.enum) {
      for (const value of typeSpec.enum) {
        out.push({
          kind: String(value),
          required: branch.required ?? [],
          fields: Object.entries(props)
            .filter(([name]) => name !== 'type')
            .map(([name, spec]) => ({ name, values: describe(spec) })),
        });
      }
    }
  }
  return out;
}

function checkCondition(node: unknown, schema: JsonSchema | undefined, root: JsonSchema, path: string): string[] {
  const condition = resolve(schema, root);
  if (!condition) return [];
  if (typeof node !== 'object' || node === null || Array.isArray(node)) {
    return [`${path}: a condition must be an object`];
  }
  const record = node as Record<string, unknown>;
  const branches = condition.oneOf ?? [];
  const composites = branches.filter((branch) => branch.properties?.type?.enum);
  const leaves = branches.filter((branch) => branch.properties?.type?.const !== undefined);

  if (composites.some((branch) => (branch.properties?.type?.enum ?? []).includes(record.type))) {
    const of = record.of;
    if (!Array.isArray(of) || of.length === 0) {
      return [`${path}.of: a composition needs a non-empty array of conditions`];
    }
    return of.flatMap((child, index) => checkCondition(child, condition, root, `${path}.of[${index}]`));
  }

  const branch = leaves.find((candidate) => candidate.properties?.type?.const === record.type);
  if (!branch) {
    const allowed = leaves.map((candidate) => String(candidate.properties?.type?.const)).join(', ');
    return [
      `${path}.type: “${String(record.type ?? '')}” is not a condition type — allowed: ${allowed}`,
    ];
  }
  const problems: string[] = [];
  for (const name of branch.required ?? []) {
    if (record[name] === undefined) problems.push(`${path}.${name}: required by a ${String(record.type)} condition`);
  }
  for (const [name, spec] of Object.entries(branch.properties ?? {})) {
    const value = record[name];
    if (value === undefined) continue;
    if (spec.type === 'number' && typeof value !== 'number') problems.push(`${path}.${name}: must be a number`);
    if (spec.type === 'boolean' && typeof value !== 'boolean') problems.push(`${path}.${name}: must be true or false`);
    if (spec.type === 'string' && typeof value !== 'string') problems.push(`${path}.${name}: must be a string`);
    if (spec.enum && !spec.enum.includes(value)) {
      problems.push(`${path}.${name}: “${String(value)}” is not allowed — use ${describe(spec)}`);
    }
    if (typeof value === 'number' && spec.minimum != null && value < spec.minimum) {
      problems.push(`${path}.${name}: must be ≥ ${spec.minimum}`);
    }
    if (typeof value === 'number' && spec.maximum != null && value > spec.maximum) {
      problems.push(`${path}.${name}: must be ≤ ${spec.maximum}`);
    }
  }
  return problems;
}

/**
 * Validate an editor document against the fetched schema.  `slug` and `version`
 * are supplied by the request path and are deliberately not judged here.
 */
export function validateDocument(document: unknown, schema: JsonSchema | null): string[] {
  if (!schema) return [];
  if (typeof document !== 'object' || document === null || Array.isArray(document)) {
    return ['the document must be a JSON object'];
  }
  const record = document as Record<string, unknown>;
  const problems: string[] = [];
  for (const name of schema.required ?? []) {
    if (name === 'slug' || name === 'version') continue;
    if (record[name] === undefined) problems.push(`${name}: required`);
  }
  for (const [name, spec] of Object.entries(schema.properties ?? {})) {
    if (name === 'slug' || name === 'version') continue;
    const value = record[name];
    if (value === undefined) continue;
    if (spec.type === 'string' && typeof value !== 'string') problems.push(`${name}: must be a string`);
    if (spec.type === 'integer' && !Number.isInteger(value)) problems.push(`${name}: must be a whole number`);
    if (spec.type === 'boolean' && typeof value !== 'boolean') problems.push(`${name}: must be true or false`);
    if (spec.type === 'object' && (typeof value !== 'object' || value === null || Array.isArray(value))) {
      problems.push(`${name}: must be an object`);
    }
    if (typeof value === 'string') {
      if (spec.minLength != null && value.length < spec.minLength) {
        problems.push(`${name}: must be at least ${spec.minLength} character(s)`);
      }
      if (spec.pattern && !new RegExp(spec.pattern).test(value)) {
        problems.push(`${name}: must match ${spec.pattern}`);
      }
    }
    if (typeof value === 'number') {
      if (spec.minimum != null && value < spec.minimum) problems.push(`${name}: must be ≥ ${spec.minimum}`);
      if (spec.maximum != null && value > spec.maximum) problems.push(`${name}: must be ≤ ${spec.maximum}`);
    }
  }
  if (record.rule !== undefined) {
    problems.push(...checkCondition(record.rule, schema.properties?.rule, schema, 'rule'));
  }
  return problems;
}
