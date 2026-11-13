/** Thin fetch client: bearer token kept in memory only, re-auth on 401, idempotency keys. */

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

export const NETWORK_ERROR_MESSAGE = "Нет связи с сервером. Проверьте интернет и попробуйте ещё раз.";

export function networkError(): ApiError {
  return new ApiError(0, "network_error", NETWORK_ERROR_MESSAGE);
}

export type Reauth = () => Promise<string | null>;

export interface RequestOptions {
  body?: unknown;
  /** Raw file sent as the request body instead of JSON. */
  file?: Blob;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export function newIdempotencyKey(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return "k-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12);
}

export class ApiClient {
  token: string | null = null;
  private reauthInFlight: Promise<string | null> | null = null;

  constructor(
    private readonly opts: {
      base?: string;
      fetchImpl?: typeof fetch;
      reauth?: Reauth;
      onUnauthorized?: () => void;
    } = {},
  ) {}

  setToken(token: string | null): void {
    this.token = token;
  }

  private async reauth(): Promise<string | null> {
    if (!this.opts.reauth) return null;
    if (!this.reauthInFlight) {
      this.reauthInFlight = this.opts.reauth().finally(() => {
        this.reauthInFlight = null;
      });
    }
    const token = await this.reauthInFlight;
    this.token = token;
    return token;
  }

  private async send(method: string, path: string, o: RequestOptions): Promise<Response> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (o.file) headers["Content-Type"] = "application/octet-stream";
    else if (o.body !== undefined) headers["Content-Type"] = "application/json";
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    if (o.idempotencyKey) headers["Idempotency-Key"] = o.idempotencyKey;
    const f = this.opts.fetchImpl ?? fetch.bind(globalThis);
    return f((this.opts.base ?? "") + path, {
      method,
      headers,
      body: o.file ?? (o.body === undefined ? undefined : JSON.stringify(o.body)),
      signal: o.signal,
    });
  }

  /** send() that maps any transport failure (offline, DNS, CORS, abort) to the friendly network ApiError. */
  private async sendSafe(method: string, path: string, o: RequestOptions): Promise<Response> {
    try {
      return await this.send(method, path, o);
    } catch {
      throw networkError();
    }
  }

  async raw(method: string, path: string, o: RequestOptions = {}): Promise<Response> {
    let resp = await this.sendSafe(method, path, o);
    if (resp.status === 401 && !path.startsWith("/api/auth/")) {
      let token: string | null;
      try {
        token = await this.reauth();
      } catch (e) {
        throw e instanceof ApiError ? e : networkError();
      }
      if (token) {
        resp = await this.sendSafe(method, path, o); // same idempotency key on retry
      }
      if (resp.status === 401) this.opts.onUnauthorized?.();
    }
    if (!resp.ok) {
      let code = "http_" + resp.status;
      let message = "Ошибка сервера, попробуйте позже.";
      let details: Record<string, unknown> = {};
      try {
        const data = await resp.json();
        if (data && data.error) {
          code = data.error.code ?? code;
          message = data.error.message ?? message;
          details = data.error.details ?? {};
        }
      } catch {
        /* not JSON */
      }
      throw new ApiError(resp.status, code, message, details);
    }
    return resp;
  }

  async request<T>(method: string, path: string, o: RequestOptions = {}): Promise<T> {
    const resp = await this.raw(method, path, o);
    try {
      return (await resp.json()) as T;
    } catch (e) {
      // Not JSON → server problem; anything else → the body stream broke off (connection lost mid-response).
      if (e instanceof SyntaxError) throw new ApiError(resp.status, "bad_response", "Ошибка сервера, попробуйте позже.");
      throw networkError();
    }
  }

  get<T>(path: string): Promise<T> {
    return this.request<T>("GET", path);
  }
  post<T>(path: string, body?: unknown, idempotencyKey?: string): Promise<T> {
    return this.request<T>("POST", path, { body: body ?? {}, idempotencyKey });
  }
  upload<T>(path: string, file: Blob): Promise<T> {
    return this.request<T>("POST", path, { file });
  }
  patch<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>("PATCH", path, { body });
  }
  del<T>(path: string): Promise<T> {
    return this.request<T>("DELETE", path);
  }
  async blobUrl(path: string): Promise<string> {
    const resp = await this.raw("GET", path);
    let blob: Blob;
    try {
      blob = await resp.blob();
    } catch {
      throw networkError();
    }
    return URL.createObjectURL(blob);
  }
}

/**
 * One idempotency key per user attempt: reused only for resending the identical request (retry after a network
 * failure), renewed after success or as soon as the request parameters change (another amount / address / network).
 */
export class Attempt {
  private current: string | null = null;
  private signature: string | null = null;
  get key(): string {
    if (!this.current) this.current = newIdempotencyKey();
    return this.current;
  }
  /** The key for a request with these parameters: the same key while they stay the same, a new one when they change. */
  keyFor(params: unknown): string {
    const sig = JSON.stringify(params);
    if (sig !== this.signature) {
      this.current = null;
      this.signature = sig;
    }
    return this.key;
  }
  done(): void {
    this.current = null;
    this.signature = null;
  }
}

export function querystring(params: Record<string, string | number | boolean | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  }
  const s = q.toString();
  return s ? "?" + s : "";
}
