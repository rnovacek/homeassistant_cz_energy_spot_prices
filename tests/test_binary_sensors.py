# pyright: reportUnusedParameter=false, reportMissingTypeStubs=false
from datetime import datetime, timedelta
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock
from homeassistant.core import HomeAssistant, State
import pytest
from freezegun import freeze_time
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.cz_energy_spot_prices.const import SpotRateIntervalType
from custom_components.cz_energy_spot_prices.coordinator import PRAGUE_TZ, Window

from . import BASE_DT, CHEAPEST_15min_WINDOW, approx, get_entry, get_rate, init_integration


@pytest.mark.asyncio
async def test_has_tomorrow_data_sensor(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    has_tomorrow = cast(str, mock_ote_electricity.param) != "today"

    now = BASE_DT
    await hass.config.async_set_time_zone("Europe/Prague")
    with freeze_time(now):
        async_fire_time_changed(hass, now)
        await hass.async_block_till_done()

        assert await init_integration(
            hass,
            [
                get_entry(
                    currency='CZK', unit='kWh', interval=SpotRateIntervalType.Hour
                ),
                get_entry(
                    currency='EUR',
                    unit='MWh',
                    interval=SpotRateIntervalType.QuarterHour,
                ),
            ],
        )

        sensor = hass.states.get("binary_sensor.spot_electricity_has_tomorrow_data")
        assert sensor
        if has_tomorrow:
            assert sensor.state == 'on'
        else:
            assert sensor.state == 'off'

        assert sensor.attributes["icon"] == "mdi:cash-clock"
        assert sensor.attributes["friendly_name"] == "Spot Electricity has Tomorrow Data"


@pytest.mark.asyncio
async def test_is_cheapest_sensors(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
    windows_60min: dict[int, Window],
    windows_15min: dict[int, Window],
):
    now = BASE_DT
    await hass.config.async_set_time_zone("Europe/Prague")

    cheapest_blocks_config = "1,2,3,4,5,6,7,8,9,10,11,12"
    cheapest_blocks = [1,2,3,4,5,6,7,8,9,10,11,12]
    with freeze_time(BASE_DT):
        async_fire_time_changed(hass, BASE_DT)
        assert await init_integration(
            hass,
            [
                get_entry(
                    currency="CZK",
                    unit="kWh",
                    interval=SpotRateIntervalType.Hour,
                    cheapest_blocks=cheapest_blocks_config,
                    allow_cross_midnight=False,
                ),
                get_entry(
                    currency="EUR",
                    unit="MWh",
                    interval=SpotRateIntervalType.QuarterHour,
                    cheapest_blocks=cheapest_blocks_config,
                    allow_cross_midnight=True,
                ),
            ],
        )

    rate_60min = get_rate('CZK', 'kWh')
    rate_15min = get_rate("EUR", "MWh")

    dt = now
    end = BASE_DT + timedelta(days=1)
    while dt < end:
        with freeze_time(dt):
            async_fire_time_changed(hass, dt)

            for trade in ('spot', 'buy', 'sell'):
                if trade == 'spot':
                    trade_name = 'Spot'
                    offset = 0
                elif trade == 'buy':
                    trade_name = 'Buy'
                    offset = 10
                elif trade == 'sell':
                    trade_name = 'Sell'
                    offset = -1
                else:
                    raise ValueError(f'Invalid trade: {trade}')

                sensor = hass.states.get(f"binary_sensor.{trade}_electricity_is_cheapest")
                check_cheapest_sensor(
                    sensor,
                    f"Current {trade_name} Electricity is Cheapest",
                    dt,
                    windows_60min[1],
                    rate_60min,
                    offset,
                )

                sensor = hass.states.get(
                    f"binary_sensor.{trade}_electricity_is_cheapest_15min"
                )
                check_cheapest_sensor(sensor, f"Current 15min {trade_name} Electricity is Cheapest", dt, CHEAPEST_15min_WINDOW, rate_15min, offset)

                for block in cheapest_blocks:
                    if block > 1:
                        # 1-hour block is the same as {trade}_electricity_is_cheapest - it's not duplicated
                        entity_id = (
                            f"binary_sensor.{trade}_electricity_is_cheapest_{block}_hours_block"
                        )
                        sensor = hass.states.get(entity_id)
                        friendly_name = (
                            f"Current {trade_name} Electricity is Cheapest {block} Hours Block"
                        )
                        check_cheapest_sensor(
                            sensor,
                            friendly_name,
                            dt,
                            windows_60min[block],
                            rate_60min,
                            offset,
                        )

                    entity_id = f"binary_sensor.{trade}_electricity_is_cheapest_{block}_hours_block_15min"
                    friendly_name = f"Current 15min {trade_name} Electricity is Cheapest {block} Hours Block"
                    sensor = hass.states.get(entity_id)
                    check_cheapest_sensor(
                        sensor,
                        friendly_name,
                        dt,
                        windows_15min[block],
                        rate_15min,
                        offset,
                    )

        dt += timedelta(minutes=15)

def check_cheapest_sensor(sensor: State | None, friendly_name: str, dt: datetime, window: Window, rate: float, offset: float):
    assert sensor
    assert sensor.attributes["friendly_name"] == friendly_name

    assert sensor.state == "on" if window.start <= dt < window.end else "off"
    attr = sensor.attributes
    start = window.start.astimezone(PRAGUE_TZ)
    end = window.end.astimezone(PRAGUE_TZ)
    assert attr['Start'] == start
    assert attr['End'] == end
    if '15min' in friendly_name:
        assert "Start hour" not in attr
        assert "End hour" not in attr
    else:
        assert attr["Start hour"] == start.hour
        assert attr['End hour'] == end.hour
    prices = [(float(price) * rate + offset) for price in window.prices]
    assert approx(cast(str, attr['Min'])) == min(prices)
    assert approx(cast(str, attr['Max'])) == max(prices)
    assert approx(cast(str, attr["Mean"])) == sum(prices) / len(prices)
    assert sensor.attributes["icon"] == "mdi:cash-clock"


@pytest.fixture
def windows_60min():
    hourly_prices = [
        Decimal(92.42), # 0
        Decimal(92.04), # 1
        Decimal(91.57), # 2
        Decimal(92.72), # 3
        Decimal(92.57), # 4
        Decimal(93.64), # 5
        Decimal(112.83), # 6
        Decimal(129.89), # 7
        Decimal(130.37), # 8
        Decimal(125.9), # 9
        Decimal(105.42), # 10
        Decimal(90.41), # 11
        Decimal(86.16), # 12
        Decimal(85.05), # 13
        Decimal(92.98), # 14
        Decimal(113.66), # 15
        Decimal(147.49), # 16
        Decimal(203.85), # 17
        Decimal(293.73), # 18
        Decimal(274.02), # 19
        Decimal(185.28), # 20
        Decimal(137.43), # 21
        Decimal(125.98), # 22
        Decimal(111.72), # 23
    ]

    # Get start of the cheapest window by its size
    cheapest_start_by_size = {
        1: 13,
        2: 12,
        3: 11,
        4: 11,
        5: 10,
        6: 0,
        7: 0,
        8: 0,
        9: 0,
        10: 4,
        11: 3,
        12: 2,
    }

    window_by_size: dict[int, Window] = {}
    for size, start in cheapest_start_by_size.items():
        window_by_size[size] = Window(
            start=BASE_DT + timedelta(hours=start),
            end=BASE_DT + timedelta(hours=start + size),
            prices=hourly_prices[start : start + size],
        )

    return window_by_size

@pytest.fixture
def windows_15min():
    interval_prices = [
        Decimal(99.54),
        Decimal(96.27),
        Decimal(87.66),
        Decimal(86.19),
        Decimal(95.43),
        Decimal(93.53),
        Decimal(91.64),
        Decimal(87.57),
        Decimal(92.04),
        Decimal(91.41),
        Decimal(91.78),
        Decimal(91.06),
        Decimal(92.23),
        Decimal(93.73),
        Decimal(94.13),
        Decimal(90.78),
        Decimal(88.15),
        Decimal(90.78),
        Decimal(95.36),
        Decimal(96.00),
        Decimal(85.49),
        Decimal(89.49),
        Decimal(96.71),
        Decimal(102.88),
        Decimal(96.47),
        Decimal(106.00),
        Decimal(112.86),
        Decimal(136.00),
        Decimal(110.89),
        Decimal(126.99),
        Decimal(142.85),
        Decimal(138.84),
        Decimal(133.81),
        Decimal(141.31),
        Decimal(135.13),
        Decimal(111.24),
        Decimal(148.00),
        Decimal(138.69),
        Decimal(115.64),
        Decimal(101.26),
        Decimal(130.54),
        Decimal(105.46),
        Decimal(95.87),
        Decimal(89.81),
        Decimal(97.63),
        Decimal(94.11),
        Decimal(88.66),
        Decimal(81.25),
        Decimal(93.51),
        Decimal(83.15),
        Decimal(83.65),
        Decimal(84.31),
        Decimal(91.89),
        Decimal(86.48),
        Decimal(83.11),
        Decimal(78.73),
        Decimal(80.42),
        Decimal(91.47),
        Decimal(95.08),
        Decimal(104.96),
        Decimal(90.24),
        Decimal(99.73),
        Decimal(125.26),
        Decimal(139.4),
        Decimal(94.88),
        Decimal(127.43),
        Decimal(160.11),
        Decimal(207.52),
        Decimal(145.5),
        Decimal(167.00),
        Decimal(224.65),
        Decimal(278.24),
        Decimal(224.66),
        Decimal(270.09),
        Decimal(334.57),
        Decimal(345.58),
        Decimal(324.05),
        Decimal(299.18),
        Decimal(254.23),
        Decimal(218.61),
        Decimal(259.64),
        Decimal(194.85),
        Decimal(150.21),
        Decimal(136.41),
        Decimal(180.73),
        Decimal(142.85),
        Decimal(120.74),
        Decimal(105.4),
        Decimal(145.83),
        Decimal(131.16),
        Decimal(117.09),
        Decimal(109.84),
        Decimal(117.79),
        Decimal(115.12),
        Decimal(111.07),
        Decimal(102.88),
        Decimal(114.14),
        Decimal(108.5),
        Decimal(100.63),
        Decimal(96.78),
        Decimal(110.81),
        Decimal(108.52),
        Decimal(98.78),
        Decimal(94.73),
        Decimal(109.68),
        Decimal(101.48),
        Decimal(93.04),
        Decimal(89.63),
        Decimal(97.49),
        Decimal(91.16),
        Decimal(82.89),
        Decimal(78.15),
        Decimal(84.18),
        Decimal(81.75),
        Decimal(81.22),
        Decimal(84.3),
        Decimal(84.88),
        Decimal(83.02),
        Decimal(98.04),
        Decimal(107.43),
        Decimal(101.68),
        Decimal(113.69),
        Decimal(120.11),
        Decimal(129.83),
        Decimal(116.41),
        Decimal(135.93),
        Decimal(130.93),
        Decimal(123.19),
        Decimal(140.67),
        Decimal(133.45),
        Decimal(109.75),
        Decimal(100.02),
        Decimal(119.78),
        Decimal(111.98),
        Decimal(103.15),
        Decimal(86.62),
        Decimal(107.71),
        Decimal(88.22),
        Decimal(75.84),
        Decimal(69.04),
        Decimal(72.44),
        Decimal(73.2),
        Decimal(71.08),
        Decimal(69.44),
        Decimal(70.87),
        Decimal(68.99),
        Decimal(68.42),
        Decimal(66.93),
        Decimal(68.96),
        Decimal(66.93),
        Decimal(66.48),
        Decimal(65.83),
        Decimal(61.55),
        Decimal(65.99),
        Decimal(84.43),
        Decimal(90.22),
        Decimal(67.61),
        Decimal(90.77),
        Decimal(99.41),
        Decimal(111.49),
        Decimal(92.73),
        Decimal(102.28),
        Decimal(113.26),
        Decimal(118.37),
        Decimal(96.03),
        Decimal(114.56),
        Decimal(121.51),
        Decimal(127.55),
        Decimal(110.46),
        Decimal(114.99),
        Decimal(129.23),
        Decimal(120.5),
        Decimal(132.91),
        Decimal(122.46),
        Decimal(112.21),
        Decimal(98.64),
        Decimal(115.85),
        Decimal(110.3),
        Decimal(100.19),
        Decimal(93.65),
        Decimal(105.1),
        Decimal(93.81),
        Decimal(85.43),
        Decimal(73.72),
        Decimal(105.17),
        Decimal(89.00),
        Decimal(74.39),
        Decimal(62.43),
        Decimal(79.42),
        Decimal(72.43),
        Decimal(69.75),
        Decimal(61.36),
    ]

    # Get start of the cheapest window by its size
    cheapest_start_by_size = {
        1: timedelta(days=1, hours=13, minutes=30),
        2: timedelta(days=1, hours=12, minutes=30),
        3: timedelta(days=1, hours=11, minutes=30),
        4: timedelta(days=1, hours=10, minutes=30),
        5: timedelta(days=1, hours=10, minutes=15),
        6: timedelta(days=1, hours=9, minutes=45),
        7: timedelta(days=1, hours=9, minutes=30),
        8: timedelta(days=1, hours=9, minutes=15),
        9: timedelta(days=1, hours=8, minutes=30),
        10: timedelta(days=1, hours=8, minutes=30),
        11: timedelta(days=1, hours=4, minutes=15),
        12: timedelta(days=1, hours=3, minutes=30),
    }

    window_by_size: dict[int, Window] = {}
    for size, start in cheapest_start_by_size.items():
        start_index = int(start.total_seconds() / (15 * 60))
        window_by_size[size] = Window(
            start=BASE_DT + start,
            end=BASE_DT + start + timedelta(minutes=15 * size * 4),
            prices=interval_prices[start_index : start_index + size * 4],
        )

    return window_by_size
