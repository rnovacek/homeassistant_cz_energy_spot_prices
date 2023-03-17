from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional
from decimal import Decimal
from zoneinfo import ZoneInfo
from dataclasses import dataclass

from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT, STATE_ON, STATE_OFF
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .spot_rate import SpotRate
from .coordinator import SpotRateCoordinator, SpotRateData, SpotRateHour, CONSECUTIVE_HOURS, CHEAPEST_HOUR_FROM_PERIOD

logger = logging.getLogger(__name__)


@dataclass
class Settings:
    resource: str
    currency: str
    currency_human: str
    unit: str
    timezone: str
    zoneinfo: ZoneInfo


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    currency = entry.data[CONF_CURRENCY]
    unit = entry.data[CONF_UNIT_OF_MEASUREMENT]

    settings = Settings(
        resource='Electricity',
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

    spot_rate = SpotRate()
    coordinator = SpotRateCoordinator(
        hass=hass,
        spot_rate=spot_rate,
        in_eur=settings.currency == 'EUR',
        unit=unit,
    )

    rate_sensor = SpotRateSensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )
    cheapest_today_sensor = CheapestTodaySensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )
    cheapest_tomorrow_sensor = CheapestTomorrowSensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )
    most_expensive_today_sensor = MostExpensiveTodaySensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )
    most_expensive_tomorrow_sensor = MostExpensiveTomorrowSensor(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )
    current_energy_hour_order = CurrentEnergyHourOrder(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )
    tomorrow_energy_hour_order = TomorrowEnergyHourOrder(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )
    has_tomorrow_data = HasTomorrowData(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )
    #energy_price_buy_sensor = EnergyPriceBuy()
    #energy_price_sell_sensor = EnergyPriceSell()

    sensors = [
        rate_sensor,
        cheapest_today_sensor,
        cheapest_tomorrow_sensor,
        most_expensive_today_sensor,
        most_expensive_tomorrow_sensor,
        current_energy_hour_order,
        tomorrow_energy_hour_order,
        has_tomorrow_data,
        #energy_price_buy_sensor,
        #energy_price_sell_sensor,
    ]

    for i in CONSECUTIVE_HOURS:
        sensors.append(
            ConsecutiveCheapestSensor(
                hours=i,
                hass=hass,
                settings=settings,
                coordinator=coordinator,
            )
        )

    for i in CHEAPEST_HOUR_FROM_PERIOD:
        sensors.append(
            CheapestHourFromPeriodSensor(
                period=i,
                hass=hass,
                settings=settings,
                coordinator=coordinator,
            )
        )

    async_add_entities(sensors)

    await coordinator.async_config_entry_first_refresh()


class SpotRateSensorMixin(CoordinatorEntity):
    coordinator: SpotRateCoordinator

    def __init__(self, hass: HomeAssistant, settings: Settings, coordinator: SpotRateCoordinator):
        super().__init__(coordinator)
        self.hass = hass
        self._settings = settings

        self._value = None
        self._attr = None
        self._available = False

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.update(self.coordinator.data)
        self.async_write_ha_state()

    def update(self, rates_by_datetime: SpotRateData):
        raise NotImplementedError()

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self._value

    @property
    def extra_state_attributes(self):
        """Return other attributes of the sensor."""
        return self._attr

    @property
    def available(self):
        """Return True if entity is available."""
        return self._available


class BinarySpotRateSensorBase(SpotRateSensorMixin, BinarySensorEntity):
    pass


class SpotRateSensorBase(SpotRateSensorMixin, SensorEntity):
    pass


class PriceSensor(SpotRateSensorBase, SensorEntity):
    @property
    def icon(self) -> str:
        return 'mdi:cash'

    @property
    def native_unit_of_measurement(self) -> Optional[str]:
        return f'{self._settings.currency_human}/{self._settings.unit}'

    @property
    def device_class(self) -> Optional[str]:
        return 'monetary'


class SpotRateSensor(PriceSensor):
    @property
    def unique_id(self) -> str:
        return f'sensor.current_spot_{self._settings.resource.lower()}_price'

    @property
    def name(self):
        """Return the name of the sensor."""
        return f'Current Spot {self._settings.resource} Price'

    def update(self, rate_data: Optional[SpotRateData]):
        attributes: Dict[str, float] = {}

        if rate_data is None:
            self._available = False
            self._value = None
            self._attr = {}
            return

        try:
            current_hour = rate_data.current_hour
            self._available = True
        except LookupError:
            logger.error(
                'Current time "%s" is not found in SpotRate values:\n%s',
                rate_data.get_now(),
                '\n\t'.join([dt.isoformat() for dt in rate_data.hour_for_dt.keys()]),
            )
            self._available = False
            return

        current_value = current_hour.price

        for hour_data in rate_data.today_day.hours_by_dt.values():
            dt_local = hour_data.dt_local.isoformat()
            attributes[dt_local] = float(hour_data.price)

        if rate_data.tomorrow_day:
            for hour_data in rate_data.tomorrow_day.hours_by_dt.values():
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
            logger.info('No value found for %s', self.name)
            return

        self._available = True
        if self._value is None:
            logger.debug('%s initialized with %.2f at %s', self.unique_id, hour.price, hour.dt_utc.isoformat())
        elif hour.price != self._value:
            logger.debug('%s updated from %.2f to %.2f at %s', self.unique_id, self._value, hour.price, hour.dt_utc.isoformat())
        else:
            logger.debug('%s unchanged with %.2f at %s', self.unique_id, hour.price, hour.dt_utc.isoformat())

        self._value = hour.price
        self._attr = {
            'at': hour.dt_local.isoformat(),
            'hour': hour.dt_local.hour,
        }


class CheapestTodaySensor(HourFindSensor):
    @property
    def unique_id(self) -> str:
        return f'sensor.current_spot_{self._settings.resource.lower()}_cheapest_today'

    @property
    def name(self):
        """Return the name of the sensor."""
        return f'Spot Cheapest {self._settings.resource} Today'

    def find_hour(self, rate_data: Optional[SpotRateData]) -> Optional[SpotRateHour]:
        if not rate_data:
            return None

        return rate_data.today_day.cheapest_hour()


class CheapestTomorrowSensor(HourFindSensor):
    @property
    def unique_id(self) -> str:
        return f'sensor.current_spot_{self._settings.resource.lower()}_cheapest_tomorrow'

    @property
    def name(self):
        """Return the name of the sensor."""
        return f'Spot Cheapest {self._settings.resource} Tomorrow'

    def find_hour(self, rate_data: Optional[SpotRateData]) -> Optional[SpotRateHour]:
        if not rate_data:
            return None

        if not rate_data.tomorrow:
            return None

        return rate_data.tomorrow.cheapest_hour()


class MostExpensiveTodaySensor(HourFindSensor):
    @property
    def unique_id(self) -> str:
        return f'sensor.current_spot_{self._settings.resource.lower()}_most_expensive_today'

    @property
    def name(self):
        """Return the name of the sensor."""
        return f'Spot Most Expensive {self._settings.resource} Today'

    def find_hour(self, rate_data: Optional[SpotRateData]) -> Optional[SpotRateHour]:
        if not rate_data:
            return None

        return rate_data.today.most_expensive_hour()


class MostExpensiveTomorrowSensor(HourFindSensor):
    @property
    def unique_id(self) -> str:
        return f'sensor.current_spot_{self._settings.resource.lower()}_most_expensive_tomorrow'

    @property
    def name(self):
        """Return the name of the sensor."""
        return f'Spot Most Expensive {self._settings.resource} Tomorrow'

    def find_hour(self, rate_data: Optional[SpotRateData]) -> Optional[SpotRateHour]:
        if not rate_data:
            return None

        if not rate_data.tomorrow:
            return None

        return rate_data.tomorrow.most_expensive_hour()


class EnergyHourOrder(SpotRateSensorBase):
    @property
    def icon(self) -> str:
        return 'mdi:hours-24'

    @property
    def native_unit_of_measurement(self) -> Optional[str]:
        return None

    @property
    def device_class(self) -> Optional[str]:
        return None


class CurrentEnergyHourOrder(EnergyHourOrder):
    @property
    def unique_id(self) -> str:
        return f'sensor.current_spot_{self._settings.resource.lower()}_hour_order'

    @property
    def name(self):
        """Return the name of the sensor."""
        return f'Current Spot {self._settings.resource} Hour Order'

    def update(self, rate_data: Optional[SpotRateData]):
        self._attr = {}
        if rate_data is None:
            self._available = False
            self._value = None
            return

        cheapest_order = rate_data.current_hour.cheapest_consecutive_order[1]
        if cheapest_order != self._value:
            logger.debug('%s updated from %s to %s', self.unique_id, self._value, cheapest_order)
            self._value = cheapest_order
        else:
            logger.debug('%s unchanged with %d', self.unique_id, cheapest_order)

        for hour in rate_data.today.hours_by_dt.values():
            self._attr[hour.dt_local.isoformat()] = [hour.cheapest_consecutive_order[1], float(round(hour.price, 3))]

        self._available = True


class TomorrowEnergyHourOrder(EnergyHourOrder):
    @property
    def unique_id(self) -> str:
        return f'sensor.tomorrow_spot_{self._settings.resource.lower()}_hour_order'

    @property
    def name(self):
        """Return the name of the sensor."""
        return f'Tomorrow Spot {self._settings.resource} Hour Order'

    def update(self, rate_data: Optional[SpotRateData]):
        self._attr = {}
        self._value = None

        if not rate_data:
            self._available = False
        elif rate_data.tomorrow is None:
            self._available = False
        else:
            self._available = True

            for hour in rate_data.tomorrow.hours_by_dt.values():
                self._attr[hour.dt_local.isoformat()] = [hour.cheapest_consecutive_order[1], float(round(hour.price, 3))]


class EnergyPriceBuy(SensorEntity):
    def __init__(self) -> None:
        super().__init__()


class EnergyPriceSell(SensorEntity):
    def __init__(self) -> None:
        super().__init__()


class ConsecutiveCheapestSensor(BinarySpotRateSensorBase):
    def __init__(self, hours: int, hass: HomeAssistant, settings: Settings, coordinator: SpotRateCoordinator) -> None:
        self.hours = hours
        super().__init__(hass=hass, settings=settings, coordinator=coordinator)

    @property
    def icon(self) -> str:
        return 'mdi:cash-clock'

    @property
    def unique_id(self) -> str:
        if self.hours == 1:
            return f'sensor.spot_{self._settings.resource.lower()}_is_cheapest'
        else:
            return f'sensor.spot_{self._settings.resource.lower()}_is_cheapest_{self.hours}_hours_block'

    @property
    def name(self):
        """Return the name of the sensor."""
        if self.hours == 1:
            return f'Spot {self._settings.resource} Is Cheapest'
        else:
            return f'Spot {self._settings.resource} Is Cheapest {self.hours} Hours Block'

    def _compute_attr(self, rate_data: SpotRateData, start: datetime, end: datetime) -> dict:
        dt = start
        min_price: Optional[Decimal] = None
        max_price: Optional[Decimal] = None
        sum_price: Decimal = Decimal(0)
        count: int = 0

        while dt <= end:
            hour = rate_data.hour_for_dt(dt)
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

            for hour in rate_data.hours_by_dt.values():
                start = hour.dt_local - timedelta(hours=self.hours - 1)
                end = hour.dt_local + timedelta(hours=1, seconds=-1)

                # Ignore start times before now, we only want future blocks
                if end < rate_data.now:
                    continue

                if hour.cheapest_consecutive_order[self.hours] == 1:
                    if not self._attr:
                        # Only put it there once, so to contains closes interval in the future
                        self._attr = self._compute_attr(rate_data, start, end)

                    if start <= rate_data.now <= end:
                        is_on = True

            self._attr_is_on = is_on
            self._available = True


class CheapestHourFromPeriodSensor(BinarySpotRateSensorBase):
    def __init__(self, period: int, hass: HomeAssistant, settings: Settings, coordinator: SpotRateCoordinator) -> None:
        self.period = period
        super().__init__(hass=hass, settings=settings, coordinator=coordinator)

    @property
    def icon(self) -> str:
        return 'mdi:cash-clock'

    @property
    def unique_id(self) -> str:
        return f'sensor.spot_{self._settings.resource.lower()}_is_cheapest_hour_in_{self.period}_hours_block'

    @property
    def name(self):
        """Return the name of the sensor."""
        return f'Spot {self._settings.resource} Is Cheapest Hour In {self.hours} Hours Block'

    def _compute_attr(self, rate_data: SpotRateData, start: datetime, end: datetime) -> dict:
        half = (self.period - 1) / 2
        my_price = rate_data.hour_for_dt(start + timedelta(hours=half)).price
        if my_price is None:
            return {False}

        dt = start
        while dt <= end:
            range_price = rate_data.hour_for_dt(dt).price
            if range_price < my_price:
                return {False}

            dt += timedelta(hours=1)
        return {True}

    def update(self, rate_data: Optional[SpotRateData]):
        self._attr = {}
        self._attr_is_on = None

        if not rate_data:
            self._available = False
        else:
            is_on = False

            for hour in rate_data.hours_by_dt.values():
                half = (self.period - 1) / 2
                start = hour.dt_local - timedelta(hours=half)
                end = hour.dt_local + timedelta(hours=half, seconds=-1)

                # Ignore start times before now, we only want future blocks
                if end < rate_data.now:
                    continue

                if not self._attr:
                    self._attr = self._compute_attr(rate_data, start, end)

                if start <= rate_data.now <= end:
                    is_on = True

            self._attr_is_on = is_on
            self._available = True

class HasTomorrowData(BinarySpotRateSensorBase):
    @property
    def icon(self) -> str:
        return 'mdi:cash-clock'

    @property
    def unique_id(self) -> str:
        return f'sensor.spot_{self._settings.resource.lower()}_has_tomorrow_data'

    @property
    def name(self):
        """Return the name of the sensor."""
        return f'Spot {self._settings.resource} Has Tomorrow Data'

    def update(self, rate_data: Optional[SpotRateData]):
        self._attr = {}
        self._attr_is_on = None

        if not rate_data:
            self._available = False
        else:
            self._attr_is_on = rate_data.tomorrow is not None
            self._available = True
