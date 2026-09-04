"""Tests for parsing raw OTE spot-rate responses."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.cz_energy_spot_prices.const import (
    Commodity,
    SpotRateIntervalType,
)
from custom_components.cz_energy_spot_prices.coordinator import SpotRateCoordinator
from custom_components.cz_energy_spot_prices.spot_rate import SpotRate


async def test_gas_ignores_empty_and_zero_placeholder_prices() -> None:
    """Missing and explicit-zero unpublished gas prices must be omitted."""
    response = """<?xml version="1.0" ?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">
  <SOAP-ENV:Body>
    <GetImPriceGResponse xmlns="http://www.ote-cr.cz/schema/service/public">
      <Result>
        <Item><Date>2026-08-29</Date><Price>67.53</Price></Item>
        <Item><Date>2026-08-30</Date><Price>   </Price></Item>
        <Item><Date>2026-08-31</Date><Price>0.00</Price></Item>
      </Result>
    </GetImPriceGResponse>
  </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""
    spot_rate = SpotRate()

    with patch.object(spot_rate, "_download", AsyncMock(return_value=response)):
        rates = await spot_rate.get_gas_rates(datetime(2026, 8, 30, 12, tzinfo=UTC))

    assert rates[SpotRateIntervalType.Day] == {
        datetime(2026, 8, 28, 22, tzinfo=UTC): Decimal("67.53")
    }


async def test_gas_discards_zero_prices_from_persisted_data(
    hass: HomeAssistant,
) -> None:
    """A previously cached OTE zero placeholder must not be restored."""
    coordinator = SpotRateCoordinator(hass, Commodity.Gas)
    coordinator._store.async_load = AsyncMock(  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
        return_value={
            "1day": {
                "2026-08-29T22:00:00+00:00": "67.53",
                "2026-08-30T22:00:00+00:00": "0.00",
            },
            "60min": {},
            "15min": {},
        }
    )

    assert await coordinator.async_load_persisted()
    assert coordinator.data is not None
    assert coordinator.data[SpotRateIntervalType.Day] == {
        datetime(2026, 8, 29, 22, tzinfo=UTC): Decimal("67.53")
    }


async def test_gas_rejects_persisted_data_without_daily_rates(
    hass: HomeAssistant,
) -> None:
    """A damaged gas store must fall through to the normal network refresh."""
    coordinator = SpotRateCoordinator(hass, Commodity.Gas)
    coordinator._store.async_load = AsyncMock(  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
        return_value={"60min": {}, "15min": {}}
    )

    assert not await coordinator.async_load_persisted()
    assert coordinator.data is None
