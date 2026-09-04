"""Czech Spot Energy Prices"""

import logging
from types import MappingProxyType
from typing import Any, cast
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigSubentry
from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT, Platform
from homeassistant.helpers import entity_registry
from homeassistant.helpers.template import Template
from jinja2 import TemplateError

from .cheapest_blocks import (
    PriceBlockSearch,
    format_search_subentry_title,
    legacy_block_searches,
)
from .config_flow import CONF_COMMODITY, CONF_INTERVAL, ELECTRICITY

from .const import (
    CONF_ALLOW_CROSS_MIDNIGHT,
    CONF_CHEAPEST_BLOCKS,
    CONF_CONFIG_SUBENTRY_ID,
    SPOT_ELECTRICTY_COORDINATOR,
    SPOT_GAS_COORDINATOR,
    FX_COORDINATOR,
    DOMAIN,
    PLATFORMS,
    PRICE_BLOCK_SUBENTRY_TYPE,
    CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY,
    CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY,
    CONF_ADDITIONAL_COSTS_BUY_GAS,
    GLOBAL_ELECTRICITY_SENSOR_OWNER,
    GLOBAL_GAS_SENSOR_OWNER,
    Commodity,
    Currency,
    EnergyUnit,
    SpotRateIntervalType,
)
from .coordinator import (
    EntryConfig,
    EntryCoordinator,
    FxCoordinator,
    SpotRateCoordinator,
)


_LOGGER = logging.getLogger(__name__)

_SHARED_COORDINATOR_CONSUMERS = "shared_coordinator_consumers"

type SpotRateConfigEntry = ConfigEntry[EntryCoordinator]


async def async_setup_entry(hass: HomeAssistant, config_entry: SpotRateConfigEntry):
    _LOGGER.debug(
        "async_setup_entry %s data: [%s]; options: [%s]",
        config_entry.unique_id,
        config_entry.data,
        config_entry.options,
    )

    domain_data = cast(dict[str, Any], hass.data.setdefault(DOMAIN, {}))

    await _migrate_unique_ids(hass, config_entry)

    commodity = Commodity(config_entry.data.get(CONF_COMMODITY, ELECTRICITY))
    currency = Currency(config_entry.data[CONF_CURRENCY])
    unit = EnergyUnit(config_entry.data[CONF_UNIT_OF_MEASUREMENT])

    buy_template = None
    sell_template = None
    cheapest_block_searches: list[PriceBlockSearch] = []
    cheapest_blocks_cross_midnight = False

    # Reuse the same coordinator for all entries
    if commodity == Commodity.Electricity:
        spot_coordinator: SpotRateCoordinator | None = domain_data.get(
            SPOT_ELECTRICTY_COORDINATOR
        )
        if not spot_coordinator:
            spot_coordinator = SpotRateCoordinator(
                hass=hass,
                commodity=commodity,
            )
            domain_data[SPOT_ELECTRICTY_COORDINATOR] = spot_coordinator
            _register_shared_coordinator_consumer(
                hass, config_entry, domain_data, SPOT_ELECTRICTY_COORDINATOR
            )
            await spot_coordinator.async_register_shutdown()
            # Restore previously persisted data so sensors have something to
            # show even before the first network fetch completes.
            await spot_coordinator.async_load_persisted()
            # Fetch initial data (first refresh)
            await spot_coordinator.async_refresh()

        else:
            _register_shared_coordinator_consumer(
                hass, config_entry, domain_data, SPOT_ELECTRICTY_COORDINATOR
            )

        buy_template_config: str | None = config_entry.options.get(
            CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY
        )
        if buy_template_config:
            try:
                buy_template = Template(buy_template_config, hass=hass)
            except TemplateError as e:
                _LOGGER.error(
                    "Invalid template for electricity buy price: %s\n%s",
                    e,
                    buy_template_config,
                )

        sell_template_config: str | None = config_entry.options.get(
            CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY
        )
        if sell_template_config:
            try:
                sell_template = Template(sell_template_config, hass=hass)
            except TemplateError as e:
                _LOGGER.error(
                    "Invalid template for electricity sell price: %s\n%s",
                    e,
                    sell_template_config,
                )

        interval_name = config_entry.data.get(CONF_INTERVAL)
        if interval_name == SpotRateIntervalType.QuarterHour.value:
            interval = SpotRateIntervalType.QuarterHour
        else:
            interval = SpotRateIntervalType.Hour

        cheapest_block_searches = _price_block_searches(config_entry)
        cheapest_blocks_cross_midnight = (
            config_entry.options.get(CONF_ALLOW_CROSS_MIDNIGHT) or False
        )

    elif commodity == Commodity.Gas:
        spot_coordinator = domain_data.get(SPOT_GAS_COORDINATOR)
        if not spot_coordinator:
            spot_coordinator = SpotRateCoordinator(
                hass=hass,
                commodity=commodity,
            )
            domain_data[SPOT_GAS_COORDINATOR] = spot_coordinator
            _register_shared_coordinator_consumer(
                hass, config_entry, domain_data, SPOT_GAS_COORDINATOR
            )
            await spot_coordinator.async_register_shutdown()
            # Restore previously persisted data so sensors have something to
            # show even before the first network fetch completes.
            await spot_coordinator.async_load_persisted()
            # Fetch initial data (first refresh)
            await spot_coordinator.async_refresh()

        else:
            _register_shared_coordinator_consumer(
                hass, config_entry, domain_data, SPOT_GAS_COORDINATOR
            )

        gas_buy_template_config: str | None = config_entry.options.get(
            CONF_ADDITIONAL_COSTS_BUY_GAS
        )
        if gas_buy_template_config:
            try:
                buy_template = Template(gas_buy_template_config, hass=hass)
            except TemplateError as e:
                _LOGGER.error(
                    "Invalid template for gas buy price: %s\n%s",
                    e,
                    gas_buy_template_config,
                )

        interval = SpotRateIntervalType.Day
    else:
        raise ValueError(f"Invalid commodity: {commodity}")

    if currency != Currency.EUR:
        fx_coordinator: FxCoordinator | None = domain_data.get(FX_COORDINATOR)
        if not fx_coordinator:
            fx_coordinator = FxCoordinator(
                hass=hass,
            )
            domain_data[FX_COORDINATOR] = fx_coordinator
            _register_shared_coordinator_consumer(
                hass, config_entry, domain_data, FX_COORDINATOR
            )
            await fx_coordinator.async_register_shutdown()
            # Restore the last known good rates before contacting CNB so
            # converted-price sensors can survive a restart during an outage.
            await fx_coordinator.async_load_persisted()
            # Fetch initial data (first refresh)
            await fx_coordinator.async_refresh()
        else:
            _register_shared_coordinator_consumer(
                hass, config_entry, domain_data, FX_COORDINATOR
            )
    else:
        fx_coordinator = None

    config = EntryConfig(
        commodity=commodity,
        unit=unit,
        currency=currency,
        currency_human={
            "EUR": "€",
            "CZK": "Kč",
            "USD": "$",
        }.get(currency)
        or "?",
        interval=interval,
        buy_template=buy_template,
        sell_template=sell_template,
        cheapest_block_searches=cheapest_block_searches,
        cheapest_blocks_cross_midnight=cheapest_blocks_cross_midnight,
        timezone=hass.config.time_zone,
        zoneinfo=ZoneInfo(hass.config.time_zone),
    )

    entry_coordinator = EntryCoordinator(
        hass=hass,
        config_entry=config_entry,
        spot_coordinator=spot_coordinator,
        fx_coordinator=fx_coordinator,
        config=config,
    )
    config_entry.runtime_data = entry_coordinator
    config_entry.async_on_unload(entry_coordinator.async_stop)
    config_entry.async_on_unload(config_entry.add_update_listener(_async_entry_updated))

    if spot_coordinator.data is not None and (
        fx_coordinator is None or fx_coordinator.data is not None
    ):
        # Recompute entry_coordinator when we have spot rate and fx data,
        # otherwise it'll automatically recompute when data are available
        await entry_coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    return True


async def _async_entry_updated(
    hass: HomeAssistant, config_entry: SpotRateConfigEntry
) -> None:
    """Apply subentry-only changes without refetching shared source data."""
    reload_entry = False
    async with config_entry.setup_lock:
        if config_entry.state is not ConfigEntryState.LOADED:
            return
        coordinator = config_entry.runtime_data
        if not coordinator.parent_config_is_current(config_entry):
            reload_entry = True
        else:
            searches = _price_block_searches(config_entry)
            # Another queued update may already have applied the latest state.
            if searches == coordinator.config.cheapest_block_searches:
                return

            coordinator.async_replace_price_block_searches(searches)
            if not await hass.config_entries.async_unload_platforms(
                config_entry, [Platform.BINARY_SENSOR]
            ):
                reload_entry = True
            else:
                domain_data = cast(dict[str, Any], hass.data.setdefault(DOMAIN, {}))
                if (
                    domain_data.get(GLOBAL_ELECTRICITY_SENSOR_OWNER)
                    == config_entry.entry_id
                ):
                    domain_data.pop(GLOBAL_ELECTRICITY_SENSOR_OWNER, None)
                await hass.config_entries.async_forward_entry_setups(
                    config_entry, [Platform.BINARY_SENSOR]
                )

    # async_reload acquires setup_lock itself, so it must remain outside.
    if reload_entry:
        await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Unload config entry."""
    _LOGGER.debug("async_unload_entry %s", config_entry.unique_id)
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, PLATFORMS
    )

    if unload_ok:
        domain_data = cast(dict[str, Any], hass.data.setdefault(DOMAIN, {}))

        # If this entry owned one of the per-commodity global binary sensors,
        # clear the ownership flag so a future entry of the same commodity can
        # take over and recreate the sensor. Previously the flag was a boolean
        # that only got cleared when the LAST entry of the integration was
        # unloaded, leaving the sensor missing whenever the original owner
        # entry was removed.
        #
        # We intentionally do NOT trigger a reload of another entry here:
        # scheduling background tasks during ``async_unload_entry`` races with
        # the Home Assistant shutdown flow and can leave coroutines unawaited
        # in the test suite. The sensor will reappear next time the user
        # reloads (or HA restarts) any remaining entry of the same commodity.
        for owner_flag in (GLOBAL_ELECTRICITY_SENSOR_OWNER, GLOBAL_GAS_SENSOR_OWNER):
            if domain_data.get(owner_flag) == config_entry.entry_id:
                domain_data.pop(owner_flag, None)

    return unload_ok


async def async_migrate_entry(
    hass: HomeAssistant, config_entry: SpotRateConfigEntry
) -> bool:
    """Migrate released legacy block lengths to native config subentries."""
    if config_entry.version > 2:
        return False
    if config_entry.version == 2:
        return True

    options = dict(config_entry.options)
    if config_entry.data.get(CONF_COMMODITY, ELECTRICITY) == ELECTRICITY:
        existing_unique_ids = {
            subentry.unique_id
            for subentry in config_entry.subentries.values()
            if subentry.subentry_type == PRICE_BLOCK_SUBENTRY_TYPE
        }
        for search in legacy_block_searches(options.pop(CONF_CHEAPEST_BLOCKS, None)):
            search_id = str(search["id"])
            if search_id in existing_unique_ids:
                continue
            hass.config_entries.async_add_subentry(
                config_entry,
                ConfigSubentry(
                    data=MappingProxyType(dict(search)),
                    subentry_type=PRICE_BLOCK_SUBENTRY_TYPE,
                    title=format_search_subentry_title(search),
                    unique_id=search_id,
                ),
            )

    hass.config_entries.async_update_entry(
        config_entry,
        options=options,
        version=2,
    )
    return True


def _price_block_searches(config_entry: SpotRateConfigEntry) -> list[PriceBlockSearch]:
    """Build coordinator search definitions from price-block subentries."""
    searches: list[PriceBlockSearch] = []
    interval = SpotRateIntervalType(
        config_entry.data.get(CONF_INTERVAL, SpotRateIntervalType.Hour)
    )
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != PRICE_BLOCK_SUBENTRY_TYPE:
            continue
        search = dict(subentry.data)
        search["id"] = subentry.unique_id or subentry.subentry_id
        search["name"] = str(search.get("name") or subentry.title)
        search[CONF_CONFIG_SUBENTRY_ID] = subentry.subentry_id
        parsed = PriceBlockSearch.from_mapping(search, interval=interval)
        if parsed is None:
            _LOGGER.warning(
                "Ignoring invalid price block subentry %s", subentry.subentry_id
            )
            continue
        searches.append(parsed)
    return searches


def _register_shared_coordinator_consumer(
    hass: HomeAssistant,
    config_entry: SpotRateConfigEntry,
    domain_data: dict[str, Any],
    coordinator_key: str,
) -> None:
    """Keep a shared coordinator alive until its final entry unloads."""
    consumers_by_coordinator = cast(
        dict[str, set[str]],
        domain_data.setdefault(_SHARED_COORDINATOR_CONSUMERS, {}),
    )
    consumers_by_coordinator.setdefault(coordinator_key, set()).add(
        config_entry.entry_id
    )

    async def async_release_consumer() -> None:
        await _async_release_shared_coordinator_consumer(
            hass,
            config_entry.entry_id,
            coordinator_key,
        )

    config_entry.async_on_unload(async_release_consumer)


async def _async_release_shared_coordinator_consumer(
    hass: HomeAssistant,
    entry_id: str,
    coordinator_key: str,
) -> None:
    """Stop a shared coordinator after its final consumer has unloaded."""
    domain_data = cast(dict[str, Any] | None, hass.data.get(DOMAIN))
    if domain_data is None:
        return

    consumers_by_coordinator = cast(
        dict[str, set[str]],
        domain_data.get(_SHARED_COORDINATOR_CONSUMERS, {}),
    )
    consumers = consumers_by_coordinator.get(coordinator_key)
    if consumers is None:
        return

    consumers.discard(entry_id)
    if consumers:
        return

    consumers_by_coordinator.pop(coordinator_key, None)
    coordinator = domain_data.pop(coordinator_key, None)
    if isinstance(coordinator, (SpotRateCoordinator, FxCoordinator)):
        await coordinator.async_stop()
        await coordinator.async_shutdown()

    if not consumers_by_coordinator:
        domain_data.pop(_SHARED_COORDINATOR_CONSUMERS, None)
    if not domain_data:
        hass.data.pop(DOMAIN, None)


async def _migrate_unique_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Migrate old unique_id format to new format."""
    ent_reg = entity_registry.async_get(hass)

    migrated = 0

    mapping = {
        "sensor.current_spot_gas_buy_price": f"{entry.entry_id}_current_spot_gas_buy_price",
        "sensor.current_spot_gas_price": f"{entry.entry_id}_current_spot_gas_price",
        "sensor.tomorrow_spot_gas_price": f"{entry.entry_id}_tomorrow_spot_gas_price",
        "sensor.current_buy_gas_price": f"{entry.entry_id}_current_buy_gas_price",
        "sensor.tomorrow_buy_gas_price": f"{entry.entry_id}_tomorrow_buy_gas_price",
    }

    for trade in ["spot", "buy", "sell"]:
        mapping.update(
            {
                f"binary_sensor.{trade}_electricity_is_cheapest_2_hours_block": f"{entry.entry_id}_{trade}_electricity_is_cheapest_2_hours_block",
                f"binary_sensor.{trade}_electricity_is_cheapest_3_hours_block": f"{entry.entry_id}_{trade}_electricity_is_cheapest_3_hours_block",
                f"binary_sensor.{trade}_electricity_is_cheapest_4_hours_block": f"{entry.entry_id}_{trade}_electricity_is_cheapest_4_hours_block",
                f"binary_sensor.{trade}_electricity_is_cheapest_6_hours_block": f"{entry.entry_id}_{trade}_electricity_is_cheapest_6_hours_block",
                f"binary_sensor.{trade}_electricity_is_cheapest_8_hours_block": f"{entry.entry_id}_{trade}_electricity_is_cheapest_8_hours_block",
                f"binary_sensor.{trade}_electricity_is_cheapest": f"{entry.entry_id}_{trade}_electricity_is_cheapest",
                f"sensor.current_{trade}_electricity_cheapest_today": f"{entry.entry_id}_{trade}_cheapest_electricity_today",
                f"sensor.current_{trade}_electricity_cheapest_tomorrow": f"{entry.entry_id}_{trade}_cheapest_electricity_tomorrow",
                f"sensor.current_{trade}_electricity_most_expensive_today": f"{entry.entry_id}_{trade}_most_expensive_electricity_today",
                f"sensor.current_{trade}_electricity_most_expensive_tomorrow": f"{entry.entry_id}_{trade}_most_expensive_electricity_tomorrow",
                f"sensor.current_{trade}_electricity_hour_order": f"{entry.entry_id}_current_{trade}_electricity_hour_order",
                # f"sensor.current_spot_electricity_{trade}_price": f"sensor.current_{trade}_electricity_price",
                f"sensor.current_{trade}_electricity_price": f"{entry.entry_id}_current_{trade}_electricity_price",
                f"sensor.tomorrow_{trade}_electricity_hour_order": f"{entry.entry_id}_tomorrow_{trade}_electricity_hour_order",
            }
        )

    # Old → new unique_id pattern
    for old_unique_id, new_unique_id in mapping.items():
        entity_id = ent_reg.async_get_entity_id(
            "binary_sensor" if old_unique_id.startswith("binary_sensor.") else "sensor",
            DOMAIN,
            old_unique_id,
        )

        if entity_id:
            _LOGGER.info(
                "Migrating %s unique_id %s → %s",
                entity_id,
                old_unique_id,
                new_unique_id,
            )
            try:
                _ = ent_reg.async_update_entity(entity_id, new_unique_id=new_unique_id)
            except ValueError as e:
                _LOGGER.info(
                    "Unable to rename entity %s to %s: %s", entity_id, new_unique_id, e
                )
            migrated += 1

    if migrated:
        _LOGGER.info("Migrated %s entities from old unique_id format.", migrated)

    deprecated_ids = [
        "sensor.spot_electricity_is_cheapest",
        "sensor.sell_most_expensive_electricity_tomorrow",
        "sensor.spot_gas_has_tomorrow_data",
        "sensor.spot_electricity_has_tomorrow_data",
        "sensor.spot_electricity_is_cheapest_2_hours_block",
        "sensor.spot_electricity_is_cheapest_3_hours_block",
        "sensor.spot_electricity_is_cheapest_4_hours_block",
        "sensor.spot_electricity_is_cheapest_6_hours_block",
        "sensor.spot_electricity_is_cheapest_8_hours_block",
        "sensor.current_spot_electricity_sell_price",
        "sensor.current_spot_electricity_buy_price",
        "sensor.current_spot_gas_buy_price",
    ]
    for unique_id in deprecated_ids:
        entity_id = ent_reg.async_get_entity_id(
            "binary_sensor" if unique_id.startswith("binary_sensor.") else "sensor",
            DOMAIN,
            unique_id,
        )
        if entity_id:
            ent_reg.async_remove(entity_id)
            _LOGGER.info("Deprecated entity %s removed", entity_id)
