import { describe, expect, it } from "vitest";
import {
  compareDecimal,
  formatCountdown,
  formatRub,
  formatUsdt,
  MSG_RUB_INTEGER,
  rangeMessage,
  sanitizeRubInput,
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
