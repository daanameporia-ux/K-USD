import type { InputHTMLAttributes, ReactNode } from "react";
import { useNavigate } from "react-router-dom";

const ICONS = {
  plus: "M12 5v14M5 12h14",
  up: "M7 17 17 7M9 7h8v8",
  down: "M17 7 7 17M15 17H7V9",
  clock: "M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
  back: "M15 5l-7 7 7 7",
  chevron: "M9 5l7 7-7 7",
  check: "M5 12.5l4.5 4.5L19 7.5",
  cross: "M6 6l12 12M18 6 6 18",
  alert: "M12 8v5M12 16.5v.5M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
  shield: "M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6l7-3ZM9 12l2.2 2.2L15 10.5",
  qr: "M4 4h6v6H4V4ZM14 4h6v6h-6V4ZM4 14h6v6H4v-6ZM14 14h2v2h-2v-2ZM18 14h2v2h-2v-2ZM14 18h2v2h-2v-2ZM18 18h2v2h-2v-2Z",
  wallet: "M4 7.5A2.5 2.5 0 0 1 6.5 5H18v3M4 7.5V17a2 2 0 0 0 2 2h14V8H6.5A2.5 2.5 0 0 1 4 7.5ZM16 13.5h1",
  paste: "M9 5h6v3H9V5ZM9 6.5H6V20h12V6.5h-3",
  chat: "M5 6h14v10H10l-4 3v-3H5V6Z",
  link: "M10 14 20 4M14 4h6v6M18 14v5H5V6h5",
  bolt: "M13 3 5 13.5h6L10 21l8-10.5h-6L13 3Z",
} as const;

export type IconName = keyof typeof ICONS;

export function Icon({ name, size = 22 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d={ICONS[name]} />
    </svg>
  );
}

/** Round coloured badge with an icon: the leading element of list tiles and result screens. */
export function Bubble({ icon, tone = "brand", size = "md" }: { icon: IconName; tone?: string; size?: "md" | "lg" }) {
  return (
    <span className={`bubble bubble-${tone} bubble-${size}`}>
      <Icon name={icon} size={size === "lg" ? 34 : 20} />
    </span>
  );
}

export function TopBar({ title, back = "/" }: { title: string; back?: string | null }) {
  const nav = useNavigate();
  return (
    <div className="topbar">
      {back !== null ? (
        <button className="round-btn" onClick={() => nav(back)} aria-label="Назад">
          <Icon name="back" size={20} />
        </button>
      ) : (
        <span className="round-btn-space" />
      )}
      <h1>{title}</h1>
      <span className="round-btn-space" />
    </div>
  );
}

/** "125.40 USDT" → the number large and the unit small and muted. */
export function Money({ value, className = "" }: { value: string; className?: string }) {
  const m = /^(.*?)[\s ]*(USDT|₽)$/.exec(value);
  if (!m) return <span className={`money ${className}`}>{value}</span>;
  return (
    <span className={`money ${className}`}>
      {m[1]}
      <span className="money-unit">{m[2]}</span>
    </span>
  );
}

/** Bottom area with the main action of a screen. */
export function Footer({ children }: { children: ReactNode }) {
  return <div className="footer">{children}</div>;
}

/** Centered state with a large icon: results, empty and error screens. */
export function Result({
  icon,
  tone,
  title,
  children,
}: {
  icon: IconName;
  tone: string;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="result">
      <Bubble icon={icon} tone={tone} size="lg" />
      <h2>{title}</h2>
      {children && <p className="muted">{children}</p>}
    </div>
  );
}

export function Card({ children }: { children: ReactNode }) {
  return <div className="card stack">{children}</div>;
}

/** Large borderless amount input that is exactly as wide as its text, so the currency sign sits right next to it. */
export function AmountField({ unit, ...input }: { unit: string } & InputHTMLAttributes<HTMLInputElement>) {
  const text = String(input.value ?? "");
  return (
    <div className={`amount-field ${text.length > 7 ? "long" : ""}`}>
      <span className="amount-box">
        <span className="amount-sizer" aria-hidden>
          {text || input.placeholder || "0"}
        </span>
        <input className="amount-input" {...input} />
      </span>
      <span className="amount-unit">{unit}</span>
    </div>
  );
}
