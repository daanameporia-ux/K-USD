/** Formatting and validation helpers. Amounts arrive from the API as strings and are never parsed to float for math. */

const NBSP = " ";

function groupDigits(int: string): string {
  return int.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
}

export function formatRub(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const s = String(value);
  const [int, frac] = s.split(".");
  const neg = int.startsWith("-");
  const body = groupDigits(neg ? int.slice(1) : int);
  const fracPart = frac && Number(frac) !== 0 ? "," + frac.padEnd(2, "0").slice(0, 2) : "";
  return (neg ? "−" : "") + body + fracPart + NBSP + "₽";
}

export function formatUsdt(value: string | null | undefined, withUnit = true): string {
  if (value === null || value === undefined || value === "") return "—";
  const [int, frac = ""] = String(value).split(".");
  const neg = int.startsWith("-");
  const body = groupDigits(neg ? int.slice(1) : int);
  const f = (frac + "00").slice(0, Math.max(2, frac.replace(/0+$/, "").length));
  return (neg ? "−" : "") + body + "." + f + (withUnit ? NBSP + "USDT" : "");
}

export function formatRate(value: string | null | undefined): string {
  if (!value) return "—";
  return value.replace(".", ",") + NBSP + "₽";
}

const MSK = "Europe/Moscow";

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: MSK,
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("ru-RU", { timeZone: MSK, hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
}

export function formatCountdown(ms: number): string {
  if (ms <= 0) return "00:00";
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
}

export const MSG_RUB_INTEGER = "Сумма должна быть целым числом рублей, без копеек.";

/** Mirrors backend domain.amounts.parse_rub for instant feedback (the backend validates again). */
export function validateRub(text: string, min: number, max: number): { value: number | null; error: string | null } {
  const raw = text.trim();
  if (!raw) return { value: null, error: null };
  if (!/^[\d\s  ]+$/.test(raw)) return { value: null, error: MSG_RUB_INTEGER };
  const digits = raw.replace(/[\s  ]/g, "");
  if (digits.replace(/^0+/, "").length > 12) return { value: null, error: rangeMessage(min, max) };
  const value = Number(digits);
  if (value < min || value > max) return { value: null, error: rangeMessage(min, max) };
  return { value, error: null };
}

export function rangeMessage(min: number, max: number): string {
  return `Сумма должна быть от ${groupDigits(String(min))} до ${groupDigits(String(max))} ₽.`;
}

/** Keep only digits for a numeric keypad input, adding thousands separators. */
export function sanitizeRubInput(text: string): string {
  const digits = text.replace(/\D/g, "").replace(/^0+(?=\d)/, "").slice(0, 12);
  return groupDigits(digits).replace(/ /g, " ");
}

export function validateUsdt(text: string): string | null {
  const raw = text.trim().replace(",", ".");
  if (!raw) return null;
  if (!/^\d+(\.\d{0,2})?$/.test(raw)) {
    return /^\d+\.\d{3,}$/.test(raw)
      ? "Укажите сумму с точностью не более 2 знаков после точки."
      : "Введите сумму числом, например 100 или 100.50.";
  }
  if (Number(raw) <= 0) return "Сумма должна быть больше нуля.";
  return null;
}

/** Compare decimal strings without floats. */
export function compareDecimal(a: string, b: string): number {
  const norm = (x: string) => {
    const [i, f = ""] = x.split(".");
    return [i.replace(/^0+(?=\d)/, ""), f.padEnd(6, "0").slice(0, 6)] as const;
  };
  const [ai, af] = norm(a);
  const [bi, bf] = norm(b);
  if (ai.length !== bi.length) return ai.length < bi.length ? -1 : 1;
  if (ai !== bi) return ai < bi ? -1 : 1;
  if (af !== bf) return af < bf ? -1 : 1;
  return 0;
}

export type Tone = "ok" | "warn" | "bad" | "muted" | "info";

const TONES: Record<string, Tone> = {
  paid: "ok",
  completed: "ok",
  pending: "warn",
  creating: "warn",
  in_progress: "info",
  verifying: "info",
  review: "warn",
  expired: "muted",
  cancelled: "muted",
  failed: "bad",
  rejected: "bad",
};

export function statusTone(status: string): Tone {
  return TONES[status] ?? "muted";
}

export function shortAddress(address: string | null | undefined, keep = 6): string {
  if (!address) return "—";
  return address.length <= keep * 2 + 1 ? address : address.slice(0, keep) + "…" + address.slice(-keep);
}

export const MSG_BUYER_PHONE = "Введите российский номер в формате +7 9XX XXX-XX-XX.";
export const MSG_BUYER_NAME = "Укажите ФИО полностью: фамилия, имя и отчество.";

/** Mirrors backend domain.buyer.normalize_phone: a Russian mobile number → "+79XXXXXXXXX", null when invalid. */
export function normalizeRuPhone(text: string): string | null {
  const raw = text.replace(/[\s()\-.]/g, "");
  let digits = raw;
  if (raw.startsWith("+7")) digits = raw.slice(2);
  else if (raw.length === 11 && (raw[0] === "7" || raw[0] === "8")) digits = raw.slice(1);
  return /^9[0-9]{9}$/.test(digits) ? "+7" + digits : null;
}

/** "+79161234567" → "+7 916 123-45-67". */
export function formatRuPhone(phone: string | null | undefined): string {
  const m = /^\+7(\d{3})(\d{3})(\d{2})(\d{2})$/.exec(phone ?? "");
  return m ? `+7 ${m[1]} ${m[2]}-${m[3]}-${m[4]}` : (phone ?? "—");
}

/** Mirrors backend domain.buyer.normalize_name: 2–6 words of letters; returns the cleaned name or null. */
export function normalizeFullName(text: string): string | null {
  const words = text.trim().split(/\s+/).filter(Boolean);
  const name = words.join(" ");
  const word = /^[A-Za-zА-Яа-яЁё]+(?:[-'’][A-Za-zА-Яа-яЁё]+)*$/;
  if (words.length < 2 || words.length > 6 || name.length > 120 || !words.every((w) => word.test(w))) return null;
  return name;
}

/**
 * Phone field with a fixed "+7" prefix: whatever is typed or pasted becomes "+7 9XX XXX-XX-XX" (as far as entered).
 * `prev` is the previous field value: when a separator was erased, the digit before it goes too.
 */
export function formatRuPhoneInput(raw: string, prev: string = ""): string {
  const national = (text: string): string => {
    const digits = text.replace(/\D/g, "");
    if (text.trim().startsWith("+7")) return digits.slice(1);
    // a full number pasted over the field: 8 916 … or 7 916 …
    if (digits.length >= 11 && (digits[0] === "7" || digits[0] === "8")) return digits.slice(1);
    return digits;
  };
  let d = national(raw);
  if (raw.length < prev.length && d === national(prev)) d = d.slice(0, -1);
  d = d.slice(0, 10);
  let out = "+7 " + d.slice(0, 3);
  if (d.length > 3) out += " " + d.slice(3, 6);
  if (d.length > 6) out += "-" + d.slice(6, 8);
  if (d.length > 8) out += "-" + d.slice(8, 10);
  return out;
}
