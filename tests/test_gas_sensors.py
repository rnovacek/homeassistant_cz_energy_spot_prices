# pyright: reportUnusedParameter=false, reportMissingTypeStubs=false
from typing import cast
from unittest.mock import AsyncMock

import pytest
from freezegun import freeze_time
from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.cz_energy_spot_prices.config_flow import (
    CONF_COMMODITY,
    GAS,
)
from custom_components.cz_energy_spot_prices.const import (
    CONF_ADDITIONAL_COSTS_BUY_GAS,
    DOMAIN,
)

from . import BASE_DT, EUR_RATE, approx, init_integration


def get_gas_entry(
    currency: str = "CZK",
    unit: str = "kWh",
    buy_template: str = "",
) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Gas Spot",
        unique_id="gas-001",
        data={
            CONF_COMMODITY: GAS,
            CONF_CURRENCY: currency,
            CONF_UNIT_OF_MEASUREMENT: unit,
        },
        options={
            CONF_ADDITIONAL_COSTS_BUY_GAS: buy_template,
        },
        minor_version=1,
    )


def _gas_rate(currency: str, unit: str) -> float:
    """Return the conversion factor from EUR/MWh to (currency)/(unit)."""
    fx = EUR_RATE if currency == "CZK" else 1.0
    return fx if unit == "MWh" else fx / 1000


@pytest.mark.asyncio
@pytest.mark.parametrize("currency", ("EUR", "CZK"))
@pytest.mark.parametrize("unit", ("kWh", "MWh"))
async def test_today_tomorrow_gas_spot_sensors(
    hass: HomeAssistant,
    mock_ote_gas: AsyncMock,
    mock_cnb: AsyncMock,
    currency: str,
    unit: str,
):
    """Today/Tomorrow gas spot price sensors expose the correct values from
    the OTE response and apply currency + unit conversion."""

    # Prices in the fixture (EUR/MWh):
    # - 2025-10-22 (today): 34.05
    # - 2025-10-23 (tomorrow): 34.31
    today_eur_per_mwh = 34.05
    tomorrow_eur_per_mwh = 34.31

    rate = _gas_rate(currency, unit)

    await hass.config.async_set_time_zone("Europe/Prague")
    with freeze_time(BASE_DT):
        async_fire_time_changed(hass, BASE_DT)
        assert await init_integration(
            hass,
            [get_gas_entry(currency=currency, unit=unit)],
        )

        today = hass.states.get("sensor.current_spot_gas_price")
        assert today is not None
        assert approx(today.state) == today_eur_per_mwh * rate
        attr = cast(dict[str, str], today.attributes)
        assert (
            attr["unit_of_measurement"]
            == f"{'€' if currency == 'EUR' else 'Kč'}/{unit}"
        )
        assert attr["icon"] == "mdi:cash"

        tomorrow = hass.states.get("sensor.tomorrow_spot_gas_price")
        assert tomorrow is not None
        assert approx(tomorrow.state) == tomorrow_eur_per_mwh * rate


@pytest.mark.asyncio
async def test_gas_buy_template_applied(
    hass: HomeAssistant,
    mock_ote_gas: AsyncMock,
    mock_cnb: AsyncMock,
):
    """When a buy-price template is configured, the buy sensor uses the
    transformed value."""
    today_eur_per_mwh = 34.05
    tomorrow_eur_per_mwh = 34.31
    offset = 5

    await hass.config.async_set_time_zone("Europe/Prague")
    with freeze_time(BASE_DT):
        async_fire_time_changed(hass, BASE_DT)
        assert await init_integration(
            hass,
            [
                get_gas_entry(
                    currency="EUR",
                    unit="MWh",
                    buy_template=f"{{{{ value + {offset} }}}}",
                )
            ],
        )

        # Spot sensor is unaffected by the template
        spot_today = hass.states.get("sensor.current_spot_gas_price")
        assert spot_today is not None
        assert approx(spot_today.state) == today_eur_per_mwh

        # Buy sensor reflects the template
        buy_today = hass.states.get("sensor.current_buy_gas_price")
        assert buy_today is not None
        assert approx(buy_today.state) == today_eur_per_mwh + offset

        buy_tomorrow = hass.states.get("sensor.tomorrow_buy_gas_price")
        assert buy_tomorrow is not None
        assert approx(buy_tomorrow.state) == tomorrow_eur_per_mwh + offset


@pytest.mark.asyncio
async def test_has_tomorrow_gas_data_sensor(
    hass: HomeAssistant,
    mock_ote_gas: AsyncMock,
    mock_cnb: AsyncMock,
):
    """The global ``binary_sensor.spot_gas_has_tomorrow_data`` is created
    once per integration and reflects whether tomorrow data is available."""
    await hass.config.async_set_time_zone("Europe/Prague")
    with freeze_time(BASE_DT):
        async_fire_time_changed(hass, BASE_DT)
        assert await init_integration(hass, [get_gas_entry()])

        sensor = hass.states.get("binary_sensor.spot_gas_has_tomorrow_data")
        assert sensor is not None
        # Fixture contains data for 2025-10-23 (tomorrow relative to BASE_DT)
        assert sensor.state == "on"
        assert (
            sensor.attributes["friendly_name"] == "Spot Gas has Tomorrow Data"
        )
