# pyright: reportUnusedParameter=false, reportMissingTypeStubs=false

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import AsyncMock, MockConfigEntry

from custom_components.cz_energy_spot_prices.spot_rate import OTEFault
from custom_components.cz_energy_spot_prices.const import DOMAIN

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

    assert not await init_integration(hass, [mock_config_entry])

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


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
