import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Attempt } from "@shared/api";
import { formatUsdt, validateUsdt } from "@shared/format";
import { confirmAction, haptic, readClipboard } from "@shared/telegram";
import { AsyncButton, ErrorView, Loader, Notice, Row, StatusBadge, errorMessage, useLoad } from "@shared/ui";
import { api, type WithdrawConfig, type WithdrawPreview, type Withdrawal } from "../api";
import { AmountField, Footer, Icon, Result, TopBar } from "./common";

type Step = "network" | "address" | "amount" | "preview" | "done";
const ORDER: Step[] = ["network", "address", "amount", "preview", "done"];

function trimAmount(available: string): string {
  const [i, f = ""] = available.split(".");
  return i + "." + (f + "00").slice(0, 2);
}

export function Withdraw() {
  const nav = useNavigate();
  const cfg = useLoad(() => api.get<WithdrawConfig>("/api/client/withdrawals/config"));
  const [step, setStep] = useState<Step>("network");
  const [network, setNetwork] = useState<"TRC20" | "BEP20" | null>(null);
  const [address, setAddress] = useState("");
  const [amount, setAmount] = useState("");
  const [preview, setPreview] = useState<WithdrawPreview | null>(null);
  const [result, setResult] = useState<Withdrawal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const attempt = useRef(new Attempt());

  if (cfg.loading) return <Loader />;
  if (cfg.error || !cfg.data) return <ErrorView error={cfg.error} onRetry={() => cfg.reload()} />;
  const c = cfg.data;
  const net = c.networks.find((n) => n.network === network);
  const amountError = validateUsdt(amount);

  async function doPreview(value: string) {
    setError(null);
    try {
      const pv = await api.post<WithdrawPreview>("/api/client/withdrawals/preview", {
        network,
        address,
        amount: value.replace(",", "."),
      });
      // New parameters → a new attempt: the key of a failed earlier attempt is never sent with another request.
      if (!preview || preview.network !== pv.network || preview.address !== pv.address || preview.amount !== pv.amount) {
        attempt.current.done();
      }
      setPreview(pv);
      setStep("preview");
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function checkAddress() {
    setError(null);
    try {
      // the preview validates the address (format, contract, blacklist) before asking the amount
      await api.post<WithdrawPreview>("/api/client/withdrawals/preview", {
        network,
        address,
        amount: c.min_amount,
      });
      setStep("amount");
    } catch (e) {
      const msg = errorMessage(e);
      if (/адрес|контракт|нулев/i.test(msg)) setError(msg);
      else setStep("amount"); // amount-related errors are shown on the next step
    }
  }

  const progress = (
    <div className="steps" aria-hidden>
      {ORDER.slice(0, 4).map((s, i) => (
        <span key={s} className={ORDER.indexOf(step) >= i ? "on" : ""} />
      ))}
    </div>
  );

  if (!c.enabled) {
    return (
      <div className="screen">
        <TopBar title="Вывод" />
        <Notice tone="warn">Вывод временно приостановлен. Попробуйте позже.</Notice>
      </div>
    );
  }

  return (
    <div className="screen">
      <TopBar title="Вывод USDT" />
      {step !== "done" && progress}

      {step === "network" && (
        <>
          <h2 className="lead">Выберите сеть</h2>
          <p className="lead-sub">
            Доступно к выводу: <b>{formatUsdt(c.available)}</b>, минимум {formatUsdt(c.min_amount)}
          </p>
          <div className="card list">
            {c.networks.map((n) => (
              <button
                key={n.network}
                className="tile"
                onClick={() => {
                  setNetwork(n.network);
                  setStep("address");
                }}
              >
                <span className={`net-mark net-${n.network}`}>{n.network === "TRC20" ? "TRX" : "BNB"}</span>
                <span className="tile-body">
                  <span className="tile-title">{n.label}</span>
                  <div className="tile-sub">комиссия {formatUsdt(n.fee)}</div>
                </span>
                <span className="tile-chevron">
                  <Icon name="chevron" size={18} />
                </span>
              </button>
            ))}
          </div>
        </>
      )}

      {step === "address" && net && (
        <>
          <h2 className="lead">Куда отправить</h2>
          <p className="lead-sub">Адрес кошелька USDT в сети {net.label}</p>
          <div className="card stack">
            <textarea
              className="input mono"
              rows={3}
              value={address}
              spellCheck={false}
              autoCapitalize="off"
              aria-label={`Адрес кошелька USDT в сети ${net.label}`}
              onChange={(e) => setAddress(e.target.value.trim())}
              placeholder={net.network === "TRC20" ? "T…" : "0x…"}
            />
            <button className="btn btn-secondary btn-block" onClick={async () => setAddress((await readClipboard()).trim())}>
              <Icon name="paste" size={18} />
              Вставить из буфера
            </button>
          </div>
          {error && <Notice tone="bad">{error}</Notice>}
          <Footer>
            <AsyncButton disabled={!address} onClick={checkAddress}>
              Далее
            </AsyncButton>
            <button className="btn btn-ghost" onClick={() => setStep("network")}>
              Назад
            </button>
          </Footer>
        </>
      )}

      {step === "amount" && net && (
        <>
          <div className="amount">
            <div className="amount-label">Сумма списания</div>
            <AmountField
              unit="USDT"
              inputMode="decimal"
              value={amount}
              placeholder="0.00"
              aria-label="Сумма списания, USDT"
              onChange={(e) => setAmount(e.target.value.replace(/[^\d.,]/g, ""))}
            />
            <div className={`amount-hint ${amount && amountError ? "error" : ""}`}>
              {amount && amountError ? amountError : `комиссия ${formatUsdt(net.fee)} вычитается из суммы`}
            </div>
          </div>
          <div className="quick" style={{ gridTemplateColumns: "1fr" }}>
            <button className="chip" onClick={() => setAmount(trimAmount(c.available))}>
              Вывести всё ({formatUsdt(c.available)})
            </button>
          </div>
          {error && <Notice tone="bad">{error}</Notice>}
          <Footer>
            <AsyncButton disabled={!amount || !!amountError} onClick={() => doPreview(amount)}>
              Далее
            </AsyncButton>
            <button className="btn btn-ghost" onClick={() => setStep("address")}>
              Назад
            </button>
          </Footer>
        </>
      )}

      {step === "preview" && preview && (
        <>
          <h2 className="lead">Проверьте данные</h2>
          <p className="lead-sub">После подтверждения средства блокируются до исполнения.</p>
          <div className="card">
            <Row label="Сеть">{preview.network_label}</Row>
            <Row label="Адрес">
              <span className="address">{preview.address}</span>
            </Row>
            <Row label="Списывается">{formatUsdt(preview.amount)}</Row>
            <Row label="Комиссия">{formatUsdt(preview.fee)}</Row>
            <div className="row row-total">
              <span className="row-label">К получению</span>
              <span className="row-value">{formatUsdt(preview.amount_to_send)}</span>
            </div>
          </div>
          <Notice tone="warn">
            Убедитесь, что адрес принадлежит сети {preview.network_label}. Перевод в другую сеть приведёт к потере
            средств.
          </Notice>
          {error && <Notice tone="bad">{error}</Notice>}
          <Footer>
            <AsyncButton
              onClick={async () => {
                if (!(await confirmAction(`Вывести ${preview.amount_to_send} USDT на ${preview.address}?`))) return;
                setError(null);
                try {
                  const body = { network: preview.network, address: preview.address, amount: preview.amount };
                  // same key only when resending exactly this request
                  const w = await api.post<Withdrawal>("/api/client/withdrawals", body, attempt.current.keyFor(body));
                  attempt.current.done();
                  haptic("success");
                  setResult(w);
                  setStep("done");
                } catch (e) {
                  setError(errorMessage(e));
                  haptic("error");
                }
              }}
            >
              Подтвердить
            </AsyncButton>
            <button
              className="btn btn-ghost"
              onClick={() => {
                setError(null);
                setStep("amount");
              }}
            >
              Изменить
            </button>
          </Footer>
        </>
      )}

      {step === "done" && result && (
        <>
          <Result
            icon={result.status === "cancelled" ? "cross" : "check"}
            tone={result.status === "cancelled" ? "info" : "ok"}
            title={`Заявка ${result.public_id} создана`}
          >
            К отправке {formatUsdt(result.amount_to_send)} ({result.network_label}). Средства заблокированы до
            исполнения. Пока заявка в очереди, её можно отменить.
          </Result>
          <div style={{ textAlign: "center", marginBottom: 12 }}>
            <StatusBadge status={result.status} label={result.status_label} />
          </div>
          {error && <Notice tone="bad">{error}</Notice>}
          <Footer>
            <button className="btn btn-primary" onClick={() => nav("/")}>
              На главную
            </button>
            {result.status === "pending" && (
              <AsyncButton
                variant="danger"
                onClick={async () => {
                  if (!(await confirmAction("Отменить вывод?"))) return;
                  try {
                    setResult(await api.post<Withdrawal>(`/api/client/withdrawals/${result.id}/cancel`));
                  } catch (e) {
                    setError(errorMessage(e));
                  }
                }}
              >
                Отменить вывод
              </AsyncButton>
            )}
          </Footer>
        </>
      )}
    </div>
  );
}
