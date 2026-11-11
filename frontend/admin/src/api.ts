/** The admin API client. The token lives only in memory (ApiClient.token). */

import { ApiClient, ApiError, querystring } from "@shared/api";
import { initData, insideTelegram } from "@shared/telegram";
import type { AuthResult, Page } from "./types";

type Handlers = { onUnauthorized: () => void };
const handlers: Handlers = { onUnauthorized: () => undefined };

export function setUnauthorizedHandler(fn: () => void): void {
  handlers.onUnauthorized = fn;
}

/** POST /api/auth/telegram with scope "admin". Throws ApiError (403 = not a staff member). */
export async function telegramLogin(): Promise<AuthResult> {
  const res = await api.post<AuthResult>("/api/auth/telegram", { init_data: initData(), scope: "admin" });
  api.setToken(res.token);
  return res;
}

export async function passwordLogin(login: string, password: string, totp: string): Promise<AuthResult> {
  const res = await api.post<AuthResult>("/api/auth/password", { login, password, totp });
  api.setToken(res.token);
  return res;
}

export async function logout(): Promise<void> {
  try {
    if (api.token) await api.post("/api/auth/logout");
  } catch {
    /* the session is dropped locally anyway */
  } finally {
    api.setToken(null);
  }
}

export const api: ApiClient = new ApiClient({
  reauth: async () => {
    // Inside Telegram a fresh initData session is created silently; in a browser the login form is shown.
    if (!insideTelegram()) return null;
    try {
      const res = await api.post<AuthResult>("/api/auth/telegram", { init_data: initData(), scope: "admin" });
      return res.token;
    } catch {
      return null;
    }
  },
  onUnauthorized: () => handlers.onUnauthorized(),
});

export type Query = Record<string, string | number | boolean | null | undefined>;

export function adminPath(path: string, params: Query = {}): string {
  return "/api/admin" + path + querystring(params);
}

export function getPage<T>(path: string, params: Query, cursor: string | null): Promise<Page<T>> {
  return api.get<Page<T>>(adminPath(path, { ...params, cursor: cursor ?? undefined }));
}

export { ApiError };
