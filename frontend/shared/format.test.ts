import { describe, expect, it } from "vitest";
import {
  compareDecimal,
  formatCountdown,
  formatRub,
  formatUsdt,
  MSG_RUB_INTEGER,
  rangeMessage,
  sanitizeRubInput,
  formatRuPhone,
  formatRuPhoneInput,
  normalizeFullName,
  normalizeRuPhone,
  validateRub,
  validateUsdt,
} from "./format";

const NB = " ";

describe("amount formatting", () => {
  it("formats rubles with grouping", () => {
    expect(formatRub("100000")).toBe(`100${NB}000${NB}₽`);
    expect(formatRub(1000)).toBe(`1${NB}000${NB}₽`);
    expect(formatRub("27.50")).toBe(`27,50${NB}₽`);
    expect(formatRub("50.00")).toBe(`50${NB}₽`);
    expect(formatRub(null)).toBe("—");
  });
  it("formats usdt without float math", () => {
    expect(formatUsdt("9.50")).toBe(`9.50${NB}USDT`);
    expect(formatUsdt("9.5")).toBe(`9.50${NB}USDT`);
    expect(formatUsdt("1234567.123456")).toBe(`1${NB}234${NB}567.123456${NB}USDT`);
    expect(formatUsdt("0.1", false)).toBe("0.10");
    expect(formatUsdt("-2.5")).toBe(`−2.50${NB}USDT`);
  });
  it("countdown", () => {
    expect(formatCountdown(65_000)).toBe("01:05");
    expect(formatCountdown(-1)).toBe("00:00");
  });
});

describe("amount validation", () => {
  it("accepts integers and spaces", () => {
    expect(validateRub("10 000", 1000, 100000)).toEqual({ value: 10000, error: null });
    expect(validateRub("1000", 1000, 100000).value).toBe(1000);
  });
  it("rejects kopecks, letters and minus", () => {
    for (const bad of ["1000.50", "1000,5", "abc", "-100"]) {
      expect(validateRub(bad, 1000, 100000).error).toBe(MSG_RUB_INTEGER);
    }
  });
  it("checks range", () => {
    for (const bad of ["999", "100001", "0", "9".repeat(40)]) {
      expect(validateRub(bad, 1000, 100000).error).toBe(rangeMessage(1000, 100000));
    }
    expect(validateRub("", 1000, 100000)).toEqual({ value: null, error: null });
  });
  it("sanitizes keypad input", () => {
    expect(sanitizeRubInput("10000")).toBe("10 000");
    expect(sanitizeRubInput("0012a3,4")).toBe("1 234");
  });
  it("validates usdt", () => {
    expect(validateUsdt("100")).toBeNull();
    expect(validateUsdt("100,5")).toBeNull();
    expect(validateUsdt("1.234")).toMatch(/2 знаков/);
    expect(validateUsdt("abc")).toMatch(/числом/);
    expect(validateUsdt("0")).toMatch(/больше нуля/);
  });
  it("compares decimals as strings", () => {
    expect(compareDecimal("9.50", "9.5")).toBe(0);
    expect(compareDecimal("10.00", "9.99")).toBe(1);
    expect(compareDecimal("0.01", "0.1")).toBe(-1);
  });
});

describe("buyer details", () => {
  it("accepts Russian mobile numbers in any common notation", () => {
    for (const ok of ["+79161234567", "89161234567", "79161234567", "9161234567", "+7 (916) 123-45-67"]) {
      expect(normalizeRuPhone(ok)).toBe("+79161234567");
    }
    expect(formatRuPhone("+79161234567")).toBe("+7 916 123-45-67");
  });
  it("rejects landlines, foreign and malformed numbers", () => {
    for (const bad of ["", "+74951234567", "+380501234567", "+7916123456", "+791612345678", "+7916123456a"]) {
      expect(normalizeRuPhone(bad)).toBeNull();
    }
  });
  it("keeps the +7 prefix in the phone field and formats what is typed", () => {
    expect(formatRuPhoneInput("")).toBe("+7 ");
    expect(formatRuPhoneInput("+7 9", "+7 ")).toBe("+7 9");
    expect(formatRuPhoneInput("+7 9161", "+7 916")).toBe("+7 916 1");
    expect(formatRuPhoneInput("+7 916 123-45-67")).toBe("+7 916 123-45-67");
    expect(formatRuPhoneInput("+7 916 123-45-678", "+7 916 123-45-67")).toBe("+7 916 123-45-67");
    // a pasted full number in any notation
    for (const pasted of ["89161234567", "79161234567", "+79161234567", "8 (916) 123-45-67", "9161234567"]) {
      expect(formatRuPhoneInput(pasted, "+7 ")).toBe("+7 916 123-45-67");
    }
    // erasing: a separator takes the digit before it, the prefix cannot be erased
    expect(formatRuPhoneInput("+7 916 123-45-6", "+7 916 123-45-67")).toBe("+7 916 123-45-6");
    expect(formatRuPhoneInput("+7 916 123-45", "+7 916 123-45-")).toBe("+7 916 123-4");
    expect(formatRuPhoneInput("+7 916", "+7 916 ")).toBe("+7 91");
    expect(formatRuPhoneInput("+7", "+7 ")).toBe("+7 ");
    expect(formatRuPhoneInput("", "+7 ")).toBe("+7 ");
    expect(normalizeRuPhone(formatRuPhoneInput("89161234567"))).toBe("+79161234567");
  });
  it("checks the full name", () => {
    expect(normalizeFullName("  Иванов   Иван Иванович ")).toBe("Иванов Иван Иванович");
    expect(normalizeFullName("Римский-Корсаков Николай")).toBe("Римский-Корсаков Николай");
    for (const bad of ["", "Иванов", "Иванов 2", "Иванов И."]) expect(normalizeFullName(bad)).toBeNull();
  });
});
