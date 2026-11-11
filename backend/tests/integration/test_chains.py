from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import respx

from app.chains.base import ChainUnavailable
from app.chains.bsc import BscVerifier
from app.chains.tron import TronVerifier
from tests.chain_fixtures import (
    BSC_AMOUNT_REAL,
    BSC_RPC1,
    BSC_RPC2,
    BSC_TO,
    BSC_TX,
    TRON_AMOUNT,
    TRON_API,
    TRON_TO,
    TRON_TX,
    bsc_handler,
    mock_bsc,
    mock_tron,
)


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


@respx.mock
async def test_tron_real_fixture(http) -> None:
    mock_tron(respx.mock, "ok")
    v = TronVerifier(http, TRON_API, "key-123")
    info = await v.get_usdt_transfer(TRON_TX, TRON_TO)
    assert info.found and info.success and info.confirmations == 1
    assert info.token_contract == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    assert info.to_address == TRON_TO and info.amount == TRON_AMOUNT
    assert info.block_time.isoformat() == "2026-11-11T21:58:39+00:00"
    assert respx.calls[0].request.headers["TRON-PRO-API-KEY"] == "key-123"
    assert v.explorer_url(TRON_TX) == f"https://tronscan.org/#/transaction/{TRON_TX}"


@respx.mock
async def test_tron_fallback_to_logs_and_not_found(http) -> None:
    mock_tron(respx.mock, "logs_only")
    info = await TronVerifier(http, TRON_API).get_usdt_transfer(TRON_TX, TRON_TO)
    assert info.to_address == TRON_TO and info.amount == TRON_AMOUNT
    assert "TRON-PRO-API-KEY" not in respx.calls[0].request.headers
    respx.mock.reset()
    mock_tron(respx.mock, "not_found")
    assert not (await TronVerifier(http, TRON_API).get_usdt_transfer(TRON_TX, TRON_TO)).found


@respx.mock
async def test_tron_unavailable(http) -> None:
    mock_tron(respx.mock, "explorer_unavailable")
    with pytest.raises(ChainUnavailable):
        await TronVerifier(http, TRON_API).get_usdt_transfer(TRON_TX, TRON_TO)


@respx.mock
async def test_bsc_real_fixture(http) -> None:
    respx.post(BSC_RPC1).mock(side_effect=bsc_handler("real"))
    v = BscVerifier(http, [BSC_RPC1, BSC_RPC2])
    info = await v.get_usdt_transfer(BSC_TX, BSC_TO)
    assert info.found and info.success
    assert info.confirmations == 0x7EE1906 - 0x7EE18DB
    assert info.token_contract == "0x55d398326f99059fF775485246999027B3197955"
    assert info.to_address == BSC_TO and info.amount == BSC_AMOUNT_REAL
    assert info.block_time.isoformat() == "2026-11-11T21:59:48+00:00"
    assert v.explorer_url(BSC_TX) == f"https://bscscan.com/tx/{BSC_TX}"


@respx.mock
async def test_bsc_failover_to_second_rpc(http) -> None:
    respx.post(BSC_RPC1).mock(
        return_value=httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32005, "message": "limit"}}
        )
    )
    second = respx.post(BSC_RPC2).mock(side_effect=bsc_handler("ok"))
    info = await BscVerifier(http, [BSC_RPC1, BSC_RPC2]).get_usdt_transfer(BSC_TX, BSC_TO)
    assert info.amount == Decimal("39.13") and second.called


@respx.mock
async def test_bsc_all_rpcs_down(http) -> None:
    mock_bsc(respx.mock, "explorer_unavailable")
    with pytest.raises(ChainUnavailable) as exc:
        await BscVerifier(http, [BSC_RPC1, BSC_RPC2]).get_usdt_transfer(BSC_TX, BSC_TO)
    assert "bsc-dataseed.binance.org" in str(exc.value)


@respx.mock
async def test_bsc_not_found(http) -> None:
    mock_bsc(respx.mock, "not_found")
    assert not (await BscVerifier(http, [BSC_RPC1]).get_usdt_transfer(BSC_TX, BSC_TO)).found


def test_bsc_requires_rpc() -> None:
    with pytest.raises(ValueError):
        BscVerifier(httpx.AsyncClient(), [])
