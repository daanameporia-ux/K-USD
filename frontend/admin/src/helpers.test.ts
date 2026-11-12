import {
  WITHDRAWAL_TABS,
  compactJson,
  draftToSetting,
  formatDuration,
  isAdminOnlyAmount,
  isWithdrawalTab,
  parsePositiveInt,
  settingsDiff,
  validateSignedAmount,
  verifyRows,
  withdrawalTabQuery,
} from "./helpers";

describe("withdrawal tabs", () => {
  it("has the four tabs in order", () => {
    expect(WITHDRAWAL_TABS.map((t) => t.label)).toEqual(["Очередь", "В работе у меня", "На проверке", "Завершённые"]);
  });

  it("maps tabs to query params", () => {
    expect(withdrawalTabQuery("queue")).toEqual({ status: "pending" });
    expect(withdrawalTabQuery("mine")).toEqual({ assigned: "me", status: "in_progress,verifying" });
    expect(withdrawalTabQuery("verifying")).toEqual({ status: "verifying" });
    expect(withdrawalTabQuery("done")).toEqual({ status: "completed,rejected,cancelled" });
  });

  it("validates tab ids", () => {
    expect(isWithdrawalTab("mine")).toBe(true);
    expect(isWithdrawalTab("other")).toBe(false);
    expect(isWithdrawalTab(null)).toBe(false);
  });
});

describe("isAdminOnlyAmount", () => {
  it("compares decimal strings without floats", () => {
    expect(isAdminOnlyAmount("5000.00", "5000.00", true)).toBe(true);
    expect(isAdminOnlyAmount("4999.99", "5000.00", true)).toBe(false);
    expect(isAdminOnlyAmount("10000", "5000.00", true)).toBe(true);
  });
  it("is off when B6 is disabled or threshold is unknown", () => {
    expect(isAdminOnlyAmount("10000", "5000", false)).toBe(false);
    expect(isAdminOnlyAmount("10000", null, true)).toBe(false);
  });
});

describe("verifyRows", () => {
  it("returns nothing before the first check", () => {
    expect(verifyRows(null)).toEqual([]);
    expect(verifyRows({ tx_hash: "x" })).toEqual([]);
  });

  it("marks mismatches", () => {
    const rows = verifyRows({
      expected: { to: "TAbc", amount: "95.50", token: "TR7" },
      actual: { to: "tabc", amount: "95.5", token: "TX1", confirmations: 3 },
    });
    const byKey = Object.fromEntries(rows.map((r) => [r.key, r]));
    expect(byKey.to.mismatch).toBe(false);
    expect(byKey.amount.mismatch).toBe(false);
    expect(byKey.token.mismatch).toBe(true);
    expect(byKey.confirmations.actual).toBe("3");
  });

  it("flags a different amount and a missing actual value", () => {
    const rows = verifyRows({
      expected: { to: "0xA", amount: "10.00", token: "0xT" },
      actual: { to: null, amount: "10.01", token: "0xT" },
    });
    expect(rows.find((r) => r.key === "amount")!.mismatch).toBe(true);
    expect(rows.find((r) => r.key === "to")!.mismatch).toBe(true);
    expect(rows.find((r) => r.key === "confirmations")).toBeUndefined();
  });
});

describe("settingsDiff", () => {
  const types = {
    order_min_rub: "int",
    default_markup_pct: "decimal",
    b2_enabled: "bool",
    support_contact: "string",
    rate_field: "choice",
  } as const;
  const original = {
    order_min_rub: 1000,
    default_markup_pct: "5.00",
    b2_enabled: true,
    support_contact: "@support",
    rate_field: "askPrice",
  };

  it("returns nothing when values are equal (decimal 5 == 5.00)", () => {
    expect(
      settingsDiff(
        original,
        { order_min_rub: "1000", default_markup_pct: "5", b2_enabled: true, support_contact: "@support", rate_field: "askPrice" },
        types,
      ),
    ).toEqual({});
  });

  it("sends only changed keys converted to API types", () => {
    expect(
      settingsDiff(
        original,
        { order_min_rub: "1500", default_markup_pct: "4,5", b2_enabled: false, support_contact: "@support", rate_field: "close" },
        types,
      ),
    ).toEqual({ order_min_rub: 1500, default_markup_pct: "4.5", b2_enabled: false, rate_field: "close" });
  });

  it("keeps invalid ints as text for the server to reject", () => {
    expect(draftToSetting("12a", "int")).toBe("12a");
  });
});

describe("misc", () => {
  it("validates signed amounts", () => {
    expect(validateSignedAmount("-10.50")).toBeNull();
    expect(validateSignedAmount("+25")).toBeNull();
    expect(validateSignedAmount("0")).not.toBeNull();
    expect(validateSignedAmount("abc")).not.toBeNull();
    expect(validateSignedAmount("")).not.toBeNull();
  });

  it("parses positive integers", () => {
    expect(parsePositiveInt("")).toBeNull();
    expect(parsePositiveInt("20 000")).toBe(20000);
    expect(parsePositiveInt("0")).toBe("invalid");
    expect(parsePositiveInt("1.5")).toBe("invalid");
  });

  it("formats durations and json", () => {
    expect(formatDuration(42)).toBe("42 с");
    expect(formatDuration(125)).toBe("2 мин 5 с");
    expect(formatDuration(null)).toBe("—");
    expect(compactJson({ a: 1 })).toBe('{"a":1}');
    expect(compactJson({})).toBe("—");
    expect(compactJson("x".repeat(500), 10)).toHaveLength(10);
  });
});
