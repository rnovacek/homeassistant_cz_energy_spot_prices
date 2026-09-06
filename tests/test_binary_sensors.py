# pyright: reportUnusedParameter=false, reportMissingTypeStubs=false
import asyncio
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import cast
from unittest.mock import AsyncMock, patch
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify
import pytest
from freezegun import freeze_time
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.cz_energy_spot_prices.const import (
    CONF_PRICE_TYPE,
    CONF_SEARCH_OBJECTIVE,
    Commodity,
    Currency,
    DOMAIN,
    EnergyUnit,
    FX_COORDINATOR,
    PRICE_BLOCK_SUBENTRY_TYPE,
    SearchObjective,
    SearchType,
    SpotRateIntervalType,
    SPOT_ELECTRICTY_COORDINATOR,
)
from custom_components.cz_energy_spot_prices.cheapest_blocks import (
    PriceBlockSearch,
    find_price_block,
    resolve_search_window,
    validate_search_definition,
)
from custom_components.cz_energy_spot_prices.coordinator import (
    EntryConfig,
    IntervalSpotRateData,
    PRAGUE_TZ,
)

from . import (
    BASE_DT,
    get_entry,
    init_integration,
)


def test_cross_midnight_time_window_resolves_current_overnight_window():
    """Test time windows after midnight resolve to the window that began yesterday."""
    search = PriceBlockSearch(
        id="overnight",
        name="Overnight",
        type=SearchType.FIXED,
        length_hours=1,
        start_time=time(22),
        end_time=time(6),
    )
    now = datetime(2025, 10, 22, 3, 0, tzinfo=PRAGUE_TZ)

    window = resolve_search_window(search, now, available_end=None)

    assert window == (
        datetime(2025, 10, 21, 22, 0, tzinfo=PRAGUE_TZ),
        datetime(2025, 10, 22, 6, 0, tzinfo=PRAGUE_TZ),
    )


@pytest.mark.parametrize("length_hours", [3, 6])
def test_fixed_overnight_search_uses_previous_complete_window_until_publish(
    length_hours: int,
):
    """A future partial window must not produce a provisional result."""
    today = datetime(2025, 10, 22, 0, 0, tzinfo=PRAGUE_TZ)
    rates = {
        (today - timedelta(days=1) + timedelta(hours=hour)).astimezone(UTC): Decimal(
            hour
        )
        for hour in range(48)
    }
    config = EntryConfig(
        commodity=Commodity.Electricity,
        interval=SpotRateIntervalType.Hour,
        currency=Currency.EUR,
        currency_human="EUR",
        unit=EnergyUnit.MWh,
        timezone="Europe/Prague",
        zoneinfo=PRAGUE_TZ,
        buy_template=None,
        sell_template=None,
        cheapest_block_searches=[
            PriceBlockSearch(
                id="overnight",
                name="Overnight",
                type=SearchType.FIXED,
                length_hours=length_hours,
                start_time=time(20),
                end_time=time(6),
            )
        ],
    )

    with freeze_time(today + timedelta(hours=7)):
        data = IntervalSpotRateData(config, rates, rate_template=None)

    window = data.search_windows["overnight"]
    assert window.start.astimezone(PRAGUE_TZ).date() == today.date() - timedelta(days=1)
    assert window.end <= today.astimezone(UTC) + timedelta(hours=6)


def test_fixed_overnight_search_switches_to_next_window_after_publish():
    """A complete upcoming occurrence replaces the retained previous one."""
    today = datetime(2025, 10, 22, 0, 0, tzinfo=PRAGUE_TZ)
    search = PriceBlockSearch(
        id="overnight",
        name="Overnight",
        type=SearchType.FIXED,
        length_hours=1,
        start_time=time(20),
        end_time=time(6),
    )

    window = resolve_search_window(
        search,
        today + timedelta(hours=14),
        available_start=today - timedelta(days=1),
        available_end=today + timedelta(days=2),
    )

    assert window == (
        today + timedelta(hours=20),
        today + timedelta(days=1, hours=6),
    )


def test_tomorrow_search_can_use_final_available_interval():
    """Test custom searches can select the last interval of available data."""
    today = datetime(2025, 10, 22, 0, 0, tzinfo=PRAGUE_TZ)
    tomorrow = datetime(2025, 10, 23, 0, 0, tzinfo=PRAGUE_TZ)
    rates = {
        (today + timedelta(hours=hour)).astimezone(UTC): Decimal(100)
        for hour in range(24)
    }
    rates.update(
        {
            (tomorrow + timedelta(hours=hour)).astimezone(UTC): Decimal(100)
            for hour in range(24)
        }
    )
    last_interval = (tomorrow + timedelta(hours=23)).astimezone(UTC)
    rates[last_interval] = Decimal(1)
    config = EntryConfig(
        commodity=Commodity.Electricity,
        interval=SpotRateIntervalType.Hour,
        currency=Currency.EUR,
        currency_human="EUR",
        unit=EnergyUnit.MWh,
        timezone="Europe/Prague",
        zoneinfo=PRAGUE_TZ,
        buy_template=None,
        sell_template=None,
        cheapest_block_searches=[
            PriceBlockSearch(
                id="tomorrow-last-hour",
                name="Tomorrow last hour",
                type=SearchType.TOMORROW,
                length_hours=1,
            )
        ],
    )

    with freeze_time(datetime(2025, 10, 22, 12, 0, tzinfo=PRAGUE_TZ)):
        data = IntervalSpotRateData(config, rates, rate_template=None)

    window = data.search_windows["tomorrow-last-hour"]
    assert window.start == last_interval
    assert window.end == last_interval + timedelta(hours=1)


def test_interval_data_uses_highest_price_search_objective():
    """Test configured highest objective reaches interval rate processing."""
    today = datetime(2025, 10, 22, 0, 0, tzinfo=PRAGUE_TZ)
    rates = {
        (today + timedelta(hours=hour)).astimezone(UTC): Decimal(1)
        for hour in range(24)
    }
    expected_start = (today + timedelta(hours=10)).astimezone(UTC)
    rates[expected_start] = Decimal(10)
    rates[expected_start + timedelta(hours=1)] = Decimal(9)
    config = EntryConfig(
        commodity=Commodity.Electricity,
        interval=SpotRateIntervalType.Hour,
        currency=Currency.EUR,
        currency_human="EUR",
        unit=EnergyUnit.MWh,
        timezone="Europe/Prague",
        zoneinfo=PRAGUE_TZ,
        buy_template=None,
        sell_template=None,
        cheapest_block_searches=[
            PriceBlockSearch(
                id="today-highest",
                name="Today highest",
                type=SearchType.TODAY,
                length_hours=2,
                objective=SearchObjective.HIGHEST,
            )
        ],
    )

    with freeze_time(datetime(2025, 10, 22, 12, 0, tzinfo=PRAGUE_TZ)):
        data = IntervalSpotRateData(config, rates, rate_template=None)

    window = data.search_windows["today-highest"]
    assert window.start == expected_start
    assert window.end == expected_start + timedelta(hours=2)
    assert window.prices == [Decimal(10), Decimal(9)]


def test_highest_price_search_supports_tomorrow_and_fixed_windows():
    """Test highest objective composes with tomorrow and fixed search periods."""
    today = datetime(2025, 10, 22, 0, 0, tzinfo=PRAGUE_TZ)
    rates = {
        (today + timedelta(hours=hour)).astimezone(UTC): Decimal(1)
        for hour in range(48)
    }
    fixed_start = (today + timedelta(hours=19)).astimezone(UTC)
    rates[fixed_start] = Decimal(10)
    rates[fixed_start + timedelta(hours=1)] = Decimal(9)
    tomorrow_start = (today + timedelta(days=1, hours=8)).astimezone(UTC)
    rates[tomorrow_start] = Decimal(12)
    rates[tomorrow_start + timedelta(hours=1)] = Decimal(11)
    config = EntryConfig(
        commodity=Commodity.Electricity,
        interval=SpotRateIntervalType.Hour,
        currency=Currency.EUR,
        currency_human="EUR",
        unit=EnergyUnit.MWh,
        timezone="Europe/Prague",
        zoneinfo=PRAGUE_TZ,
        buy_template=None,
        sell_template=None,
        cheapest_block_searches=[
            PriceBlockSearch(
                id="tomorrow-highest",
                name="Tomorrow highest",
                type=SearchType.TOMORROW,
                length_hours=2,
                objective=SearchObjective.HIGHEST,
            ),
            PriceBlockSearch(
                id="fixed-highest",
                name="Evening highest",
                type=SearchType.FIXED,
                length_hours=2,
                start_time=time(18),
                end_time=time(22),
                objective=SearchObjective.HIGHEST,
            ),
        ],
    )

    with freeze_time(datetime(2025, 10, 22, 12, 0, tzinfo=PRAGUE_TZ)):
        data = IntervalSpotRateData(config, rates, rate_template=None)

    assert data.search_windows["tomorrow-highest"].start == tomorrow_start
    assert data.search_windows["fixed-highest"].start == fixed_start


@pytest.mark.parametrize("interval_seconds", [900, 3600])
@pytest.mark.parametrize("end_time", ["23:00:30", "01:00:30"])
def test_fixed_window_preserves_seconds(interval_seconds, end_time):
    """Whole price intervals must fit inside the exact configured times."""
    interval = (
        SpotRateIntervalType.QuarterHour
        if interval_seconds == 900
        else SpotRateIntervalType.Hour
    )
    search = PriceBlockSearch.from_mapping(
        {
            "id": "seconds",
            "name": "Exact window",
            "type": SearchType.FIXED,
            "length_hours": interval_seconds / 3600,
            "start_time": "22:00:30",
            "end_time": end_time,
        },
        interval=interval,
    )
    assert search is not None
    assert search.start_time == time(22, 0, 30)
    assert search.end_time == time.fromisoformat(end_time)
    today = datetime(2025, 10, 22, tzinfo=PRAGUE_TZ)
    bounds = resolve_search_window(search, today + timedelta(hours=12), None)
    assert bounds is not None
    start, end = (bound.astimezone(UTC) for bound in bounds)
    assert start.second == end.second == 30
    base = start.replace(second=0)
    step = timedelta(seconds=interval_seconds)
    intervals = [(base + step * index, Decimal(index)) for index in range(20)]
    result = find_price_block(
        intervals, start, end, interval_seconds / 3600,
        interval_seconds=interval_seconds,
        require_complete_window=True,
    )
    if interval_seconds == 3600 and end_time == "23:00:30":
        assert result is None
    else:
        assert result is not None
        assert result["start"] == base + step
        assert result["end"] <= end


@pytest.mark.parametrize(
    ("start_time", "end_time", "error_field", "error"),
    [
        ("22:00:30", "23:00:00", "length_hours", "longer_than_window"),
        ("23:00:30", "00:00:00", "length_hours", "longer_than_window"),
        ("22:00", "22:00:00", "end_time", "start_equals_end"),
    ],
)
def test_fixed_window_validation_uses_seconds(start_time, end_time, error_field, error):
    """Validation and persisted parsing agree on the actual window duration."""
    search = {
        "id": "seconds",
        "name": "Exact window",
        "type": SearchType.FIXED,
        "length_hours": 1,
        "start_time": start_time,
        "end_time": end_time,
    }
    assert validate_search_definition(search) == {error_field: error}
    assert PriceBlockSearch.from_mapping(search, interval=SpotRateIntervalType.Hour) is None


def test_find_price_block_requires_interval_to_end_inside_window():
    """Test partial intervals at a time window boundary are not selected."""
    base = datetime(2025, 10, 22, 20, 0, tzinfo=UTC)
    intervals = [
        (base + timedelta(hours=2), Decimal(50)),
        (base + timedelta(hours=3), Decimal(1)),
    ]

    result = find_price_block(
        intervals,
        base + timedelta(hours=2, minutes=30),
        base + timedelta(hours=3, minutes=30),
        length_hours=1,
    )

    assert result is None


@pytest.mark.parametrize("interval_seconds", [900, 3600])
@pytest.mark.parametrize("objective", list(SearchObjective))
def test_find_price_block_skips_gaps(interval_seconds, objective):
    """Missing prices cannot stretch a block beyond its requested duration."""
    base = datetime(2025, 10, 22, tzinfo=UTC)
    step = timedelta(seconds=interval_seconds)
    preferred = Decimal(-10 if objective == SearchObjective.LOWEST else 10)
    intervals = [
        (base, preferred),
        (base + step * 2, preferred),
        (base + step * 3, Decimal(0)),
        (base + step * 4, Decimal(0)),
    ]

    result = find_price_block(
        intervals,
        base,
        base + step * 5,
        length_hours=interval_seconds * 2 / 3600,
        objective=objective,
        interval_seconds=interval_seconds,
    )

    assert result is not None
    assert result["start"] == base + step * 2
    assert result["end"] - result["start"] == step * 2
    assert result["prices"] == [preferred, Decimal(0)]
    assert find_price_block(
        intervals[:2],
        base,
        base + step * 3,
        length_hours=interval_seconds * 2 / 3600,
        interval_seconds=interval_seconds,
    ) is None


@pytest.mark.parametrize("search_type", list(SearchType))
def test_configured_search_requires_all_prices_in_window(search_type):
    """Even a gap outside the winning block invalidates a configured window."""
    today = datetime(2025, 10, 22, tzinfo=PRAGUE_TZ)
    rates = {
        (today + timedelta(hours=hour)).astimezone(UTC): Decimal(hour)
        for hour in range(48)
    }
    config = EntryConfig(
        commodity=Commodity.Electricity,
        interval=SpotRateIntervalType.Hour,
        currency=Currency.EUR,
        currency_human="EUR",
        unit=EnergyUnit.MWh,
        timezone="Europe/Prague",
        zoneinfo=PRAGUE_TZ,
        buy_template=None,
        sell_template=None,
        cheapest_block_searches=[
            PriceBlockSearch(
                id="complete",
                name="Complete window",
                type=search_type,
                length_hours=2,
                start_time=time(18),
                end_time=time(23),
            )
        ],
    )
    missing_hour = 45 if search_type == SearchType.TOMORROW else 21
    with freeze_time(today + timedelta(hours=12)):
        complete = IntervalSpotRateData(config, rates, rate_template=None)
        assert "complete" in complete.search_windows
        del rates[(today + timedelta(hours=missing_hour)).astimezone(UTC)]
        incomplete = IntervalSpotRateData(config, rates, rate_template=None)
        assert "complete" not in incomplete.search_windows


def test_find_price_block_handles_ties_and_negative_prices():
    """Test price objectives handle ties and the negative prices common on OTE."""
    base = datetime(2025, 10, 22, 0, 0, tzinfo=UTC)
    intervals = [
        (base + timedelta(hours=hour), price)
        for hour, price in enumerate([Decimal(5), Decimal(5), Decimal(1), Decimal(9)])
    ]

    result = find_price_block(
        intervals,
        base,
        base + timedelta(hours=4),
        length_hours=2,
        objective=SearchObjective.HIGHEST,
    )

    assert result is not None
    assert result["start"] == base
    assert result["end"] == base + timedelta(hours=2)
    assert result["total"] == Decimal(10)

    negative = [
        (base + timedelta(hours=hour), price)
        for hour, price in enumerate(
            [Decimal(-5), Decimal(-5), Decimal(0), Decimal(-10)]
        )
    ]
    result = find_price_block(
        negative,
        base,
        base + timedelta(hours=4),
        length_hours=2,
        objective=SearchObjective.LOWEST,
    )
    assert result is not None
    assert result["start"] == base
    assert result["total"] == Decimal(-10)


def test_custom_windows_resolve_across_both_dst_transitions():
    """Test recurring windows use real elapsed intervals on short and long days."""
    cases = (
        (datetime(2026, 3, 29, 0, 30, tzinfo=PRAGUE_TZ), 2),
        (datetime(2025, 10, 26, 0, 30, tzinfo=PRAGUE_TZ), 4),
    )
    search = PriceBlockSearch(
        id="dst",
        name="DST",
        type=SearchType.FIXED,
        length_hours=2,
        start_time=time(1),
        end_time=time(4),
    )

    for now, elapsed_hours in cases:
        window = resolve_search_window(search, now, available_end=None)
        assert window is not None
        start, end = window
        assert end.astimezone(UTC) - start.astimezone(UTC) == timedelta(
            hours=elapsed_hours
        )

        intervals = [
            (start.astimezone(UTC) + timedelta(hours=hour), Decimal(hour))
            for hour in range(elapsed_hours)
        ]
        result = find_price_block(
            intervals,
            start.astimezone(UTC),
            end.astimezone(UTC),
            length_hours=2,
        )
        assert result is not None
        assert result["end"] - result["start"] == timedelta(hours=2)


@pytest.mark.asyncio
async def test_has_tomorrow_data_sensor(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    has_tomorrow = cast(str, mock_ote_electricity.param) != "today"

    now = BASE_DT
    entries = [
        get_entry(currency="CZK", unit="kWh", interval=SpotRateIntervalType.Hour),
        get_entry(
            currency="EUR",
            unit="MWh",
            interval=SpotRateIntervalType.QuarterHour,
        ),
    ]
    await hass.config.async_set_time_zone("Europe/Prague")
    with freeze_time(now):
        async_fire_time_changed(hass, now)
        await hass.async_block_till_done()

        assert await init_integration(hass, entries)

        sensor = hass.states.get("binary_sensor.spot_electricity_has_tomorrow_data")
        assert sensor
        if has_tomorrow:
            assert sensor.state == "on"
        else:
            assert sensor.state == "off"

        assert sensor.attributes["icon"] == "mdi:cash-clock"
        assert (
            sensor.attributes["friendly_name"] == "Spot Electricity has Tomorrow Data"
        )

        registry = er.async_get(hass)
        tomorrow_entries = [
            item
            for item in registry.entities.values()
            if item.platform == DOMAIN
            and item.unique_id == "spot_electricity_has_tomorrow_data"
        ]
        assert len(tomorrow_entries) == 1


@pytest.mark.asyncio
async def test_search_based_cheapest_sensors(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    """Test search-based cheapest block sensors."""
    await hass.config.async_set_time_zone("Europe/Prague")

    searches_60min = [
        {
            "id": "today-1",
            "name": "Today 1h",
            "type": SearchType.TODAY,
            "length_hours": 1,
            CONF_PRICE_TYPE: "spot",
        },
        {
            "id": "today-3",
            "name": "Today 3h",
            "type": SearchType.TODAY,
            "length_hours": 3,
            CONF_PRICE_TYPE: "buy",
        },
        {
            "id": "today-high-2",
            "name": "Today high 2h",
            "type": SearchType.TODAY,
            "length_hours": 2,
            CONF_PRICE_TYPE: "spot",
            CONF_SEARCH_OBJECTIVE: SearchObjective.HIGHEST,
        },
    ]
    searches_15min = [
        {
            "id": "today-1",
            "name": "Today 1h",
            "type": SearchType.TODAY,
            "length_hours": 1,
            CONF_PRICE_TYPE: "spot",
        },
        {
            "id": "today-3",
            "name": "Today 3h",
            "type": SearchType.TODAY,
            "length_hours": 3,
            CONF_PRICE_TYPE: "sell",
        },
    ]

    with freeze_time(BASE_DT):
        async_fire_time_changed(hass, BASE_DT)
        assert await init_integration(
            hass,
            [
                get_entry(
                    currency="CZK",
                    unit="kWh",
                    interval=SpotRateIntervalType.Hour,
                    cheapest_block_searches=searches_60min,
                ),
                get_entry(
                    currency="EUR",
                    unit="MWh",
                    interval=SpotRateIntervalType.QuarterHour,
                    cheapest_block_searches=searches_15min,
                ),
            ],
        )

    sensor = hass.states.get("binary_sensor.spot_cheapest_block_today_1h")
    assert sensor is not None
    assert "Start" in sensor.attributes
    assert "End" in sensor.attributes
    assert "Min" in sensor.attributes
    assert "Max" in sensor.attributes
    assert "Mean" in sensor.attributes
    assert sensor.attributes["Price type"] == "spot"
    assert sensor.attributes["Objective"] == SearchObjective.LOWEST

    highest_sensor = hass.states.get(
        "binary_sensor.spot_highest_price_block_today_high_2h"
    )
    assert highest_sensor is not None
    assert highest_sensor.attributes["Objective"] == SearchObjective.HIGHEST

    sensor = hass.states.get("binary_sensor.buy_cheapest_block_today_3h")
    assert sensor is not None
    assert sensor.attributes["Price type"] == "buy"

    sensor_15min = hass.states.get("binary_sensor.spot_cheapest_block_today_1h_15min")
    assert sensor_15min is not None

    sensor_15min = hass.states.get("binary_sensor.sell_cheapest_block_today_3h_15min")
    assert sensor_15min is not None
    assert sensor_15min.attributes["Price type"] == "sell"

    assert hass.states.get("binary_sensor.buy_cheapest_block_today_1h") is None
    assert hass.states.get("binary_sensor.sell_cheapest_block_today_1h") is None
    assert hass.states.get("sensor.spot_cheapest_block_today_1h") is None
    assert (
        hass.states.get("binary_sensor.buy_electricity_is_cheapest_3_hours_block")
        is None
    )

    # Verify sensor state at a specific time
    # Today cheapest 1h is at 13:00-14:00 Prague time = 11:00-12:00 UTC
    cheapest_1h_start = BASE_DT + timedelta(hours=13)
    with freeze_time(cheapest_1h_start + timedelta(minutes=30)):
        async_fire_time_changed(hass, cheapest_1h_start + timedelta(minutes=30))
        sensor = hass.states.get("binary_sensor.spot_cheapest_block_today_1h")
        assert sensor is not None
        assert sensor.state == "on"
        assert sensor.attributes["Start hour"] == 13
        assert sensor.attributes["End hour"] == 14

    # Verify sensor attributes are correct
    sensor = hass.states.get("binary_sensor.spot_cheapest_block_today_1h")
    assert sensor is not None
    assert sensor.attributes["Length hours"] == 1.0
    assert sensor.attributes["Search type"] == "today"


@pytest.mark.asyncio
async def test_highest_price_15min_entities_support_all_price_types(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    """Test 15-minute highest searches for spot, buy, and sell prices."""
    searches = [
        {
            "id": f"highest-{price_type}",
            "name": f"Highest {price_type}",
            "type": SearchType.TODAY,
            "length_hours": 0.5,
            CONF_PRICE_TYPE: price_type,
            CONF_SEARCH_OBJECTIVE: SearchObjective.HIGHEST,
        }
        for price_type in ("spot", "buy", "sell")
    ]

    with freeze_time(BASE_DT):
        async_fire_time_changed(hass, BASE_DT)
        assert await init_integration(
            hass,
            [
                get_entry(
                    currency="EUR",
                    unit="MWh",
                    interval=SpotRateIntervalType.QuarterHour,
                    cheapest_block_searches=searches,
                )
            ],
        )

    sensors = {
        price_type: hass.states.get(
            f"binary_sensor.{price_type}_highest_price_block_highest_{price_type}_15min"
        )
        for price_type in ("spot", "buy", "sell")
    }
    assert all(sensor is not None for sensor in sensors.values())

    spot = sensors["spot"]
    buy = sensors["buy"]
    sell = sensors["sell"]
    assert spot is not None and buy is not None and sell is not None
    for price_type, sensor in sensors.items():
        assert sensor is not None
        assert sensor.attributes["Objective"] == SearchObjective.HIGHEST
        assert sensor.attributes["Price type"] == price_type
        assert "Highest Price Block" in sensor.attributes["friendly_name"]

    assert buy.attributes["Start"] == spot.attributes["Start"]
    assert sell.attributes["Start"] == spot.attributes["Start"]
    assert buy.attributes["Mean"] == pytest.approx(spot.attributes["Mean"] + 10)
    assert sell.attributes["Mean"] == pytest.approx(spot.attributes["Mean"] - 1)


@pytest.mark.asyncio
async def test_invalid_search_objective_falls_back_to_lowest(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    """Test corrupt stored objectives remain usable as lowest-price searches."""
    await hass.config.async_set_time_zone("Europe/Prague")
    search = {
        "id": "invalid-objective",
        "name": "Invalid objective",
        "type": SearchType.TODAY,
        "length_hours": 1,
        CONF_PRICE_TYPE: "spot",
        CONF_SEARCH_OBJECTIVE: "invalid",
    }

    with freeze_time(BASE_DT):
        async_fire_time_changed(hass, BASE_DT)
        assert await init_integration(
            hass,
            [
                get_entry(
                    interval=SpotRateIntervalType.Hour,
                    cheapest_block_searches=[search],
                )
            ],
        )

    sensor = hass.states.get("binary_sensor.spot_cheapest_block_invalid_objective")
    assert sensor is not None
    assert sensor.attributes["Objective"] == SearchObjective.LOWEST
    assert sensor.attributes["Mean"] == pytest.approx(85.05)


@pytest.mark.asyncio
async def test_legacy_cheapest_block_entities_are_preserved_after_migration(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    """Test legacy cheapest block config still creates old entity IDs."""
    entry = get_entry(
        currency="EUR",
        unit="MWh",
        interval=SpotRateIntervalType.Hour,
        cheapest_blocks="2, 3",
    )
    with freeze_time(BASE_DT):
        async_fire_time_changed(hass, BASE_DT)
        assert await init_integration(hass, [entry])

    assert hass.states.get("binary_sensor.spot_electricity_is_cheapest") is not None
    assert hass.states.get("binary_sensor.buy_electricity_is_cheapest") is not None
    assert hass.states.get("binary_sensor.sell_electricity_is_cheapest") is not None
    assert (
        hass.states.get("binary_sensor.spot_electricity_is_cheapest_2_hours_block")
        is not None
    )
    assert (
        hass.states.get("binary_sensor.buy_electricity_is_cheapest_2_hours_block")
        is not None
    )
    assert (
        hass.states.get("binary_sensor.sell_electricity_is_cheapest_2_hours_block")
        is not None
    )
    assert (
        hass.states.get("binary_sensor.spot_electricity_is_cheapest_3_hours_block")
        is not None
    )
    assert hass.states.get("binary_sensor.spot_cheapest_block_today_2h") is not None
    assert hass.states.get("binary_sensor.spot_cheapest_block_tomorrow_2h") is not None

    ent_reg = er.async_get(hass)
    registry_entry = ent_reg.async_get(
        "binary_sensor.spot_electricity_is_cheapest_2_hours_block"
    )
    assert registry_entry is not None
    assert (
        registry_entry.unique_id
        == f"{entry.entry_id}_spot_electricity_is_cheapest_2_hours_block"
    )


@pytest.mark.asyncio
async def test_legacy_entity_survives_one_sibling_and_is_removed_after_both(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    """Two migrated searches jointly keep one released compatibility entity."""
    entry = get_entry(
        currency="EUR",
        interval=SpotRateIntervalType.Hour,
        cheapest_blocks="3",
    )
    with freeze_time(BASE_DT):
        async_fire_time_changed(hass, BASE_DT)
        assert await init_integration(hass, [entry])

    ent_reg = er.async_get(hass)
    unique_id = f"{entry.entry_id}_spot_electricity_is_cheapest_3_hours_block"
    entity_id = ent_reg.async_get_entity_id("binary_sensor", DOMAIN, unique_id)
    assert entity_id is not None
    ent_reg.async_update_entity(entity_id, name="My retained block")

    searches = list(entry.subentries.values())
    today_search = next(
        search for search in searches if search.data["type"] == SearchType.TODAY
    )
    tomorrow_search = next(
        search for search in searches if search.data["type"] == SearchType.TOMORROW
    )

    assert hass.config_entries.async_remove_subentry(entry, today_search.subentry_id)
    await hass.async_block_till_done()
    retained = ent_reg.async_get(entity_id)
    assert retained is not None
    assert retained.name == "My retained block"
    assert hass.states.get(entity_id) is not None

    assert hass.config_entries.async_remove_subentry(entry, tomorrow_search.subentry_id)
    await hass.async_block_till_done()
    assert ent_reg.async_get(entity_id) is None


@pytest.mark.asyncio
async def test_only_stale_released_legacy_block_sensors_are_removed(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    """Cleanup targets released legacy IDs, not unreleased search IDs."""
    search = {
        "id": "today-1",
        "name": "Today 1h",
        "type": SearchType.TODAY,
        "length_hours": 1,
        CONF_PRICE_TYPE: "buy",
    }
    entry = get_entry(
        currency="CZK",
        unit="kWh",
        interval=SpotRateIntervalType.Hour,
        cheapest_block_searches=[search],
    )
    entry.add_to_hass(hass)

    ent_reg = er.async_get(hass)
    stale_entry = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{entry.entry_id}_spot_cheapest_block_today-1",
        suggested_object_id="spot_cheapest_block_today_1h",
        config_entry=entry,
    )
    stale_price_entry = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_spot_cheapest_block_price_today-1",
        suggested_object_id="spot_cheapest_block_today_1h",
        config_entry=entry,
    )
    current_entry = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{entry.entry_id}_price_block_search_today-1",
        suggested_object_id="buy_cheapest_block_today_1h",
        config_entry=entry,
    )
    stale_legacy_entry = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{entry.entry_id}_spot_electricity_is_cheapest_3_hours_block",
        suggested_object_id="spot_electricity_is_cheapest_3_hours_block",
        config_entry=entry,
    )

    with freeze_time(BASE_DT):
        async_fire_time_changed(hass, BASE_DT)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ent_reg.async_get(stale_entry.entity_id) is not None
    assert ent_reg.async_get(stale_price_entry.entity_id) is not None
    assert ent_reg.async_get(stale_legacy_entry.entity_id) is None
    assert ent_reg.async_get(current_entry.entity_id) is not None
    assert hass.states.get(current_entry.entity_id) is not None


@pytest.mark.asyncio
async def test_time_window_search_finds_cheapest_block_inside_window(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    """Test a custom time window search chooses the cheapest block in that window."""
    await hass.config.async_set_time_zone("Europe/Prague")
    search = {
        "id": "ev-window",
        "name": "EV window",
        "type": SearchType.FIXED,
        "length_hours": 1,
        "start_time": "12:00",
        "end_time": "16:00",
        CONF_PRICE_TYPE: "spot",
    }

    with freeze_time(BASE_DT):
        async_fire_time_changed(hass, BASE_DT)
        assert await init_integration(
            hass,
            [
                get_entry(
                    currency="EUR",
                    unit="MWh",
                    interval=SpotRateIntervalType.Hour,
                    cheapest_block_searches=[search],
                ),
            ],
        )

    sensor = hass.states.get("binary_sensor.spot_cheapest_block_ev_window")
    assert sensor is not None
    assert sensor.state == "off"
    assert sensor.attributes["Search type"] == SearchType.FIXED
    assert sensor.attributes["Start"] == datetime(2025, 10, 22, 13, 0, tzinfo=PRAGUE_TZ)
    assert sensor.attributes["End"] == datetime(2025, 10, 22, 14, 0, tzinfo=PRAGUE_TZ)

    active_time = datetime(2025, 10, 22, 13, 30, tzinfo=PRAGUE_TZ)
    with freeze_time(active_time):
        async_fire_time_changed(hass, active_time)
        await hass.async_block_till_done()

    sensor = hass.states.get("binary_sensor.spot_cheapest_block_ev_window")
    assert sensor is not None
    assert sensor.state == "on"

    for boundary, expected_state in (
        (datetime(2025, 10, 22, 13, 0, tzinfo=PRAGUE_TZ), "on"),
        (datetime(2025, 10, 22, 14, 0, tzinfo=PRAGUE_TZ), "off"),
    ):
        with freeze_time(boundary):
            async_fire_time_changed(hass, boundary)
            await hass.async_block_till_done()
        sensor = hass.states.get("binary_sensor.spot_cheapest_block_ev_window")
        assert sensor is not None
        assert sensor.state == expected_state


@pytest.mark.asyncio
async def test_cross_midnight_time_window_search_can_be_active(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    """Test custom time windows can search and activate across midnight."""
    await hass.config.async_set_time_zone("Europe/Prague")
    search = {
        "id": "overnight",
        "name": "Overnight",
        "type": SearchType.FIXED,
        "length_hours": 2,
        "start_time": "22:00",
        "end_time": "06:00",
        CONF_PRICE_TYPE: "spot",
    }

    current_time = datetime(2025, 10, 21, 22, 30, tzinfo=PRAGUE_TZ)
    with freeze_time(current_time):
        async_fire_time_changed(hass, current_time)
        assert await init_integration(
            hass,
            [
                get_entry(
                    currency="EUR",
                    unit="MWh",
                    interval=SpotRateIntervalType.Hour,
                    cheapest_block_searches=[search],
                ),
            ],
        )

    sensor = hass.states.get("binary_sensor.spot_cheapest_block_overnight")
    assert sensor is not None
    start = cast(datetime, sensor.attributes["Start"])
    end = cast(datetime, sensor.attributes["End"])
    assert start < end
    assert start.date() < end.date()
    assert start.hour >= 22
    assert end.hour <= 6
    assert sensor.state == "on"


@pytest.mark.asyncio
async def test_tomorrow_search_is_plan_only_off_with_attributes(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    """Test tomorrow searches expose the plan while staying off today."""
    await hass.config.async_set_time_zone("Europe/Prague")
    search = {
        "id": "tomorrow-plan",
        "name": "Tomorrow plan",
        "type": SearchType.TOMORROW,
        "length_hours": 3,
        CONF_PRICE_TYPE: "spot",
    }

    with freeze_time(BASE_DT):
        async_fire_time_changed(hass, BASE_DT)
        assert await init_integration(
            hass,
            [
                get_entry(
                    currency="EUR",
                    unit="MWh",
                    interval=SpotRateIntervalType.Hour,
                    cheapest_block_searches=[search],
                ),
            ],
        )

    sensor = hass.states.get("binary_sensor.spot_cheapest_block_tomorrow_plan")
    assert sensor is not None
    assert sensor.state == "off"
    assert sensor.attributes["Search type"] == SearchType.TOMORROW
    assert (
        sensor.attributes["Start"].date()
        == datetime(2025, 10, 23, tzinfo=PRAGUE_TZ).date()
    )
    assert sensor.attributes["End"] > sensor.attributes["Start"]
    assert "Min" in sensor.attributes
    assert "Max" in sensor.attributes
    assert "Mean" in sensor.attributes


@pytest.mark.asyncio
async def test_changing_search_price_type_preserves_entity_identity(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    """Test changing price type does not replace the search entity."""
    search = {
        "id": "ev",
        "name": "EV",
        "type": SearchType.TODAY,
        "length_hours": 2,
        CONF_PRICE_TYPE: "buy",
    }
    entry = get_entry(
        currency="EUR",
        unit="MWh",
        interval=SpotRateIntervalType.Hour,
        cheapest_block_searches=[search],
    )
    entry.add_to_hass(hass)

    ent_reg = er.async_get(hass)
    existing_entry = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{entry.entry_id}_price_block_search_ev",
        suggested_object_id="spot_cheapest_block_ev",
        config_entry=entry,
    )

    with freeze_time(BASE_DT):
        async_fire_time_changed(hass, BASE_DT)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    registry_entry = ent_reg.async_get(existing_entry.entity_id)
    assert registry_entry is not None
    assert registry_entry.unique_id == existing_entry.unique_id
    assert hass.states.get(existing_entry.entity_id) is not None


@pytest.mark.asyncio
async def test_changing_search_objective_preserves_entity_identity(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    """Test changing objective does not replace the search entity."""
    search = {
        "id": "export",
        "name": "Export",
        "type": SearchType.TODAY,
        "length_hours": 2,
        CONF_PRICE_TYPE: "spot",
        CONF_SEARCH_OBJECTIVE: SearchObjective.HIGHEST,
    }
    entry = get_entry(
        currency="EUR",
        unit="MWh",
        interval=SpotRateIntervalType.Hour,
        cheapest_block_searches=[search],
    )
    entry.add_to_hass(hass)

    ent_reg = er.async_get(hass)
    existing_entry = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{entry.entry_id}_price_block_search_export",
        suggested_object_id="spot_cheapest_block_export",
        config_entry=entry,
    )

    with freeze_time(BASE_DT):
        async_fire_time_changed(hass, BASE_DT)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    registry_entry = ent_reg.async_get(existing_entry.entity_id)
    assert registry_entry is not None
    assert registry_entry.unique_id == existing_entry.unique_id
    assert hass.states.get(existing_entry.entity_id) is not None


@pytest.mark.asyncio
async def test_search_name_with_spaces_and_punctuation_has_stable_entity(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    """Test custom block names with common punctuation still create an entity."""
    search_name = "Boiler: Noční/EV"
    search = {
        "id": "night-ev-pump",
        "name": search_name,
        "type": SearchType.TODAY,
        "length_hours": 1,
        CONF_PRICE_TYPE: "spot",
    }

    with freeze_time(BASE_DT):
        async_fire_time_changed(hass, BASE_DT)
        assert await init_integration(
            hass,
            [
                get_entry(
                    currency="EUR",
                    unit="MWh",
                    interval=SpotRateIntervalType.Hour,
                    cheapest_block_searches=[search],
                ),
            ],
        )

    ent_reg = er.async_get(hass)
    registry_entries = [
        entry
        for entry in er.async_entries_for_config_entry(
            ent_reg, hass.config_entries.async_entries(DOMAIN)[0].entry_id
        )
        if entry.unique_id.endswith("_price_block_search_night-ev-pump")
    ]
    assert len(registry_entries) == 1
    assert registry_entries[0].entity_id == (
        f"binary_sensor.spot_cheapest_block_{slugify(search_name)}"
    )
    sensor = hass.states.get(registry_entries[0].entity_id)
    assert sensor is not None


@pytest.mark.asyncio
async def test_new_subentry_activates_without_manual_reload(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    """Adding a price-block subentry reloads its parent and creates the sensor."""
    entry = get_entry(currency="CZK", cheapest_block_searches=[])
    with freeze_time(BASE_DT):
        async_fire_time_changed(hass, BASE_DT)
        assert await init_integration(hass, [entry])

        spot_coordinator = hass.data[DOMAIN][SPOT_ELECTRICTY_COORDINATOR]
        fx_coordinator = hass.data[DOMAIN][FX_COORDINATOR]
        ote_calls = mock_ote_electricity.call_count
        cnb_calls = mock_cnb.call_count

        search = {
            "id": "added-after-setup",
            "name": "Added after setup",
            "type": SearchType.TODAY,
            "length_hours": 1,
            CONF_PRICE_TYPE: "spot",
            CONF_SEARCH_OBJECTIVE: SearchObjective.LOWEST,
        }
        assert hass.config_entries.async_add_subentry(
            entry,
            ConfigSubentry(
                data=MappingProxyType(search),
                subentry_type=PRICE_BLOCK_SUBENTRY_TYPE,
                title=str(search["name"]),
                unique_id=str(search["id"]),
            ),
        )
        await hass.async_block_till_done()

    assert hass.data[DOMAIN][SPOT_ELECTRICTY_COORDINATOR] is spot_coordinator
    assert hass.data[DOMAIN][FX_COORDINATOR] is fx_coordinator
    assert mock_ote_electricity.call_count == ote_calls
    assert mock_cnb.call_count == cnb_calls

    entity_id = er.async_get(hass).async_get_entity_id(
        "binary_sensor",
        DOMAIN,
        f"{entry.entry_id}_price_block_search_added-after-setup",
    )
    assert entity_id is not None
    assert hass.states.get(entity_id) is not None


@pytest.mark.asyncio
async def test_quick_subentry_updates_serialize_platform_reload(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    """Concurrent HA update-listener tasks must not unload twice at once."""
    entry = get_entry(currency="CZK", cheapest_block_searches=[])
    with freeze_time(BASE_DT):
        async_fire_time_changed(hass, BASE_DT)
        assert await init_integration(hass, [entry])

        original_unload = hass.config_entries.async_unload_platforms
        first_unload_started = asyncio.Event()
        allow_first_unload = asyncio.Event()
        active_unloads = 0
        max_active_unloads = 0
        unload_calls = 0

        async def tracked_unload(config_entry, platforms):
            nonlocal active_unloads, max_active_unloads, unload_calls
            unload_calls += 1
            active_unloads += 1
            max_active_unloads = max(max_active_unloads, active_unloads)
            try:
                if unload_calls == 1:
                    first_unload_started.set()
                    await allow_first_unload.wait()
                return await original_unload(config_entry, platforms)
            finally:
                active_unloads -= 1

        def add_search(search_id: str) -> None:
            search = {
                "id": search_id,
                "name": search_id,
                "type": SearchType.TODAY,
                "length_hours": 1,
                CONF_PRICE_TYPE: "spot",
            }
            assert hass.config_entries.async_add_subentry(
                entry,
                ConfigSubentry(
                    data=MappingProxyType(search),
                    subentry_type=PRICE_BLOCK_SUBENTRY_TYPE,
                    title=search_id,
                    unique_id=search_id,
                ),
            )

        ote_calls = mock_ote_electricity.call_count
        cnb_calls = mock_cnb.call_count
        with patch.object(
            hass.config_entries,
            "async_unload_platforms",
            side_effect=tracked_unload,
        ):
            add_search("first")
            await first_unload_started.wait()
            add_search("second")
            await asyncio.sleep(0)
            allow_first_unload.set()
            await hass.async_block_till_done()

    assert max_active_unloads == 1
    assert unload_calls == 2
    assert mock_ote_electricity.call_count == ote_calls
    assert mock_cnb.call_count == cnb_calls
    ent_reg = er.async_get(hass)
    for search_id in ("first", "second"):
        entity_id = ent_reg.async_get_entity_id(
            "binary_sensor",
            DOMAIN,
            f"{entry.entry_id}_price_block_search_{search_id}",
        )
        assert entity_id is not None
        assert hass.states.get(entity_id) is not None


@pytest.mark.asyncio
async def test_custom_entity_lifecycle_is_stable_and_scoped_per_entry(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    """Test safe edits preserve identity and cleanup never crosses entry boundaries."""
    search = {
        "id": "shared",
        "name": "Shared schedule",
        "type": SearchType.TODAY,
        "length_hours": 1,
        CONF_PRICE_TYPE: "spot",
    }
    entries = [
        get_entry(
            interval=SpotRateIntervalType.Hour,
            cheapest_block_searches=[dict(search)],
        )
        for _ in range(2)
    ]

    with freeze_time(BASE_DT):
        async_fire_time_changed(hass, BASE_DT)
        assert await init_integration(hass, entries)

    ent_reg = er.async_get(hass)
    unique_ids = [f"{entry.entry_id}_price_block_search_shared" for entry in entries]
    registry_entries = [
        ent_reg.async_get_entity_id("binary_sensor", DOMAIN, unique_id)
        for unique_id in unique_ids
    ]
    assert all(entity_id is not None for entity_id in registry_entries)
    assert len(set(registry_entries)) == 2

    first_entity_id = registry_entries[0]
    first_subentry = next(iter(entries[0].subentries.values()))
    edited = {
        **search,
        "name": "Renamed schedule",
        "length_hours": 2,
    }
    with freeze_time(BASE_DT):
        hass.config_entries.async_update_subentry(
            entries[0],
            first_subentry,
            data=edited,
            title=str(edited["name"]),
        )
        await hass.config_entries.async_reload(entries[0].entry_id)
        await hass.async_block_till_done()

    assert (
        ent_reg.async_get_entity_id("binary_sensor", DOMAIN, unique_ids[0])
        == first_entity_id
    )

    with freeze_time(BASE_DT):
        assert hass.config_entries.async_remove_subentry(
            entries[0], first_subentry.subentry_id
        )
        await hass.config_entries.async_reload(entries[0].entry_id)
        await hass.async_block_till_done()

    assert ent_reg.async_get_entity_id("binary_sensor", DOMAIN, unique_ids[0]) is None
    assert (
        ent_reg.async_get_entity_id("binary_sensor", DOMAIN, unique_ids[1])
        == registry_entries[1]
    )
    assert hass.states.get(cast(str, registry_entries[1])) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("mock_ote_electricity", ["today"], indirect=True)
async def test_searches_follow_template_and_tomorrow_data_availability(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    """Test template-backed searches recover while an unpublished plan stays unavailable."""
    await hass.config.async_set_time_zone("Europe/Prague")
    searches = [
        {
            "id": f"{price_type}-today",
            "name": f"{price_type.title()} today",
            "type": SearchType.TODAY,
            "length_hours": 1,
            CONF_PRICE_TYPE: price_type,
        }
        for price_type in ("buy", "sell")
    ]
    searches.append(
        {
            "id": "tomorrow-plan",
            "name": "Tomorrow plan",
            "type": SearchType.TOMORROW,
            "length_hours": 1,
            CONF_PRICE_TYPE: "spot",
        }
    )
    entry = get_entry(
        currency="EUR",
        unit="MWh",
        interval=SpotRateIntervalType.Hour,
        cheapest_block_searches=searches,
    )
    template_options = dict(entry.options)

    with freeze_time(BASE_DT):
        async_fire_time_changed(hass, BASE_DT)
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    for price_type in ("buy", "sell"):
        sensor = hass.states.get(
            f"binary_sensor.{price_type}_cheapest_block_{price_type}_today"
        )
        assert sensor is not None
        assert sensor.state != "unknown"
    tomorrow = hass.states.get("binary_sensor.spot_cheapest_block_tomorrow_plan")
    assert tomorrow is not None
    assert tomorrow.state == "unknown"

    with freeze_time(BASE_DT):
        hass.config_entries.async_update_entry(
            entry,
            options={},
        )
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    for price_type in ("buy", "sell"):
        sensor = hass.states.get(
            f"binary_sensor.{price_type}_cheapest_block_{price_type}_today"
        )
        assert sensor is not None
        assert sensor.state == "unknown"

    with freeze_time(BASE_DT):
        hass.config_entries.async_update_entry(entry, options=template_options)
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    for price_type in ("buy", "sell"):
        sensor = hass.states.get(
            f"binary_sensor.{price_type}_cheapest_block_{price_type}_today"
        )
        assert sensor is not None
        assert sensor.state != "unknown"
