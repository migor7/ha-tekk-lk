"""Клиент личного кабинета ТЕКК (https://lk.tekk.pro).

Как устроен кабинет (биллинг ООО «ГТК», Java JSF + Spring Security):
- вход: POST /j_spring_security_check, поля j_username и j_password,
  без капчи и CSRF; сессия в cookie JSESSIONID; неверный пароль → 302 на
  /login.xhtml?error=1;
- без сессии любая /user/* отвечает 302 на /login.xhtml;
- данные только в HTML: главная /user/index.xhtml (баланс, тариф, абонплата);
- таблицы «Списания» и «Платежи» приходят по JSF ajax (кнопка «Показать»):
  POST на ту же страницу с Faces-Request: partial/ajax, ответ — XML.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any

import aiohttp

from . import parse
from .const import BASE_URL, LOGIN_RETRY_PAUSE, REPORT_DAYS, REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)

PAGE_INDEX = "/user/index.xhtml"
PAGE_DISCARDINGS = "/user/report/discardings/index.xhtml"
PAGE_PAYMENTS = "/user/report/payments/index.xhtml"
PAGE_PROMISE = "/user/payments/promise/index.xhtml"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (HomeAssistant tekk_lk)",
    "Accept-Language": "ru-RU,ru;q=0.9",
}


class TekkError(Exception):
    """Ошибка обращения к кабинету."""


class TekkAuthError(TekkError):
    """Неверный логин или пароль."""


class _SessionExpired(TekkError):
    """Кабинет отправил на страницу входа."""


def describe_error(err: BaseException, step: str) -> str:
    if isinstance(err, (asyncio.TimeoutError, TimeoutError)):
        return f"{step}: сайт не ответил за {REQUEST_TIMEOUT} с"
    return f"{step}: {str(err) or err.__class__.__name__}"


def _to_login(status: int, location: str | None) -> bool:
    return status in (301, 302, 303, 307) and "login" in (location or "")


class TekkApi:
    def __init__(self, session: aiohttp.ClientSession, login: str, password: str) -> None:
        self._session = session
        self._login = login
        self._password = password
        self._logged_in = False
        self._login_lock = asyncio.Lock()
        self._last_failed_login = 0.0
        self.raw: dict[str, Any] = {}  # для диагностики (личные данные вырезаются там)

    # ---------- транспорт ----------

    async def _request(
        self, method: str, path: str, *, data: Any = None, headers: dict | None = None
    ) -> tuple[int, str | None, str]:
        hdrs = dict(_HEADERS)
        if headers:
            hdrs.update(headers)
        async with asyncio.timeout(REQUEST_TIMEOUT):
            async with self._session.request(
                method,
                f"{BASE_URL}{path}",
                data=data,
                headers=hdrs,
                allow_redirects=False,
            ) as resp:
                body = (await resp.read()).decode(resp.charset or "utf-8", "replace")
                return resp.status, resp.headers.get("Location"), body

    # ---------- вход ----------

    async def async_login(self) -> None:
        """Вход по номеру договора (логину) и паролю."""
        # GET страницы входа: сервер выдаёт JSESSIONID до отправки пароля
        await self._request("GET", "/login.xhtml")
        status, location, _ = await self._request(
            "POST",
            "/j_spring_security_check",
            data={"j_username": self._login, "j_password": self._password},
            headers={"Referer": f"{BASE_URL}/login.xhtml"},
        )
        self.raw["login"] = {"status": status, "location": _path_only(location)}
        if status not in (301, 302, 303):
            raise TekkError(f"вход: неожиданный ответ HTTP {status}")
        if "error" in (location or "") or "login" in (location or ""):
            raise TekkAuthError("неверный логин или пароль")
        self._logged_in = True

    async def async_ensure_login(self) -> None:
        if self._logged_in:
            return
        async with self._login_lock:
            if self._logged_in:
                return
            pause = LOGIN_RETRY_PAUSE - (time.monotonic() - self._last_failed_login)
            if self._last_failed_login and pause > 0:
                raise TekkError(f"вход: пауза после неудачной попытки ещё {int(pause)} с")
            try:
                await self.async_login()
            except TekkAuthError:
                self._last_failed_login = time.monotonic()
                raise
            self._last_failed_login = 0.0

    async def _page(self, method: str, path: str, **kw) -> str:
        """Страница кабинета; если сессия истекла — один повторный вход."""
        await self.async_ensure_login()
        for attempt in range(2):
            status, location, body = await self._request(method, path, **kw)
            expired = _to_login(status, location) or (
                status == 200 and parse.is_login_page(body)
            )
            if not expired:
                if status >= 400:
                    raise TekkError(f"{path}: HTTP {status}")
                if status in (301, 302, 303):
                    raise TekkError(f"{path}: перенаправление на {_path_only(location)}")
                return body
            if attempt:
                break
            self._logged_in = False
            await self.async_ensure_login()
        # пароль только что подошёл, а страница всё равно просит вход
        raise TekkError(f"{path}: кабинет не сохранил вход")

    # ---------- отчёты ----------

    async def async_report(self, path: str, days: int = REPORT_DAYS) -> dict[str, Any]:
        """Таблица «Списания» или «Платежи» за последние days дней."""
        page = await self._page("GET", path)
        form = parse.report_form(page, path)
        if not form or not form.get("button") or not form["fields"].get("javax.faces.ViewState"):
            self.raw[path] = {"error": "форма отчёта не найдена"}
            raise TekkError(f"{path}: форма отчёта не найдена")
        now = datetime.now()
        start = (now - timedelta(days=days)).strftime("%d.%m.%Y 00:00:00")
        stop = now.strftime("%d.%m.%Y 23:59:59")
        fields = dict(form["fields"])
        fields["dateStart"] = start
        fields["dateStop"] = stop
        button = form["button"]["name"]
        render = form.get("render") or "paymentListTable noData"
        fields.update(
            {
                button: form["button"]["value"],
                "javax.faces.source": button,
                "javax.faces.partial.event": "click",
                "javax.faces.partial.execute": f"{button} @form",
                "javax.faces.partial.render": render,
                "javax.faces.behavior.event": "action",
                "javax.faces.partial.ajax": "true",
            }
        )
        body = await self._page(
            "POST",
            form.get("action") or path,
            data=fields,
            headers={
                "Faces-Request": "partial/ajax",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{BASE_URL}{path}",
            },
        )
        updates = parse.partial_updates(body)
        table_html = next(
            (v for k, v in updates.items() if "paymentListTable" in k or "<table" in v), ""
        )
        table = parse.parse_table(table_html)
        records = parse.table_records(table)
        self.raw[path] = {
            "period": [start, stop],
            "updates": sorted(updates),
            "headers": table["headers"],
            "rows": table["rows"][:15],
            "rows_total": len(table["rows"]),
            "response_head": body[:300] if not updates else None,
        }
        return {"records": records, "headers": table["headers"]}

    # ---------- всё сразу ----------

    async def async_fetch_all(self) -> dict[str, Any]:
        index_html = await self._page("GET", PAGE_INDEX)
        index = parse.parse_index(index_html)
        if index["balance"] is None:
            self.raw[PAGE_INDEX] = {"text": parse.content_text(index_html)[:1500]}
            raise TekkError("главная: баланс не найден на странице")
        self.raw[PAGE_INDEX] = {k: v for k, v in index.items()}

        async def optional(coro, step):
            try:
                return await coro
            except TekkAuthError:
                raise
            except (TekkError, aiohttp.ClientError, TimeoutError, ValueError) as err:
                _LOGGER.debug("%s", describe_error(err, step))
                self.raw.setdefault("errors", {})[step] = describe_error(err, step)
                return None

        # по очереди: JSF держит состояние форм в сессии, параллель ему ни к чему
        discardings = await optional(self.async_report(PAGE_DISCARDINGS), "списания")
        payments = await optional(self.async_report(PAGE_PAYMENTS), "платежи")
        promise = await optional(self._promise(), "обещанный платёж")
        return {
            "index": index,
            "discardings": (discardings or {}).get("records"),
            "payments": (payments or {}).get("records"),
            "promise": promise,
        }

    async def _promise(self) -> dict[str, Any]:
        page = await self._page("GET", PAGE_PROMISE)
        data = parse.parse_promise(page)
        self.raw[PAGE_PROMISE] = data
        return data


def _path_only(url: str | None) -> str | None:
    if not url:
        return url
    return "/" + url.split("://", 1)[-1].split("/", 1)[-1] if "://" in url else url
