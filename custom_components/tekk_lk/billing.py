"""Расчёт следующего списания и срока, на который хватит баланса.

Без зависимостей от Home Assistant: на входе данные из api.async_fetch_all
и сегодняшняя дата.

Кабинет ТЕКК не показывает дату следующего списания, поэтому она выводится
из истории «Списаний»:
- если списания идут почти каждый день, абонплата посуточная;
- иначе — раз в месяц, в тот же день месяца, что и последнее крупное списание;
- если истории нет, считаем, что спишут 1-го числа (обычная схема «Авансовая»).
"""

from __future__ import annotations

import calendar
import math
from datetime import date, timedelta
from statistics import median
from typing import Any

from .parse import add_months

DEFAULT_DAY = 1


def services(data: dict) -> list[dict]:
    return (data.get("index") or {}).get("services") or []


def main_service(data: dict) -> dict:
    svc = services(data)
    for s in svc:
        if "интернет" in (s.get("name") or "").lower():
            return s
    paid = [s for s in svc if (s.get("fee") or 0) > 0]
    return (paid or svc or [{}])[0]


def monthly_fee(data: dict) -> float | None:
    fees = [s["fee"] for s in services(data) if s.get("fee") is not None]
    return round(sum(fees), 2) if fees else None


def balance(data: dict) -> float | None:
    return (data.get("index") or {}).get("balance")


def threshold(data: dict) -> float:
    return (data.get("index") or {}).get("threshold") or 0.0


def charges(data: dict) -> list[dict]:
    """Списания с датой и суммой, новые первыми (суммы по модулю)."""
    out = []
    for r in data.get("discardings") or []:
        if r.get("date") and r.get("amount") is not None and r["amount"] != 0:
            out.append({**r, "amount": abs(r["amount"])})
    return out


def last_record(records: list[dict] | None) -> dict | None:
    recs = [r for r in records or [] if r.get("date")]
    return recs[0] if recs else None


def schedule(data: dict, today: date) -> dict[str, Any]:
    """{mode, source, next_date, next_amount, paid_until, days_left, daily}."""
    fee = monthly_fee(data)
    bal = balance(data)
    avail = None if bal is None else bal - threshold(data)
    recs = charges(data)
    recent_days = {r["date"].date() for r in recs if (today - r["date"].date()).days <= 40}

    if len(recent_days) >= 5:
        # посуточное списание
        amounts = [r["amount"] for r in recs[:15]]
        daily = round(median(amounts), 2) if amounts else None
        if not daily and fee:
            daily = round(fee / calendar.monthrange(today.year, today.month)[1], 2)
        left = None
        if avail is not None and daily:
            left = max(0, math.floor(avail / daily)) if avail > 0 else 0
        return {
            "mode": "daily",
            "source": "списания",
            "charge_day": None,
            "next_date": today + timedelta(days=1),
            "next_amount": daily,
            "daily": daily,
            "days_left": left,
            "paid_until": today + timedelta(days=left) if left is not None else None,
        }

    big = [r for r in recs if not fee or r["amount"] >= 0.5 * fee]
    if big:
        last = big[0]["date"].date()
        day = last.day
        nxt = add_months(last, 1, day)
        source = "списания"
    else:
        day = DEFAULT_DAY
        nxt = today if today.day == day else add_months(today.replace(day=1), 1, day)
        source = "предположение: 1-е число"
    while nxt < today:
        nxt = add_months(nxt, 1, day)

    paid_until = days_left = None
    if fee and avail is not None:
        months = max(0, math.floor(avail / fee + 1e-9)) if avail > 0 else 0
        paid_until = add_months(nxt, months, day) if months else nxt
        days_left = max(0, (paid_until - today).days)
    return {
        "mode": "monthly",
        "source": source,
        "charge_day": day,
        "next_date": nxt,
        "next_amount": fee,
        "daily": None,
        "days_left": days_left,
        "paid_until": paid_until,
    }


def shortfall(data: dict, today: date) -> float | None:
    bal = balance(data)
    amount = schedule(data, today)["next_amount"]
    if bal is None or amount is None:
        return None
    return round(max(0.0, amount - (bal - threshold(data))), 2)
