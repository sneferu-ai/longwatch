// The API client.  The optional bearer token is stored in localStorage and
// attached as a header on fetches and as ?token= on media URLs (a <video>
// element cannot send headers).  Every response carries
// Referrer-Policy: no-referrer, and the document sets the matching meta tag, so
// the URL-borne token cannot leak through a Referer header.
//
// The accepted-product launcher seeds one versioned bootstrap record at
// localStorage["sneferu.preview.bootstrap.v1"].  When it is present the client
// initialises the same authenticated state the normal login flow uses; when it
// is absent, normal login is unchanged.

const TOKEN_KEY = 'longwatch.token';
const BOOTSTRAP_KEY = 'sneferu.preview.bootstrap.v1';

export interface PreviewBootstrap {
  username?: string;
  role?: string;
  session_token?: string;
  [key: string]: unknown;
}

export function readPreviewBootstrap(): PreviewBootstrap | null {
  try {
    const raw = window.localStorage.getItem(BOOTSTRAP_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    return parsed as PreviewBootstrap;
  } catch {
    return null;
  }
}

export function getToken(): string {
  try {
    return window.localStorage.getItem(TOKEN_KEY) || '';
  } catch {
    return '';
  }
}

export function setToken(token: string): void {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode: the token simply does not persist */
  }
}

export interface PreviewIdentity {
  username: string;
  role: string;
}

let previewIdentity: PreviewIdentity | null = null;

/**
 * Apply the launcher's bootstrap record exactly once, before the first render.
 * Returns the identity for display; the session token becomes the API token the
 * normal login flow uses, so a preview session is authenticated the same way.
 */
export function applyPreviewBootstrap(): PreviewIdentity | null {
  const bootstrap = readPreviewBootstrap();
  if (!bootstrap) return null;
  if (typeof bootstrap.session_token === 'string' && bootstrap.session_token) {
    setToken(bootstrap.session_token);
  }
  const username = typeof bootstrap.username === 'string' ? bootstrap.username : '';
  const role = typeof bootstrap.role === 'string' ? bootstrap.role : '';
  previewIdentity = username || role ? { username, role } : null;
  return previewIdentity;
}

/** The identity resolved at startup, for display only (never a credential). */
export function previewIdentityOf(): PreviewIdentity | null {
  return previewIdentity;
}

export class UnauthorizedError extends Error {}

/** The upload connection died mid-stream; the server discarded the partial file. */
export class UploadDroppedError extends Error {}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const token = getToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (init?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(path, { ...init, headers });
  if (response.status === 401) throw new UnauthorizedError('unauthorized');
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }
  if (!response.ok) throw new ApiError(response.status, (payload as { detail?: unknown })?.detail ?? payload);
  return payload as T;
}

export function mediaUrl(path: string): string {
  const token = getToken();
  return token ? `${path}?token=${encodeURIComponent(token)}` : path;
}

// Bundle downloads are not media routes, so `?token=` is refused there by
// design; the fetch carries the token as a header and the blob becomes the
// download.  The anchor keeps its href for the unset-token deployment.
export async function downloadFile(path: string, filename: string): Promise<void> {
  const headers = new Headers();
  const token = getToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(path, { headers });
  if (response.status === 401) throw new UnauthorizedError('unauthorized');
  if (!response.ok) throw new ApiError(response.status, await response.text());
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  return api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });
}

export function put<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, { method: 'PUT', body: JSON.stringify(body) });
}

/** Upload with real progress; fetch cannot report request-body progress. */
export function uploadFile(
  path: string,
  file: File,
  onProgress: (loaded: number, total: number) => void,
): Promise<{ status: number; body: unknown }> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', path);
    const token = getToken();
    if (token) request.setRequestHeader('Authorization', `Bearer ${token}`);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded, event.total);
    };
    request.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(request.responseText);
      } catch {
        body = request.responseText;
      }
      resolve({ status: request.status, body });
    };
    request.onerror = () => reject(new UploadDroppedError());
    request.onabort = () => reject(new Error('the upload was cancelled'));
    const form = new FormData();
    form.append('file', file, file.name);
    request.send(form);
  });
}

/** Turn any thrown value into a sentence an operator can act on. */
export function describeError(error: unknown): { title: string; detail: string; term?: string } {
  if (error instanceof UploadDroppedError) {
    // what failed, where, and the recovery (DESIGN § 6's error contract)
    return {
      title: 'Upload failed',
      detail:
        'The connection dropped mid-stream, so the partial file was discarded and nothing was registered. Choose the file again to retry.',
    };
  }
  if (error instanceof UnauthorizedError) {
    return { title: 'This deployment requires a bearer token.', detail: 'Enter it below; it is stored in this browser only.' };
  }
  if (error instanceof ApiError) {
    const detail = error.detail as Record<string, unknown> | string | null;
    if (detail && typeof detail === 'object' && typeof detail.term === 'string') {
      return {
        title: typeof detail.error === 'string' ? detail.error : 'Rejected',
        detail: typeof detail.guidance === 'string' ? detail.guidance : 'Describe observables rather than conclusions.',
        term: detail.term,
      };
    }
    if (detail && typeof detail === 'object') {
      const named = typeof detail.error === 'string' ? detail.error : `Request failed (${error.status})`;
      const message = typeof detail.detail === 'string' ? detail.detail : JSON.stringify(detail);
      return { title: named, detail: message };
    }
    return {
      title: `Request failed (${error.status})`,
      detail: typeof detail === 'string' && detail ? detail : 'The server refused the request without a message.',
    };
  }
  if (error instanceof Error) return { title: error.name || 'Error', detail: error.message };
  return { title: 'Error', detail: String(error) };
}
