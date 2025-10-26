# pyright: reportMissingTypeStubs=false

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cz_energy_spot_prices.config_flow import (
    CONF_COMMODITY,
    CONF_INTERVAL,
)
from custom_components.cz_energy_spot_prices.const import (
    CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY,
    CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY,
    CONF_ALLOW_CROSS_MIDNIGHT,
    CONF_CHEAPEST_BLOCKS,
    DOMAIN,
    SpotRateIntervalType,
)
from custom_components.cz_energy_spot_prices.coordinator import PRAGUE_TZ, Window

EUR_RATE = 24.315

BASE_DT = datetime(2025, 10, 22, 0, 0, 0, tzinfo=PRAGUE_TZ).astimezone(UTC)

CHEAPEST_15min_WINDOW = Window(
    start=BASE_DT + timedelta(hours=13, minutes=45),
    end=BASE_DT + timedelta(hours=14),
    prices=[Decimal(78.73)],
)

TODAY_CHEAPEST_60min_PRICE = 85.05
TODAY_CHEAPEST_60min_DT = "2025-10-22T11:00:00+00:00"
TODAY_CHEAPEST_15min_PRICE = 78.73
TODAY_CHEAPEST_15min_DT = "2025-10-22T11:45:00+00:00"

TOMORROW_CHEAPEST_60min_PRICE = 67.05
TOMORROW_CHEAPEST_60min_DT = "2025-10-23T11:00:00+00:00"
TOMORROW_CHEAPEST_15min_PRICE = 61.36
TOMORROW_CHEAPEST_15min_DT = "2025-10-23T21:45:00+00:00"


def approx(expected: float | str):
    return pytest.approx(float(expected), abs=0.00001)  # pyright: ignore[reportUnknownMemberType]


def get_rate(currency: str, unit: str) -> float:
    fx_rate = EUR_RATE if currency == "CZK" else 1.0  # Default is EUR
    return fx_rate if unit == "MWh" else fx_rate / 1000


def get_entry(
    commodity: str = "electricity",
    currency: str = "EUR",
    unit: str = "MWh",
    interval: SpotRateIntervalType = SpotRateIntervalType.Hour,
    cheapest_blocks: str | None = "",
    allow_cross_midnight: bool = False,
):
    return MockConfigEntry(
        domain=DOMAIN,
        title=(
            "Current Spot 60min"
            if interval == SpotRateIntervalType.Hour
            else "Current Spot 15min"
        ),
        unique_id="001",
        data={
            CONF_COMMODITY: commodity,
            CONF_CURRENCY: currency,
            CONF_UNIT_OF_MEASUREMENT: unit,
            CONF_INTERVAL: interval,
        },
        options={
            CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY: "{{ value + 10 }}",
            CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY: "{{ value - 1 }}",
            CONF_CHEAPEST_BLOCKS: cheapest_blocks,
            CONF_ALLOW_CROSS_MIDNIGHT: allow_cross_midnight,
        },
        minor_version=1,
    )


async def init_integration(hass: HomeAssistant, entries: Sequence[MockConfigEntry]):
    for entry in entries:
        entry.add_to_hass(hass)

        if not await hass.config_entries.async_setup(entry.entry_id):
            return False

    await hass.async_block_till_done()
    return True
