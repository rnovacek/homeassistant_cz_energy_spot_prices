from __future__ import annotations
import logging
from typing import Any, cast, override

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify

from custom_components.cz_energy_spot_prices.config_flow import (
    ELECTRICITY,
    GAS,
)


from . import SpotRateConfigEntry
from .const import (
    DOMAIN,
    GLOBAL_ELECTRICITY_SENSOR_OWNER,
    GLOBAL_GAS_SENSOR_OWNER,
    PriceType,
    SearchObjective,
    SpotRateIntervalType,
)
from .cheapest_blocks import PriceBlockSearch
from .coordinator import (
    EntryCoordinator,
    IntervalTradeRateData,
    get_now,
)
from .spot_rate_mixin import SpotRateSensorMixin, Trade

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpotRateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    _LOGGER.debug(
        "binary_sensor.async_setup_entry %s, data: [%s] options: [%s]",
        entry.unique_id,
        entry.data,
        entry.options,
    )

    domain_data = cast(dict[str, Any], hass.data[DOMAIN])
    coordinator = entry.runtime_data

    commodity = coordinator.config.commodity

    sensors: list[Entity] = []
    search_entities: list[tuple[Entity, str | None]] = []

    # Add these sensors only once per integration as they are shared between services
    if commodity == ELECTRICITY:
        if GLOBAL_ELECTRICITY_SENSOR_OWNER not in domain_data:
            has_tomorrow_electricity_data = HasTomorrowElectricityData(
                hass=hass,
                coordinator=coordinator,
                device_id=entry.entry_id,
            )
            sensors.append(has_tomorrow_electricity_data)
            # Remember which entry owns the sensor so it can be recreated on
            # another entry of the same commodity if this owner is unloaded.
            hass.data[DOMAIN][GLOBAL_ELECTRICITY_SENSOR_OWNER] = entry.entry_id

    elif commodity == GAS:
        if GLOBAL_GAS_SENSOR_OWNER not in domain_data:
            has_tomorrow_gas_data = HasTomorrowGasData(
                hass=hass,
                coordinator=coordinator,
                device_id=entry.entry_id,
            )
            sensors.append(has_tomorrow_gas_data)
            hass.data[DOMAIN][GLOBAL_GAS_SENSOR_OWNER] = entry.entry_id

    if commodity == ELECTRICITY:
        _async_remove_stale_legacy_block_entities(hass, entry, coordinator)

        cheapest_blocks = coordinator.config.all_cheapest_blocks()
        for hours in cheapest_blocks:
            sensors.append(
                ConsecutiveCheapestElectricitySensor(
                    hours=hours,
                    hass=hass,
                    coordinator=coordinator,
                    device_id=entry.entry_id,
                    trade=Trade.SPOT,
                )
            )

        if coordinator.buy_template:
            for hours in cheapest_blocks:
                sensors.append(
                    ConsecutiveCheapestElectricitySensor(
                        hours=hours,
                        hass=hass,
                        coordinator=coordinator,
                        device_id=entry.entry_id,
                        trade=Trade.BUY,
                    )
                )

        if coordinator.sell_template:
            for hours in cheapest_blocks:
                sensors.append(
                    ConsecutiveCheapestElectricitySensor(
                        hours=hours,
                        hass=hass,
                        coordinator=coordinator,
                        device_id=entry.entry_id,
                        trade=Trade.SELL,
                    )
                )

        for search in coordinator.config.cheapest_block_searches:
            search_entities.append(
                (
                    SearchBasedCheapestElectricitySensor(
                        search=search,
                        hass=hass,
                        coordinator=coordinator,
                        device_id=entry.entry_id,
                        trade=_trade_from_search(search),
                    ),
                    search.config_subentry_id,
                )
            )

    async_add_entities(sensors)
    if commodity == ELECTRICITY:
        for entity, subentry_id in search_entities:
            async_add_entities(
                [entity],
                config_subentry_id=subentry_id,
            )


def _async_remove_stale_legacy_block_entities(
    hass: HomeAssistant,
    entry: SpotRateConfigEntry,
    coordinator: EntryCoordinator,
) -> None:
    """Remove released N-hour entities after their last legacy search is gone."""
    ent_reg = er.async_get(hass)
    interval = (
        "_15min"
        if coordinator.config.interval == SpotRateIntervalType.QuarterHour
        else ""
    )
    active_lengths = {
        search.legacy_block_length
        for search in coordinator.config.cheapest_block_searches
        if search.legacy_block_length is not None
    }
    stale_unique_ids = {
        f"{entry.entry_id}_{trade}_electricity_is_cheapest_{hours}_hours_block{interval}"
        for trade in ("spot", "buy", "sell")
        for hours in range(1, 24)
        if hours not in active_lengths
    }

    for registry_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if registry_entry.unique_id in stale_unique_ids:
            ent_reg.async_remove(registry_entry.entity_id)
            _LOGGER.info(
                "Removed stale legacy price block entity %s", registry_entry.entity_id
            )


def _search_block_unique_id(
    entry_id: str,
    search_id: str,
    interval: str,
) -> str:
    """Return the stable unique ID for a custom price block entity."""
    return f"{entry_id}_price_block_search_{search_id}{interval}"


def _trade_from_search(search: PriceBlockSearch) -> Trade:
    """Return the selected price type for a cheapest block search."""
    match search.price_type:
        case PriceType.BUY:
            return Trade.BUY
        case PriceType.SELL:
            return Trade.SELL
        case _:
            return Trade.SPOT


class BinarySpotRateSensorBase(  # pyright: ignore[reportIncompatibleVariableOverride]
    SpotRateSensorMixin, BinarySensorEntity
):
    pass


class ConsecutiveCheapestElectricitySensor(BinarySpotRateSensorBase):
    _attr_icon: str | None = "mdi:cash-clock"

    def __init__(
        self,
        hours: int | None,
        hass: HomeAssistant,
        coordinator: EntryCoordinator,
        device_id: str,
        trade: Trade,
    ) -> None:
        self.hours = hours

        interval = (
            "_15min"
            if coordinator.config.interval == SpotRateIntervalType.QuarterHour
            else ""
        )

        if self.hours is None:
            self._attr_unique_id = (
                f"{device_id}_{trade.lower()}_electricity_is_cheapest{interval}"
            )
            self._attr_translation_key = (
                f"{trade.lower()}_electricity_is_cheapest{interval}"
            )
            self.entity_id = (
                f"binary_sensor.{trade.lower()}_electricity_is_cheapest{interval}"
            )
        else:
            self._attr_unique_id = f"{device_id}_{trade.lower()}_electricity_is_cheapest_{self.hours}_hours_block{interval}"
            self._attr_translation_key = (
                f"{trade.lower()}_electricity_is_cheapest_hours_block{interval}"
            )
            self._attr_translation_placeholders = {
                "hours": str(self.hours),
            }
            self.entity_id = f"binary_sensor.{trade.lower()}_electricity_is_cheapest_{self.hours}_hours_block{interval}"

        super().__init__(
            hass=hass,
            coordinator=coordinator,
            device_id=device_id,
            trade=trade,
        )

    @override
    def update(self, rate_data: IntervalTradeRateData | None):
        self._attr = {}

        now = get_now()

        if not rate_data:
            self._attr_available = False
            self._attr_is_on = None
            return

        trade_rates = self._get_trade_rates(rate_data)
        if not trade_rates:
            self._attr_available = False
            self._attr_is_on = None
            return

        try:
            window = trade_rates.cheapest_windows[self.hours]
        except KeyError:
            if self.hours is None:
                _LOGGER.error("Unable to find cheapest interval")
            else:
                _LOGGER.error("Unable to find cheapest %s hour block", self.hours)
            self._attr_available = False
            return

        self._attr_is_on = window.start <= now < window.end
        start = window.start.astimezone(self.coordinator.config.zoneinfo)
        end = window.end.astimezone(self.coordinator.config.zoneinfo)
        self._attr = {
            "Start": start,
            "End": end,
            "Min": float(round(min(window.prices), 4)),
            "Max": float(round(max(window.prices), 4)),
            "Mean": float(round(sum(window.prices) / len(window.prices), 4)),
        }
        if self.coordinator.config.interval == SpotRateIntervalType.Hour:
            # Doesn't make sense to have these on 15min intervals
            self._attr["Start hour"] = start.hour
            self._attr["End hour"] = end.hour
        self._attr_available = True


class SearchBasedCheapestElectricitySensor(BinarySpotRateSensorBase):
    _attr_icon: str | None = "mdi:cash-clock"

    def __init__(
        self,
        search: PriceBlockSearch,
        hass: HomeAssistant,
        coordinator: EntryCoordinator,
        device_id: str,
        trade: Trade,
    ) -> None:
        self.search = search
        self.search_id = search.id
        self.search_name = search.name
        self.length_hours = search.length_hours
        self.objective = search.objective

        interval = (
            "_15min"
            if coordinator.config.interval == SpotRateIntervalType.QuarterHour
            else ""
        )

        self._attr_unique_id = _search_block_unique_id(
            device_id,
            self.search_id,
            interval,
        )
        block_type = (
            "cheapest_block_search"
            if self.objective == SearchObjective.LOWEST
            else "highest_price_block_search"
        )
        self._attr_translation_key = f"{trade.lower()}_{block_type}{interval}"
        self._attr_translation_placeholders = {
            "name": self.search_name,
        }
        slug = slugify(self.search_name)
        object_type = (
            "cheapest_block"
            if self.objective == SearchObjective.LOWEST
            else "highest_price_block"
        )
        self.entity_id = f"binary_sensor.{trade.lower()}_{object_type}_{slug}{interval}"

        super().__init__(
            hass=hass,
            coordinator=coordinator,
            device_id=device_id,
            trade=trade,
        )

    @override
    def update(self, rate_data: IntervalTradeRateData | None):
        self._attr = {}

        now = get_now()

        if not rate_data:
            self._attr_available = False
            self._attr_is_on = None
            return

        trade_rates = self._get_trade_rates(rate_data)
        if not trade_rates:
            self._attr_available = False
            self._attr_is_on = None
            return

        try:
            window = trade_rates.search_windows[self.search_id]
        except KeyError:
            _LOGGER.debug("Unable to find cheapest block for search %s", self.search_id)
            self._attr_available = False
            return

        self._attr_is_on = window.start <= now < window.end
        start = window.start.astimezone(self.coordinator.config.zoneinfo)
        end = window.end.astimezone(self.coordinator.config.zoneinfo)
        self._attr = {
            "Start": start,
            "End": end,
            "Min": float(round(min(window.prices), 4)),
            "Max": float(round(max(window.prices), 4)),
            "Mean": float(round(sum(window.prices) / len(window.prices), 4)),
            "Length hours": self.length_hours,
            "Price type": self._trade.lower(),
            "Objective": self.objective.value,
            "Search type": self.search.type.value,
        }
        if self.coordinator.config.interval == SpotRateIntervalType.Hour:
            # Doesn't make sense to have these on 15min intervals
            self._attr["Start hour"] = start.hour
            self._attr["End hour"] = end.hour
        self._attr_available = True


class HasTomorrowElectricityData(BinarySpotRateSensorBase):
    _attr_icon = "mdi:cash-clock"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EntryCoordinator,
        device_id: str,
    ) -> None:
        self._attr_unique_id = "spot_electricity_has_tomorrow_data"
        self._attr_translation_key = "spot_electricity_has_tomorrow_data"
        self.entity_id = "binary_sensor.spot_electricity_has_tomorrow_data"

        super().__init__(
            hass=hass,
            coordinator=coordinator,
            device_id=device_id,
            trade=Trade.SPOT,
        )

    @override
    def update(self, rate_data: IntervalTradeRateData | None):
        self._attr = {}

        if not rate_data:
            self._attr_is_on = None
            self._attr_available = False
            return

        trade_rates = self._get_trade_rates(rate_data)
        if not trade_rates:
            self._attr_is_on = None
            self._attr_available = False
            return

        self._attr_is_on = trade_rates.tomorrow is not None
        self._attr_available = True


class HasTomorrowGasData(BinarySpotRateSensorBase):
    _attr_icon = "mdi:cash-clock"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EntryCoordinator,
        device_id: str,
    ) -> None:
        self._attr_unique_id = "spot_gas_has_tomorrow_data"
        self._attr_translation_key = "spot_gas_has_tomorrow_data"
        self.entity_id = "binary_sensor.spot_gas_has_tomorrow_data"

        super().__init__(
            hass=hass,
            coordinator=coordinator,
            device_id=device_id,
            trade=Trade.SPOT,
        )

    @override
    def update(self, rate_data: IntervalTradeRateData | None):
        self._attr = {}

        if not rate_data:
            self._attr_is_on = None
            self._attr_available = False
            return

        trade_rates = self._get_trade_rates(rate_data)
        if not trade_rates:
            self._attr_is_on = None
            self._attr_available = False
            return

        self._attr_is_on = trade_rates.tomorrow is not None
        self._attr_available = True
