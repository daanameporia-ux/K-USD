// Smoke tests on the built apps with a fake Telegram.WebApp and a mocked API.
// Uses the preinstalled Playwright/Chromium (do not run `playwright install`).
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { preview } from "vite";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const root = resolve(fileURLToPath(import.meta.url), "../..");

const now = Date.now();
const iso = (ms) => new Date(ms).toISOString();

const ORDER = {
  id: 1, public_id: "B-100001", status: "pending", status_label: "Ожидает оплаты", amount_rub: "1000",
  rate: "100.00", markup_pct: "5", usdt_amount: "9.50", credited_usdt: null,
  qr_url: "https://qr.nspk.ru/MOCK1?type=02&bank=100000000000&sum=100000&cur=RUB&crc=0000",
  created_at: iso(now), expires_at: iso(now + 15 * 60_000), paid_at: null, finished_at: null,
};
const ME = {
  id: 1, status: "active", registered: true, blocked: false, balance: "0.00", hold: "0.00",
  available_for_withdrawal: "0.00", total_bought: "0.00", total_withdrawn: "0.00", markup_pct: "5",
  limits: { min_rub: "1000", max_rub: "100000" }, can_create_orders: true, orders_blocked_message: null,
  purchases_enabled: true, withdrawals_enabled: true, support_contact: "@support", active_order: null,
};
// 1x1 PNG
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=", "base64");

function json(route, body, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function fakeTelegram(page) {
  await page.route("https://telegram.org/**", (r) => r.fulfill({ status: 200, contentType: "text/javascript", body: "" }));
  await page.addInitScript(() => {
    window.Telegram = {
      WebApp: {
        initData: "user=%7B%22id%22%3A1%7D&auth_date=1&hash=fake",
        initDataUnsafe: { user: { id: 1 } },
        colorScheme: "light",
        ready() {}, expand() {}, openLink(url) { window.__opened = url; },
        showConfirm(_m, cb) { cb(true); },
        HapticFeedback: { notificationOccurred() {} },
      },
    };
  });
}

async function clientSmoke(browser, base) {
  const page = await browser.newPage({ viewport: { width: 320, height: 640 } });
  await fakeTelegram(page);
  const created = [];
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === "/api/auth/telegram") return json(route, { token: "T", subject: "user", user_id: 1 });
    if (req.headers().authorization !== "Bearer T") return json(route, { error: { code: "unauthorized", message: "x" } }, 401);
    if (path === "/api/client/me") return json(route, ME);
    if (path === "/api/client/rate") return json(route, { rate: "100.00", source: "rapira", age_seconds: 3, markup_pct: "5", effective_rate: "105.27" });
    if (path === "/api/client/orders/preview") {
      const amount = JSON.parse(req.postData()).amount_rub;
      return json(route, { amount_rub: amount, rate: "100.00", markup_pct: "5", fee_rub: "50", usdt_amount: "9.50", effective_rate: "105.27" });
    }
    if (path === "/api/client/orders" && req.method() === "POST") {
      created.push(req.headers()["idempotency-key"]);
      return json(route, { ...ORDER, created: true });
    }
    if (path === "/api/client/orders/1/qr.png") return route.fulfill({ status: 200, contentType: "image/png", body: PNG });
    if (path === "/api/client/orders/1") return json(route, ORDER);
    return json(route, { error: { code: "not_found", message: "Не найдено" } }, 404);
  });
  await page.goto(base + "/app/");
  await page.getByTestId("balance").waitFor();
  await page.getByRole("button", { name: "Купить" }).click();
  await page.getByLabel("Сумма в рублях").fill("1000");
  await page.getByTestId("quote").filter({ hasText: "9.50" }).waitFor();
  await page.getByRole("button", { name: "Продолжить" }).click();
  await page.getByText("К зачислению").waitFor();
  await page.getByRole("button", { name: "Подтвердить" }).click();
  await page.getByTestId("payment").waitFor();
  await page.getByAltText("QR-код для оплаты по СБП").waitFor();
  await page.getByRole("button", { name: "Оплатить" }).click();
  const opened = await page.evaluate(() => window.__opened);
  if (!opened?.startsWith("https://qr.nspk.ru/")) throw new Error("openLink was not called with the QR url");
  if (created.length !== 1 || !created[0]) throw new Error("order must be created once with an Idempotency-Key");
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  if (width > 320) throw new Error(`horizontal scroll at 320px: ${width}`);
  await page.close();
  console.log("client smoke: OK (purchase up to the QR screen)");
}

const WITHDRAWAL = {
  id: 5, public_id: "W-100001", status: "pending", status_label: "В очереди", network: "TRC20", network_label: "TRC-20",
  address: "TGfb53j2wY8J2F7J5RQhs6gdKpC2j6zsBY", amount: "60.00", fee: "4.00", amount_to_send: "56.00", tx_hash: null,
  explorer_url: null, reject_reason: null, created_at: iso(now), finished_at: null, user_id: 1, verify_status: null,
  verify_label: null, verify_details: {}, verify_attempts: 0, last_verify_at: null, last_tx_hash: null,
  last_explorer_url: null, forced: false, assigned_staff_id: null, completed_staff_id: null, taken_at: null,
  hash_submitted_at: null, user: { id: 1, tg_id: 500, tg_username: "client" },
};
const STAFF = { id: 2, tg_id: 1, tg_username: "op", role: "operator", is_active: true, is_superadmin: false, login: null,
  totp_enabled: false, invite_pending: false, invite_expires_at: null, created_at: iso(now), last_login_at: iso(now) };

async function adminSmoke(browser, base) {
  const page = await browser.newPage({ viewport: { width: 360, height: 700 } });
  await fakeTelegram(page);
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.pathname === "/api/auth/telegram") return json(route, { token: "A", subject: "staff", staff: STAFF });
    if (req.headers().authorization !== "Bearer A") return json(route, { error: { code: "unauthorized", message: "x" } }, 401);
    if (url.pathname === "/api/admin/me")
      return json(route, { staff: STAFF, auth_method: "telegram", is_admin: false, payment_provider: "mock", is_mock: true,
        withdraw_admin_only_from_usdt: "5000.00", b6_enabled: true });
    if (url.pathname === "/api/admin/withdrawals") return json(route, { items: [WITHDRAWAL], next_cursor: null });
    if (url.pathname === "/api/admin/withdrawals/5") return json(route, WITHDRAWAL);
    return json(route, { items: [], next_cursor: null });
  });
  await page.goto(base + "/admin/");
  await page.getByRole("heading", { name: "Выводы" }).first().waitFor();
  await page.getByText("Очередь").first().waitFor();
  await page.getByText("W-100001").first().waitFor();
  await page.close();
  console.log("admin smoke: OK (operator opens the withdrawal queue)");
}

const server = await preview({ configFile: resolve(root, "vite.client.config.ts"), preview: { port: 4180, strictPort: true },
  build: { outDir: resolve(root, "dist") }, base: "/" });
const base = "http://localhost:4180";
const browser = await chromium.launch();
let failed = false;
try {
  const only = process.argv[2];
  if (!only || only === "client") await clientSmoke(browser, base);
  if (!only || only === "admin") await adminSmoke(browser, base);
} catch (e) {
  failed = true;
  console.error("SMOKE FAILED:", e);
} finally {
  await browser.close();
  server.httpServer.close();
}
process.exit(failed ? 1 : 0);
