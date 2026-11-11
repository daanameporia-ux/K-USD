import { describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError, Attempt, querystring } from "./api";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("ApiClient", () => {
  it("sends bearer token and parses JSON", async () => {
    const fetchImpl = vi.fn(async () => json(200, { ok: 1 }));
    const api = new ApiClient({ fetchImpl });
    api.setToken("T1");
    expect(await api.get("/api/x")).toEqual({ ok: 1 });
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer T1");
  });

  it("re-authenticates once on 401 and reuses the idempotency key", async () => {
    const calls: Array<Record<string, string>> = [];
    let n = 0;
    const fetchImpl = vi.fn(async (_url: string, init: RequestInit) => {
      calls.push(init.headers as Record<string, string>);
      n += 1;
      return n === 1 ? json(401, { error: { code: "session_expired", message: "x" } }) : json(200, { id: 7 });
    });
    const reauth = vi.fn(async () => "T2");
    const api = new ApiClient({ fetchImpl: fetchImpl as unknown as typeof fetch, reauth });
    api.setToken("T1");
    const attempt = new Attempt();
    const key = attempt.key;
    expect(await api.post("/api/client/orders", { amount_rub: "1000" }, key)).toEqual({ id: 7 });
    expect(reauth).toHaveBeenCalledTimes(1);
    expect(calls[0].Authorization).toBe("Bearer T1");
    expect(calls[1].Authorization).toBe("Bearer T2");
    expect(calls[0]["Idempotency-Key"]).toBe(key);
    expect(calls[1]["Idempotency-Key"]).toBe(key);
    expect(api.token).toBe("T2");
  });

  it("calls onUnauthorized when re-auth is impossible", async () => {
    const onUnauthorized = vi.fn();
    const api = new ApiClient({
      fetchImpl: (async () => json(401, { error: { code: "unauthorized", message: "Требуется авторизация" } })) as typeof fetch,
      onUnauthorized,
    });
    await expect(api.get("/api/admin/me")).rejects.toMatchObject({ status: 401, code: "unauthorized" });
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("maps error body to ApiError with Russian message", async () => {
    const api = new ApiClient({
      fetchImpl: (async () =>
        json(409, { error: { code: "price_changed", message: "Курс изменился", details: { usdt_amount: "9.40" } } })) as typeof fetch,
    });
    const err = (await api.post("/api/client/orders").catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("Курс изменился");
    expect(err.details.usdt_amount).toBe("9.40");
  });

  it("network failure becomes a friendly error", async () => {
    const api = new ApiClient({ fetchImpl: (async () => { throw new TypeError("Failed to fetch"); }) as typeof fetch });
    await expect(api.get("/api/x")).rejects.toMatchObject({ code: "network_error" });
  });

  it("shares a single re-auth between parallel requests", async () => {
    let authorized = false;
    const fetchImpl = (async (_u: string, init: RequestInit) => {
      const h = init.headers as Record<string, string>;
      return h.Authorization === "Bearer NEW" || authorized ? json(200, {}) : json(401, {});
    }) as unknown as typeof fetch;
    const reauth = vi.fn(async () => {
      authorized = true;
      return "NEW";
    });
    const api = new ApiClient({ fetchImpl, reauth });
    await Promise.all([api.get("/a"), api.get("/b"), api.get("/c")]);
    expect(reauth).toHaveBeenCalledTimes(1);
  });
});

describe("Attempt", () => {
  it("keeps the key until done", () => {
    const a = new Attempt();
    const k = a.key;
    expect(a.key).toBe(k);
    a.done();
    expect(a.key).not.toBe(k);
  });
});

it("querystring skips empty values", () => {
  expect(querystring({ a: 1, b: "", c: null, d: "x y" })).toBe("?a=1&d=x+y");
  expect(querystring({})).toBe("");
});
