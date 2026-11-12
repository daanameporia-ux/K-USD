import { ApiClient, networkError } from "@shared/api";
import { initData } from "@shared/telegram";

export interface Order {
  id: number;
  public_id: string;
  status: string;
  status_label: string;
  amount_rub: string;
  rate: string;
  markup_pct: string;
  usdt_amount: string;
  credited_usdt: string | null;
  qr_url: string | null;
  created_at: string;
  expires_at: string;
  paid_at: string | null;
  finished_at: string | null;
  created?: boolean;
  error?: { code: string; message: string };
}

export interface Me {
  id: number;
  status: string;
  registered: boolean;
  blocked: boolean;
  balance: string;
  hold: string;
  available_for_withdrawal: string;
  total_bought: string;
  total_withdrawn: string;
  markup_pct: string;
  limits: { min_rub: string; max_rub: string };
  can_create_orders: boolean;
  orders_blocked_message: string | null;
  purchases_enabled: boolean;
  withdrawals_enabled: boolean;
  support_contact: string;
  active_order: Order | null;
}

export interface Rate {
  rate: string;
  source: string;
  age_seconds: number | null;
  markup_pct: string;
  effective_rate: string;
}

export interface Quote {
  amount_rub: string;
  rate: string;
  markup_pct: string;
  fee_rub: string;
  usdt_amount: string;
  effective_rate: string;
}

export interface WithdrawConfig {
  networks: { network: "TRC20" | "BEP20"; label: string; fee: string }[];
  min_amount: string;
  available: string;
  enabled: boolean;
}

export interface WithdrawPreview {
  network: string;
  network_label: string;
  address: string;
  amount: string;
  fee: string;
  amount_to_send: string;
}

export interface Withdrawal {
  id: number;
  public_id: string;
  status: string;
  status_label: string;
  network: string;
  network_label: string;
  address: string;
  amount: string;
  fee: string;
  amount_to_send: string;
  tx_hash: string | null;
  explorer_url: string | null;
  reject_reason: string | null;
  created_at: string;
  created?: boolean;
}

export interface HistoryItem {
  kind: "order" | "withdrawal";
  id: number;
  public_id: string;
  created_at: string;
  status: string;
  status_label: string;
  usdt: string;
  amount_rub: string | null;
  rate: string | null;
  network: string | null;
  address: string | null;
  fee: string | null;
  amount_to_send: string | null;
  tx_hash: string | null;
  explorer_url: string | null;
  reject_reason: string | null;
}

/** Returns the token, null when Telegram rejected the session; throws ApiError(0, "network_error") when offline. */
async function telegramLogin(): Promise<string | null> {
  const data = initData();
  if (!data) return null;
  let resp: Response;
  try {
    resp = await fetch("/api/auth/telegram", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ init_data: data, scope: "client" }),
    });
  } catch {
    throw networkError();
  }
  if (!resp.ok) return null;
  try {
    return ((await resp.json()) as { token: string }).token;
  } catch {
    throw networkError();
  }
}

export const api = new ApiClient({ reauth: telegramLogin });

export async function login(): Promise<boolean> {
  const token = await telegramLogin();
  api.setToken(token);
  return token !== null;
}
