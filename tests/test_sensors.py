from zoneinfo import ZoneInfo
from datetime import timezone, datetime
from typing import Coroutine, Literal
from pathlib import Path
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest
from unittest.mock import AsyncMock, MagicMock

from custom_components.cz_energy_spot_prices.sensor import (
    SpotRateElectricitySensor, CurrentElectricityHourOrder, SpotRateCoordinator,
    ConsecutiveCheapestElectricitySensor, TodayGasSensor, TomorrowGasSensor,
)
from custom_components.cz_energy_spot_prices.spot_rate import SpotRate
from custom_components.cz_energy_spot_prices.spot_rate_settings import SpotRateSettings
from custom_components.cz_energy_spot_prices import coordinator
from homeassistant.core import HomeAssistant


def on_ote_cr_post(*args, **kwargs):
    in_xml = ET.fromstring(kwargs['data'])
    body = in_xml.find('{http://schemas.xmlsoap.org/soap/envelope/}Body')
    assert body  is not None and body[0]
    fn_tag = body[0]
    in_eur = True

    if 'GetDamPriceE' in fn_tag.tag:
        in_eur_tag = fn_tag.find('{http://www.ote-cr.cz/schema/service/public}InEur')
        assert in_eur_tag is not None
        if in_eur_tag.text == 'false':
            in_eur = False
        elif in_eur_tag.text == 'true':
            in_eur = True
        else:
            raise ValueError(f'Unexpected value of InEur: {in_eur_tag.text}')
        resource = 'electricity'
    elif 'GetImPriceG' in fn_tag.tag:
        resource = 'gas'
    else:
        raise ValueError(f'Unexpected function: {fn_tag}')

    session_mock = MagicMock(name='session_mock')
    currency = "EUR" if in_eur else "CZK"
    with open(Path(__file__).parent / 'fixtures' / f'ote-{resource}-2022-12-03_{currency}.xml') as f:
        text = f.read()
        session_mock.__aenter__.return_value.status = 200
        session_mock.__aenter__.return_value.text = AsyncMock(name='text', return_value=text)
    return session_mock

def on_cnb_get(*args, **kwargs):
    assert args[1] == 'https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/denni_kurz.txt'
    date = kwargs['params']['date']
    assert date == '02.04.2023'

    session_mock = MagicMock(name='session_mock')
    with open(Path(__file__).parent / 'fixtures' / f'cnb-2022-12-03.xml') as f:
        text = f.read()
        session_mock.__aenter__.return_value.status = 200
        session_mock.__aenter__.return_value.text = AsyncMock(name='text', return_value=text)
    return session_mock


class TestSensorBase:
    def _setup(self, hass: HomeAssistant, currency: str, unit: SpotRate.EnergyUnit, resource: Literal['electricity', 'gas']):
        self.resource = resource
        self.timezone = 'Europe/Prague'
        self.hass = hass
        self.settings = SpotRateSettings(
            currency=currency,
            unit=unit,
            currency_human={
                'EUR': '€',
                'CZK': 'Kč',
                'USD': '$',
            }.get(currency) or '?',
            timezone=self.timezone,
            zoneinfo=ZoneInfo(self.timezone),
        )

        self.spot_rate = SpotRate()
        self.coordinator = SpotRateCoordinator(
            hass=self.hass,
            spot_rate=self.spot_rate,
            in_eur=self.settings.currency == 'EUR',
            unit=unit,
        )

    async def _refresh(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr('aiohttp.ClientSession.post', on_ote_cr_post)
        monkeypatch.setattr('aiohttp.ClientSession.get', on_cnb_get)
        await self.coordinator.async_refresh()

    def _convert_unit(self, value: Decimal, unit: SpotRate.EnergyUnit) -> Decimal:
        return value * (Decimal('0.001') if unit == 'kWh' else Decimal(1))


@pytest.mark.parametrize('currency,unit', (
    ('CZK', 'kWh'),
    ('EUR', 'kWh'),
    ('CZK', 'MWh'),
    ('EUR', 'MWh'),
))
@pytest.mark.asyncio
class TestElectricitySensors(TestSensorBase):
    TIME_ATTRIBUTES = {
        '2022-12-03T00:00:00+01:00': {
            'CZK': Decimal('6362.85'),
            'EUR': Decimal('261.04'),
            'order': 8,
        },
        '2022-12-03T01:00:00+01:00': {
            'CZK': Decimal('5967.49'),
            'EUR': Decimal('244.82'),
            'order': 5,
        },
        '2022-12-03T02:00:00+01:00': {
            'CZK': Decimal('5579.19'),
            'EUR': Decimal('228.89'),
            'order': 2,
        },
        '2022-12-03T03:00:00+01:00': {
            'CZK': Decimal('5417.59'),
            'EUR': Decimal('222.26'),
            'order': 1,
        },
        '2022-12-03T04:00:00+01:00': {
            'CZK': Decimal('5624.04'),
            'EUR': Decimal('230.73'),
            'order': 3,
        },
        '2022-12-03T05:00:00+01:00': {
            'CZK': Decimal('6139.09'),
            'EUR': Decimal('251.86'),
            'order': 6,
        },
        '2022-12-03T06:00:00+01:00': {
            'CZK': Decimal('6311.18'),
            'EUR': Decimal('258.92'),
            'order': 7,
        },
        '2022-12-03T07:00:00+01:00': {
            'CZK': Decimal('7036.09'),
            'EUR': Decimal('288.66'),
            'order': 10,
        },
        '2022-12-03T08:00:00+01:00': {
            'CZK': Decimal('7509.69'),
            'EUR': Decimal('308.09'),
            'order': 13,
        },
        '2022-12-03T09:00:00+01:00': {
            'CZK': Decimal('8110.29'),
            'EUR': Decimal('332.73'),
            'order': 22,
        },
        '2022-12-03T10:00:00+01:00': {
            'CZK': Decimal('7952.83'),
            'EUR': Decimal('326.27'),
            'order': 19,
        },
        '2022-12-03T11:00:00+01:00': {
            'CZK': Decimal('7965.26'),
            'EUR': Decimal('326.78'),
            'order': 20,
        },
        '2022-12-03T12:00:00+01:00': {
            'CZK': Decimal('7779.52'),
            'EUR': Decimal('319.16'),
            'order': 18,
        },
        '2022-12-03T13:00:00+01:00': {
            'CZK': Decimal('7634.98'),
            'EUR': Decimal('313.23'),
            'order': 15,
        },
        '2022-12-03T14:00:00+01:00': {
            'CZK': Decimal('7700.31'),
            'EUR': Decimal('315.91'),
            'order': 16,
        },
        '2022-12-03T15:00:00+01:00': {
            'CZK': Decimal('7700.31'),
            'EUR': Decimal('315.91'),
            'order': 17,
        },
        '2022-12-03T16:00:00+01:00': {
            'CZK': Decimal('8057.64'),
            'EUR': Decimal('330.57'),
            'order': 21,
        },
        '2022-12-03T17:00:00+01:00': {
            'CZK': Decimal('8744.04'),
            'EUR': Decimal('358.73'),
            'order': 24,
        },
        '2022-12-03T18:00:00+01:00': {
            'CZK': Decimal('8353.56'),
            'EUR': Decimal('342.71'),
            'order': 23,
        },
        '2022-12-03T19:00:00+01:00': {
            'CZK': Decimal('7556.25'),
            'EUR': Decimal('310.00'),
            'order': 14,
        },
        '2022-12-03T20:00:00+01:00': {
            'CZK': Decimal('7105.07'),
            'EUR': Decimal('291.49'),
            'order': 12,
        },
        '2022-12-03T21:00:00+01:00': {
            'CZK': Decimal('7066.31'),
            'EUR': Decimal('289.90'),
            'order': 11,
        },
        '2022-12-03T22:00:00+01:00': {
            'CZK': Decimal('6815.49'),
            'EUR': Decimal('279.61'),
            'order': 9,
        },
        '2022-12-03T23:00:00+01:00': {
            'CZK': Decimal('5958.96'),
            'EUR': Decimal('244.47'),
            'order': 4,
        },
        '2022-12-04T00:00:00+01:00': {
            'CZK': Decimal('5794.43'),
            'EUR': Decimal('237.72'),
            'order': 0,
        },
        '2022-12-04T01:00:00+01:00': {
            'CZK': Decimal('5535.56'),
            'EUR': Decimal('227.10'),
            'order': 0,
        },
        '2022-12-04T02:00:00+01:00': {
            'CZK': Decimal('5746.65'),
            'EUR': Decimal('235.76'),
            'order': 0,
        },
        '2022-12-04T03:00:00+01:00': {
            'CZK': Decimal('5203.82'),
            'EUR': Decimal('213.49'),
            'order': 0,
        },
        '2022-12-04T04:00:00+01:00': {
            'CZK': Decimal('5154.34'),
            'EUR': Decimal('211.46'),
            'order': 0,
        },
        '2022-12-04T05:00:00+01:00': {
            'CZK': Decimal('5239.16'),
            'EUR': Decimal('214.94'),
            'order': 0,
        },
        '2022-12-04T06:00:00+01:00': {
            'CZK': Decimal('5211.13'),
            'EUR': Decimal('213.79'),
            'order': 0,
        },
        '2022-12-04T07:00:00+01:00': {
            'CZK': Decimal('5381.02'),
            'EUR': Decimal('220.76'),
            'order': 0,
        },
        '2022-12-04T08:00:00+01:00': {
            'CZK': Decimal('6302.89'),
            'EUR': Decimal('258.58'),
            'order': 0,
        },
        '2022-12-04T09:00:00+01:00': {
            'CZK': Decimal('7045.11'),
            'EUR': Decimal('289.03'),
            'order': 0,
        },
        '2022-12-04T10:00:00+01:00': {
            'CZK': Decimal('7346.38'),
            'EUR': Decimal('301.39'),
            'order': 0,
        },
        '2022-12-04T11:00:00+01:00': {
            'CZK': Decimal('7499.46'),
            'EUR': Decimal('307.67'),
            'order': 0,
        },
        '2022-12-04T12:00:00+01:00': {
            'CZK': Decimal('7462.89'),
            'EUR': Decimal('306.17'),
            'order': 0,
        },
        '2022-12-04T13:00:00+01:00': {
            'CZK': Decimal('7360.76'),
            'EUR': Decimal('301.98'),
            'order': 0,
        },
        '2022-12-04T14:00:00+01:00': {
            'CZK': Decimal('7460.94'),
            'EUR': Decimal('306.09'),
            'order': 0,
        },
        '2022-12-04T15:00:00+01:00': {
            'CZK': Decimal('7513.35'),
            'EUR': Decimal('308.24'),
            'order': 0,
        },
        '2022-12-04T16:00:00+01:00': {
            'CZK': Decimal('7920.66'),
            'EUR': Decimal('324.95'),
            'order': 0,
        },
        '2022-12-04T17:00:00+01:00': {
            'CZK': Decimal('8523.45'),
            'EUR': Decimal('349.68'),
            'order': 0,
        },
        '2022-12-04T18:00:00+01:00': {
            'CZK': Decimal('7655.94'),
            'EUR': Decimal('314.09'),
            'order': 0,
        },
        '2022-12-04T19:00:00+01:00': {
            'CZK': Decimal('7570.39'),
            'EUR': Decimal('310.58'),
            'order': 0,
        },
        '2022-12-04T20:00:00+01:00': {
            'CZK': Decimal('6970.27'),
            'EUR': Decimal('285.96'),
            'order': 0,
        },
        '2022-12-04T21:00:00+01:00': {
            'CZK': Decimal('6872.53'),
            'EUR': Decimal('281.95'),
            'order': 0,
        },
        '2022-12-04T22:00:00+01:00': {
            'CZK': Decimal('6411.11'),
            'EUR': Decimal('263.02'),
            'order': 0,
        },
        '2022-12-04T23:00:00+01:00': {
            'CZK': Decimal('5916.79'),
            'EUR': Decimal('242.74'),
            'order': 0,
        },
    }

    async def test_spot_rate(self, hass: Coroutine[None, None, HomeAssistant], monkeypatch: pytest.MonkeyPatch, currency, unit):
        self._setup(await hass, currency, unit, 'electricity')

        rate_sensor = SpotRateElectricitySensor(
            hass=self.hass,
            settings=self.settings,
            coordinator=self.coordinator,
        )
        rate_sensor.entity_id = rate_sensor.unique_id
        await rate_sensor.async_added_to_hass()
        assert rate_sensor.available is False
        assert rate_sensor.state is None
        assert rate_sensor.extra_state_attributes is None

        # Midnight == 1st hour of the day
        now = datetime(2022, 12, 3, 0, tzinfo=ZoneInfo('Europe/Prague'))
        monkeypatch.setattr(coordinator, 'get_now', lambda zoneinfo = timezone.utc: now.astimezone(zoneinfo))
        await self._refresh(monkeypatch)

        assert rate_sensor.available is True
        assert rate_sensor.state == self._convert_unit(Decimal('6362.85') if currency == 'CZK' else Decimal('261.04'), unit)
        assert rate_sensor.extra_state_attributes == {k: float(self._convert_unit(v[currency], unit)) for k, v in self.TIME_ATTRIBUTES.items()}

        # 1am == 2nd hour of the day
        now = datetime(2022, 12, 3, 1, tzinfo=ZoneInfo('Europe/Prague'))
        monkeypatch.setattr(coordinator, 'get_now', lambda zoneinfo = timezone.utc: now.astimezone(zoneinfo))
        await self._refresh(monkeypatch)

        assert rate_sensor.available is True
        assert rate_sensor.state == self._convert_unit(Decimal('5967.49') if currency == 'CZK' else Decimal('244.82'), unit)
        assert rate_sensor.extra_state_attributes == {k: float(self._convert_unit(v[currency], unit)) for k, v in self.TIME_ATTRIBUTES.items()}

    async def test_hour_order(self, hass: Coroutine[None, None, HomeAssistant], monkeypatch: pytest.MonkeyPatch, currency, unit):
        self._setup(await hass, currency, unit, 'electricity')

        hour_order = CurrentElectricityHourOrder(
            hass=self.hass,
            settings=self.settings,
            coordinator=self.coordinator,
        )
        hour_order.entity_id = hour_order.unique_id
        await hour_order.async_added_to_hass()
        assert hour_order.available is False
        assert hour_order.state is None
        assert hour_order.extra_state_attributes is None

        # Midnight == 1st hour of the day
        now = datetime(2022, 12, 3, 0, tzinfo=ZoneInfo('Europe/Prague'))
        monkeypatch.setattr(coordinator, 'get_now', lambda zoneinfo = timezone.utc: now.astimezone(zoneinfo))
        await self._refresh(monkeypatch)

        assert hour_order.available is True
        assert hour_order.state == 8
        assert hour_order.extra_state_attributes == {
            k: [v['order'], round(float(self._convert_unit(v[currency], unit)), 3)]
            for k, v in self.TIME_ATTRIBUTES.items()
            if v['order'] > 0
        }

        # 1am == 2nd hour of the day
        now = datetime(2022, 12, 3, 1, tzinfo=ZoneInfo('Europe/Prague'))
        monkeypatch.setattr(coordinator, 'get_now', lambda zoneinfo = timezone.utc: now.astimezone(zoneinfo))
        await self._refresh(monkeypatch)

        assert hour_order.available is True
        assert hour_order.state == 5
        assert hour_order.extra_state_attributes == {
            k: [v['order'], round(float(self._convert_unit(v[currency], unit)), 3)]
            for k, v in self.TIME_ATTRIBUTES.items()
            if v['order'] > 0
        }

    async def test_consecutive(self, hass: Coroutine[None, None, HomeAssistant], monkeypatch: pytest.MonkeyPatch, currency, unit):
        self._setup(await hass, currency, unit, 'electricity')
        consecutive = ConsecutiveCheapestElectricitySensor(2, hass=self.hass, settings=self.settings, coordinator=self.coordinator)
        consecutive.entity_id = consecutive.unique_id
        await consecutive.async_added_to_hass()
        assert consecutive.available is False
        assert consecutive.state is None
        assert consecutive.extra_state_attributes is None

        now = datetime(2022, 12, 3, 0, tzinfo=ZoneInfo('Europe/Prague'))
        monkeypatch.setattr(coordinator, 'get_now', lambda zoneinfo = timezone.utc: now.astimezone(zoneinfo))
        await self._refresh(monkeypatch)


@pytest.mark.parametrize('currency,unit', (
    ('CZK', 'kWh'),
    ('EUR', 'kWh'),
    ('CZK', 'MWh'),
    ('EUR', 'MWh'),
))
@pytest.mark.asyncio
class TestGasSensors(TestSensorBase):
    EUR_RATE = Decimal('24.375')

    async def test_spot_rate(self, hass: Coroutine[None, None, HomeAssistant], monkeypatch: pytest.MonkeyPatch, currency, unit):
        self._setup(await hass, currency, unit, 'gas')

        rate_sensor = TodayGasSensor(
            hass=self.hass,
            settings=self.settings,
            coordinator=self.coordinator,
        )
        rate_sensor.entity_id = rate_sensor.unique_id
        await rate_sensor.async_added_to_hass()
        assert rate_sensor.available is False
        assert rate_sensor.state is None
        assert rate_sensor.extra_state_attributes is None

        # Midnight == 1st hour of the day
        now = datetime(2022, 12, 3, 0, tzinfo=ZoneInfo('Europe/Prague'))
        monkeypatch.setattr(coordinator, 'get_now', lambda zoneinfo = timezone.utc: now.astimezone(zoneinfo))
        await self._refresh(monkeypatch)

        assert rate_sensor.available is True
        price_eur = Decimal('140.00')
        price_czk = price_eur * self.EUR_RATE
        assert rate_sensor.state == self._convert_unit(price_czk if currency == 'CZK' else price_eur, unit)

        # 1am == 2nd hour of the day
        now = datetime(2022, 12, 3, 1, tzinfo=ZoneInfo('Europe/Prague'))
        monkeypatch.setattr(coordinator, 'get_now', lambda zoneinfo = timezone.utc: now.astimezone(zoneinfo))
        await self._refresh(monkeypatch)

        assert rate_sensor.available is True
        assert rate_sensor.state == self._convert_unit(price_czk if currency == 'CZK' else price_eur, unit)

        # 0am == 1nd hour of second day
        now = datetime(2022, 12, 4, 0, tzinfo=ZoneInfo('Europe/Prague'))
        monkeypatch.setattr(coordinator, 'get_now', lambda zoneinfo = timezone.utc: now.astimezone(zoneinfo))
        await self._refresh(monkeypatch)

        assert rate_sensor.available is True
        price_eur = Decimal('141.56')
        price_czk = price_eur * self.EUR_RATE
        assert rate_sensor.state == self._convert_unit(price_czk if currency == 'CZK' else price_eur, unit)
