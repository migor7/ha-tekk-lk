"""Разбор HTML-страниц кабинета lk.tekk.pro (JSF, данные приходят в разметке).

Модуль без зависимостей от Home Assistant, чтобы его можно было проверять отдельно.
"""

from __future__ import annotations

import html as _html
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Any

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_NUM = re.compile(r"[-−–]?\s*\d[\d\s]*(?:[.,]\d+)?")
_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?")


def clean(fragment: str) -> str:
    """HTML → обычный текст в одну строку."""
    text = _html.unescape(_TAG.sub(" ", fragment or ""))
    return _WS.sub(" ", text.replace("\xa0", " ")).strip()


def money(text: str | None) -> float | None:
    """«2 345,67 руб.» → 2345.67; «-12,5» → -12.5."""
    if not text:
        return None
    m = _NUM.search(text.replace("\xa0", " "))
    if not m:
        return None
    raw = re.sub(r"\s", "", m.group(0)).replace("−", "-").replace("–", "-")
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def parse_date(text: str | None) -> datetime | None:
    """«31.10.2026 12:30:00» или «31.10.2026» → datetime (без часового пояса)."""
    if not text:
        return None
    m = _DATE.search(text)
    if not m:
        return None
    d, mo, y, hh, mm, ss = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0))
    except ValueError:
        return None


def is_login_page(page: str) -> bool:
    return 'name="j_username"' in page or "j_spring_security_check" in page and "j_password" in page


# ---------- главная ----------

_LABEL_PAIR = re.compile(
    r"<label[^>]*>(.*?)</label>\s*</div>\s*<div class=\"col-md-8\">(.*?)</div>", re.S
)
_SIX_PAIR = re.compile(
    r"<div class=\"col-md-6\">(.*?)</div>\s*<div class=\"col-md-6\">(.*?)</div>", re.S
)
_BALANCE = re.compile(r"Баланс\s*([-−–]?\s*\d[\d\s]*(?:[.,]\d+)?)\s*руб", re.S)
_STATUS = re.compile(r"Статус:\s*([^<]+)")
_UNREAD = re.compile(r"У Вас есть\s*(\d+)\s*сообщ")


def parse_index(page: str) -> dict[str, Any]:
    """Главная /user/index.xhtml: баланс, статус, данные абонента, услуги."""
    text = clean(page)
    m = _BALANCE.search(text)
    balance = money(m.group(1)) if m else None
    st = _STATUS.search(page)
    info = {clean(k): clean(v) for k, v in _LABEL_PAIR.findall(page)}
    unread = _UNREAD.search(text)

    services: list[dict[str, Any]] = []
    start = page.find("Подключенные услуги")
    if start >= 0:
        chunks = page[start:].split('<div class="caption">')[1:]
        for chunk in chunks:
            name = clean(chunk.split("</div>", 1)[0])
            pairs = {clean(k): clean(v) for k, v in _SIX_PAIR.findall(chunk)}
            if not name or not pairs:
                continue
            fee_text = pairs.get("Абонентская плата")
            services.append(
                {
                    "name": name,
                    "tariff": pairs.get("Текущий тариф"),
                    "fee": money(fee_text),
                    "fee_text": fee_text,
                    "address": pairs.get("Адрес предоставления"),
                    "fields": pairs,
                }
            )

    contract = info.get("Договор") or ""
    return {
        "balance": balance,
        "status": clean(st.group(1)) if st else None,
        "contract": contract.split(" от ")[0].strip() or None,
        "account": info.get("Номер лицевого счета"),
        "scheme": info.get("Схема расчетов"),
        "threshold": money(info.get("Порог отключения")),
        "subscriber_type": info.get("Тип абонента"),
        "unread_messages": int(unread.group(1)) if unread else None,
        "info": info,
        "services": services,
    }


# ---------- формы отчётов (JSF ajax) ----------

_FORM = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.S)
_ATTR = re.compile(r"([\w:.-]+)=\"([^\"]*)\"")
_INPUT = re.compile(r"<input\b([^>]*)>", re.S)


def report_form(page: str, action_part: str) -> dict[str, Any] | None:
    """Форма поиска отчёта: id формы, поля, кнопка «Показать», ViewState."""
    for attrs, body in _FORM.findall(page):
        a = dict(_ATTR.findall(attrs))
        if action_part not in a.get("action", "") or 'type="submit"' not in body:
            continue  # на странице есть и форма сообщений с тем же action
        fields: dict[str, str] = {}
        button = None
        for inp in _INPUT.findall(body):
            ia = dict(_ATTR.findall(inp))
            name = ia.get("name")
            if not name:
                continue
            if ia.get("type") == "submit":
                button = button or {"name": name, "value": _html.unescape(ia.get("value", ""))}
                continue
            fields[name] = _html.unescape(ia.get("value", ""))
        render = None
        if button:
            m = re.search(r"'@form','([^']*)'", body)
            render = m.group(1) if m else None
        return {
            "id": a.get("id") or a.get("name"),
            "action": a.get("action"),
            "fields": fields,
            "button": button,
            "render": render,
        }
    return None


def partial_updates(xml_text: str) -> dict[str, str]:
    """partial-response JSF → {id: html}."""
    out: dict[str, str] = {}
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError:
        return out
    for upd in root.iter("update"):
        out[upd.get("id", "")] = upd.text or ""
    return out


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headers: list[str] = []
        self.rows: list[list[str]] = []
        self._section = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in ("thead", "tbody", "tfoot"):
            self._section = tag
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(_WS.sub(" ", "".join(self._cell).replace("\xa0", " ")).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._section == "thead":
                self.headers = self._row
            elif self._section != "tfoot" and any(self._row):
                self.rows.append(self._row)
            self._row = None
        elif tag in ("thead", "tbody", "tfoot"):
            self._section = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_table(fragment: str) -> dict[str, Any]:
    p = _TableParser()
    p.feed(fragment or "")
    return {"headers": p.headers, "rows": p.rows}


def _col(headers: list[str], *names: str) -> int | None:
    low = [h.lower().replace("c", "с") for h in headers]  # «Cумма» с латинской C
    for n in names:
        for i, h in enumerate(low):
            if n in h:
                return i
    return None


def table_records(table: dict[str, Any]) -> list[dict[str, Any]]:
    """Строки отчёта (списания или платежи) → [{date, amount, ...}], новые первыми."""
    headers = table.get("headers") or []
    i_date = _col(headers, "дата")
    i_sum = _col(headers, "сумма")
    out = []
    for row in table.get("rows") or []:
        cells = dict(zip(headers, row)) if headers else {}
        when = parse_date(row[i_date]) if i_date is not None and i_date < len(row) else None
        if when is None:
            when = next((parse_date(c) for c in row if parse_date(c)), None)
        amount = money(row[i_sum]) if i_sum is not None and i_sum < len(row) else None
        if when is None and amount is None:
            continue
        out.append({"date": when, "amount": amount, "cells": cells or row})
    out.sort(key=lambda r: r["date"] or datetime.min, reverse=True)
    return out


# ---------- обещанный платёж ----------

_PROMISE_UNTIL = re.compile(
    r"(?:действ\w*|актив\w*|подключ\w*|предоставл\w*)[^.<]{0,60}?до\s*(\d{2}\.\d{2}\.\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)",
    re.I,
)


def content_text(page: str) -> str:
    """Текст основной части страницы (без меню и подвала)."""
    i = page.find('class="page-content"')
    body = page[page.find(">", i) + 1 :] if i >= 0 else page
    j = body.find('class="footer"')
    body = body[:j] if j >= 0 else body
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", body, flags=re.S)
    return clean(body)


def parse_promise(page: str) -> dict[str, Any]:
    text = content_text(page)
    m = _PROMISE_UNTIL.search(text)
    can = bool(re.search(r"value=\"Подключить\"|>\s*Подключить\s*<", page))
    active: bool | None
    if m:
        active = True
    elif can:
        active = False
    else:
        active = None
    return {
        "active": active,
        "until": parse_date(m.group(1)) if m else None,
        "can_activate": can,
        "text": text[:600],
    }


def add_months(d: date, n: int, day: int | None = None) -> date:
    """Дата через n месяцев на тот же (или заданный) день, с поправкой на короткие месяцы."""
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    want = day or d.day
    for dd in range(want, 27, -1) if want > 28 else (want,):
        try:
            return date(y, m, dd)
        except ValueError:
            continue
    return date(y, m, 28)
