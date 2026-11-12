/** JSON shapes of the admin API (see backend/app/api/serializers.py). Money and rates are strings. */

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface Staff {
  id: number;
  tg_id: number | null;
  tg_username: string | null;
  role: "admin" | "operator";
  is_active: boolean;
  is_superadmin: boolean;
  login: string | null;
  totp_enabled: boolean;
  invite_pending: boolean;
  invite_expires_at: string | null;
  created_at: string | null;
  last_login_at: string | null;
}

export interface Me {
  staff: Staff;
  auth_method: "telegram" | "password";
  is_admin: boolean;
  payment_provider: string;
  is_mock: boolean;
  withdraw_admin_only_from_usdt: string | null;
  b6_enabled: boolean;
}

export interface AuthResult {
  token: string;
  subject: string;
  staff?: Staff;
}

export interface UserRef {
  id: number;
  tg_id: number | null;
  tg_username: string | null;
  is_suspicious?: boolean;
}

export interface VerifyValues {
  to?: string | null;
  amount?: string | null;
  token?: string | null;
  confirmations?: number | null;
  found?: boolean;
  success?: boolean;
  block_time?: string | null;
}

export interface VerifyDetails {
  tx_hash?: string;
  explorer_url?: string;
  expected?: VerifyValues;
  actual?: VerifyValues;
  error?: string;
  [key: string]: unknown;
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
  created_at: string | null;
  finished_at: string | null;
  user_id: number;
  verify_status: string | null;
  verify_label: string | null;
  verify_details: VerifyDetails | null;
  verify_attempts: number;
  last_verify_at: string | null;
  last_tx_hash: string | null;
  last_explorer_url: string | null;
  forced: boolean;
  assigned_staff_id: number | null;
  completed_staff_id: number | null;
  taken_at: string | null;
  hash_submitted_at: string | null;
  user: UserRef | null;
  assigned_staff?: Staff | null;
}

export interface Order {
  id: number;
  public_id: string;
  status: string;
  status_label: string;
  amount_rub: string;
  rate: string | null;
  markup_pct: string | null;
  usdt_amount: string | null;
  credited_usdt: string | null;
  qr_url: string | null;
  created_at: string | null;
  expires_at: string | null;
  paid_at: string | null;
  finished_at: string | null;
  user_id: number;
  rate_source: string | null;
  margin_rub: string | null;
  provider: string | null;
  provider_payment_id: string | null;
  paid_amount_rub: string | null;
  payer_info: unknown;
  review_reason: string | null;
  source: string;
  user?: UserRef | null;
}

export interface AfEvent {
  id: number;
  user_id: number;
  rule: string;
  severity: "info" | "suspicious" | "block";
  details: Record<string, unknown> | null;
  created_at: string | null;
  resolved_at: string | null;
  resolved_by: number | null;
}

export interface User {
  id: number;
  tg_id: number | null;
  tg_username: string | null;
  first_name: string | null;
  last_name: string | null;
  status: "registering" | "active" | "blocked";
  kyc_status: string;
  markup_pct: string | null;
  order_min_rub: number | null;
  order_max_rub: number | null;
  daily_turnover_limit_rub: number | null;
  balance: string;
  hold: string;
  can_create_orders: boolean;
  orders_block_reason: string | null;
  is_suspicious: boolean;
  af_enabled: boolean;
  af_reset_at: string | null;
  bot_blocked: boolean;
  created_at: string | null;
  registered_at: string | null;
  last_seen_at: string | null;
  available_for_withdrawal?: string | null;
  antifraud_events?: AfEvent[];
}

export interface Counts {
  created: number;
  paid: number;
  expired: number;
  cancelled: number;
  rejected: number;
  review: number;
  failed: number;
  pending: number;
  finished: number;
  conversion_pct: string | null;
}

export interface DayStats extends Counts {
  day: string;
  turnover_rub: string;
  usdt: string | null;
  margin_rub: string | null;
}

export interface ClientStats {
  counts: Counts;
  conversion_pct: string | null;
  window_size: number;
  window_finished: number;
  window_conversion_pct: string | null;
  unpaid_streak: number;
  turnover_rub: string;
  usdt_received: string | null;
  margin_rub: string | null;
  avg_check_rub: string | null;
  median_pay_seconds: number | null;
  withdrawals_count: number;
  withdrawals_sum: string | null;
  withdrawals_fees: string | null;
  first_deal_at: string | null;
  last_deal_at: string | null;
}

export interface LedgerEntry {
  id: number;
  type: string;
  balance_delta: string | null;
  hold_delta: string | null;
  balance_after: string | null;
  hold_after: string | null;
  order_id: number | null;
  withdrawal_id: number | null;
  staff_id: number | null;
  comment: string | null;
  created_at: string | null;
}

export interface PeriodStats {
  start: string | null;
  end: string | null;
  orders: Counts;
  conversion_pct: string | null;
  turnover_rub: string;
  usdt_issued: string | null;
  margin_rub: string | null;
  new_clients: number;
  active_clients: number;
  withdrawals_completed: number;
  withdrawals_completed_sum: string | null;
  fees_collected: string | null;
}

export interface CurrentRate {
  value: string | null;
  source: string;
  age_seconds: number;
}

export interface Dashboard {
  periods: Record<string, PeriodStats>;
  queue: { count: number; sum: string | null };
  liabilities: { balance: string | null; hold: string | null };
  rate: CurrentRate | null;
  last_rate_fetched_at: string | null;
  af_blocked: number;
  suspicious: number;
}

export interface RateView {
  current: CurrentRate | null;
  manual_rate: string | null;
  rate_field: string;
  last_fetch: {
    value: string | null;
    accepted: boolean;
    reject_reason: string | null;
    fetched_at: string | null;
    ask: string | null;
    bid: string | null;
  } | null;
  last_accepted: { value: string | null; fetched_at: string | null } | null;
  pending_jump: boolean;
}

export type SettingType = "bool" | "int" | "decimal" | "choice" | "string";
export type SettingValue = string | number | boolean | null;

export interface SettingMeta {
  key: string;
  label: string;
  type: SettingType;
}

export interface GeneralSettingMeta extends SettingMeta {
  group: string;
}

export interface RuleMeta {
  rule: string;
  title: string;
  description: string;
  toggle: string | null;
  params: SettingMeta[];
}

export interface SettingsResponse {
  values: Record<string, SettingValue>;
  meta: { general: GeneralSettingMeta[]; rules: RuleMeta[] };
}

export interface BlacklistEntry {
  id: number;
  network: string;
  address: string;
  comment: string | null;
  created_by: number | null;
  created_at: string | null;
}

export interface AuditEntry {
  id: number;
  staff_id: number | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  before: unknown;
  after: unknown;
  comment: string | null;
  ip: string | null;
  created_at: string | null;
}

export interface TotpSetup {
  secret: string;
  otpauth_uri: string;
  qr_png_base64: string;
}
