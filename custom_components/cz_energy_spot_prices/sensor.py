from __future__ import annotations
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, cast, override
from zoneinfo import ZoneInfo

from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity

from . import SpotRateConfigEntry
from .binary_sensor import ElectricityBinarySpotRateSensorBase, GasBinarySpotRateSensorBase
from .coordinator import SpotRateCoordinator, SpotRateData, SpotRateHour, CONSECUTIVE_HOURS
from .spot_rate_mixin import ElectricitySpotRateSensorMixin, GasSpotRateSensorMixin, Trade
from .spot_rate_settings import SpotRateSettings

logger = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpotRateConfigEntry,
    async_add_entities: Callable[[list[Entity]], None],
):
    logger.info('async_setup_entry %s, data: [%s] options: [%s]', entry.unique_id, entry.data, entry.options)

    coordinator = entry.runtime_data
    currency = cast(str, entry.data[CONF_CURRENCY])
    unit = cast(str, entry.data[CONF_UNIT_OF_MEASUREMENT])

    settings = SpotRateSettings(
        currency=currency,
        unit=unit,
        currency_human={
            'EUR': '€',
            'CZK': 'Kč',
            'USD': '$',
        }.get(currency) or '?',
        timezone=hass.config.time_zone,
        zoneinfo=ZoneInfo(hass.config.time_zone),
    )

    # Electricity sensors
    sensors = __get_electricity_sensors(hass, settings, coordinator, Trade.SPOT)
    if coordinator.has_electricity_buy_rate_template():
        sensors += __get_electricity_sensors(hass, settings, coordinator, Trade.BUY)
    if coordinator.has_electricity_sell_rate_template():
        sensors += __get_electricity_sensors(hass, settings, coordinator, Trade.SELL)

    # Gas sensors
    sensors += __get_gas_sensors(hass, settings, coordinator, Trade.SPOT)
    if coordinator.has_gas_buy_rate_template():
        sensors += __get_gas_sensors(hass, settings, coordinator, Trade.BUY)

    # Deprecated sensors
    sensors += __get_deprecated_sensors(hass, settings, coordinator)

    async_add_entities(sensors)

def __get_electricity_sensors(hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator, trade: Trade):
    electricity_rate_sensor = SpotRateElectricitySensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
        trade = trade,
    )
    cheapest_today_electricity_sensor = CheapestTodayElectricitySensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
        trade = trade,
    )
    cheapest_tomorrow_electricity_sensor = CheapestTomorrowElectricitySensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
        trade = trade,
    )
    most_expensive_today_electricity_sensor = MostExpensiveTodayElectricitySensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
        trade = trade,
    )
    most_expensive_tomorrow_electricity_sensor = MostExpensiveTomorrowElectricitySensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
        trade = trade,
    )
    current_electricity_hour_order = CurrentElectricityHourOrder(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
        trade = trade,
    )
    tomorrow_electricity_hour_order = TomorrowElectricityHourOrder(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
        trade = trade,
    )

    sensors = [
        electricity_rate_sensor,
        cheapest_today_electricity_sensor,
        cheapest_tomorrow_electricity_sensor,
        most_expensive_today_electricity_sensor,
        most_expensive_tomorrow_electricity_sensor,
        current_electricity_hour_order,
        tomorrow_electricity_hour_order,
    ]

    return sensors

def __get_gas_sensors(hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator, trade: Trade):
    today_gas = TodayGasSensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
        trade = trade,
    )
    tomorrow_gas = TomorrowGasSensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
        trade = trade,
    )

    sensors = [
        today_gas,
        tomorrow_gas,
    ]

    return sensors

def __get_deprecated_sensors(hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator):
    deprecated_buy_electricity_price = SpotRateElectricitySensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
        trade = Trade.BUY,
        deprecated = True,
    )
    deprecated_sell_electricity_price = SpotRateElectricitySensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
        trade = Trade.SELL,
        deprecated = True,
    )
    deprecated_has_tomorrow_electricity_data = HasTomorrowElectricityData(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
        trade = Trade.SPOT,
    )
    deprecated_buy_gas_price = TodayGasSensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
        trade = Trade.BUY,
        deprecated = True,
    )
    deprecated_has_tomorrow_gas_data = HasTomorrowGasData(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
        trade = Trade.SPOT,
    )

    sensors: list[Entity] = [
        deprecated_buy_electricity_price,
        deprecated_sell_electricity_price,
        deprecated_has_tomorrow_electricity_data,
        deprecated_buy_gas_price,
        deprecated_has_tomorrow_gas_data,
    ]

    for i in CONSECUTIVE_HOURS:
        sensors.append(
            ConsecutiveCheapestElectricitySensor(
                hours=i,
                hass=hass,
                settings=settings,
                coordinator=coordinator,
                trade = Trade.SPOT,
            )
        )

    return sensors


class ElectricitySpotRateSensorBase(ElectricitySpotRateSensorMixin, SensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    pass


class ElectricityPriceSensor(ElectricitySpotRateSensorBase):
    _attr_icon: str | None = "mdi:cash"

    def __init__(self, hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator, trade: Trade) -> None:
        self._attr_native_unit_of_measurement = f'{settings.currency_human}/{settings.unit}'
        self._attr_suggested_display_precision = 2

        super().__init__(hass=hass, settings=settings, coordinator=coordinator, trade=trade)


class SpotRateElectricitySensor(ElectricityPriceSensor):
    def __init__(self, hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator, trade: Trade, deprecated: bool = False) -> None:
        self._deprecated: bool = deprecated

        if self._deprecated:
            self._attr_unique_id = f'sensor.current_spot_electricity_{trade.lower()}_price'
            self._attr_translation_key = f'current_spot_electricity_{trade.lower()}_price'
        else:
            self._attr_unique_id = f'sensor.current_{trade.lower()}_electricity_price'
            self._attr_translation_key = f'current_{trade.lower()}_electricity_price'

        match trade:
            case Trade.BUY:
                self._attr_icon = 'mdi:cash-minus'
            case Trade.SELL:
                self._attr_icon = 'mdi:cash-plus'
            case Trade.SPOT:
                self._attr_icon = "mdi:cash"

        self.entity_id = self._attr_unique_id

        super().__init__(hass=hass, settings=settings, coordinator=coordinator, trade=trade)

    @override
    def update(self, rate_data: SpotRateData | None):
        attributes: dict[str, float] = {}

        if rate_data is None:
            self._attr_available = False
            self._value = None
            self._attr = {}
            return

        hourly_rates = None
        try:
            hourly_rates = self._get_trade_rates(rate_data)
            self._value = hourly_rates.current_hour.price
        except LookupError:
            logger.error(
                'Current time "%s" is not found in SpotRate values:\n%s',
                rate_data.get_now(),
                "\n\t".join([dt.isoformat() for dt in hourly_rates.hours_by_dt.keys()])
                if hourly_rates
                else "",
            )
            self._attr_available = False
            return

        if self._deprecated:
            self._attr = {}
            self._attr_available = True
            return

        for hour_data in hourly_rates.today_day.hours_by_dt.values():
            dt_local = hour_data.dt_local.isoformat()
            attributes[dt_local] = float(hour_data.price)

        if hourly_rates.tomorrow_day:
            for hour_data in hourly_rates.tomorrow_day.hours_by_dt.values():
                dt_local = hour_data.dt_local.isoformat()
                attributes[dt_local] = float(hour_data.price)

        self._attr = attributes
        self._attr_available = True


class HourFindSensor(ElectricityPriceSensor):
    def find_hour(self, _rate_data: SpotRateData | None) -> SpotRateHour | None:
        raise NotImplementedError()

    @override
    def update(self, rate_data: SpotRateData | None):
        hour = self.find_hour(rate_data)

        if hour is None:
            self._attr_available = False
            self._value = None
            self._attr = {}
            logger.info('No value found for %s', self.unique_id)
            return

        self._attr_available = True
        if self._value is None:
            logger.debug('%s initialized with %.2f at %s', self.unique_id, hour.price, hour.dt_utc.isoformat())
        elif round(hour.price or 0, 2) != round(self._value, 2):
            logger.debug('%s updated from %.2f to %.2f at %s', self.unique_id, self._value, hour.price, hour.dt_utc.isoformat())
        else:
            logger.debug('%s unchanged with %.2f at %s', self.unique_id, hour.price, hour.dt_utc.isoformat())

        self._value = hour.price
        self._attr = {
            'at': hour.dt_local.isoformat(),
            'hour': hour.dt_local.hour,
        }


class CheapestTodayElectricitySensor(HourFindSensor):
    def __init__(self, hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator, trade: Trade) -> None:
        self._attr_unique_id = f'sensor.current_{trade.lower()}_electricity_cheapest_today'
        self._attr_translation_key = f'{trade.lower()}_electricity_cheapest_today'

        self.entity_id = f'sensor.{trade.lower()}_cheapest_electricity_today'

        super().__init__(hass=hass, settings=settings, coordinator=coordinator, trade=trade)

    @override
    def find_hour(self, rate_data: SpotRateData | None) -> SpotRateHour | None:
        if not rate_data:
            return None

        hourly_rates = self._get_trade_rates(rate_data)
        return hourly_rates.today_day.cheapest_hour()


class CheapestTomorrowElectricitySensor(HourFindSensor):
    def __init__(self, hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator, trade: Trade) -> None:
        self._attr_unique_id = f'sensor.current_{trade.lower()}_electricity_cheapest_tomorrow'
        self._attr_translation_key = f'{trade.lower()}_electricity_cheapest_tomorrow'

        self.entity_id = f'sensor.{trade.lower()}_cheapest_electricity_tomorrow'

        super().__init__(hass=hass, settings=settings, coordinator=coordinator, trade=trade)

    @override
    def find_hour(self, rate_data: SpotRateData | None) -> SpotRateHour | None:
        if not rate_data:
            return None

        hourly_rates = self._get_trade_rates(rate_data)
        if not hourly_rates.tomorrow:
            return None

        return hourly_rates.tomorrow.cheapest_hour()


class MostExpensiveTodayElectricitySensor(HourFindSensor):
    def __init__(self, hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator, trade: Trade) -> None:
        self._attr_unique_id = f'sensor.current_{trade.lower()}_electricity_most_expensive_today'
        self._attr_translation_key = f'{trade.lower()}_electricity_most_expensive_today'

        self.entity_id = f'sensor.{trade.lower()}_most_expensive_electricity_today'

        super().__init__(hass=hass, settings=settings, coordinator=coordinator, trade=trade)

    @override
    def find_hour(self, rate_data: SpotRateData | None) -> SpotRateHour | None:
        if not rate_data:
            return None

        hourly_rates = self._get_trade_rates(rate_data)
        return hourly_rates.today.most_expensive_hour()


class MostExpensiveTomorrowElectricitySensor(HourFindSensor):
    def __init__(self, hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator, trade: Trade) -> None:
        self._attr_unique_id = f'sensor.current_{trade.lower()}_electricity_most_expensive_tomorrow'
        self._attr_translation_key = f'{trade.lower()}_electricity_most_expensive_tomorrow'

        self.entity_id = f'sensor.{trade.lower()}_most_expensive_electricity_tomorrow'

        super().__init__(hass=hass, settings=settings, coordinator=coordinator, trade=trade)

    @override
    def find_hour(self, rate_data: SpotRateData | None) -> SpotRateHour | None:
        if not rate_data:
            return None

        hourly_rates = self._get_trade_rates(rate_data)
        if not hourly_rates.tomorrow:
            return None

        return hourly_rates.tomorrow.most_expensive_hour()


class EnergyHourOrder(ElectricitySpotRateSensorBase):
    _attr_icon = 'mdi:hours-24'


class CurrentElectricityHourOrder(EnergyHourOrder):
    def __init__(self, hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator, trade: Trade) -> None:
        self._attr_unique_id = f'sensor.current_{trade.lower()}_electricity_hour_order'
        self._attr_translation_key = f'{trade.lower()}_electricity_hour_order_today'

        self.entity_id = self._attr_unique_id

        super().__init__(hass=hass, settings=settings, coordinator=coordinator, trade=trade)

    @override
    def update(self, rate_data: SpotRateData | None):
        self._attr = {}

        if rate_data is None:
            self._attr_available = False
            self._value = None
            return

        hourly_rates = self._get_trade_rates(rate_data)
        cheapest_order = hourly_rates.current_hour.cheapest_consecutive_order[1]
        if cheapest_order != self._value:
            logger.debug('%s updated from %s to %s', self.unique_id, self._value, cheapest_order)
            self._value = cheapest_order
        else:
            logger.debug('%s unchanged with %d', self.unique_id, cheapest_order)

        for hour in hourly_rates.today.hours_by_dt.values():
            self._attr[hour.dt_local.isoformat()] = [hour.cheapest_consecutive_order[1], float(round(hour.price, 3))]

        self._attr_available = True


class TomorrowElectricityHourOrder(EnergyHourOrder):
    def __init__(self, hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator, trade: Trade) -> None:
        self._attr_unique_id = f'sensor.tomorrow_{trade.lower()}_electricity_hour_order'
        self._attr_translation_key = f'{trade.lower()}_electricity_hour_order_tomorrow'

        self.entity_id = self._attr_unique_id

        super().__init__(hass=hass, settings=settings, coordinator=coordinator, trade=trade)

    @override
    def update(self, rate_data: SpotRateData | None):
        self._attr = {}
        self._value = None

        if not rate_data:
            self._attr_available = False
            return

        hourly_rates = self._get_trade_rates(rate_data)
        if hourly_rates.tomorrow is None:
            self._attr_available = False
            return

        for hour in hourly_rates.tomorrow.hours_by_dt.values():
            self._attr[hour.dt_local.isoformat()] = [hour.cheapest_consecutive_order[1], float(round(hour.price, 3))]

        self._attr_available = True


#BC
class ConsecutiveCheapestElectricitySensor(ElectricityBinarySpotRateSensorBase):
    _attr_icon = 'mdi:cash-clock'

    def __init__(self, hours: int, hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator, trade: Trade) -> None:
        self.hours = hours

        if self.hours == 1:
            self._attr_unique_id = f'sensor.{trade.lower()}_electricity_is_cheapest'
            self._attr_translation_key = f'{trade.lower()}_electricity_is_cheapest'
        else:
            self._attr_unique_id = f'sensor.{trade.lower()}_electricity_is_cheapest_{self.hours}_hours_block'
            self._attr_translation_key = f'{trade.lower()}_electricity_is_cheapest_hours_block'
            self._attr_translation_placeholders = {
                "hours": str(self.hours),
            }

        self.entity_id = self._attr_unique_id

        super().__init__(hass=hass, settings=settings, coordinator=coordinator, trade=trade)

    def _compute_attr(
        self, rate_data: SpotRateData, start: datetime, end: datetime
    ) -> dict[str, Any]:
        dt = start
        min_price: Decimal | None = None
        max_price: Decimal | None = None
        sum_price: Decimal = Decimal(0)
        count: int = 0

        hourly_rates = self._get_trade_rates(rate_data)
        while dt <= end:
            hour = hourly_rates.hour_for_dt(dt)
            sum_price += hour.price
            count += 1
            if min_price is None or hour.price < min_price:
                min_price = hour.price

            if max_price is None or hour.price > max_price:
                max_price = hour.price

            dt += timedelta(hours=1)
        return {
            'Start': start,
            'Start hour': start.hour,
            'End': end,
            'End hour': end.hour,
            'Min': float(min_price or 0),
            'Max': float(max_price or 0),
            'Mean': float(sum_price / count) if count > 0 else 0,
        }

    @override
    def update(self, rate_data: SpotRateData | None):
        self._attr = {}

        if not rate_data:
            self._attr_available = False
            self._attr_is_on = None
            return

        is_on = False
        hourly_rates = self._get_trade_rates(rate_data)
        for hour in hourly_rates.hours_by_dt.values():
            start = hour.dt_local - timedelta(hours=self.hours - 1)
            end = hour.dt_local + timedelta(hours=1, seconds=-1)

            # Ignore start times before now, we only want future blocks
            if end < hourly_rates.now:
                continue

            if hour.cheapest_consecutive_order[self.hours] == 1:
                if not self._attr:
                    # Only put it there once, so to contains closes interval in the future
                    self._attr = self._compute_attr(rate_data, start, end)

                if start <= hourly_rates.now <= end:
                    is_on = True

            self._attr_is_on = is_on
            self._attr_available = True


#BC
class HasTomorrowElectricityData(ElectricityBinarySpotRateSensorBase):
    _attr_icon = 'mdi:cash-clock'

    def __init__(self, hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator, trade: Trade) -> None:
        self._attr_unique_id = f'sensor.{trade.lower()}_electricity_has_tomorrow_data'
        self._attr_translation_key = f'{trade.lower()}_electricity_has_tomorrow_data'

        self.entity_id = self._attr_unique_id

        super().__init__(hass=hass, settings=settings, coordinator=coordinator, trade=trade)

    @override
    def update(self, rate_data: SpotRateData | None):
        self._attr = {}

        if not rate_data:
            self._attr_is_on = None
            self._attr_available = False
            return

        self._attr_is_on = self._get_trade_rates(rate_data).tomorrow is not None
        self._attr_available = True


class GasSpotRateSensorBase(GasSpotRateSensorMixin, SensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    pass


class GasPriceSensor(GasSpotRateSensorBase):
    _attr_icon = 'mdi:cash'

    def __init__(self, hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator, trade: Trade) -> None:
        self._attr_native_unit_of_measurement = f'{settings.currency_human}/{settings.unit}'
        self._attr_suggested_display_precision = 2

        super().__init__(hass=hass, settings=settings, coordinator=coordinator, trade=trade)


class TodayGasSensor(GasPriceSensor):
    def __init__(self, hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator, trade: Trade, deprecated: bool = False) -> None:
        if deprecated:
            self._attr_unique_id = f'sensor.current_spot_gas_{trade.lower()}_price'
            self._attr_translation_key = f'current_spot_gas_{trade.lower()}_price'
        else:
            self._attr_unique_id = f'sensor.current_{trade.lower()}_gas_price'
            self._attr_translation_key = f'current_{trade.lower()}_gas_price'

        match trade:
            case Trade.BUY:
                self._attr_icon = 'mdi:cash-minus'
            case Trade.SELL:
                self._attr_icon = 'mdi:cash-plus'
            case Trade.SPOT:
                self._attr_icon = "mdi:cash"

        self.entity_id = self._attr_unique_id

        super().__init__(hass=hass, settings=settings, coordinator=coordinator, trade=trade)

    @override
    def update(self, rate_data: SpotRateData | None):
        self._attr = {}

        if rate_data is None:
            self._attr_available = False
            self._value = None
            return

        self._attr_available = True
        self._value = self._get_trade_rates(rate_data).today


class TomorrowGasSensor(GasPriceSensor):
    def __init__(self, hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator, trade: Trade) -> None:
        self._attr_unique_id = f'sensor.tomorrow_{trade.lower()}_gas_price'
        self._attr_translation_key = f'tomorrow_{trade.lower()}_gas_price'

        match trade:
            case Trade.BUY:
                self._attr_icon = 'mdi:cash-minus'
            case Trade.SELL:
                self._attr_icon = 'mdi:cash-plus'
            case Trade.SPOT:
                self._attr_icon = "mdi:cash"

        self.entity_id = self._attr_unique_id

        super().__init__(hass=hass, settings=settings, coordinator=coordinator, trade=trade)

    @override
    def update(self, rate_data: SpotRateData | None):
        self._attr = {}

        if rate_data is None:
            self._attr_available = False
            self._value = None
            return

        self._value = self._get_trade_rates(rate_data).tomorrow
        self._attr_available = self._value is not None


class HasTomorrowGasData(GasBinarySpotRateSensorBase):
    _attr_icon = 'mdi:cash-clock'

    def __init__(self, hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator, trade: Trade) -> None:
        self._attr_unique_id = f'sensor.{trade.lower()}_gas_has_tomorrow_data'
        self._attr_translation_key = f'{trade.lower()}_gas_has_tomorrow_data'

        self.entity_id = self._attr_unique_id

        super().__init__(hass=hass, settings=settings, coordinator=coordinator, trade=trade)

    @override
    def update(self, rate_data: SpotRateData | None):
        self._attr = {}

        if not rate_data:
            self._attr_is_on = None
            self._attr_available = False
            return

        self._attr_is_on = self._get_trade_rates(rate_data).tomorrow is not None
        self._attr_available = True
