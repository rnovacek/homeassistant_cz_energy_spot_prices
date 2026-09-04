"""Tests for CNB exchange-rate coordination and persistence."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

from freezegun import freeze_time
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cz_energy_spot_prices.cnb_rate import CnbRateError
from custom_components.cz_energy_spot_prices.coordinator import FxCoordinator

from . import BASE_DT, get_entry, init_integration


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
