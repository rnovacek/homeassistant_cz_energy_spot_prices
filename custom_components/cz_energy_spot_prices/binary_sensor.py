from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import Optional
from decimal import Decimal
from zoneinfo import ZoneInfo

from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant

from . import SpotRateConfigEntry
from .coordinator import SpotRateCoordinator, SpotRateData, CONSECUTIVE_HOURS
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

    has_tomorrow_electricity_data = HasTomorrowElectricityData(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )

    has_tomorrow_gas_data = HasTomorrowGasData(
        hass=hass,
        settings=settings,
        coordinator=coordinator,
    )

    sensors = [
        has_tomorrow_electricity_data,
        has_tomorrow_gas_data
    ]

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

    await coordinator.async_config_entry_first_refresh()


class BinarySpotRateSensorBase(SpotRateSensorMixin, BinarySensorEntity):
    pass


class ConsecutiveCheapestElectricitySensor(BinarySpotRateSensorBase):
    def __init__(self, hours: int, hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator) -> None:
        self.hours = hours

        if self.hours == 1:
            self._attr_translation_key = 'current_spot_electricity_is_cheapest'
        else:
            self._attr_translation_key = 'current_spot_electricity_is_cheapest_hours_block'
            self._attr_translation_placeholders = {
                'hours': self.hours,
            }

        super().__init__(hass=hass, settings=settings, coordinator=coordinator)

    @property
    def icon(self) -> str:
        return 'mdi:cash-clock'

    @property
    def unique_id(self) -> str:
        if self.hours == 1:
            return f'sensor.spot_electricity_is_cheapest'
        else:
            return f'sensor.spot_electricity_is_cheapest_{self.hours}_hours_block'

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


class HasTomorrowElectricityData(BinarySpotRateSensorBase):
    _attr_translation_key = 'tomorrow_spot_electricity_has_data'

    @property
    def icon(self) -> str:
        return 'mdi:cash-clock'

    @property
    def unique_id(self) -> str:
        return f'sensor.spot_electricity_has_tomorrow_data'

    def update(self, rate_data: Optional[SpotRateData]):
        self._attr = {}
        self._attr_is_on = None

        if not rate_data:
            self._available = False
        else:
            self._attr_is_on = rate_data.electricity.tomorrow is not None
            self._available = True



class HasTomorrowGasData(BinarySpotRateSensorBase):
    _attr_translation_key = 'tomorrow_spot_gas_has_data'

    @property
    def icon(self) -> str:
        return 'mdi:cash-clock'

    @property
    def unique_id(self) -> str:
        return f'sensor.spot_gas_has_tomorrow_data'

    def update(self, rate_data: Optional[SpotRateData]):
        self._attr = {}
        self._attr_is_on = None

        if not rate_data:
            self._available = False
        else:
            self._attr_is_on = rate_data.gas.tomorrow is not None
            self._available = True
