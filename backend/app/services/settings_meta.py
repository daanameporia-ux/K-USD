"""Labels and descriptions of settings for the admin UI (Russian)."""

from __future__ import annotations

from typing import Any

from app.services.settings import BizSettings

GENERAL: list[tuple[str, str, str]] = [
    # group, key, label
    ("Покупки", "purchases_enabled", "Покупки включены (глобальный рубильник)"),
    ("Покупки", "order_min_rub", "Минимальная сумма заявки, ₽"),
    ("Покупки", "order_max_rub", "Максимальная сумма заявки, ₽"),
    ("Покупки", "order_ttl_minutes", "Срок жизни заявки, мин"),
    ("Покупки", "default_markup_pct", "Наценка нового клиента, %"),
    ("Выводы", "withdrawals_enabled", "Выводы включены (глобальный рубильник)"),
    ("Выводы", "withdraw_min_usdt", "Минимальная сумма вывода, USDT"),
    ("Выводы", "withdraw_fee_trc20", "Комиссия TRC-20, USDT"),
    ("Выводы", "withdraw_fee_bep20", "Комиссия BEP-20, USDT"),
    ("Выводы", "withdraw_admin_only_from_usdt", "Выводы от этой суммы закрывает только админ, USDT"),
    ("Курс", "rate_ttl_seconds", "Период обновления курса, с"),
    ("Курс", "rate_max_age_seconds", "Курс старше — заявки не создаются, с"),
    ("Курс", "rate_jump_guard_pct", "Защита от скачка курса, %"),
    ("Курс", "rate_field", "Поле курса Rapira (askPrice / bidPrice / close)"),
    ("Прочее", "support_contact", "Контакт поддержки"),
    ("Антифрод", "af_enabled", "Антифрод включён глобально"),
    ("Антифрод", "af_window_orders", "A1: окно — последних завершённых заявок"),
    ("Антифрод", "af_min_orders", "A1: минимум завершённых заявок"),
    ("Антифрод", "af_min_conversion_pct", "A1: порог конверсии, %"),
    ("Антифрод", "af_streak_suspicious", "A2: неоплаченных подряд → «подозрительный»"),
    ("Антифрод", "af_streak_block", "A3: неоплаченных подряд → блокировка заявок"),
]

RULES: list[dict[str, Any]] = [
    {
        "rule": "B1",
        "title": "Одна активная заявка",
        "description": "У клиента может быть только одна заявка в статусе «создаётся» или «ожидает оплаты». "
        "Гарантируется уникальным индексом в БД, поэтому выключить нельзя.",
        "toggle": None,
        "params": [],
    },
    {
        "rule": "B2",
        "title": "Лимит частоты заявок",
        "description": "Ограничивает количество заявок за час и за сутки. Клиенту сообщается, когда можно снова.",
        "toggle": "b2_enabled",
        "params": [("b2_per_hour", "Заявок в час"), ("b2_per_day", "Заявок в сутки")],
    },
    {
        "rule": "B3",
        "title": "Нарастающий кулдаун после неоплат",
        "description": "После нескольких неоплаченных заявок подряд — пауза перед созданием новой. "
        "Формат: «неоплат подряд:минут», через запятую.",
        "toggle": "b3_enabled",
        "params": [("b3_cooldowns", "Шаги паузы")],
    },
    {
        "rule": "B4",
        "title": "Общий адрес вывода у разных клиентов",
        "description": "Если разные клиенты выводят на один адрес — оба помечаются подозрительными, уходит алерт.",
        "toggle": "b4_enabled",
        "params": [],
    },
    {
        "rule": "B5",
        "title": "Чёрный список адресов",
        "description": "Вывод на адреса из чёрного списка запрещён.",
        "toggle": "b5_enabled",
        "params": [],
    },
    {
        "rule": "B6",
        "title": "Крупный вывод только через админа",
        "description": "Выводы от порога (раздел «Выводы») может закрыть только админ.",
        "toggle": "b6_enabled",
        "params": [("withdraw_admin_only_from_usdt", "Порог, USDT")],
    },
    {
        "rule": "B7",
        "title": "Несовпадение суммы оплаты",
        "description": "Если пришла сумма, отличная от суммы заявки, — заявка уходит на разбор без зачисления. "
        "Правило защищает деньги, поэтому выключить нельзя.",
        "toggle": None,
        "params": [],
    },
    {
        "rule": "B8",
        "title": "Глобальная просадка конверсии",
        "description": "Алерт, если за окно завершено не меньше N заявок и конверсия ниже порога. "
        "Опционально — автоматически выключает покупки.",
        "toggle": "b8_enabled",
        "params": [
            ("b8_window_minutes", "Окно, мин"),
            ("b8_min_orders", "Минимум завершённых"),
            ("b8_min_conversion_pct", "Порог конверсии, %"),
            ("b8_auto_pause", "Автоматически выключать покупки"),
        ],
    },
    {
        "rule": "B9",
        "title": "Сниженные лимиты для новичков",
        "description": "Пока у клиента меньше N оплаченных заявок — ограничены сумма заявки и суточный оборот.",
        "toggle": "b9_enabled",
        "params": [
            ("b9_paid_orders_threshold", "Оплаченных заявок до снятия"),
            ("b9_max_order_rub", "Макс. сумма заявки, ₽"),
            ("b9_max_daily_rub", "Макс. в сутки, ₽"),
        ],
    },
    {
        "rule": "B10",
        "title": "Суточный лимит оборота клиента",
        "description": "Ограничивает сумму заявок клиента за сутки (МСК). "
        "Индивидуальный лимит задаётся в карточке клиента.",
        "toggle": "b10_enabled",
        "params": [("b10_daily_rub", "Лимит в сутки, ₽")],
    },
    {
        "rule": "B11",
        "title": "Холд на вывод после покупки",
        "description": "Свежекупленные USDT недоступны к выводу N часов.",
        "toggle": "b11_enabled",
        "params": [("b11_hold_hours", "Часов")],
    },
    {
        "rule": "B12",
        "title": "Быстрый транзит",
        "description": "Вывод почти всего баланса вскоре после покупки — клиент помечается подозрительным.",
        "toggle": "b12_enabled",
        "params": [("b12_window_minutes", "Окно после покупки, мин"), ("b12_balance_pct", "Доля баланса, %")],
    },
    {
        "rule": "B13",
        "title": "Много разных плательщиков",
        "description": "Больше N разных плательщиков за период — клиент помечается подозрительным. "
        "Работает, только если платёжный провайдер передаёт данные плательщика.",
        "toggle": "b13_enabled",
        "params": [("b13_max_payers", "Макс. плательщиков"), ("b13_window_days", "Период, дней")],
    },
    {
        "rule": "B14",
        "title": "Суточный лимит вывода",
        "description": "Ограничивает сумму выводов клиента за сутки (МСК).",
        "toggle": "b14_enabled",
        "params": [("b14_daily_usdt", "Лимит в сутки, USDT")],
    },
]


def _type(key: str) -> str:
    annotation = str(BizSettings.model_fields[key].annotation)
    if "bool" in annotation:
        return "bool"
    if "int" in annotation:
        return "int"
    if "Decimal" in annotation:
        return "decimal"
    if "Literal" in annotation:
        return "choice"
    return "string"


def settings_meta() -> dict[str, Any]:
    return {
        "general": [{"group": g, "key": k, "label": label, "type": _type(k)} for g, k, label in GENERAL],
        "rules": [
            {
                **{k: v for k, v in r.items() if k != "params"},
                "params": [{"key": k, "label": label, "type": _type(k)} for k, label in r["params"]],
            }
            for r in RULES
        ],
    }
