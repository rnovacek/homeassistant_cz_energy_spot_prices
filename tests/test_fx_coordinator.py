"""Tests for CNB exchange-rate coordination and persistence."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from freezegun import freeze_time
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.cz_energy_spot_prices.cnb_rate import CnbRateError
from custom_components.cz_energy_spot_prices.coordinator import FxCoordinator

from . import BASE_DT, get_entry, init_integration


async def test_retry_refreshes_rates_and_preserves_midnight_schedule(
    hass: HomeAssistant,
) -> None:
    """A real timer retries a failed request without losing the daily refresh."""
    with freeze_time(BASE_DT) as freezer:
        await hass.config.async_set_time_zone("Europe/Prague")
        coordinator = FxCoordinator(hass)
        rates = {"CZK": Decimal(1), "EUR": Decimal("24.315")}
        fetch = AsyncMock(side_effect=[CnbRateError("CNB unavailable"), rates, rates])
        with (
            patch.object(coordinator, "_fetch_data", fetch),
            patch.object(coordinator._store, "async_save", AsyncMock()),
        ):
            await coordinator.async_refresh()
            assert not coordinator.last_update_success

            freezer.tick(timedelta(seconds=10))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()
            assert fetch.await_count == 2
            assert coordinator.data == rates
            assert coordinator.last_update_success

            freezer.tick(timedelta(days=1))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()
            assert fetch.await_count == 3
        await coordinator.async_stop()
        await coordinator.async_shutdown()


@pytest.mark.parametrize("stop", [False, True])
async def test_pending_retry_is_cancelled(hass: HomeAssistant, stop: bool) -> None:
    """Stopping or a successful manual refresh cancels the outstanding retry."""
    with freeze_time(BASE_DT) as freezer:
        coordinator = FxCoordinator(hass)
        fetch = AsyncMock(
            side_effect=[CnbRateError("CNB unavailable"), {"EUR": Decimal("24.315")}]
        )
        with (
            patch.object(coordinator, "_fetch_data", fetch),
            patch.object(coordinator._store, "async_save", AsyncMock()),
        ):
            await coordinator.async_refresh()
            if stop:
                await coordinator.async_stop()
            else:
                await coordinator.async_refresh()
            expected_calls = fetch.await_count
            freezer.tick(timedelta(seconds=10))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()
            assert fetch.await_count == expected_calls
        await coordinator.async_stop()
        await coordinator.async_shutdown()


async def test_loads_persisted_rates(hass: HomeAssistant) -> None:
    """Persisted rates are restored with Decimal precision."""
    coordinator = FxCoordinator(hass)
    coordinator._store.async_load = AsyncMock(  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
        return_value={"CZK": "1", "EUR": "24.315"}
    )

    assert await coordinator.async_load_persisted()
    assert coordinator.data == {
        "CZK": Decimal("1"),
        "EUR": Decimal("24.315"),
    }
    await coordinator.async_stop()


async def test_rejects_invalid_persisted_rates(hass: HomeAssistant) -> None:
    """A damaged cache falls through to the normal network refresh."""
    coordinator = FxCoordinator(hass)
    coordinator._store.async_load = AsyncMock(  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
        return_value={"CZK": "not-a-number"}
    )

    assert not await coordinator.async_load_persisted()
    assert coordinator.data is None
    await coordinator.async_stop()


async def test_saves_successful_rates(hass: HomeAssistant) -> None:
    """Every successful CNB refresh replaces the persisted rates."""
    coordinator = FxCoordinator(hass)
    rates = {"CZK": Decimal("1"), "EUR": Decimal("24.315")}
    coordinator._fetch_data_with_retry = AsyncMock(  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
        return_value=rates
    )
    coordinator._store.async_save = AsyncMock()  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]

    assert await coordinator._async_update_data() == rates  # pyright: ignore[reportPrivateUsage]
    coordinator._store.async_save.assert_awaited_once_with(  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
        {"CZK": "1", "EUR": "24.315"}
    )
    await coordinator.async_stop()


async def test_keeps_persisted_rates_when_cnb_is_unavailable(
    hass: HomeAssistant,
) -> None:
    """A failed startup refresh does not discard restored rates."""
    coordinator = FxCoordinator(hass)
    coordinator._store.async_load = AsyncMock(  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
        return_value={"CZK": "1", "EUR": "24.315"}
    )
    coordinator._fetch_data = AsyncMock(  # pyright: ignore[reportPrivateUsage,method-assign]
        side_effect=CnbRateError("CNB unavailable")
    )

    assert await coordinator.async_load_persisted()
    with patch(
        "custom_components.cz_energy_spot_prices.coordinator.event.async_call_later"
    ):
        await coordinator.async_refresh()

    assert coordinator.data == {
        "CZK": Decimal("1"),
        "EUR": Decimal("24.315"),
    }
    assert not coordinator.last_update_success
    await coordinator.async_stop()


async def test_setup_uses_persisted_rates_during_cnb_outage(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
) -> None:
    """A restarted CZK entry computes its sensors from cached CNB rates."""
    entry: MockConfigEntry = get_entry(currency="CZK")
    await hass.config.async_set_time_zone("Europe/Prague")

    with freeze_time(BASE_DT):
        assert await init_integration(hass, [entry])
        assert entry.runtime_data.data is not None
        assert await hass.config_entries.async_unload(entry.entry_id)

        mock_cnb.side_effect = CnbRateError("CNB unavailable")
        with patch(
            "custom_components.cz_energy_spot_prices.coordinator.event.async_call_later"
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data.data is not None
        assert await hass.config_entries.async_unload(entry.entry_id)
