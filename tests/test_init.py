# pyright: reportUnusedParameter=false, reportMissingTypeStubs=false

from typing import cast
from unittest.mock import patch
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import now
import pytest
from pytest_homeassistant_custom_component.common import AsyncMock, MockConfigEntry

from custom_components.cz_energy_spot_prices.config_flow import CONF_COMMODITY
from custom_components.cz_energy_spot_prices.cheapest_blocks import (
    legacy_block_searches,
)
from custom_components.cz_energy_spot_prices.coordinator import SpotRateCoordinator
from custom_components.cz_energy_spot_prices.spot_rate import OTEFault
from custom_components.cz_energy_spot_prices.const import (
    CONF_ALLOW_CROSS_MIDNIGHT,
    CONF_CHEAPEST_BLOCKS,
    DOMAIN,
    FX_COORDINATOR,
    GLOBAL_ELECTRICITY_SENSOR_OWNER,
    PRICE_BLOCK_SUBENTRY_TYPE,
    SPOT_ELECTRICTY_COORDINATOR,
)

from custom_components.cz_energy_spot_prices import (
    _async_entry_updated,
    async_unload_entry,
)

from . import get_entry, init_integration


async def test_queued_update_ignores_entry_that_is_no_longer_loaded(
    hass: HomeAssistant,
) -> None:
    """A failed concurrent reload can leave no runtime data for a queued task."""
    entry = get_entry(cheapest_block_searches=[])
    entry.add_to_hass(hass)

    assert entry.state is ConfigEntryState.NOT_LOADED
    await _async_entry_updated(hass, entry)  # pyright: ignore[reportArgumentType]


async def test_unloading_non_owner_preserves_global_sensor_ownership(
    hass: HomeAssistant,
) -> None:
    """A non-owner reload must not allow a duplicate global sensor setup."""
    owner = MockConfigEntry(domain=DOMAIN, entry_id="owner")
    reloading = MockConfigEntry(domain=DOMAIN, entry_id="reloading")
    hass.data[DOMAIN] = {
        GLOBAL_ELECTRICITY_SENSOR_OWNER: owner.entry_id,
    }

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        AsyncMock(return_value=True),
    ):
        assert await async_unload_entry(hass, reloading)

    assert hass.data[DOMAIN][GLOBAL_ELECTRICITY_SENSOR_OWNER] == owner.entry_id


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
    assert mock_config_entry.runtime_data is not None


async def test_reloading_creator_keeps_shared_coordinator_running(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
) -> None:
    """Reloading one consumer must not stop a coordinator used by another."""
    creator = get_entry(currency="EUR")
    other = get_entry(currency="EUR")
    assert await init_integration(hass, [creator, other])

    coordinator = cast(
        SpotRateCoordinator, hass.data[DOMAIN][SPOT_ELECTRICTY_COORDINATOR]
    )
    assert coordinator._update_schedule is not None  # pyright: ignore[reportPrivateUsage]

    await hass.config_entries.async_reload(creator.entry_id)
    await hass.async_block_till_done()

    assert creator.state is ConfigEntryState.LOADED
    assert hass.data[DOMAIN][SPOT_ELECTRICTY_COORDINATOR] is coordinator
    assert coordinator._update_schedule is not None  # pyright: ignore[reportPrivateUsage]
    assert coordinator.config_entry is None
    assert not coordinator._shutdown_requested  # pyright: ignore[reportPrivateUsage]
    assert creator.runtime_data is not other.runtime_data

    calls_before_schedule = mock_ote_electricity.call_count
    coordinator.on_schedule(now())
    await hass.async_block_till_done()
    assert mock_ote_electricity.call_count == calls_before_schedule + 1


async def test_reloading_creator_keeps_shared_fx_coordinator_running(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
) -> None:
    """Reloading one consumer must not shut down the shared FX coordinator."""
    creator = get_entry(currency="CZK")
    other = get_entry(currency="CZK")
    assert await init_integration(hass, [creator, other])

    coordinator = hass.data[DOMAIN][FX_COORDINATOR]
    await hass.config_entries.async_reload(creator.entry_id)
    await hass.async_block_till_done()

    assert coordinator.config_entry is None
    assert not coordinator._shutdown_requested  # pyright: ignore[reportPrivateUsage]
    assert not coordinator._debounced_refresh._shutdown_requested  # pyright: ignore[reportPrivateUsage]


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
    coordinator = cast(
        SpotRateCoordinator, hass.data[DOMAIN][SPOT_ELECTRICTY_COORDINATOR]
    )

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    assert coordinator._update_schedule is None  # pyright: ignore[reportPrivateUsage]
    assert not hass.data.get(DOMAIN)


async def test_migration_persists_searches(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
) -> None:
    """Test that legacy cheapest_blocks are migrated and persisted."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Cz Spot",
        unique_id="migrate-test",
        data={
            CONF_COMMODITY: "electricity",
            CONF_CURRENCY: "CZK",
            CONF_UNIT_OF_MEASUREMENT: "kWh",
        },
        options={
            CONF_CHEAPEST_BLOCKS: "2, 3, 3",
            CONF_ALLOW_CROSS_MIDNIGHT: False,
        },
    )

    assert await init_integration(hass, [entry])
    assert entry.state is ConfigEntryState.LOADED

    # Old key should be removed
    assert CONF_CHEAPEST_BLOCKS not in entry.options

    # Searches now live as native subentries, outside the parent options.
    searches = list(entry.subentries.values())
    assert len(searches) == 4  # Today 2h, Tomorrow 2h, Today 3h, Tomorrow 3h
    assert all(search.subentry_type == PRICE_BLOCK_SUBENTRY_TYPE for search in searches)

    names = {search.data["name"] for search in searches}
    assert names == {"Today 2h", "Tomorrow 2h", "Today 3h", "Tomorrow 3h"}
    assert {search.title for search in searches} == {
        "Today 2h · Today · Lowest Spot · 2 h",
        "Tomorrow 2h · Tomorrow · Lowest Spot · 2 h",
        "Today 3h · Today · Lowest Spot · 3 h",
        "Tomorrow 3h · Tomorrow · Lowest Spot · 3 h",
    }
    assert all(search.data["legacy"] is True for search in searches)

    # IDs should be stable (uuid format)
    ids = [search.unique_id for search in searches]
    assert all(uid is not None and len(uid) == 36 for uid in ids)
    assert len(set(ids)) == 4  # All unique
    assert entry.version == 2


def test_legacy_block_migration_accepts_only_released_integer_range(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Migration mirrors the block lengths that 0.8.3 could produce."""
    searches = legacy_block_searches("2, 2.0, 2.5, 0, -1, 24, bad, 23")

    assert [search["length_hours"] for search in searches] == [2, 2, 23, 23]
    assert "2.0" in caplog.text
    assert "2.5" in caplog.text
    assert "24" in caplog.text
