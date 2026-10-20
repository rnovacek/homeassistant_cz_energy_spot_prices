"""Startup outages must not stop shared source coordinators."""

from datetime import timedelta
from decimal import Decimal
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.cz_energy_spot_prices.cnb_rate import CnbRateError
from custom_components.cz_energy_spot_prices.const import (
    DOMAIN,
    FX_COORDINATOR,
    SPOT_ELECTRICTY_COORDINATOR,
    SPOT_GAS_COORDINATOR,
    Commodity,
    SpotRateIntervalType,
)
from custom_components.cz_energy_spot_prices.coordinator import (
    FxCoordinator,
    SpotRateCoordinator,
)
from custom_components.cz_energy_spot_prices.spot_rate import OTEFault, SpotRate

from . import BASE_DT, get_entry

EMPTY_OTE_RESPONSE = (
    '<Envelope xmlns="http://schemas.xmlsoap.org/soap/envelope/">'
    '<Body /></Envelope>'
)


@pytest.mark.parametrize("commodity", (Commodity.Electricity, Commodity.Gas))
@pytest.mark.parametrize("persisted", (True, False), ids=("persisted", "no-store"))
@pytest.mark.parametrize("failure", ("exception", "empty"))
async def test_startup_ote_failure_still_fetches_next_day(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    request: pytest.FixtureRequest,
    commodity: Commodity,
    persisted: bool,
    failure: str,
) -> None:
    """A real startup retry must recover and keep the next publication timer."""
    await hass.config.async_set_time_zone("Europe/Prague")
    start = BASE_DT + timedelta(hours=16, minutes=46, seconds=40)
    freezer.move_to(start)
    mock_ote: AsyncMock = request.getfixturevalue(f"mock_ote_{commodity.value}")
    xml: str = mock_ote.return_value
    client = SpotRate()
    if commodity == Commodity.Electricity:
        rates = await client.get_electricity_rates(start)
        coordinator_key = SPOT_ELECTRICTY_COORDINATOR
        interval = SpotRateIntervalType.QuarterHour
    else:
        rates = await client.get_gas_rates(start)
        coordinator_key = SPOT_GAS_COORDINATOR
        interval = SpotRateIntervalType.Day

    if persisted:
        store_key = f"{DOMAIN}.spot_rates.{commodity.value}"
        hass_storage[store_key] = {
            "version": 1,
            "minor_version": 1,
            "key": store_key,
            "data": SpotRateCoordinator._serialize(rates),  # pyright: ignore[reportPrivateUsage]
        }

    mock_ote.reset_mock()
    if failure == "exception":
        mock_ote.side_effect = OTEFault("OTE unavailable during startup")
    else:
        mock_ote.return_value = EMPTY_OTE_RESPONSE
    entry = get_entry(commodity=commodity.value, unit="kWh", interval=interval)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = cast(SpotRateCoordinator, hass.data[DOMAIN][coordinator_key])
    assert coordinator.data == (rates if persisted else None)

    mock_ote.side_effect = None
    mock_ote.return_value = xml
    for _ in range(60):
        freezer.tick(timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.data[DOMAIN][coordinator_key] is coordinator

    mock_ote.return_value = (
        xml.replace("2025-10-23", "2025-10-24")
        .replace("2025-10-22", "2025-10-23")
        .replace("2025-10-21", "2025-10-22")
    )
    freezer.move_to(BASE_DT + timedelta(days=1, hours=13, minutes=15))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert coordinator.has_tomorrow_data(), "OTE updates stopped after startup"
    assert mock_ote.await_count >= 3
    assert coordinator.last_update_success
    state = hass.states.get(
        f"binary_sensor.spot_{commodity.value}_has_tomorrow_data"
    )
    assert state is not None
    assert state.state == "on"
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.usefixtures("mock_ote_electricity")
async def test_startup_cnb_failure_still_refreshes_at_midnight(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_cnb: AsyncMock,
) -> None:
    """FX recovery must use a live debouncer and retain its daily refresh."""
    await hass.config.async_set_time_zone("Europe/Prague")
    freezer.move_to(BASE_DT + timedelta(hours=16, minutes=46, seconds=40))
    mock_cnb.side_effect = CnbRateError("CNB unavailable during startup")
    entry = get_entry(currency="CZK")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = cast(FxCoordinator, hass.data[DOMAIN][FX_COORDINATOR])

    mock_cnb.side_effect = None
    for _ in range(60):
        freezer.tick(timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.state is ConfigEntryState.LOADED
    assert hass.data[DOMAIN][FX_COORDINATOR] is coordinator
    assert coordinator.data is not None
    assert coordinator.data["EUR"] == pytest.approx(Decimal("24.315"))
    assert coordinator.last_update_success
    assert entry.runtime_data.data is not None

    calls_before_midnight = mock_cnb.await_count
    freezer.move_to(BASE_DT + timedelta(days=1, seconds=15))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert mock_cnb.await_count == calls_before_midnight + 1
    assert coordinator.last_update_success
    assert entry.runtime_data.data is not None
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("failure", ("exception", "empty"))
@pytest.mark.parametrize(
    "interval", (SpotRateIntervalType.Hour, SpotRateIntervalType.QuarterHour)
)
async def test_restart_ote_outage_preserves_cached_sensors(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    mock_ote_electricity: AsyncMock,
    failure: str,
    interval: SpotRateIntervalType,
) -> None:
    """Restored prices stay usable and are not overwritten by an empty response."""
    await hass.config.async_set_time_zone("Europe/Prague")
    freezer.move_to(BASE_DT + timedelta(hours=10))
    entry = get_entry(interval=interval)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    sensor_id = (
        "sensor.current_buy_electricity_"
        + ("hour" if interval == SpotRateIntervalType.Hour else "15min")
        + "_order"
    )
    before = hass.states.get(sensor_id)
    assert before is not None
    assert before.state not in ("unknown", "unavailable")
    assert await hass.config_entries.async_unload(entry.entry_id)

    store_key = f"{DOMAIN}.spot_rates.electricity"
    cached_rates = hass_storage[store_key]["data"]
    if failure == "exception":
        mock_ote_electricity.side_effect = OTEFault("OTE unavailable")
    else:
        mock_ote_electricity.return_value = EMPTY_OTE_RESPONSE

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    after = hass.states.get(sensor_id)
    assert after is not None
    assert after.state == before.state
    assert hass_storage[store_key]["data"] == cached_rates
    assert await hass.config_entries.async_unload(entry.entry_id)
