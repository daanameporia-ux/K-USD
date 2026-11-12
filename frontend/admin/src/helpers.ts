/** Pure helpers of the admin app (covered by helpers.test.ts). No float math on money. */

import { compareDecimal } from "@shared/format";
import type { SettingType, SettingValue, VerifyDetails } from "./types";

// --- withdrawals tabs ----------------------------------------------------------------------

export type WithdrawalTab = "queue" | "mine" | "verifying" | "done";

export const WITHDRAWAL_TABS: { id: WithdrawalTab; label: string }[] = [
  { id: "queue", label: "Очередь" },
  { id: "mine", label: "В работе у меня" },
  { id: "verifying", label: "На проверке" },
  { id: "done", label: "Завершённые" },
];

export function isWithdrawalTab(value: string | null | undefined): value is WithdrawalTab {
  return WITHDRAWAL_TABS.some((t) => t.id === value);
}

/** Query parameters of GET /api/admin/withdrawals for a tab. */
export function withdrawalTabQuery(tab: WithdrawalTab): Record<string, string> {
  switch (tab) {
    case "queue":
      return { status: "pending" };
    case "mine":
      return { assigned: "me", status: "in_progress,verifying" };
    case "verifying":
      return { status: "verifying" };
    case "done":
      return { status: "completed,rejected,cancelled" };
  }
}

/** B6: withdrawals with amount >= threshold can be closed only by an admin. */
export function isAdminOnlyAmount(amount: string, threshold: string | null | undefined, b6Enabled: boolean): boolean {
  if (!b6Enabled || !threshold) return false;
  return compareDecimal(amount, threshold) >= 0;
}

// --- verification result ---------------------------------------------------------------------

export interface VerifyRow {
  key: "to" | "amount" | "token" | "confirmations";
  label: string;
  expected: string;
  actual: string;
  mismatch: boolean;
}

const VERIFY_FIELDS: { key: VerifyRow["key"]; label: string }[] = [
  { key: "to", label: "Получатель" },
  { key: "amount", label: "Сумма" },
  { key: "token", label: "Токен (контракт)" },
  { key: "confirmations", label: "Подтверждения" },
];

function show(v: unknown): string {
  return v === null || v === undefined || v === "" ? "—" : String(v);
}

function sameDecimal(a: string, b: string): boolean {
  if (!/^-?\d+(\.\d+)?$/.test(a) || !/^-?\d+(\.\d+)?$/.test(b)) return a === b;
  return compareDecimal(a, b) === 0;
}

/** Expected vs actual table rows from withdrawal.verify_details. Empty when nothing was checked yet. */
export function verifyRows(details: VerifyDetails | null | undefined): VerifyRow[] {
  if (!details || (!details.expected && !details.actual)) return [];
  const exp = details.expected ?? {};
  const act = details.actual;
  return VERIFY_FIELDS.filter((f) => f.key !== "confirmations" || (act && act.confirmations !== undefined)).map((f) => {
    const e = exp[f.key];
    const a = act ? act[f.key] : undefined;
    let mismatch = false;
    if (act && f.key !== "confirmations" && e !== undefined && e !== null) {
      if (a === null || a === undefined) mismatch = true;
      else if (f.key === "amount") mismatch = !sameDecimal(String(e), String(a));
      else mismatch = String(e).toLowerCase() !== String(a).toLowerCase();
    }
    return { key: f.key, label: f.label, expected: f.key === "confirmations" ? "—" : show(e), actual: show(a), mismatch };
  });
}

// --- settings ----------------------------------------------------------------------------

/** Value for an <input>: everything is edited as text, booleans as checkboxes. */
export function settingToDraft(value: SettingValue): SettingValue {
  if (typeof value === "boolean") return value;
  if (value === null || value === undefined) return "";
  return String(value);
}

/** Convert an edited value back to the JSON the backend expects. Decimals stay strings. */
export function draftToSetting(draft: SettingValue, type: SettingType): SettingValue {
  if (type === "bool") return Boolean(draft);
  const text = draft === null || draft === undefined ? "" : String(draft).trim();
  if (type === "int") return /^-?\d+$/.test(text) ? Number(text) : text;
  if (type === "decimal") return text.replace(",", ".");
  return text;
}

function sameSetting(original: SettingValue, next: SettingValue, type: SettingType): boolean {
  if (type === "bool") return Boolean(original) === Boolean(next);
  if (type === "decimal") {
    const a = original === null || original === undefined ? "" : String(original);
    const b = next === null || next === undefined ? "" : String(next);
    if (a === "" || b === "") return a === b;
    return sameDecimal(a, b);
  }
  return String(original ?? "") === String(next ?? "");
}

/** Only changed keys, converted to API values: body.changes for PATCH /api/admin/settings. */
export function settingsDiff(
  original: Record<string, SettingValue>,
  draft: Record<string, SettingValue>,
  types: Record<string, SettingType>,
): Record<string, SettingValue> {
  const changes: Record<string, SettingValue> = {};
  for (const key of Object.keys(draft)) {
    const type = types[key] ?? "string";
    const next = draftToSetting(draft[key], type);
    if (!sameSetting(original[key] ?? null, next, type)) changes[key] = next;
  }
  return changes;
}

// --- misc -----------------------------------------------------------------------------------

/** Signed amount for a balance adjustment, e.g. "-10.5" or "+100". Returns an error text or null. */
export function validateSignedAmount(text: string): string | null {
  const raw = text.trim().replace(",", ".");
  if (!raw) return "Введите сумму.";
  if (!/^[+-]?\d+(\.\d{1,6})?$/.test(raw)) return "Сумма — число со знаком, например -10.50 или 25.";
  if (/^[+-]?0+(\.0+)?$/.test(raw)) return "Сумма не может быть нулевой.";
  return null;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} с`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} мин ${s % 60} с`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} ч ${m % 60} мин`;
  return `${Math.floor(h / 24)} д ${h % 24} ч`;
}

export function formatPct(value: string | null | undefined): string {
  return value === null || value === undefined || value === "" ? "—" : `${value.replace(".", ",")} %`;
}

/** Compact one-line JSON for audit before/after, truncated. */
export function compactJson(value: unknown, max = 300): string {
  if (value === null || value === undefined) return "—";
  let s: string;
  try {
    s = JSON.stringify(value);
  } catch {
    s = String(value);
  }
  if (s === "{}" || s === "[]") return "—";
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

export function userTitle(u: { id: number; tg_username?: string | null; tg_id?: number | null } | null | undefined): string {
  if (!u) return "—";
  if (u.tg_username) return "@" + u.tg_username;
  if (u.tg_id) return "tg " + u.tg_id;
  return "id " + u.id;
}

export function staffTitle(s: { id: number; tg_username: string | null; tg_id: number | null } | null | undefined): string {
  if (!s) return "—";
  if (s.tg_username) return "@" + s.tg_username;
  if (s.tg_id) return "tg " + s.tg_id;
  return "#" + s.id;
}

/** Positive integer from an input; null for empty; NaN-safe. */
export function parsePositiveInt(text: string): number | null | "invalid" {
  const raw = text.trim().replace(/[\s ]/g, "");
  if (!raw) return null;
  if (!/^\d+$/.test(raw) || /^0+$/.test(raw)) return "invalid";
  return Number(raw);
}
