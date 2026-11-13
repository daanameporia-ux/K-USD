"""Withdrawal networks switch (TRC20 only in production) and the client's address book."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.chains.addresses import validate_trc20
from app.config import load_config
from app.domain.enums import Network
from app.models import SavedAddress, User, Withdrawal
from app.services import address_book
from app.services import users as users_svc
from app.telegram import texts
from app.telegram.fsm import FSM
from tests.chain_fixtures import BSC_TO, TRON_TO
from tests.conftest import fund, login_client, login_staff

TRON_2 = "TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE"
TRON_3 = "TLa2f6VPqDgRE67v1736s7bJ8Ray5wYjU7"


@pytest.fixture
def trc20_only(container):
    """The production setup: only TRC20 is offered."""
    container.config = container.config.model_copy(update={"withdraw_networks": [Network.TRC20]})
    return container


async def user_id_of(container, tg_id: int) -> int:
    async with container.db() as s:
        return (await s.execute(select(User.id).where(User.tg_id == tg_id))).scalar_one()


def test_extra_test_addresses_are_valid() -> None:
    assert validate_trc20(TRON_2) == TRON_2 and validate_trc20(TRON_3) == TRON_3


def test_only_trc20_is_offered_by_default(monkeypatch) -> None:
    monkeypatch.delenv("WITHDRAW_NETWORKS")
    assert load_config().withdraw_networks == [Network.TRC20]
    monkeypatch.setenv("WITHDRAW_NETWORKS", " ")
    assert load_config().withdraw_networks == [Network.TRC20]
    monkeypatch.setenv("WITHDRAW_NETWORKS", "TRC20, BEP20,TRC20")
    assert load_config().withdraw_networks == [Network.TRC20, Network.BEP20]


async def test_bep20_is_not_offered_or_accepted(api, trc20_only, clock) -> None:
    h = await login_client(api, clock, 900)
    async with trc20_only.db() as s:
        user = await users_svc.get_by_tg(s, 900)
    await fund(trc20_only, user, "100")
    cfg = (await api.get("/api/client/withdrawals/config", headers=h)).json()
    assert [n["network"] for n in cfg["networks"]] == ["TRC20"]

    body = {"network": "BEP20", "address": BSC_TO, "amount": "50"}
    for path, extra in [
        ("/api/client/withdrawals/preview", {}),
        ("/api/client/withdrawals", {"Idempotency-Key": "k" * 16}),
    ]:
        resp = await api.post(path, headers={**h, **extra}, json=body)
        assert resp.status_code == 400 and resp.json()["error"]["code"] == "network_disabled", resp.text
    saved = await api.post("/api/client/addresses", headers=h, json={"network": "BEP20", "address": BSC_TO})
    assert saved.status_code == 400 and saved.json()["error"]["code"] == "network_disabled"
    async with trc20_only.db() as s:
        assert (await s.execute(select(Withdrawal))).scalars().all() == []

    ok = await api.post(
        "/api/client/withdrawals/preview", headers=h, json={**body, "network": "TRC20", "address": TRON_TO}
    )
    assert ok.status_code == 200

    admin = await login_staff(api, trc20_only, clock, 9100)
    keys = [i["key"] for i in (await api.get("/api/admin/settings", headers=admin)).json()["meta"]["general"]]
    assert "withdraw_fee_trc20" in keys and "withdraw_fee_bep20" not in keys


async def test_address_book_api(api, container, clock) -> None:
    h = await login_client(api, clock, 901)
    assert (await api.get("/api/client/addresses", headers=h)).json() == {"items": [], "max": 20}

    bad = await api.post("/api/client/addresses", headers=h, json={"network": "TRC20", "address": "T123"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_address"
    long = await api.post(
        "/api/client/addresses", headers=h, json={"network": "TRC20", "address": TRON_TO, "label": "я" * 41}
    )
    assert long.status_code == 400 and long.json()["error"]["code"] == "invalid_label"

    first = await api.post(
        "/api/client/addresses",
        headers=h,
        json={"network": "TRC20", "address": f" {TRON_TO} ", "label": "  Мой   кошелёк "},
    )
    assert first.status_code == 200, first.text
    assert first.json() == {
        "id": first.json()["id"],
        "network": "TRC20",
        "network_label": "TRC-20",
        "address": TRON_TO,
        "label": "Мой кошелёк",
    }
    second = (await api.post("/api/client/addresses", headers=h, json={"network": "TRC20", "address": TRON_2})).json()
    assert second["label"] == ""

    # saving the same address again renames it instead of creating a duplicate
    renamed = (
        await api.post(
            "/api/client/addresses", headers=h, json={"network": "TRC20", "address": TRON_TO, "label": "Binance"}
        )
    ).json()
    assert renamed["id"] == first.json()["id"] and renamed["label"] == "Binance"
    kept = (await api.post("/api/client/addresses", headers=h, json={"network": "TRC20", "address": TRON_TO})).json()
    assert kept["label"] == "Binance"
    items = (await api.get("/api/client/addresses", headers=h)).json()["items"]
    assert [(i["address"], i["label"]) for i in items] == [(TRON_2, ""), (TRON_TO, "Binance")]

    other = await login_client(api, clock, 902)
    assert (await api.get("/api/client/addresses", headers=other)).json()["items"] == []
    assert (await api.delete(f"/api/client/addresses/{second['id']}", headers=other)).status_code == 404
    assert (await api.delete(f"/api/client/addresses/{second['id']}", headers=h)).status_code == 200
    assert (await api.delete(f"/api/client/addresses/{second['id']}", headers=h)).status_code == 404
    assert len((await api.get("/api/client/addresses", headers=h)).json()["items"]) == 1


async def test_address_book_limit_and_blacklist(api, container, clock) -> None:
    h = await login_client(api, clock, 903)
    uid = await user_id_of(container, 903)
    async with container.db.begin() as s:
        for i in range(address_book.MAX_ADDRESSES):
            s.add(SavedAddress(user_id=uid, network=Network.BEP20, address=f"0x{i:040x}", label=""))
    full = await api.post("/api/client/addresses", headers=h, json={"network": "TRC20", "address": TRON_TO})
    assert full.status_code == 409 and full.json()["error"]["code"] == "too_many_addresses"

    admin = await login_staff(api, container, clock, 9101)
    listed = await api.post(
        "/api/admin/blacklist", headers=admin, json={"network": "TRC20", "address": TRON_2, "comment": "x"}
    )
    assert listed.status_code == 200, listed.text
    h2 = await login_client(api, clock, 904)
    banned = await api.post("/api/client/addresses", headers=h2, json={"network": "TRC20", "address": TRON_2})
    assert banned.status_code == 400 and banned.json()["error"]["code"] == "address_blacklisted"


# --- bot ---------------------------------------------------------------------------------


async def test_bot_single_network_and_address_book(bot, trc20_only, fake_tg) -> None:
    container = trc20_only
    await bot.register(50)
    async with container.db() as s:
        user = await users_svc.get_by_tg(s, 50)
    await fund(container, user, "200")

    await bot.send(50, "/addresses")
    assert bot.last(50) == texts.BOOK_EMPTY

    # one network: no network question, the address is asked right away
    await bot.send(50, texts.BTN_WITHDRAW)
    assert "Доступно к выводу: <b>200.00 USDT</b>" in bot.last(50) and "сети <b>TRC-20</b>" in bot.last(50)
    assert "BEP" not in bot.last(50) and bot.callback_data(50) == ["nav:cancel"]
    assert (await FSM.load(container.db, 50)).state == "wd:address"
    await bot.click(50, "wd:net:BEP20")  # a button from an old message
    assert (await FSM.load(container.db, 50)).data == {"network": "TRC20"}

    await bot.send(50, TRON_TO)
    await bot.send(50, "50")
    await bot.click(50, "wd:confirm", message_id=fake_tg.next_message_id)
    async with container.db() as s:
        (w,) = (await s.execute(select(Withdrawal))).scalars().all()
    assert bot.callback_data(50) == [f"wd:cancel:{w.id}", f"wd:save:{w.id}"]
    await bot.click(50, f"wd:save:{w.id}")
    assert bot.last(50) == texts.WD_SAVED
    await bot.click(50, f"wd:save:{w.id}")  # twice: still one entry
    async with container.db() as s:
        (saved,) = (await s.execute(select(SavedAddress))).scalars().all()
    assert (saved.user_id, saved.address, saved.label) == (user.id, TRON_TO, "")

    # the next withdrawal offers the saved address as a button
    await bot.send(50, texts.BTN_WITHDRAW)
    assert "книги адресов" in bot.last(50)
    assert bot.callback_data(50) == [f"wd:book:{saved.id}", "nav:cancel"]
    assert bot.buttons(50)[0]["text"] == "TGfb53…C2j6zsBY"[:6] + "…" + TRON_TO[-6:]
    await bot.click(50, f"wd:book:{saved.id}")
    assert "Введите сумму списания" in bot.last(50)
    assert (await FSM.load(container.db, 50)).data == {"network": "TRC20", "address": TRON_TO}
    await bot.send(50, "60")
    await bot.click(50, "wd:confirm", message_id=fake_tg.next_message_id)
    async with container.db() as s:
        second = (await s.execute(select(Withdrawal).order_by(Withdrawal.id.desc()))).scalars().first()
    assert second.address == TRON_TO and bot.callback_data(50) == [f"wd:cancel:{second.id}"]  # already saved

    # somebody else's saved address or withdrawal cannot be used
    await bot.register(51)
    async with container.db() as s:
        stranger = await users_svc.get_by_tg(s, 51)
    await fund(container, stranger, "100")
    await bot.send(51, texts.BTN_WITHDRAW)
    await bot.click(51, f"wd:book:{saved.id}")
    assert (await FSM.load(container.db, 51)).state == "wd:address"
    await bot.click(51, f"wd:save:{w.id}")
    async with container.db() as s:
        assert len((await s.execute(select(SavedAddress))).scalars().all()) == 1

    await bot.send(50, "/addresses")
    assert f"<code>{TRON_TO}</code>" in bot.last(50) and bot.callback_data(50) == [f"ab:del:{saved.id}"]
    await bot.click(51, f"ab:del:{saved.id}")
    async with container.db() as s:
        assert len((await s.execute(select(SavedAddress))).scalars().all()) == 1
    await bot.click(50, f"ab:del:{saved.id}")
    assert bot.last(50) == texts.BOOK_DELETED
    async with container.db() as s:
        assert (await s.execute(select(SavedAddress))).scalars().all() == []
