// Minimal HTTP client for the KnowAct frontend.
//
// Authentication is cookie-based: the backend sets a signed JWT as an
// HTTP-only cookie on login, and the browser returns it automatically. Every
// request therefore uses `credentials: "include"` so the cookie is sent (and
// stored) on both same-origin (Vite proxy) and cross-origin calls. Client
// JavaScript never reads or handles the token directly.

import { cachedGet, canCache, cacheSession, clearApiCache, invalidateApiCache, mutationDependencies } from "./cache";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

// The auth store registers a callback here so the client can react to 401
// responses globally (clear the stored token and let ProtectedRoute redirect
// the user to /login). Individual requests can opt out via
// `skipAuthRedirect` (e.g. the login form, which surfaces 401 inline, and the
// session-restore probe, which handles 401 itself).
let unauthorizedHandler: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  /**
   * When true, a 401 response does NOT trigger the global unauthorized handler.
   * Used by the login request and the session-restore probe, which handle
   * authentication failures themselves.
   */
  skipAuthRedirect?: boolean;
}

/**
 * Perform a JSON request against the backend API.
 *
 * Sends credentials (the HTTP-only auth cookie) with every request so the
 * browser-managed session is applied automatically.
 */
export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  if ((options.method ?? "GET") === "GET" && canCache(path) && options.cache !== "no-store") {
    return cachedGet(path, () => requestJson<T>(path, { ...options, signal: undefined }), options.signal);
  }
  return requestJson<T>(path, options);
}

async function requestJson<T>(path: string, options: RequestOptions): Promise<T> {
  const session = cacheSession();
  const { body, headers, skipAuthRedirect, ...rest } = options;

  const finalHeaders = new Headers(headers);
  finalHeaders.set("Accept", "application/json");

  let payload: BodyInit | undefined;
  if (body !== undefined) {
    finalHeaders.set("Content-Type", "application/json");
    payload = JSON.stringify(body);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...rest,
    credentials: "include",
    headers: finalHeaders,
    body: payload,
  });

  const isJson = response.headers
    .get("Content-Type")
    ?.includes("application/json");
  const data = isJson ? await response.json().catch(() => undefined) : undefined;

  if (!response.ok) {
    if (response.status === 401 && !skipAuthRedirect && session === cacheSession()) {
      clearApiCache();
      unauthorizedHandler?.();
    }
    // Sync/processing endpoints can persist their failure status.
    if (session === cacheSession() && /\/(sync-now|initial-sync|process)$/.test(path)) invalidateApiCache(mutationDependencies(path, options.method));
    throw new ApiError(
      response.status,
      `Request to ${path} failed with status ${response.status}`,
      data,
    );
  }

  if (session === cacheSession() && !["GET", "HEAD"].includes(options.method ?? "GET")) {
    invalidateApiCache(mutationDependencies(path, options.method));
  }
  return data as T;
}

/**
 * Perform a multipart/form-data upload against the backend API.
 *
 * Sends credentials (the HTTP-only auth cookie) and lets the browser set the
 * multipart `Content-Type` boundary automatically. Used for document uploads
 * where a JSON body is not appropriate.
 */
export async function apiUpload<T>(
  path: string,
  form: FormData,
  options: Omit<RequestOptions, "body"> = {},
): Promise<T> {
  const session = cacheSession();
  const { headers, skipAuthRedirect, ...rest } = options;

  const finalHeaders = new Headers(headers);
  finalHeaders.set("Accept", "application/json");
  // Intentionally do NOT set Content-Type: the browser adds the correct
  // multipart boundary for the FormData payload.

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...rest,
    method: rest.method ?? "POST",
    credentials: "include",
    headers: finalHeaders,
    body: form,
  });

  const isJson = response.headers
    .get("Content-Type")
    ?.includes("application/json");
  const data = isJson ? await response.json().catch(() => undefined) : undefined;

  if (!response.ok) {
    if (response.status === 401 && !skipAuthRedirect && session === cacheSession()) {
      clearApiCache();
      unauthorizedHandler?.();
    }
    throw new ApiError(
      response.status,
      `Upload to ${path} failed with status ${response.status}`,
      data,
    );
  }

  if (session === cacheSession()) invalidateApiCache(mutationDependencies(path, options.method));
  return data as T;
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) =>
    apiRequest<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiRequest<T>(path, { ...options, method: "POST", body }),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiRequest<T>(path, { ...options, method: "PATCH", body }),
  del: <T>(path: string, options?: RequestOptions) =>
    apiRequest<T>(path, { ...options, method: "DELETE" }),
  upload: <T>(path: string, form: FormData, options?: RequestOptions) =>
    apiUpload<T>(path, form, options),
};
