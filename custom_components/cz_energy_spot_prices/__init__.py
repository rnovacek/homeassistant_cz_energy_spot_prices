"""Czech Spot Energy Prices"""

import logging
from typing import Any, cast
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT
from homeassistant.helpers import entity_registry
from homeassistant.helpers.template import Template
from jinja2 import TemplateError

from .config_flow import CONF_COMMODITY, CONF_INTERVAL, ELECTRICITY

from .const import (
    CONF_ALLOW_CROSS_MIDNIGHT,
    CONF_CHEAPEST_BLOCKS,
    ENTRY_COORDINATOR,
    SPOT_ELECTRICTY_COORDINATOR,
    SPOT_GAS_COORDINATOR,
    FX_COORDINATOR,
    DOMAIN,
    PLATFORMS,
    CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY,
    CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY,
    CONF_ADDITIONAL_COSTS_BUY_GAS,
    GLOBAL_ELECTRICITY_SENSOR_FLAG,
    GLOBAL_GAS_SENSOR_FLAG,
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


logger = logging.getLogger(__name__)

type SpotRateConfigEntry = ConfigEntry[EntryCoordinator]


async def async_setup_entry(hass: HomeAssistant, config_entry: SpotRateConfigEntry):
    logger.debug(
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
    cheapest_blocks = None
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
            # Fetch initial data (first refresh)
            await spot_coordinator.async_config_entry_first_refresh()

            config_entry.async_on_unload(spot_coordinator.async_stop)

        buy_template_config: str | None = config_entry.options.get(
            CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY
        )
        if buy_template_config:
            try:
                buy_template = Template(buy_template_config, hass=hass)
            except TemplateError as e:
                logger.error(
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
                logger.error(
                    "Invalid template for electricity sell price: %s\n%s",
                    e,
                    sell_template_config,
                )

        interval_name = config_entry.data.get(CONF_INTERVAL)
        if interval_name == SpotRateIntervalType.QuarterHour.value:
            interval = SpotRateIntervalType.QuarterHour
        else:
            interval = SpotRateIntervalType.Hour

        cheapest_blocks_conf: str | None = config_entry.options.get(
            CONF_CHEAPEST_BLOCKS
        )
        if cheapest_blocks_conf is not None:
            try:
                cheapest_blocks = sorted(
                    [int(block) for block in cheapest_blocks_conf.split(",")]
                )
            except ValueError:
                logger.error(
                    "Invalid config for cheapest_blocks: %s", cheapest_blocks_conf
                )
        else:
            cheapest_blocks = []

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
            # Fetch initial data (first refresh)
            await spot_coordinator.async_config_entry_first_refresh()

            config_entry.async_on_unload(spot_coordinator.async_stop)

        gas_buy_template_config: str | None = config_entry.options.get(
            CONF_ADDITIONAL_COSTS_BUY_GAS
        )
        if gas_buy_template_config:
            try:
                buy_template = Template(gas_buy_template_config, hass=hass)
            except TemplateError as e:
                logger.error(
                    "Invalid template for gas buy price: %s\n%s",
                    e,
                    gas_buy_template_config,
                )

        interval = SpotRateIntervalType.Day
    else:
        raise ValueError("Invalid commodity: %s", commodity)

    if currency != Currency.EUR:
        fx_coordinator: FxCoordinator | None = domain_data.get(FX_COORDINATOR)
        if not fx_coordinator:
            fx_coordinator = FxCoordinator(
                hass=hass,
            )
            domain_data[FX_COORDINATOR] = fx_coordinator
            # Fetch initial data (first refresh)
            await fx_coordinator.async_config_entry_first_refresh()

            config_entry.async_on_unload(fx_coordinator.async_stop)
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
        cheapest_blocks=cheapest_blocks,
        cheapest_blocks_cross_midnight=cheapest_blocks_cross_midnight,
        timezone=hass.config.time_zone,
        zoneinfo=ZoneInfo(hass.config.time_zone),
    )

    entry_coordinator = EntryCoordinator(
        hass=hass,
        spot_coordinator=spot_coordinator,
        fx_coordinator=fx_coordinator,
        config=config,
    )
    entries_data = cast(dict[str, Any], domain_data.setdefault(ENTRY_COORDINATOR, {}))
    entries_data[config_entry.entry_id] = entry_coordinator

    if spot_coordinator.data is not None and (
        fx_coordinator is None or fx_coordinator.data is not None
    ):
        # Recompute entry_coordinator when we have spot rate and fx data,
        # otherwise it'll automatically recompute when data are available
        await entry_coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Unload config entry."""
    logger.debug("async_unload_entry %s", config_entry.unique_id)
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, PLATFORMS
    )

    if unload_ok:
        domain_data = cast(dict[str, Any], hass.data.setdefault(DOMAIN, {}))
        entries_data = cast(dict[str, Any], domain_data.get(ENTRY_COORDINATOR, {}))

        entries_data.pop(config_entry.entry_id, None)

        if not entries_data:
            for coordiantor in [
                SPOT_ELECTRICTY_COORDINATOR,
                SPOT_GAS_COORDINATOR,
                FX_COORDINATOR,
            ]:
                try:
                    domain_data.pop(coordiantor)
                except LookupError:
                    pass

        if ENTRY_COORDINATOR in domain_data:
            domain_data.pop(ENTRY_COORDINATOR, None)

        if GLOBAL_ELECTRICITY_SENSOR_FLAG in domain_data:
            domain_data.pop(GLOBAL_ELECTRICITY_SENSOR_FLAG, None)

        if GLOBAL_GAS_SENSOR_FLAG in domain_data:
            domain_data.pop(GLOBAL_GAS_SENSOR_FLAG, None)

    return unload_ok


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
            logger.info(
                "Migrating %s unique_id %s → %s",
                entity_id,
                old_unique_id,
                new_unique_id,
            )
            try:
                _ = ent_reg.async_update_entity(entity_id, new_unique_id=new_unique_id)
            except ValueError as e:
                logger.info(
                    "Unable to rename entity %s to %s: %s", entity_id, new_unique_id, e
                )
            migrated += 1

    if migrated:
        logger.info("Migrated %s entities from old unique_id format.", migrated)

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
            logger.info("Deprecated entity %s removed", entity_id)
