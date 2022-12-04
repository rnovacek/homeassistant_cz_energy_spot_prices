from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, cast, Tuple, Optional, List
from zoneinfo import ZoneInfo
from decimal import Decimal
from dataclasses import dataclass

from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, Event, State, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .spot_rate import SpotRate
from .coordinator import SpotRateCoordinator

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
    print('ENTRY DATA', entry.state)
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
    energy_hour_order = EnergyHourOrder(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )
    #energy_price_buy_sensor = EnergyPriceBuy()
    #energy_price_sell_sensor = EnergyPriceSell()
    #consecutive_cheapest_sensor = ConsecutiveCheapestSensor()

    async_add_entities([
        rate_sensor,
        cheapest_today_sensor,
        cheapest_tomorrow_sensor,
        most_expensive_today_sensor,
        most_expensive_tomorrow_sensor,
        energy_hour_order,
        #energy_price_buy_sensor,
        #energy_price_sell_sensor,
        #consecutive_cheapest_sensor,
    ])

    await coordinator.async_config_entry_first_refresh()


class SpotRateSensorBase(CoordinatorEntity, SensorEntity):
    coordinator: SpotRateCoordinator

    def __init__(self, hass: HomeAssistant, settings: Settings, coordinator: SpotRateCoordinator):
        super().__init__(coordinator)
        self._hass = hass
        self._settings = settings

        self._value = None
        self._attr = None
        self._available = False

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.update(self.coordinator.data)
        self.async_write_ha_state()

    def update(self, rates_by_datetime: SpotRate.RateByDatetime):
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


class PriceSensor(SpotRateSensorBase):
    @property
    def icon(self) -> str:
        return 'mdi:cash'

    @property
    def native_unit_of_measurement(self) -> str | None:
        return f'{self._settings.currency_human}/{self._settings.unit}'

    @property
    def device_class(self) -> str | None:
        return 'monetary'


class SpotRateSensor(PriceSensor):
    @property
    def unique_id(self) -> str:
        return f'sensor.current_spot_{self._settings.resource.lower()}_price'

    @property
    def name(self):
        """Return the name of the sensor."""
        return f'Current Spot {self._settings.resource} Price'

    def update(self, rates_by_datetime: SpotRate.RateByDatetime):
        now = datetime.now(timezone.utc)
        now_hour = now.replace(minute=0, second=0, microsecond=0)

        attributes: Dict[str, float] = {}
        current_value = None

        for dt_utc, rate in rates_by_datetime.items():
            dt_local = dt_utc.astimezone(self._settings.zoneinfo).isoformat()
            attributes[dt_local] = float(rate)

            if dt_utc == now_hour:
                current_value = rate

        if current_value is None:
            logger.error(
                'Current time "%s" is not found in SpotRate values:\n%s',
                now_hour,
                '\n\t'.join([dt.isoformat() for dt in rates_by_datetime.keys()]),
            )
            self._available = False
        else:
            self._available = True

        self._attr = attributes
        self._value = current_value


class HourFindSensor(PriceSensor):
    def filter(self, dt: datetime, value: Decimal) -> bool:
        raise NotImplementedError()

    def compare(self, value1: Decimal, value2: Decimal) -> bool:
        raise NotImplementedError()

    def find_rate(
        self,
        rates: SpotRate.RateByDatetime,
        zoneinfo: ZoneInfo,
    ) -> Tuple[Optional[datetime], Optional[Decimal]]:
        found_dt = None
        found_value = None

        for dt_utc, rate in rates.items():
            dt_local = dt_utc.astimezone(zoneinfo)

            if not self.filter(dt_local, rate):
                continue

            if found_value is None or self.compare(rate, found_value):
                found_value = rate
                found_dt = dt_local

        return found_dt, found_value

    def update(self, rates_by_datetime: SpotRate.RateByDatetime):
        self.now = datetime.now(self._settings.zoneinfo)
        self.today = self.now.date()
        self.tomorrow = self.today + timedelta(days=1)

        found_dt, found_value = self.find_rate(
            rates_by_datetime,
            zoneinfo=self._settings.zoneinfo,
        )

        if found_dt is None or found_value is None:
            logger.info('No value found for %s', self.name)
            return

        self._available = True
        if self._value is None:
            logger.debug('%s initialized with %.2f at %s', self.unique_id, found_value, found_dt.isoformat())
        elif found_value != self._value:
            logger.debug('%s updated from %.2f to %.2f at %s', self.unique_id, self._value, found_value, found_dt.isoformat())
        else:
            logger.debug('%s unchanged with %.2f at %s', self.unique_id, found_value, found_dt.isoformat())

        self._value = found_value
        self._attr = {
            'at': found_dt.isoformat(),
            'hour': found_dt.hour,
        }


class CheapestTodaySensor(HourFindSensor):
    @property
    def unique_id(self) -> str:
        return f'sensor.current_spot_{self._settings.resource.lower()}_cheapest_today'

    @property
    def name(self):
        """Return the name of the sensor."""
        return f'Spot Cheapest {self._settings.resource} Today'

    def filter(self, dt: datetime, value: float) -> bool:
        return dt.date() == self.today

    def compare(self, value1: float, value2: float) -> bool:
        return value1 < value2


class CheapestTomorrowSensor(HourFindSensor):
    @property
    def unique_id(self) -> str:
        return f'sensor.current_spot_{self._settings.resource.lower()}_cheapest_tomorrow'

    @property
    def name(self):
        """Return the name of the sensor."""
        return f'Spot Cheapest {self._settings.resource} Tomorrow'

    def filter(self, dt: datetime, value: float) -> bool:
        return dt.date() == self.tomorrow

    def compare(self, value1: float, value2: float) -> bool:
        return value1 < value2


class MostExpensiveTodaySensor(HourFindSensor):
    @property
    def unique_id(self) -> str:
        return f'sensor.current_spot_{self._settings.resource.lower()}_most_expensive_today'

    @property
    def name(self):
        """Return the name of the sensor."""
        return f'Spot Most Expensive {self._settings.resource} Today'

    def filter(self, dt: datetime, value: float) -> bool:
        return dt.date() == self.today

    def compare(self, value1: float, value2: float) -> bool:
        return value1 > value2


class MostExpensiveTomorrowSensor(HourFindSensor):
    @property
    def unique_id(self) -> str:
        return f'sensor.current_spot_{self._settings.resource.lower()}_most_expensive_tomorrow'

    @property
    def name(self):
        """Return the name of the sensor."""
        return f'Spot Most Expensive {self._settings.resource} Tomorrow'

    def filter(self, dt: datetime, value: float) -> bool:
        return dt.date() == self.tomorrow

    def compare(self, value1: float, value2: float) -> bool:
        return value1 > value2


class EnergyHourOrder(SpotRateSensorBase):
    @property
    def unique_id(self) -> str:
        return f'sensor.current_spot_{self._settings.resource.lower()}_hour_order'

    @property
    def name(self):
        """Return the name of the sensor."""
        return f'Current Spot {self._settings.resource} Hour Order'

    @property
    def icon(self) -> str:
        return 'mdi:hours-24'

    @property
    def native_unit_of_measurement(self) -> str | None:
        return None

    @property
    def device_class(self) -> str | None:
        return None

    def update(self, rates_by_datetime: SpotRate.RateByDatetime):
        now = datetime.now(self._settings.zoneinfo)
        now_hour = now.replace(minute=0, second=0, microsecond=0)
        today = now.date()

        rates: List[dict] = []
        for dt_utc, rate in rates_by_datetime.items():
            dt_local = dt_utc.astimezone(self._settings.zoneinfo)

            if dt_local.date() != today:
                # Ignore tomorrow (at least for now)
                continue

            rates.append({
                'dt_local': dt_local,
                'rate': rate,
            })

        attributes = {}
        current_order = None

        sorted_prices = sorted(rates, key=lambda item: item['rate'])
        for order, d in enumerate(sorted_prices, 1):
            d['order'] = order

        for d in rates:
            attributes[d['dt_local'].isoformat()] = d['order']

            if d['dt_local'] == now_hour:
                current_order = d['order']

        if current_order is None:
            logger.info('No value found for %s', self.name)
            return

        self._available = True
        logger.debug('%s updated to %d', self.unique_id, current_order)
        self._value = current_order
        self._attr = attributes


class EnergyPriceBuy(SensorEntity):
    def __init__(self) -> None:
        super().__init__()


class EnergyPriceSell(SensorEntity):
    def __init__(self) -> None:
        super().__init__()


class ConsecutiveCheapestSensor(SensorEntity):
    def __init__(self) -> None:
        super().__init__()
