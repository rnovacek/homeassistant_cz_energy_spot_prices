from __future__ import annotations
import logging
from typing import Any, Callable, cast, override

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity

from custom_components.cz_energy_spot_prices.config_flow import (
    ELECTRICITY,
    GAS,
)


from . import SpotRateConfigEntry
from .const import (
    ENTRY_COORDINATOR,
    DOMAIN,
    GLOBAL_ELECTRICITY_SENSOR_FLAG,
    GLOBAL_GAS_SENSOR_FLAG,
    SpotRateIntervalType,
)
from .coordinator import (
    EntryCoordinator,
    IntervalTradeRateData,
    get_now,
)
from .spot_rate_mixin import SpotRateSensorMixin, Trade

logger = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpotRateConfigEntry,
    async_add_entities: Callable[[list[Entity]], None],
) -> None:
    logger.debug(
        "binary_sensor.async_setup_entry %s, data: [%s] options: [%s]",
        entry.unique_id,
        entry.data,
        entry.options,
    )

    domain_data = cast(dict[str, Any], hass.data[DOMAIN])
    coordinator = cast(EntryCoordinator, domain_data[ENTRY_COORDINATOR][entry.entry_id])

    commodity = coordinator.config.commodity

    sensors: list[Entity] = []

    # Add these sensors only once per integration as they are shared between services
    if commodity == ELECTRICITY:
        if GLOBAL_ELECTRICITY_SENSOR_FLAG not in domain_data:
            has_tomorrow_electricity_data = HasTomorrowElectricityData(
                hass=hass,
                coordinator=coordinator,
                device_id=entry.entry_id,
            )
            sensors.append(has_tomorrow_electricity_data)
            hass.data[DOMAIN][GLOBAL_ELECTRICITY_SENSOR_FLAG] = True

    elif commodity == GAS:
        if GLOBAL_GAS_SENSOR_FLAG not in domain_data:
            has_tomorrow_gas_data = HasTomorrowGasData(
                hass=hass,
                coordinator=coordinator,
                device_id=entry.entry_id,
            )
            sensors.append(has_tomorrow_gas_data)
            hass.data[DOMAIN][GLOBAL_GAS_SENSOR_FLAG] = True

    if commodity == ELECTRICITY:
        cheapest_blocks = coordinator.config.all_cheapest_blocks()

        for i in cheapest_blocks:
            sensors.append(
                ConsecutiveCheapestElectricitySensor(
                    hours=i,
                    hass=hass,
                    coordinator=coordinator,
                    device_id=entry.entry_id,
                    trade=Trade.SPOT,
                )
            )

        if coordinator.buy_template:
            for i in cheapest_blocks:
                sensors.append(
                    ConsecutiveCheapestElectricitySensor(
                        hours=i,
                        hass=hass,
                        coordinator=coordinator,
                        device_id=entry.entry_id,
                        trade=Trade.BUY,
                    )
                )

        if coordinator.sell_template:
            for i in cheapest_blocks:
                sensors.append(
                    ConsecutiveCheapestElectricitySensor(
                        hours=i,
                        hass=hass,
                        coordinator=coordinator,
                        device_id=entry.entry_id,
                        trade=Trade.SELL,
                    )
                )

    async_add_entities(sensors)


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
                logger.error("Unable to find cheapest interval")
            else:
                logger.error("Unable to find cheapest %s hour block", self.hours)
            self._attr_available = False
            return

        self._attr_is_on = window.start <= now < window.end
        start = window.start.astimezone(self.coordinator.config.zoneinfo)
        end = window.end.astimezone(self.coordinator.config.zoneinfo)
        self._attr = {
            "Start": start,
            "End": end,
            "Min": float(min(window.prices)),
            "Max": float(max(window.prices)),
            "Mean": float(sum(window.prices) / len(window.prices)),
        }
        if self.coordinator.config.interval == SpotRateIntervalType.Hour:
            # Doesn't make sense to have these on 15min intervals
            self._attr["Start hour"] = start.hour
            self._attr["End hour"] = end.hour
        self._attr_available = True


class HasTomorrowElectricityData(BinarySpotRateSensorBase):
    _attr_icon = 'mdi:cash-clock'

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EntryCoordinator,
        device_id: str,
    ) -> None:
        # Not device specific - only one exists for all the devices
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
    _attr_icon = 'mdi:cash-clock'

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EntryCoordinator,
        device_id: str,
    ) -> None:
        # Not device specific - only one exists for all the devices
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
