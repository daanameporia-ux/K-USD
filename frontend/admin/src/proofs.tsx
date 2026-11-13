import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { formatDateTime, formatRuPhone, formatRub } from "@shared/format";
import { AsyncButton, errorMessage } from "@shared/ui";
import { adminPath, api } from "./api";
import type { Order } from "./types";

const EXT: Record<string, string> = {
  "image/jpeg": "jpg",
  "image/png": "png",
  "image/webp": "webp",
  "image/heic": "heic",
  "application/pdf": "pdf",
};
const INLINE = ["image/jpeg", "image/png", "image/webp"];

/**
 * The client's payment screenshot with the staff decision: it is loaded on demand with the staff token, and
 * «Проверено» becomes available only after the file has actually been opened.
 */
export function ProofReview({ order, onChanged }: { order: Order; onChanged: (next: Order) => void }) {
  const [file, setFile] = useState<{ url: string; type: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [comment, setComment] = useState("");

  useEffect(() => {
    setFile(null);
    setError(null);
    setRejecting(false);
    setComment("");
  }, [order.id, order.proof_uploaded_at]);
  useEffect(() => () => void (file && URL.revokeObjectURL(file.url)), [file]);

  if (!order.proof_uploaded_at) return null;
  const reviewed = !!order.proof_reviewed_at;

  async function open() {
    setError(null);
    try {
      const resp = await api.raw("GET", adminPath(`/orders/${order.id}/proof`));
      const blob = await resp.blob();
      setFile({ url: URL.createObjectURL(blob), type: blob.type });
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function decide(action: "review" | "reject") {
    setError(null);
    try {
      const body = action === "reject" ? { comment: comment.trim() } : undefined;
      onChanged(await api.post<Order>(adminPath(`/orders/${order.id}/proof/${action}`), body));
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <div className="stack" data-testid={`proof-review-${order.id}`}>
      {error && <div className="field-error">{error}</div>}
      {file ? (
        <>
          {INLINE.includes(file.type) ? (
            <a href={file.url} target="_blank" rel="noreferrer">
              <img className="proof-img" src={file.url} alt={`Скриншот оплаты по заявке ${order.public_id}`} />
            </a>
          ) : (
            <p className="muted small">Этот формат не показывается в браузере — скачайте файл и откройте его.</p>
          )}
          <div className="btn-row">
            <a className="btn btn-secondary" href={file.url} download={`${order.public_id}.${EXT[file.type] ?? "bin"}`}>
              Скачать файл
            </a>
            {!reviewed && !rejecting && (
              <>
                <AsyncButton onClick={() => decide("review")}>Проверено</AsyncButton>
                <button className="btn btn-danger" onClick={() => setRejecting(true)}>
                  Отклонить скриншот
                </button>
              </>
            )}
          </div>
          {rejecting && !reviewed && (
            <div className="subform stack">
              <label className="field">
                <span>Что не так (клиент увидит этот текст и пришлёт новый скриншот)</span>
                <textarea rows={2} value={comment} maxLength={500} onChange={(e) => setComment(e.target.value)} />
              </label>
              <div className="btn-row">
                <AsyncButton variant="danger" disabled={comment.trim().length < 3} onClick={() => decide("reject")}>
                  Отклонить и запросить новый
                </AsyncButton>
                <button className="btn btn-secondary" onClick={() => setRejecting(false)}>
                  Отмена
                </button>
              </div>
            </div>
          )}
        </>
      ) : (
        <div className="btn-row">
          <AsyncButton variant="secondary" onClick={open}>
            Показать скриншот
          </AsyncButton>
          {!reviewed && <span className="muted small">Отметить «Проверено» можно после просмотра.</span>}
        </div>
      )}
    </div>
  );
}

export interface ProofsState {
  to_review: number;
  missing: number;
  reviewed: number;
  blocking: number;
  orders: Order[];
}

/** Withdrawal card: every paid order of the client whose screenshot still has to be reviewed. */
export function ClientProofs({ proofs, onChanged }: { proofs: ProofsState; onChanged: () => void }) {
  if (proofs.blocking === 0) {
    return (
      <p className="muted" data-testid="proofs-ok">
        {proofs.reviewed > 0
          ? `Все скриншоты оплат клиента проверены (${proofs.reviewed}).`
          : "У клиента нет оплат, по которым требуется скриншот."}
      </p>
    );
  }
  return (
    <div className="stack" data-testid="proofs-pending">
      <p>
        Осталось проверить: <b>{proofs.to_review}</b>
        {proofs.missing > 0 && (
          <>
            {" "}
            · клиент не загрузил: <b>{proofs.missing}</b>
          </>
        )}
        {proofs.reviewed > 0 && <span className="muted"> · уже проверено: {proofs.reviewed}</span>}
      </p>
      {proofs.orders.map((o) => (
        <div key={o.id} className="subform stack">
          <div>
            <Link to={`/orders/${o.id}`}>
              <b>{o.public_id}</b>
            </Link>{" "}
            · {formatRub(o.amount_rub)} · оплачена {formatDateTime(o.paid_at)}
            <div className="muted small">
              {o.buyer_name ?? "ФИО не запрашивалось"} · {o.buyer_phone ? formatRuPhone(o.buyer_phone) : "телефон не запрашивался"}
            </div>
          </div>
          {o.proof_uploaded_at ? (
            <ProofReview order={o} onChanged={onChanged} />
          ) : (
            <p className="muted small">Клиент ещё не загрузил скриншот. Вывод нельзя провести, пока он этого не сделает.</p>
          )}
        </div>
      ))}
    </div>
  );
}
