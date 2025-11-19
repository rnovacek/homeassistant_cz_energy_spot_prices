# pyright: reportUnusedParameter=false, reportMissingTypeStubs=false

from typing import cast
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import now
import pytest
from pytest_homeassistant_custom_component.common import AsyncMock, MockConfigEntry

from custom_components.cz_energy_spot_prices.coordinator import SpotRateCoordinator
from custom_components.cz_energy_spot_prices.spot_rate import OTEFault
from custom_components.cz_energy_spot_prices.const import (
    DOMAIN,
    SPOT_ELECTRICTY_COORDINATOR,
)

from . import init_integration

@pytest.mark.asyncio
async def test_async_setup_entry(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test a successful setup entry."""
    assert await init_integration(hass, [mock_config_entry])

    assert mock_config_entry.state is ConfigEntryState.LOADED


async def test_config_not_ready(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test for setup failure if connection to broker is missing."""
    mock_ote_electricity.side_effect = OTEFault

    assert await init_integration(hass, [mock_config_entry])

    # Config entry will be in loaded state, but coordinator will schedule retries
    assert mock_config_entry.state is ConfigEntryState.LOADED

    # No data will be available yet
    coordinator = cast(
        SpotRateCoordinator, hass.data[DOMAIN][SPOT_ELECTRICTY_COORDINATOR]
    )
    assert coordinator.data is None
    # Retry is in progress
    assert coordinator._retry_attempt > 0  # pyright: ignore[reportPrivateUsage]
    assert coordinator._next_update is not None  # pyright: ignore[reportPrivateUsage]
    assert (coordinator._next_update - now()).total_seconds() < 100  # pyright: ignore[reportPrivateUsage]

    mock_ote_electricity.side_effect = None
    await coordinator.async_refresh()
    assert coordinator.data is not None
    assert coordinator._retry_attempt == 0  # pyright: ignore[reportPrivateUsage]
    assert coordinator._next_update is not None  # pyright: ignore[reportPrivateUsage]
    assert (coordinator._next_update - now()).total_seconds() >= 100  # pyright: ignore[reportPrivateUsage]


async def test_unload_entry(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test successful unload of entry."""
    assert await init_integration(hass, [mock_config_entry])

    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert mock_config_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    assert not hass.data.get(DOMAIN)
