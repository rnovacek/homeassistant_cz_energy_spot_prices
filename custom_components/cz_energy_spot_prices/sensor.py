from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict

from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT, CONF_RESOURCE
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .cnb_rate import CnbRate
from .spot_rate import SpotRate

logger = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    sensor = SpotRateSensor(
        resource=entry.data[CONF_RESOURCE],
        currency=entry.data[CONF_CURRENCY],
        unit=entry.data[CONF_UNIT_OF_MEASUREMENT],
    )
    async_add_entities([sensor])


class SpotRateSensor(SensorEntity):
    def __init__(self, resource: str, currency: str, unit: str):
        super().__init__()
        self._resource = resource
        self._currency = currency
        self._currency_human = {
            'EUR': '€',
            'CZK': 'Kč',
            'USD': '$',
        }.get(currency) or '?'
        self._unit = unit

        self._value = None
        self._attr = None
        self._available = False

        self._spot_rate = SpotRate()
        self._cnb_rate = CnbRate()

    @property
    def unique_id(self) -> str:
        return f'sensor.current_spot_{self._resource.lower()}_price'

    @property
    def icon(self) -> str:
        return 'mdi:cash'

    @property
    def name(self):
        """Return the name of the sensor."""
        return f'Current Spot {self._resource} Price'

    @property
    def native_unit_of_measurement(self) -> str | None:
        return f'{self._currency_human}/{self._unit}'

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self._value

    @property
    def extra_state_attributes(self):
        """Return other attributes of the sensor."""
        return self._attr

    @property
    def device_class(self) -> str | None:
        return 'monetary'

    @property
    def available(self):
        """Return True if entity is available."""
        return self._available

    def _energy_unit_scale(self, unit: str) -> int:
        if unit == 'Wh':
            return 1
        elif unit == 'kWh':
            return 1_000
        elif unit == 'MWh':
            return 1_000_000
        elif unit == 'GWh':
            return 1_000_000_000

        raise ValueError(f'Unknown energy unit {unit}')


    def _convert(self, value: float, from_currency: str, to_currency: str, currency_rates: Dict[str, float]) -> float:
        try:
            from_rate = currency_rates[from_currency]
        except KeyError:
            raise ValueError(f'Invalid currency {from_currency}')
        try:
            to_rate = currency_rates[to_currency]
        except KeyError:
            raise ValueError(f'Invalid currency {to_currency}')

        if from_rate == to_rate:
            value_in_currency = value
        else:
            value_in_currency = value / to_rate * from_rate

        if self._spot_rate.UNIT == self._unit:
            return value_in_currency

        value_in_base = value_in_currency / self._energy_unit_scale(self._spot_rate.UNIT)
        return value_in_base * self._energy_unit_scale(self._unit)

    def _convert_rates(self, energy_rates: Dict[str, float], currency_rates: Dict[str, float]) -> Dict[str, float]:
        converted_rates: Dict[str, float] = {}
        for dt, rate in energy_rates.items():
            converted_rates[dt] = self._convert(
                rate,
                from_currency=self._spot_rate.CURRENCY,
                to_currency=self._currency,
                currency_rates=currency_rates,
            )
        return converted_rates

    async def async_update(self):
        now = datetime.now(timezone.utc)
        try:
            energy_rates, currency_rates = await asyncio.gather(
                self._spot_rate.get_two_days_rates(now),
                self._cnb_rate.get_current_rates(),
            )

            self._attr = self._convert_rates(energy_rates, currency_rates)
            now_iso = now.replace(minute=0, second=0, microsecond=0).isoformat()
            try:
                self._value = self._attr[now_iso]
            except KeyError:
                logger.error(
                    'Current time "%s" is not found in SpotRate values:\n%s',
                    now_iso,
                    '\n\t'.join(self._attr.keys()),
                )
                self._available = False
            else:
                self._available = True
            logger.info('CZ Energy Spot Prices Updated')
        except Exception:
            logger.exception('Unable to fetch rates')
            self._available = False

if __name__ == '__main__':
    import asyncio
    sensor = SpotRateSensor(resource='Electricity', currency='EUR', unit='MWh')
    asyncio.run(sensor.async_update())
    print(sensor._value)

    sensor = SpotRateSensor(resource='Electricity', currency='EUR', unit='kWh')
    asyncio.run(sensor.async_update())
    print(sensor._value)

    sensor = SpotRateSensor(resource='Electricity', currency='CZK', unit='kWh')
    asyncio.run(sensor.async_update())
    print(sensor._value)

