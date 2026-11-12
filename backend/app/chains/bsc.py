"""BEP-20 USDT verification via BSC JSON-RPC (with RPC failover)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from app.chains.addresses import BSC_USDT_CONTRACT, to_checksum_address
from app.chains.base import NOT_FOUND, TRANSFER_TOPIC, ChainUnavailable, Transfer, TransferInfo, pick_transfer
from app.logging import get_logger

log = get_logger(__name__)

DECIMALS = Decimal(10) ** 18


class BscVerifier:
    usdt_contract = BSC_USDT_CONTRACT
    required_confirmations = 15

    def __init__(self, http: httpx.AsyncClient, rpc_urls: list[str]) -> None:
        if not rpc_urls:
            raise ValueError("at least one BSC RPC URL is required")
        self._http = http
        self._urls = list(rpc_urls)

    def explorer_url(self, tx_hash: str) -> str:
        return f"https://bscscan.com/tx/{tx_hash}"

    async def rpc(self, method: str, params: list[Any]) -> Any:
        errors: list[str] = []
        for url in self._urls:
            try:
                resp = await self._http.post(
                    url,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                    timeout=httpx.Timeout(15.0, connect=5.0),
                )
                if resp.status_code != 200:
                    raise ChainUnavailable(f"HTTP {resp.status_code}")
                doc = resp.json()
                if not isinstance(doc, dict):
                    raise ChainUnavailable("invalid JSON-RPC response")
                if doc.get("error"):
                    raise ChainUnavailable(f"RPC error {doc['error']}")
                if "result" not in doc:
                    raise ChainUnavailable("no result")
                return doc["result"]
            except (httpx.HTTPError, ValueError, ChainUnavailable) as exc:
                errors.append(f"{httpx.URL(url).host}: {type(exc).__name__}")
                log.warning("bsc_rpc_failed", host=httpx.URL(url).host, method=method, error=str(exc)[:200])
        raise ChainUnavailable("all BSC RPC endpoints failed: " + "; ".join(errors))

    async def get_usdt_transfer(self, tx_hash: str, expected_to: str) -> TransferInfo:
        receipt = await self.rpc("eth_getTransactionReceipt", [tx_hash])
        if receipt is None:
            return NOT_FOUND
        if not isinstance(receipt, dict) or "blockNumber" not in receipt:
            raise ChainUnavailable("unexpected receipt format")
        success = receipt.get("status") == "0x1"
        try:
            tx_block = int(receipt["blockNumber"], 16)
            head = int(await self.rpc("eth_blockNumber", []), 16)
            block = await self.rpc("eth_getBlockByNumber", [receipt["blockNumber"], False])
            raw_ts = block.get("timestamp") if isinstance(block, dict) else None
            if not raw_ts:
                # a lagging node may not know the block yet; logs carry blockTimestamp on most nodes
                raw_ts = next(
                    (lg.get("blockTimestamp") for lg in receipt.get("logs") or [] if lg.get("blockTimestamp")), None
                )
            if not raw_ts:
                raise ChainUnavailable("block timestamp is unknown")
            block_time = datetime.fromtimestamp(int(raw_ts, 16), tz=UTC)
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
            raise ChainUnavailable(f"unexpected RPC response: {type(exc).__name__}") from exc
        transfers: list[Transfer] = []
        for entry in receipt.get("logs") or []:
            try:
                topics = entry["topics"]
                if len(topics) < 3 or topics[0].lower().removeprefix("0x") != TRANSFER_TOPIC:
                    continue
                contract = to_checksum_address(str(entry["address"]))
                to = to_checksum_address("0x" + topics[2][-40:])
                amount = Decimal(int(str(entry["data"]), 16)) / DECIMALS
            except (KeyError, ValueError, TypeError, IndexError, AttributeError, ArithmeticError):
                continue
            transfers.append(Transfer(contract, to, amount))
        chosen = pick_transfer(transfers, self.usdt_contract, expected_to, case_insensitive=True)
        return TransferInfo(
            found=True,
            success=success,
            confirmations=max(head - tx_block, 0),
            token_contract=chosen.contract if chosen else None,
            to_address=chosen.to_address if chosen else None,
            amount=chosen.amount if chosen else None,
            block_time=block_time,
            raw={"receipt_block": tx_block, "head": head},
        )
