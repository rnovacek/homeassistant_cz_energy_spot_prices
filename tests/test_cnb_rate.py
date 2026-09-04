"""Tests for parsing Czech National Bank exchange rates."""

from datetime import date
from decimal import Decimal
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp

from custom_components.cz_energy_spot_prices.cnb_rate import CnbRate


def _session_returning(payload: str) -> MagicMock:
    """Build an aiohttp session mock which decodes a raw JSON response."""
    response = MagicMock(status=200)

    async def json_response(*, loads=json.loads):
        return loads(payload)

    response.json = AsyncMock(side_effect=json_response)
    request_context = MagicMock()
    request_context.__aenter__ = AsyncMock(return_value=response)
    request_context.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock(spec=aiohttp.ClientSession)
    session.get.return_value = request_context
    return session


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
