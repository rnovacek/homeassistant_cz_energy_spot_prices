from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Literal
from decimal import Decimal
from zoneinfo import ZoneInfo

from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template, TemplateError

from . import SpotRateConfigEntry
from .const import ADDITIONAL_COSTS_SELL_ELECTRICITY, ADDITIONAL_COSTS_BUY_ELECTRICITY, ADDITIONAL_COSTS_BUY_GAS
from .coordinator import SpotRateCoordinator, SpotRateData, SpotRateHour, CONSECUTIVE_HOURS
from .binary_sensor import BinarySpotRateSensorBase
from .spot_rate_mixin import SpotRateSensorMixin
from .spot_rate_settings import SpotRateSettings

logger = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: SpotRateConfigEntry, async_add_entities):
    logger.info('async_setup_entry %s, data: [%s] options: [%s]', entry.unique_id, entry.data, entry.options)

    coordinator = entry.runtime_data
    currency = entry.data[CONF_CURRENCY]
    unit = entry.data[CONF_UNIT_OF_MEASUREMENT]

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

    electricity_rate_sensor = SpotRateElectricitySensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )
    cheapest_today_electricity_sensor = CheapestTodayElectricitySensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )
    cheapest_tomorrow_electricity_sensor = CheapestTomorrowElectricitySensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )
    most_expensive_today_electricity_sensor = MostExpensiveTodayElectricitySensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )
    most_expensive_tomorrow_electricity_sensor = MostExpensiveTomorrowElectricitySensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )
    current_electricity_hour_order = CurrentElectricityHourOrder(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )
    tomorrow_electricity_hour_order = TomorrowElectricityHourOrder(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )
    has_tomorrow_electricity_data = HasTomorrowElectricityData(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )

    today_gas = TodayGasSensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )
    tomorrow_gas = TomorrowGasSensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )
    has_tomorrow_gas_data = HasTomorrowGasData(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )

    additional_costs_buy_electricity = entry.options.get(ADDITIONAL_COSTS_BUY_ELECTRICITY) or ''
    additional_costs_sell_electricity = entry.options.get(ADDITIONAL_COSTS_SELL_ELECTRICITY) or ''
    additional_costs_buy_gas = entry.options.get(ADDITIONAL_COSTS_BUY_GAS) or ''

    sensors = [
        electricity_rate_sensor,
        cheapest_today_electricity_sensor,
        cheapest_tomorrow_electricity_sensor,
        most_expensive_today_electricity_sensor,
        most_expensive_tomorrow_electricity_sensor,
        current_electricity_hour_order,
        tomorrow_electricity_hour_order,
        has_tomorrow_electricity_data,
        today_gas,
        tomorrow_gas,
        has_tomorrow_gas_data
    ]

    if additional_costs_buy_electricity:
        energy_price_buy_electricity_sensor = ElectricityPriceBuy(
            hass=hass,
            resource='electricity',
            settings=settings,
            coordinator=coordinator,
            template_code=additional_costs_buy_electricity,
        )
        sensors.append(energy_price_buy_electricity_sensor)

    if additional_costs_sell_electricity:
        energy_price_sell_electricity_sensor = ElectricityPriceSell(
            hass=hass,
            resource='electricity',
            settings=settings,
            coordinator=coordinator,
            template_code=additional_costs_sell_electricity,
        )
        sensors.append(energy_price_sell_electricity_sensor)

    if additional_costs_buy_gas:
        energy_price_buy_gas_sensor = GasPriceBuy(
            hass=hass,
            resource='gas',
            settings=settings,
            coordinator=coordinator,
            template_code=additional_costs_buy_gas,
        )
        sensors.append(energy_price_buy_gas_sensor)

    for i in CONSECUTIVE_HOURS:
        sensors.append(
            ConsecutiveCheapestElectricitySensor(
                hours=i,
                hass=hass,
                settings=settings,
                coordinator=coordinator,
            )
        )

    async_add_entities(sensors)


class SpotRateSensorBase(SpotRateSensorMixin, SensorEntity):
    pass


class PriceSensor(SpotRateSensorBase, SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = 'mdi:cash'

    def __init__(self, hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator) -> None:
        self._attr_unit_of_measurement = f'{settings.currency_human}/{settings.unit}'

        super().__init__(hass, settings, coordinator)


class SpotRateElectricitySensor(PriceSensor):
    _attr_unique_id = 'sensor.current_spot_electricity_price'
    _attr_translation_key = 'current_spot_electricity_price'

    def update(self, rate_data: Optional[SpotRateData]):
        attributes: Dict[str, float] = {}

        if rate_data is None:
            self._available = False
            self._value = None
            self._attr = {}
            return

        try:
            current_hour = rate_data.electricity.current_hour
            self._available = True
        except LookupError:
            logger.error(
                'Current time "%s" is not found in SpotRate values:\n%s',
                rate_data.get_now(),
                '\n\t'.join([dt.isoformat() for dt in rate_data.electricity.hour_for_dt.keys()]),
            )
            self._available = False
            return

        current_value = current_hour.price

        for hour_data in rate_data.electricity.today_day.hours_by_dt.values():
            dt_local = hour_data.dt_local.isoformat()
            attributes[dt_local] = float(hour_data.price)

        if rate_data.electricity.tomorrow_day:
            for hour_data in rate_data.electricity.tomorrow_day.hours_by_dt.values():
                dt_local = hour_data.dt_local.isoformat()
                attributes[dt_local] = float(hour_data.price)

        self._attr = attributes
        self._value = current_value


class HourFindSensor(PriceSensor):
    def find_hour(self, rate_data: Optional[SpotRateData]) -> Optional[SpotRateHour]:
        raise NotImplementedError()

    def update(self, rate_data: Optional[SpotRateData]):
        hour = self.find_hour(rate_data)

        if hour is None:
            self._available = False
            self._value = None
            self._attr = {}
            logger.info('No value found for %s', self.unique_id)
            return

        self._available = True
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
    _attr_unique_id = 'sensor.current_spot_electricity_cheapest_today'
    _attr_translation_key = 'today_spot_electricity_cheapest'

    def find_hour(self, rate_data: Optional[SpotRateData]) -> Optional[SpotRateHour]:
        if not rate_data:
            return None

        return rate_data.electricity.today_day.cheapest_hour()


class CheapestTomorrowElectricitySensor(HourFindSensor):
    _attr_unique_id = 'sensor.current_spot_electricity_cheapest_tomorrow'
    _attr_translation_key = 'tomorrow_spot_electricity_cheapest'

    def find_hour(self, rate_data: Optional[SpotRateData]) -> Optional[SpotRateHour]:
        if not rate_data:
            return None

        if not rate_data.electricity.tomorrow:
            return None

        return rate_data.electricity.tomorrow.cheapest_hour()


class MostExpensiveTodayElectricitySensor(HourFindSensor):
    _attr_unique_id = 'sensor.current_spot_electricity_most_expensive_today'
    _attr_translation_key = 'today_spot_electricity_most_expensive'

    def find_hour(self, rate_data: Optional[SpotRateData]) -> Optional[SpotRateHour]:
        if not rate_data:
            return None

        return rate_data.electricity.today.most_expensive_hour()


class MostExpensiveTomorrowElectricitySensor(HourFindSensor):
    _attr_unique_id = 'sensor.current_spot_electricity_most_expensive_tomorrow'
    _attr_translation_key = 'tomorrow_spot_electricity_most_expensive'

    def find_hour(self, rate_data: Optional[SpotRateData]) -> Optional[SpotRateHour]:
        if not rate_data:
            return None

        if not rate_data.electricity.tomorrow:
            return None

        return rate_data.electricity.tomorrow.most_expensive_hour()


class EnergyHourOrder(SpotRateSensorBase):
    _attr_icon = 'mdi:hours-24'


class CurrentElectricityHourOrder(EnergyHourOrder):
    _attr_unique_id = 'sensor.current_spot_electricity_hour_order'
    _attr_translation_key = 'current_spot_electricity_hour_order'

    def update(self, rate_data: Optional[SpotRateData]):
        self._attr = {}
        if rate_data is None:
            self._available = False
            self._value = None
            return

        cheapest_order = rate_data.electricity.current_hour.cheapest_consecutive_order[1]
        if cheapest_order != self._value:
            logger.debug('%s updated from %s to %s', self.unique_id, self._value, cheapest_order)
            self._value = cheapest_order
        else:
            logger.debug('%s unchanged with %d', self.unique_id, cheapest_order)

        for hour in rate_data.electricity.today.hours_by_dt.values():
            self._attr[hour.dt_local.isoformat()] = [hour.cheapest_consecutive_order[1], float(round(hour.price, 3))]

        self._available = True


class TomorrowElectricityHourOrder(EnergyHourOrder):
    _attr_unique_id = 'sensor.tomorrow_spot_electricity_hour_order'
    _attr_translation_key = 'tomorrow_spot_electricity_hour_order'

    def update(self, rate_data: Optional[SpotRateData]):
        self._attr = {}
        self._value = None

        if not rate_data:
            self._available = False
        elif rate_data.electricity.tomorrow is None:
            self._available = False
        else:
            self._available = True

            for hour in rate_data.electricity.tomorrow.hours_by_dt.values():
                self._attr[hour.dt_local.isoformat()] = [hour.cheapest_consecutive_order[1], float(round(hour.price, 3))]


class TemplatePriceSensor(PriceSensor):
    def __init__(self, hass: HomeAssistant, resource: Literal['electricity', 'gas'], settings: SpotRateSettings, coordinator: SpotRateCoordinator, template_code: str) -> None:
        super().__init__(hass, settings, coordinator)
        self._resource = resource
        try:
            self.template = Template(template_code, hass=hass)
        except TemplateError as e:
            logger.error('Template error in %s: %s', self.unique_id, e)
            self.template = None

    def get_current_price(self, rate_data: SpotRateData) -> float:
        if self._resource == 'gas':
            return float(rate_data.gas.today)
        return float(rate_data.electricity.current_hour.price)

    def update(self, rate_data: Optional[SpotRateData]):
        if rate_data is None or not self.template:
            self._available = False
            self._value = None
            self._attr = {}
            return

        try:
            current_price = self.get_current_price(rate_data)
            self._available = True
        except LookupError:
            logger.error(
                'Current time "%s" is not found in SpotRate values:\n%s',
                rate_data.get_now(),
                '\n\t'.join([dt.isoformat() for dt in rate_data.electricity.hour_for_dt.keys()]),
            )
            self._available = False
            return

        current_value = self.template.async_render({
            'value': float(current_price),
        })
        logger.info('%s updated from %s to %s', self.unique_id, self._value, current_value)

        self._value = current_value


class ElectricityPriceBuy(TemplatePriceSensor):
    _attr_unique_id = 'sensor.current_spot_electricity_buy_price'
    _attr_translation_key = 'current_spot_electricity_buy_price'
    _attr_icon = 'mdi:cash-minus'


class ElectricityPriceSell(TemplatePriceSensor):
    _attr_unique_id = 'sensor.current_spot_electricity_sell_price'
    _attr_translation_key = 'current_spot_electricity_sell_price'
    _attr_icon = 'mdi:cash-plus'


class GasPriceBuy(TemplatePriceSensor):
    _attr_unique_id = 'sensor.current_spot_gas_buy_price'
    _attr_translation_key = 'current_spot_gas_buy_price'
    _attr_icon = 'mdi:cash-minus'

#BC
class ConsecutiveCheapestElectricitySensor(BinarySpotRateSensorBase):
    _attr_icon = 'mdi:cash-clock'

    def __init__(self, hours: int, hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator) -> None:
        self.hours = hours

        if self.hours == 1:
            self._attr_unique_id = 'sensor.spot_electricity_is_cheapest'
            self._attr_translation_key = 'current_spot_electricity_is_cheapest'
        else:
            self._attr_unique_id = f'sensor.spot_electricity_is_cheapest_{self.hours}_hours_block'
            self._attr_translation_key = 'current_spot_electricity_is_cheapest_hours_block'
            self._attr_translation_placeholders = {
                'hours': self.hours,
            }

        super().__init__(hass=hass, settings=settings, coordinator=coordinator)

    def _compute_attr(self, rate_data: SpotRateData, start: datetime, end: datetime) -> dict:
        dt = start
        min_price: Optional[Decimal] = None
        max_price: Optional[Decimal] = None
        sum_price: Decimal = Decimal(0)
        count: int = 0

        while dt <= end:
            hour = rate_data.electricity.hour_for_dt(dt)
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

    def update(self, rate_data: Optional[SpotRateData]):
        self._attr = {}
        self._attr_is_on = None

        if not rate_data:
            self._available = False
        else:
            is_on = False

            for hour in rate_data.electricity.hours_by_dt.values():
                start = hour.dt_local - timedelta(hours=self.hours - 1)
                end = hour.dt_local + timedelta(hours=1, seconds=-1)

                # Ignore start times before now, we only want future blocks
                if end < rate_data.electricity.now:
                    continue

                if hour.cheapest_consecutive_order[self.hours] == 1:
                    if not self._attr:
                        # Only put it there once, so to contains closes interval in the future
                        self._attr = self._compute_attr(rate_data, start, end)

                    if start <= rate_data.electricity.now <= end:
                        is_on = True

            self._attr_is_on = is_on
            self._available = True

#BC
class HasTomorrowElectricityData(BinarySpotRateSensorBase):
    _attr_unique_id = 'sensor.spot_electricity_has_tomorrow_data'
    _attr_translation_key = 'tomorrow_spot_electricity_has_data'
    _attr_icon = 'mdi:cash-clock'

    def update(self, rate_data: Optional[SpotRateData]):
        self._attr = {}
        self._attr_is_on = None

        if not rate_data:
            self._available = False
        else:
            self._attr_is_on = rate_data.electricity.tomorrow is not None
            self._available = True



class TodayGasSensor(PriceSensor):
    _attr_unique_id = 'sensor.current_spot_gas_price'
    _attr_translation_key = 'current_spot_gas_price'

    def update(self, rate_data: Optional[SpotRateData]):
        if rate_data is None:
            self._available = False
            self._value = None
            self._attr = {}
            return

        self._value = rate_data.gas.today
        self._available = True


class TomorrowGasSensor(PriceSensor):
    _attr_unique_id = 'sensor.tomorrow_spot_gas_price'
    _attr_translation_key = 'tomorrow_spot_gas_price'

    def update(self, rate_data: Optional[SpotRateData]):
        if rate_data is None:
            self._available = False
            self._value = None
            self._attr = {}
            return

        self._value = rate_data.gas.tomorrow
        self._available = self._value is not None


#BC
class HasTomorrowGasData(BinarySpotRateSensorBase):
    _attr_unique_id = 'sensor.spot_gas_has_tomorrow_data'
    _attr_translation_key = 'tomorrow_spot_gas_has_data'
    _attr_icon = 'mdi:cash-clock'

    def update(self, rate_data: Optional[SpotRateData]):
        self._attr = {}
        self._attr_is_on = None

        if not rate_data:
            self._available = False
        else:
            self._attr_is_on = rate_data.gas.tomorrow is not None
            self._available = True
