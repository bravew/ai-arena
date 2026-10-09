import Ajv2020, { type AnySchema, type ErrorObject, type ValidateFunction } from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import bundleV1Schema from '../../../../docs/bundle-schema-v1.json';
import bundleV2Schema from '../../../../docs/bundle-schema.json';
import eventSchema from '../../../../docs/event-schema.json';
import type { Bundle, RunEvent } from './types';

export interface SchemaIssue {
  /** JSON pointer to the offending value; empty for the document root. */
  path: string;
  message: string;
}

export type Validated<T> = { ok: true; value: T } | { ok: false; issues: SchemaIssue[] };

const ajv = new Ajv2020({ allErrors: true, strict: false });
addFormats(ajv);
const isBundleV1 = ajv.compile<Bundle>(bundleV1Schema as AnySchema);
const isBundleV2 = ajv.compile<Bundle>(bundleV2Schema as AnySchema);
const isEvent = ajv.compile<RunEvent>(eventSchema as AnySchema);

function issueFrom(error: ErrorObject): SchemaIssue {
  const params = error.params as Record<string, unknown>;
  let path = error.instancePath;
  let message = error.message ?? 'does not match the schema';

  // Ajv attaches these errors to the enclosing object; point at the actual offending key/value.
  if (error.keyword === 'required' && typeof params['missingProperty'] === 'string') {
    message = `is missing required property "${params['missingProperty']}"`;
  } else if (error.keyword === 'additionalProperties' && typeof params['additionalProperty'] === 'string') {
    message = `has unexpected property "${params['additionalProperty']}"`;
  } else if (error.keyword === 'enum' && Array.isArray(params['allowedValues'])) {
    message = `must be one of: ${params['allowedValues'].join(', ')}`;
  } else if (error.keyword === 'const' && (params['allowedValue'] === 1 || params['allowedValue'] === 2)) {
    message = `must be version ${String(params['allowedValue'])}`;
  }

  if (error.keyword === 'required' && typeof params['missingProperty'] === 'string') {
    path = `${path}/${escapePointer(params['missingProperty'])}`;
  } else if (error.keyword === 'additionalProperties' && typeof params['additionalProperty'] === 'string') {
    path = `${path}/${escapePointer(params['additionalProperty'])}`;
  }
  return { path, message };
}

function escapePointer(value: string): string {
  return value.replaceAll('~', '~0').replaceAll('/', '~1');
}

function validate<T>(input: unknown, check: ValidateFunction<T>): Validated<T> {
  if (typeof input !== 'object' || input === null || Array.isArray(input)) {
    return { ok: false, issues: [{ path: '', message: 'must be an object' }] };
  }
  if (check(input)) return { ok: true, value: input as T };
  const issues = (check.errors ?? []).map(issueFrom);
  return { ok: false, issues };
}

export function validateBundle(input: unknown): Validated<Bundle> {
  if (typeof input !== 'object' || input === null || Array.isArray(input)) {
    return { ok: false, issues: [{ path: '', message: 'must be an object' }] };
  }
  const version = Reflect.get(input, 'bundle_version');
  if (version === 1) return validate(input, isBundleV1);
  if (version === 2) return validate(input, isBundleV2);
  return {
    ok: false,
    issues: [{ path: '/bundle_version', message: `unsupported bundle version ${String(version)}; this viewer reads versions 1 and 2` }],
  };
}

export function validateEvent(input: unknown): Validated<RunEvent> {
  const version = typeof input === 'object' && input !== null ? Reflect.get(input, 'event_version') : undefined;
  if (version !== undefined && version !== 1) {
    return {
      ok: false,
      issues: [{ path: '/event_version', message: `unsupported event version ${String(version)}; this viewer reads version 1` }],
    };
  }
  return validate(input, isEvent);
}

export function validateEventStream(text: string): Validated<RunEvent[]> {
  const lines = text.split(/\r?\n/).filter((line) => line.trim() !== '');
  const result: RunEvent[] = [];
  const issues: SchemaIssue[] = [];
  lines.forEach((line, index) => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(line);
    } catch (error) {
      issues.push({ path: `/line/${index + 1}`, message: `is not valid JSON: ${errorMessage(error)}` });
      return;
    }
    const event = validateEvent(parsed);
    if (event.ok) result.push(event.value);
    else for (const issue of event.issues) issues.push({ ...issue, path: `/line/${index + 1}${issue.path}` });
  });
  return issues.length === 0 ? { ok: true, value: result } : { ok: false, issues };
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
