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
  buyer_phone: "+79161234567", buyer_name: "Иванов Иван Иванович", proof_required: true, proof_uploaded_at: null,
};
const ME = {
  id: 1, status: "active", registered: true, blocked: false, balance: "0.00", hold: "0.00",
  available_for_withdrawal: "0.00", total_bought: "0.00", total_withdrawn: "0.00", markup_pct: "5",
  limits: { min_rub: "1000", max_rub: "100000" }, can_create_orders: true, orders_blocked_message: null,
  purchases_enabled: true, withdrawals_enabled: true, support_contact: "@support", active_order: null,
  requirements: { buyer_phone: true, buyer_name: true, payment_proof: true }, proof_pending_order: null,
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
  const bodies = [];
  const uploads = [];
  let order = ORDER;
  const book = [];
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
      bodies.push(JSON.parse(req.postData()));
      return json(route, { ...ORDER, created: true });
    }
    if (path === "/api/client/orders/buyer-check") return json(route, JSON.parse(req.postData()));
    if (path === "/api/client/orders/1/proof" && req.method() === "POST") {
      uploads.push({ type: req.headers()["content-type"], size: req.postDataBuffer()?.length ?? 0 });
      order = { ...order, proof_uploaded_at: iso(Date.now()) };
      return json(route, order);
    }
    if (path === "/api/client/orders/1/qr.png") return route.fulfill({ status: 200, contentType: "image/png", body: PNG });
    if (path === "/api/client/orders/1") return json(route, order);
    if (path === "/api/client/history") return json(route, { items: [], next_cursor: null });
    if (path === "/api/client/withdrawals/config") {
      return json(route, { networks: [{ network: "TRC20", label: "TRC-20", fee: "4.00" }], min_amount: "50.00", available: "120.00", enabled: true });
    }
    if (path === "/api/client/addresses" && req.method() === "GET") return json(route, { items: book, max: 20 });
    if (path === "/api/client/addresses" && req.method() === "POST") {
      const item = { id: book.length + 1, network_label: "TRC-20", ...JSON.parse(req.postData()) };
      book.unshift(item);
      return json(route, item);
    }
    if (path === "/api/client/withdrawals/preview") {
      const b = JSON.parse(req.postData());
      if (!b.address.startsWith("T")) return json(route, { error: { code: "invalid_address", message: "Неверный адрес TRC-20." } }, 400);
      return json(route, { network: "TRC20", network_label: "TRC-20", address: b.address, amount: b.amount, fee: "4.00", amount_to_send: "46.00" });
    }
    return json(route, { error: { code: "not_found", message: "Не найдено" } }, 404);
  });
  await page.goto(base + "/app/");
  await page.getByTestId("balance").waitFor();
  // The exchange rate and the client's commission are shown separately (not one "effective" rate).
  await page.getByTestId("rate").filter({ hasText: "1 USDT = 100,00" }).waitFor();
  await page.getByTestId("commission").filter({ hasText: "5%" }).waitFor();
  await page.getByRole("button", { name: "Купить" }).click();
  await page.getByLabel("Сумма в рублях").fill("1000");
  await page.getByTestId("quote").filter({ hasText: "9.50" }).waitFor();
  await page.getByRole("button", { name: "Продолжить" }).click();
  // Buyer details: the step cannot be passed without a valid Russian phone number and a full name.
  await page.getByText("привязан к СБП").waitFor();
  if ((await page.getByTestId("buyer-phone").inputValue()) !== "+7 ") throw new Error("the phone field must start with +7");
  await page.getByRole("button", { name: "Продолжить" }).click();
  await page.getByText("Введите российский номер").waitFor();
  await page.getByText("Укажите ФИО полностью").waitFor();
  await page.getByTestId("buyer-phone").fill("+7 495 123-45-67");
  await page.getByTestId("buyer-name").fill("Иванов Иван Иванович");
  await page.getByRole("button", { name: "Продолжить" }).click();
  await page.getByText("Введите российский номер").waitFor();
  if (await page.getByText("К зачислению").count()) throw new Error("a landline number must not pass the buyer step");
  // The field always keeps "+7": the client clears it and just types the number starting from 9.
  await page.getByTestId("buyer-phone").fill("");
  if ((await page.getByTestId("buyer-phone").inputValue()) !== "+7 ") throw new Error("the phone field must keep the +7 prefix");
  await page.getByTestId("buyer-phone").pressSequentially("9161234567");
  if ((await page.getByTestId("buyer-phone").inputValue()) !== "+7 916 123-45-67") throw new Error("the typed phone is not formatted");
  await page.getByRole("button", { name: "Продолжить" }).click();
  await page.getByText("К зачислению").waitFor();
  await page.getByText("+7 916 123-45-67").waitFor();
  await page.getByRole("button", { name: "Подтвердить" }).click();
  await page.getByTestId("payment").waitFor();
  await page.getByAltText("QR-код для оплаты по СБП").waitFor();
  // The QR must actually render: the blob URL must stay alive (not revoked by a re-running effect).
  await page.waitForFunction(() => {
    const img = document.querySelector("img.qr");
    return img && img.complete && img.naturalWidth > 0;
  }, null, { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(500); // a re-render that revoked the URL would break the image right after load
  const qr = await page.evaluate(() => {
    const img = document.querySelector("img.qr");
    return img ? { complete: img.complete, naturalWidth: img.naturalWidth } : null;
  });
  if (!qr || !qr.complete || qr.naturalWidth <= 0) throw new Error(`QR image did not render: ${JSON.stringify(qr)}`);
  await page.getByRole("button", { name: "Оплатить" }).click();
  const opened = await page.evaluate(() => window.__opened);
  if (!opened?.startsWith("https://qr.nspk.ru/")) throw new Error("openLink was not called with the QR url");
  if (created.length !== 1 || !created[0]) throw new Error("order must be created once with an Idempotency-Key");
  if (bodies[0].buyer_phone !== "+79161234567" || bodies[0].buyer_name !== "Иванов Иван Иванович")
    throw new Error(`buyer details were not sent with the order: ${JSON.stringify(bodies[0])}`);
  // The payment arrives: the screen asks for the screenshot and uploads the chosen file.
  order = { ...ORDER, status: "paid", status_label: "Оплачена", credited_usdt: "9.50", paid_at: iso(Date.now()) };
  await page.getByTestId("proof").waitFor({ timeout: 8000 });
  await page.getByText("из истории операций").waitFor();
  await page.getByRole("button", { name: "Загрузить скриншот" }).waitFor();
  await page.getByTestId("proof-input").setInputFiles({ name: "history.png", mimeType: "image/png", buffer: PNG });
  await page.getByText("Скриншот загружен").waitFor();
  if (uploads.length !== 1 || uploads[0].size !== PNG.length || uploads[0].type !== "application/octet-stream")
    throw new Error(`screenshot upload is wrong: ${JSON.stringify(uploads)}`);
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  if (width > 320) throw new Error(`horizontal scroll at 320px: ${width}`);

  // Withdrawal: one network, so the address is asked right away; a new address is saved to the address book.
  const ADDR = "TGfb53j2wY8J2F7J5RQhs6gdKpC2j6zsBY";
  await page.goto(base + "/app/");
  await page.getByRole("button", { name: "Вывести" }).click();
  await page.getByText("Куда отправить").waitFor();
  if (await page.getByText("Выберите сеть").count()) throw new Error("the network step must be skipped for one network");
  if (await page.getByText("BEP").count()) throw new Error("BEP-20 must not be offered");
  await page.getByLabel("Адрес кошелька USDT в сети TRC-20").fill(ADDR);
  await page.getByTestId("save-address").check();
  await page.getByLabel("Название адреса").fill("Мой кошелёк");
  await page.getByRole("button", { name: "Далее" }).click();
  await page.getByLabel("Сумма списания, USDT").waitFor();
  if (book.length !== 1 || book[0].address !== ADDR || book[0].label !== "Мой кошелёк")
    throw new Error(`the address was not saved: ${JSON.stringify(book)}`);
  // Next time the saved address is one tap away.
  await page.goto(base + "/app/");
  await page.getByRole("button", { name: "Вывести" }).click();
  await page.getByTestId("address-book").getByText("Мой кошелёк").click();
  await page.getByLabel("Сумма списания, USDT").waitFor();
  const width2 = await page.evaluate(() => document.documentElement.scrollWidth);
  if (width2 > 320) throw new Error(`horizontal scroll at 320px on withdraw: ${width2}`);
  await page.close();
  console.log("client smoke: OK (purchase with buyer details, QR, payment screenshot; withdrawal with the address book)");
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

const PAID = { ...ORDER, status: "paid", status_label: "Оплачена", credited_usdt: "9.50", paid_at: iso(now),
  proof_uploaded_at: iso(now), proof_reviewed_at: null, proof_reviewed_by: null, user_id: 1 };

async function adminSmoke(browser, base) {
  let reviewed = false;
  const proofs = () => reviewed
    ? { to_review: 0, missing: 0, reviewed: 1, blocking: 0, orders: [] }
    : { to_review: 1, missing: 0, reviewed: 0, blocking: 1, orders: [PAID] };
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
    if (url.pathname === "/api/admin/withdrawals/5") return json(route, { ...WITHDRAWAL, proofs: proofs() });
    if (url.pathname === "/api/admin/orders/1/proof") return route.fulfill({ status: 200, contentType: "image/png", body: PNG });
    if (url.pathname === "/api/admin/orders/1/proof/review" && req.method() === "POST") {
      reviewed = true;
      return json(route, { ...PAID, proof_reviewed_at: iso(Date.now()) });
    }
    return json(route, { items: [], next_cursor: null });
  });
  await page.goto(base + "/admin/");
  await page.getByRole("heading", { name: "Выводы" }).first().waitFor();
  await page.getByText("Очередь").first().waitFor();
  await page.getByText("W-100001").first().waitFor();
  // The withdrawal cannot be taken until the client's payment screenshots are opened and marked as reviewed.
  await page.getByText("W-100001").first().click();
  await page.getByTestId("proofs-block").waitFor();
  const take = page.getByRole("button", { name: "Взять в работу" });
  if (!(await take.isDisabled())) throw new Error("«Взять в работу» must be disabled while screenshots are not reviewed");
  if (await page.getByRole("button", { name: "Проверено" }).count()) throw new Error("«Проверено» must appear only after the screenshot is opened");
  await page.getByRole("button", { name: "Показать скриншот" }).click();
  await page.getByAltText("Скриншот оплаты по заявке B-100001").waitFor();
  await page.getByRole("button", { name: "Проверено" }).click();
  await page.getByTestId("proofs-ok").waitFor();
  if (await take.isDisabled()) throw new Error("«Взять в работу» must be enabled after the review");
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  if (width > 360) throw new Error(`horizontal scroll in the admin at 360px: ${width}`);
  if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS, fullPage: true });
  await page.close();
  console.log("admin smoke: OK (withdrawal queue; screenshots are reviewed before the withdrawal is taken)");
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
