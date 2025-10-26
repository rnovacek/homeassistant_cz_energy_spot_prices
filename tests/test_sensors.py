# pyright: reportUnusedParameter=false, reportMissingTypeStubs=false
from datetime import datetime, timedelta
from typing import cast
from homeassistant.core import HomeAssistant
import pytest
from freezegun import freeze_time

from pytest_homeassistant_custom_component.common import (
    AsyncMock,
    async_fire_time_changed,
)


from custom_components.cz_energy_spot_prices.const import (
    SpotRateIntervalType,
)
from custom_components.cz_energy_spot_prices.coordinator import PRAGUE_TZ

from . import (
    BASE_DT,
    TODAY_CHEAPEST_15min_DT,
    TODAY_CHEAPEST_15min_PRICE,
    TODAY_CHEAPEST_60min_DT,
    TODAY_CHEAPEST_60min_PRICE,
    TOMORROW_CHEAPEST_15min_DT,
    TOMORROW_CHEAPEST_15min_PRICE,
    TOMORROW_CHEAPEST_60min_DT,
    TOMORROW_CHEAPEST_60min_PRICE,
    approx,
    get_entry,
    get_rate,
    init_integration,
)


price_by_delta = {
    0: (99.54, 92.42),
    15: (96.27, 92.42),
    30: (87.66, 92.42),
    45: (86.19, 92.42),
    60: (95.43, 92.04),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("currency", ("EUR", "CZK"))
@pytest.mark.parametrize("unit", ("kWh", "MWh"))
@pytest.mark.parametrize("trade", ("spot", "buy", "sell"))
@pytest.mark.parametrize(
    "mock_ote_electricity", ("today", "today+tomorrow"), indirect=True
)
async def test_electricity_spot_rate_sensor(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
    currency: str,
    unit: str,
    trade: str,
):
    offset = 0
    icon = "mdi:cash"
    trade_label = "Spot"
    if trade == "buy":
        offset = 10
        icon = "mdi:cash-minus"
        trade_label = "Buy"
    elif trade == "sell":
        offset = -1
        icon = "mdi:cash-plus"
        trade_label = "Sell"

    rate = get_rate(currency, unit)
    has_tomorrow = cast(str, mock_ote_electricity.param) != "today"

    await hass.config.async_set_time_zone("Europe/Prague")
    with freeze_time(BASE_DT):
        assert await init_integration(
            hass,
            [
                get_entry(
                    currency=currency, unit=unit, interval=SpotRateIntervalType.Hour
                ),
                get_entry(
                    currency=currency,
                    unit=unit,
                    interval=SpotRateIntervalType.QuarterHour,
                ),
            ],
        )

    for delta, (price, hourly_price) in price_by_delta.items():
        now = BASE_DT + timedelta(minutes=delta)
        with freeze_time(now):
            async_fire_time_changed(hass, now)
            await hass.async_block_till_done()

            sensor_60min = hass.states.get(f"sensor.current_{trade}_electricity_price")
            assert sensor_60min
            assert approx(sensor_60min.state) == hourly_price * rate + offset
            attr = cast(dict[str, str], sensor_60min.attributes)
            assert approx(attr["2025-10-22T00:00:00+02:00"]) == 92.42 * rate + offset
            assert approx(attr["2025-10-22T01:00:00+02:00"]) == 92.04 * rate + offset
            assert approx(attr["2025-10-22T02:00:00+02:00"]) == 91.57 * rate + offset
            if has_tomorrow:
                assert (
                    approx(attr["2025-10-23T02:00:00+02:00"]) == 98.46 * rate + offset
                )
            else:
                assert "2025-10-23T02:00:00+02:00" not in attr

            # Check that there are no 15min intervals in there
            assert "2025-10-22T00:15:00+02:00" not in sensor_60min.attributes

            assert (
                sensor_60min.attributes["unit_of_measurement"]
                == f"{'€' if currency == 'EUR' else 'Kč'}/{unit}"
            )
            assert sensor_60min.attributes["icon"] == icon
            assert (
                sensor_60min.attributes["friendly_name"]
                == f"Current {trade_label} Electricity Price"
            )

            sensor_15min = hass.states.get(
                f"sensor.current_{trade}_electricity_price_15min"
            )
            assert sensor_15min
            value = sensor_15min.state
            attr = cast(dict[str, str], sensor_15min.attributes)
            assert approx(value) == price * rate + offset
            assert approx(attr["2025-10-22T00:00:00+02:00"]) == 99.54 * rate + offset
            assert approx(attr["2025-10-22T00:15:00+02:00"]) == 96.27 * rate + offset
            assert approx(attr["2025-10-22T00:30:00+02:00"]) == 87.66 * rate + offset
            assert approx(attr["2025-10-22T00:45:00+02:00"]) == 86.19 * rate + offset
            assert approx(attr["2025-10-22T01:00:00+02:00"]) == 95.43 * rate + offset
            assert approx(attr["2025-10-22T01:15:00+02:00"]) == 93.53 * rate + offset
            if has_tomorrow:
                assert (
                    approx(attr["2025-10-23T02:15:00+02:00"]) == 101.48 * rate + offset
                )
            else:
                assert "2025-10-23T02:15:00+02:00" not in attr
            assert (
                sensor_15min.attributes["unit_of_measurement"]
                == f"{'€' if currency == 'EUR' else 'Kč'}/{unit}"
            )
            assert sensor_15min.attributes["icon"] == icon
            assert (
                sensor_15min.attributes["friendly_name"]
                == f"Current 15min {trade_label} Electricity Price"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("currency", ("EUR", "CZK"))
@pytest.mark.parametrize("unit", ("kWh", "MWh"))
@pytest.mark.parametrize("trade", ("spot", "buy", "sell"))
@pytest.mark.parametrize(
    "mock_ote_electricity", ("today", "today+tomorrow"), indirect=True
)
async def test_cheapest_sensor(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
    currency: str,
    trade: str,
    unit: str,
):
    offset = 0
    if trade == "buy":
        offset = 10
    elif trade == "sell":
        offset = -1

    now = BASE_DT
    rate = get_rate(currency, unit)
    has_tomorrow = cast(str, mock_ote_electricity.param) != "today"

    await hass.config.async_set_time_zone("Europe/Prague")
    with freeze_time(now):
        async_fire_time_changed(hass, now)
        await hass.async_block_till_done()

        assert await init_integration(
            hass,
            [
                get_entry(
                    currency=currency, unit=unit, interval=SpotRateIntervalType.Hour
                ),
                get_entry(
                    currency=currency,
                    unit=unit,
                    interval=SpotRateIntervalType.QuarterHour,
                ),
            ],
        )
        sensor_60min = hass.states.get(f"sensor.{trade}_cheapest_electricity_today")
        assert sensor_60min
        assert approx(sensor_60min.state) == TODAY_CHEAPEST_60min_PRICE * rate + offset
        attr = cast(dict[str, str], sensor_60min.attributes)
        at = datetime.fromisoformat(TODAY_CHEAPEST_60min_DT).astimezone(PRAGUE_TZ)
        assert attr["at"] == at.isoformat()
        assert attr["hour"] == at.hour
        assert (
            attr["unit_of_measurement"]
            == f"{'€' if currency == 'EUR' else 'Kč'}/{unit}"
        )
        assert attr["icon"] == "mdi:cash"

        sensor_15min = hass.states.get(
            f"sensor.{trade}_cheapest_electricity_today_15min"
        )
        assert sensor_15min
        assert approx(sensor_15min.state) == TODAY_CHEAPEST_15min_PRICE * rate + offset
        attr = cast(dict[str, str], sensor_15min.attributes)
        assert (
            attr["at"]
            == datetime.fromisoformat(TODAY_CHEAPEST_15min_DT)
            .astimezone(PRAGUE_TZ)
            .isoformat()
        )
        # No `hour` for 15min sensor
        assert "hour" not in attr
        assert (
            attr["unit_of_measurement"]
            == f"{'€' if currency == 'EUR' else 'Kč'}/{unit}"
        )
        assert attr["icon"] == "mdi:cash"

        sensor_60min_tomorrow = hass.states.get(
            f"sensor.{trade}_cheapest_electricity_tomorrow"
        )
        assert sensor_60min_tomorrow
        attr = cast(dict[str, str], sensor_60min_tomorrow.attributes)

        if has_tomorrow:
            assert (
                approx(sensor_60min_tomorrow.state)
                == TOMORROW_CHEAPEST_60min_PRICE * rate + offset
            )
            at = datetime.fromisoformat(TOMORROW_CHEAPEST_60min_DT).astimezone(
                PRAGUE_TZ
            )
            assert at.isoformat()
            assert attr["hour"] == at.hour
        else:
            assert sensor_60min_tomorrow.state == "unknown"
            assert "at" not in attr

        assert (
            attr["unit_of_measurement"]
            == f"{'€' if currency == 'EUR' else 'Kč'}/{unit}"
        )
        assert attr["icon"] == "mdi:cash"

        sensor_15min_tomorrow = hass.states.get(
            f"sensor.{trade}_cheapest_electricity_tomorrow_15min"
        )
        assert sensor_15min_tomorrow
        attr = cast(dict[str, str], sensor_15min_tomorrow.attributes)

        if has_tomorrow:
            assert (
                approx(sensor_15min_tomorrow.state)
                == TOMORROW_CHEAPEST_15min_PRICE * rate + offset
            )
            assert (
                attr["at"]
                == datetime.fromisoformat(TOMORROW_CHEAPEST_15min_DT)
                .astimezone(PRAGUE_TZ)
                .isoformat()
            )

        else:
            assert sensor_15min_tomorrow.state == "unknown"
            assert "at" not in attr

        # No `hour` for 15min sensor
        assert "hour" not in attr
        assert (
            attr["unit_of_measurement"]
            == f"{'€' if currency == 'EUR' else 'Kč'}/{unit}"
        )
        assert attr["icon"] == "mdi:cash"


# TODO: test for most expensive (could be combined with above)
# TODO: test for interval order sensor
