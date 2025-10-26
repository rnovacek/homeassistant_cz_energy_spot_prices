from __future__ import annotations
from collections.abc import Sequence
import logging
from typing import Any, Callable, cast, override

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity

from . import SpotRateConfigEntry
from .config_flow import (
    ELECTRICITY,
    GAS,
)
from .const import (
    DOMAIN,
    ENTRY_COORDINATOR,
    SpotRateIntervalType,
)
from .coordinator import (
    EntryCoordinator,
    IntervalTradeRateData,
    SpotRateInterval,
    get_now,
)
from .spot_rate_mixin import (
    SpotRateSensorMixin,
    Trade,
)

logger = logging.getLogger(__name__)


GLOBAL_SENSOR_FLAG = "global_sensor_created"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpotRateConfigEntry,
    async_add_entities: Callable[[Sequence[Entity]], None],
):
    logger.debug(
        "sensor.async_setup_entry %s, data: [%s] options: [%s]",
        entry.as_dict(),
        entry.data,
        entry.options,
    )

    domain_data = cast(dict[str, Any], hass.data[DOMAIN])
    coordinator = cast(EntryCoordinator, domain_data[ENTRY_COORDINATOR][entry.entry_id])

    commodity = coordinator.config.commodity

    sensors: list[SensorEntity | BinarySensorEntity] = []

    # Electricity sensors
    if commodity == ELECTRICITY:
        sensors += _get_electricity_sensors(
            hass,
            coordinator,
            trade=Trade.SPOT,
            device_id=entry.entry_id,
        )
        if coordinator.buy_template:
            sensors += _get_electricity_sensors(
                hass,
                coordinator,
                trade=Trade.BUY,
                device_id=entry.entry_id,
            )
        if coordinator.sell_template:
            sensors += _get_electricity_sensors(
                hass,
                coordinator,
                trade=Trade.SELL,
                device_id=entry.entry_id,
            )

    # Gas sensors
    if commodity == GAS:
        sensors += _get_gas_sensors(
            hass, coordinator, trade=Trade.SPOT, device_id=entry.entry_id
        )
        if coordinator.buy_template:
            sensors += _get_gas_sensors(
                hass, coordinator, trade=Trade.BUY, device_id=entry.entry_id
            )

    async_add_entities(sensors)


def _get_electricity_sensors(
    hass: HomeAssistant,
    coordinator: EntryCoordinator,
    device_id: str,
    trade: Trade,
) -> list[SensorEntity | BinarySensorEntity]:
    sensors: list[SensorEntity | BinarySensorEntity] = []

    sensors.append(
        SpotRateElectricitySensor(
            hass=hass,
            coordinator=coordinator,
            device_id=device_id,
            trade=trade,
        )
    )
    sensors.append(
        CheapestTodayElectricitySensor(
            hass=hass,
            coordinator=coordinator,
            device_id=device_id,
            trade=trade,
        )
    )
    sensors.append(
        CheapestTomorrowElectricitySensor(
            hass=hass,
            coordinator=coordinator,
            device_id=device_id,
            trade=trade,
        )
    )
    sensors.append(
        MostExpensiveTodayElectricitySensor(
            hass=hass,
            coordinator=coordinator,
            device_id=device_id,
            trade=trade,
        )
    )
    sensors.append(
        MostExpensiveTomorrowElectricitySensor(
            hass=hass,
            coordinator=coordinator,
            device_id=device_id,
            trade=trade,
        )
    )
    sensors.append(
        CurrentElectricityIntervalOrder(
            hass=hass,
            coordinator=coordinator,
            device_id=device_id,
            trade=trade,
        )
    )
    sensors.append(
        TomorrowElectricityIntervalOrder(
            hass=hass,
            coordinator=coordinator,
            device_id=device_id,
            trade=trade,
        )
    )

    return sensors


def _get_gas_sensors(
    hass: HomeAssistant,
    coordinator: EntryCoordinator,
    device_id: str,
    trade: Trade,
) -> list[SensorEntity | BinarySensorEntity]:
    today_gas = TodayGasSensor(
        hass=hass,
        coordinator=coordinator,
        device_id=device_id,
        trade=trade,
    )
    tomorrow_gas = TomorrowGasSensor(
        hass=hass,
        coordinator=coordinator,
        device_id=device_id,
        trade=trade,
    )

    sensors: list[SensorEntity | BinarySensorEntity] = [
        today_gas,
        tomorrow_gas,
    ]

    return sensors


class ElectricitySpotRateSensorBase(SpotRateSensorMixin, SensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    _name_template: str | None = None

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EntryCoordinator,
        device_id: str,
        trade: Trade,
    ) -> None:
        if self._name_template:
            interval = (
                "_15min"
                if coordinator.config.interval == SpotRateIntervalType.QuarterHour
                else ""
            )
            hour_or_15min = (
                "15min"
                if coordinator.config.interval == SpotRateIntervalType.QuarterHour
                else "hour"
            )
            name = self._name_template.format(
                trade=trade.lower(),
                interval=interval,
                hour_or_15min=hour_or_15min,
            )
            self._attr_unique_id = f"{device_id}_{name}"
            self._attr_translation_key = name
            self.entity_id = f"sensor.{name}"

        super().__init__(
            hass=hass,
            coordinator=coordinator,
            device_id=device_id,
            trade=trade,
        )

        if self._attr_unique_id is None:
            raise ValueError(
                "Sensor %s does not have unique_id", self.__class__.__name__
            )


class ElectricityPriceSensor(ElectricitySpotRateSensorBase):
    _attr_icon: str | None = "mdi:cash"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EntryCoordinator,
        device_id: str,
        trade: Trade,
    ) -> None:
        self._attr_native_unit_of_measurement = (
            f"{coordinator.config.currency_human}/{coordinator.config.unit}"
        )
        self._attr_suggested_display_precision = 2

        super().__init__(
            hass=hass,
            coordinator=coordinator,
            device_id=device_id,
            trade=trade,
        )


class SpotRateElectricitySensor(ElectricityPriceSensor):
    _name_template = "current_{trade}_electricity_price{interval}"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EntryCoordinator,
        device_id: str,
        trade: Trade,
    ) -> None:
        match trade:
            case Trade.BUY:
                self._attr_icon = "mdi:cash-minus"
            case Trade.SELL:
                self._attr_icon = "mdi:cash-plus"
            case Trade.SPOT:
                self._attr_icon = "mdi:cash"

        super().__init__(
            hass=hass,
            coordinator=coordinator,
            device_id=device_id,
            trade=trade,
        )

    @override
    def update(self, rate_data: IntervalTradeRateData | None):
        attributes: dict[str, float] = {}

        if rate_data is None:
            logger.debug("No rate data for %s", self.entity_id)
            self._attr_available = False
            self._value = None
            self._attr = {}
            return

        trade_rates = self._get_trade_rates(rate_data)
        if not trade_rates:
            logger.debug("No trade rate data for %s", self.entity_id)
            self._attr_available = False
            self._value = None
            self._attr = {}
            return

        try:
            self._value = trade_rates.current_interval.price
            logger.debug("Setting %s to %s", self.unique_id, self._value)
        except LookupError:
            logger.error(
                'Current time "%s" is not found in SpotRate values:\n%s',
                get_now(),
                "\n\t".join(
                    [dt.isoformat() for dt in trade_rates.interval_by_dt.keys()]
                )
                if trade_rates
                else "",
            )
            self._attr_available = False
            return

        if not trade_rates.today:
            logger.error("No today spot rate data found for %s", self.entity_id)
            self._attr_available = False
            return

        for interval_data in trade_rates.today.interval_by_dt.values():
            dt_local = interval_data.dt_local.isoformat()
            attributes[dt_local] = float(interval_data.price)

        if trade_rates.tomorrow:
            for interval_data in trade_rates.tomorrow.interval_by_dt.values():
                dt_local = interval_data.dt_local.isoformat()
                attributes[dt_local] = float(interval_data.price)

        self._attr = attributes
        self._attr_available = True
        logger.debug("Setting %s _attr_available to %s", self.unique_id, self._value)


class HourFindSensor(ElectricityPriceSensor):
    def find_interval(
        self, _rate_data: IntervalTradeRateData | None
    ) -> SpotRateInterval | None:
        raise NotImplementedError()

    @override
    def update(self, rate_data: IntervalTradeRateData | None):
        interval = self.find_interval(rate_data)

        if interval is None:
            self._attr_available = False
            self._value = None
            self._attr = {}
            logger.debug("No value found for %s", self.unique_id)
            return

        self._attr_available = True
        if self._value is None:
            logger.debug(
                "%s initialized with %.2f at %s",
                self.unique_id,
                interval.price,
                interval.dt_utc.isoformat(),
            )
        elif round(interval.price or 0, 2) != round(self._value, 2):
            logger.debug(
                "%s updated from %.2f to %.2f at %s",
                self.unique_id,
                self._value,
                interval.price,
                interval.dt_utc.isoformat(),
            )
        else:
            logger.debug(
                "%s unchanged with %.2f at %s",
                self.unique_id,
                interval.price,
                interval.dt_utc.isoformat(),
            )

        self._value = interval.price
        self._attr = {
            "at": interval.dt_local.isoformat(),
        }
        # Add hour if it's 60min sensor
        if self.coordinator.config.interval == SpotRateIntervalType.Hour:
            self._attr["hour"] = interval.dt_local.hour


class CheapestTodayElectricitySensor(HourFindSensor):
    _name_template = "{trade}_cheapest_electricity_today{interval}"

    @override
    def find_interval(
        self, rate_data: IntervalTradeRateData | None
    ) -> SpotRateInterval | None:
        if not rate_data:
            return None

        trade_rates = self._get_trade_rates(rate_data)
        if not trade_rates or not trade_rates.today:
            return None

        return trade_rates.today.cheapest_interval()


class CheapestTomorrowElectricitySensor(HourFindSensor):
    _name_template = "{trade}_cheapest_electricity_tomorrow{interval}"

    @override
    def find_interval(
        self, rate_data: IntervalTradeRateData | None
    ) -> SpotRateInterval | None:
        if not rate_data:
            return None

        trade_rates = self._get_trade_rates(rate_data)
        if not trade_rates or not trade_rates.tomorrow:
            return None

        return trade_rates.tomorrow.cheapest_interval()


class MostExpensiveTodayElectricitySensor(HourFindSensor):
    _name_template = "{trade}_most_expensive_electricity_today{interval}"

    @override
    def find_interval(
        self, rate_data: IntervalTradeRateData | None
    ) -> SpotRateInterval | None:
        if not rate_data:
            return None

        trade_rates = self._get_trade_rates(rate_data)
        if not trade_rates or not trade_rates.today:
            return None

        return trade_rates.today.most_expensive_interval()


class MostExpensiveTomorrowElectricitySensor(HourFindSensor):
    _name_template = "{trade}_most_expensive_electricity_tomorrow{interval}"

    @override
    def find_interval(
        self, rate_data: IntervalTradeRateData | None
    ) -> SpotRateInterval | None:
        if not rate_data:
            return None

        trade_rates = self._get_trade_rates(rate_data)
        if not trade_rates or not trade_rates.tomorrow:
            return None

        return trade_rates.tomorrow.most_expensive_interval()


class EnergyIntervalOrder(ElectricitySpotRateSensorBase):
    _attr_icon = "mdi:hours-24"


class CurrentElectricityIntervalOrder(EnergyIntervalOrder):
    _name_template = "current_{trade}_electricity_{hour_or_15min}_order"

    @override
    def update(self, rate_data: IntervalTradeRateData | None):
        self._attr = {}

        if rate_data is None:
            self._attr_available = False
            self._value = None
            return

        trade_rates = self._get_trade_rates(rate_data)
        if not trade_rates:
            self._attr_available = False
            self._value = None
            return

        if not trade_rates.today:
            logger.error("No today spot rate data found for %s", self.entity_id)

            self._attr_available = False
            self._value = None
            return

        interval_order = trade_rates.today.interval_order()
        now = trade_rates.current_interval.dt_utc
        cheapest_order = interval_order[now]
        if cheapest_order != self._value:
            logger.debug(
                "%s updated from %s to %s", self.unique_id, self._value, cheapest_order
            )
            self._value = cheapest_order
        else:
            logger.debug("%s unchanged with %d", self.unique_id, cheapest_order)

        for interval in trade_rates.today.interval_by_dt.values():
            self._attr[interval.dt_local.isoformat()] = [
                interval_order[interval.dt_utc],
                float(round(interval.price, 3)),
            ]

        self._attr_available = True


class TomorrowElectricityIntervalOrder(EnergyIntervalOrder):
    _name_template = "tomorrow_{trade}_electricity_{hour_or_15min}_order"

    @override
    def update(self, rate_data: IntervalTradeRateData | None):
        self._attr = {}
        self._value = None

        if not rate_data:
            self._attr_available = False
            return

        trade_rates = self._get_trade_rates(rate_data)
        if not trade_rates or trade_rates.tomorrow is None:
            self._attr_available = False
            return

        interval_order = trade_rates.tomorrow.interval_order()
        for interval in trade_rates.tomorrow.interval_by_dt.values():
            self._attr[interval.dt_local.isoformat()] = [
                interval_order[interval.dt_utc],
                float(round(interval.price, 3)),
            ]

        self._attr_available = True


class SpotRateSensorBase(SpotRateSensorMixin, SensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    pass


class GasPriceSensor(SpotRateSensorBase):
    _attr_icon = "mdi:cash"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EntryCoordinator,
        device_id: str,
        trade: Trade,
    ) -> None:
        self._attr_native_unit_of_measurement = (
            f"{coordinator.config.currency_human}/{coordinator.config.unit}"
        )
        self._attr_suggested_display_precision = 2

        super().__init__(
            hass=hass,
            coordinator=coordinator,
            device_id=device_id,
            trade=trade,
        )


class TodayGasSensor(GasPriceSensor):
    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EntryCoordinator,
        trade: Trade,
        device_id: str,
    ) -> None:
        name = f"current_{trade.lower()}_gas_price"
        self._attr_unique_id = f"{device_id}_{name}"
        self._attr_translation_key = name
        self.entity_id = f"sensor.{name}"
        match trade:
            case Trade.BUY:
                self._attr_icon = "mdi:cash-minus"
            case Trade.SELL:
                self._attr_icon = "mdi:cash-plus"
            case Trade.SPOT:
                self._attr_icon = "mdi:cash"

        super().__init__(
            hass=hass,
            coordinator=coordinator,
            device_id=device_id,
            trade=trade,
        )

    @override
    def update(self, rate_data: IntervalTradeRateData | None):
        self._attr = {}

        if rate_data is None:
            self._attr_available = False
            self._value = None
            return

        trade_rates = self._get_trade_rates(rate_data)
        if not trade_rates or trade_rates.today is None:
            self._attr_available = False
            self._value = None
            return

        first = trade_rates.today.first()
        if not first:
            self._attr_available = False
            self._value = None
            return

        self._value = first.price
        self._attr_available = True


class TomorrowGasSensor(GasPriceSensor):
    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EntryCoordinator,
        device_id: str,
        trade: Trade,
    ) -> None:
        name = f"tomorrow_{trade.lower()}_gas_price"
        self._attr_unique_id = f"{device_id}_{name}"
        self._attr_translation_key = name
        self.entity_id = f"sensor.{name}"

        match trade:
            case Trade.BUY:
                self._attr_icon = "mdi:cash-minus"
            case Trade.SELL:
                self._attr_icon = "mdi:cash-plus"
            case Trade.SPOT:
                self._attr_icon = "mdi:cash"

        super().__init__(
            hass=hass,
            coordinator=coordinator,
            device_id=device_id,
            trade=trade,
        )

    @override
    def update(self, rate_data: IntervalTradeRateData | None):
        self._attr = {}

        if rate_data is None:
            self._attr_available = False
            self._value = None
            return

        trade_rates = self._get_trade_rates(rate_data)
        if not trade_rates or trade_rates.tomorrow is None:
            self._attr_available = False
            self._value = None
            return

        first = trade_rates.tomorrow.first()
        if not first:
            self._attr_available = False
            self._value = None
            return

        self._value = first.price
        self._attr_available = True
