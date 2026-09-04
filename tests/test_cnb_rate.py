"""Tests for parsing Czech National Bank exchange rates."""

from datetime import date
from decimal import Decimal
import json
from types import TracebackType
from typing import Any, Callable, Self, cast

import aiohttp

from custom_components.cz_energy_spot_prices.cnb_rate import CnbRate


class _Response:
    """Minimal asynchronous JSON response for the CNB client tests."""

    status = 200

    def __init__(self, payload: str) -> None:
        self._payload = payload

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass

    async def json(
        self, *, loads: Callable[[str], Any] = json.loads
    ) -> Any:
        return loads(self._payload)


class _Session:
    """Minimal client session returning one predefined response."""

    def __init__(self, payload: str) -> None:
        self._payload = payload

    def get(self, *args: object, **kwargs: object) -> _Response:
        return _Response(self._payload)


def _session_returning(payload: str) -> aiohttp.ClientSession:
    return cast(aiohttp.ClientSession, _Session(payload))


async def test_download_preserves_rate_decimal_precision() -> None:
    """CNB decimal literals must not make a binary float round-trip."""
    session = _session_returning(
        '{"rates": [{"currencyCode": "EUR", '
        '"rate": 24.3150000000000000000000000001}]}'
    )

    rates = await CnbRate(session=session).download_rates(date(2026, 9, 4))

    assert rates["rates"][0]["rate"] == Decimal(
        "24.3150000000000000000000000001"
    )


async def test_integer_rate_is_returned_as_decimal() -> None:
    """Integer-form JSON rates must still satisfy the Decimal API contract."""
    session = _session_returning(
        '{"rates": [{"currencyCode": "EUR", "rate": 24}]}'
    )

    rates = await CnbRate(session=session).get_day_rates(date(2026, 9, 4))

    assert rates["EUR"] == Decimal("24")
    assert isinstance(rates["EUR"], Decimal)
