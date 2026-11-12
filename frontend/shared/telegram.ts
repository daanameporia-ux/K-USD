/** Wrapper around window.Telegram.WebApp with safe fallbacks for a regular browser. */

export interface TgWebApp {
  initData: string;
  initDataUnsafe?: { user?: { id: number; username?: string; first_name?: string } };
  colorScheme?: "light" | "dark";
  ready(): void;
  expand(): void;
  openLink?(url: string): void;
  showConfirm?(message: string, cb: (ok: boolean) => void): void;
  showAlert?(message: string, cb?: () => void): void;
  HapticFeedback?: { notificationOccurred(type: "success" | "error" | "warning"): void };
  BackButton?: { show(): void; hide(): void; onClick(cb: () => void): void; offClick(cb: () => void): void };
  readTextFromClipboard?(cb: (text: string | null) => void): void;
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TgWebApp };
  }
}

export function webApp(): TgWebApp | null {
  return window.Telegram?.WebApp ?? null;
}

/** initData is present only when the page is opened inside Telegram. */
export function initData(): string {
  return webApp()?.initData ?? "";
}

export function insideTelegram(): boolean {
  return initData().length > 0;
}

export function boot(): void {
  const wa = webApp();
  try {
    wa?.ready();
    wa?.expand();
  } catch {
    /* ignore */
  }
  document.documentElement.dataset.scheme = wa?.colorScheme ?? "";
}

export function confirmAction(message: string): Promise<boolean> {
  const wa = webApp();
  if (wa?.showConfirm && insideTelegram()) {
    return new Promise((resolve) => wa.showConfirm!(message, (ok) => resolve(ok)));
  }
  return Promise.resolve(window.confirm(message));
}

export function openLink(url: string): void {
  const wa = webApp();
  if (wa?.openLink && insideTelegram()) wa.openLink(url);
  else window.open(url, "_blank", "noopener");
}

export function haptic(type: "success" | "error" | "warning"): void {
  try {
    webApp()?.HapticFeedback?.notificationOccurred(type);
  } catch {
    /* ignore */
  }
}

export async function readClipboard(): Promise<string> {
  try {
    if (navigator.clipboard?.readText) return (await navigator.clipboard.readText()) ?? "";
  } catch {
    /* fall through */
  }
  const wa = webApp();
  if (wa?.readTextFromClipboard) {
    return new Promise((resolve) => wa.readTextFromClipboard!((t) => resolve(t ?? "")));
  }
  return "";
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}
