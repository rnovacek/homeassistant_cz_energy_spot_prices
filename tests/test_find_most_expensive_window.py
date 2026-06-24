from datetime import UTC, datetime, timedelta
from decimal import Decimal
import pytest
from custom_components.cz_energy_spot_prices.const import SpotRateIntervalType
from custom_components.cz_energy_spot_prices.coordinator import PRAGUE_TZ, SpotRateInterval, find_cheapest_window


BASE_DT = datetime(2025, 1, 1, 0, tzinfo=PRAGUE_TZ)

# 17th is the most expensive (price 19)
TEST_PRICES = [
    #     60min  15min
    10,  #  0:00  0:00
    12,  #  1:00  0:15
    14,  #  2:00  0:30
    11,  #  3:00  0:45
    13,  #  4:00  1:00
    15,  #  5:00  1:15
    9,   #  6:00  1:30
    11,  #  7:00  1:45
    3,   #  8:00  2:00
    1,   #  9:00  2:15
    2,   # 10:00  2:30
    4,   # 11:00  2:45
    10,  # 12:00  3:00
    12,  # 13:00  3:15
    14,  # 14:00  3:30
    13,  # 15:00  3:45
    14,  # 16:00  4:00
    19,  # 17:00  4:15  < most expensive interval  <              < most expensive 3-block <
    17,  # 18:00  4:30                              < exp 2-block  <                        <
    18,  # 19:00  4:45                                             < exp 2-block (tie→first) < most expensive 4-block
    14,  # 20:00  5:00
    15,  # 21:00  5:15
    17,  # 22:00  5:30
    11,  # 23:00  5:45
]


@pytest.fixture
def interval_60mins():
    interval_by_dt: dict[datetime, SpotRateInterval] = {}
    for i, price in enumerate(TEST_PRICES):
        dt = BASE_DT + timedelta(hours=i)
        dt_utc = dt.astimezone(UTC)
        interval_by_dt[dt_utc] = SpotRateInterval(dt_utc, dt, Decimal(price))
    return interval_by_dt


@pytest.fixture
def interval_15mins():
    interval_by_dt: dict[datetime, SpotRateInterval] = {}
    for i, price in enumerate(TEST_PRICES):
        dt = BASE_DT + timedelta(minutes=i * 15)
        dt_utc = dt.astimezone(UTC)
        interval_by_dt[dt_utc] = SpotRateInterval(dt_utc, dt, Decimal(price))
    return interval_by_dt


@pytest.fixture
def interval_one_value():
    interval_by_dt: dict[datetime, SpotRateInterval] = {}
    dt = BASE_DT
    dt_utc = dt.astimezone(UTC)
    interval_by_dt[dt_utc] = SpotRateInterval(dt_utc, dt, Decimal(10))
    return interval_by_dt


def test_find_most_expensive_window_no_data():
    with pytest.raises(ValueError):
        find_cheapest_window({}, hours=None, interval=SpotRateIntervalType.Hour, maximize=True)

    with pytest.raises(ValueError):
        find_cheapest_window({}, hours=2, interval=SpotRateIntervalType.Hour, maximize=True)

    with pytest.raises(ValueError):
        find_cheapest_window({}, hours=None, interval=SpotRateIntervalType.QuarterHour, maximize=True)

    with pytest.raises(ValueError):
        find_cheapest_window({}, hours=2, interval=SpotRateIntervalType.QuarterHour, maximize=True)


def test_find_most_expensive_window_not_enough_data(interval_one_value):
    with pytest.raises(ValueError):
        find_cheapest_window(
            interval_by_dt=interval_one_value, hours=2, interval=SpotRateIntervalType.Hour, maximize=True
        )

    with pytest.raises(ValueError):
        find_cheapest_window(
            interval_by_dt=interval_one_value, hours=1, interval=SpotRateIntervalType.QuarterHour, maximize=True
        )


@pytest.mark.parametrize("hours,interval", [
    (None, SpotRateIntervalType.Hour),
    (1, SpotRateIntervalType.Hour),
    (None, SpotRateIntervalType.QuarterHour),
])
def test_find_most_expensive_window_one_interval(interval_one_value, hours, interval):
    first_interval = list(interval_one_value.values())[0]
    window = find_cheapest_window(
        interval_by_dt=interval_one_value, hours=hours, interval=interval, maximize=True
    )
    assert window.prices == [Decimal(10)]
    assert window.start == first_interval.dt_utc
    if interval == SpotRateIntervalType.Hour:
        assert window.end == first_interval.dt_utc + timedelta(hours=1)
    else:
        assert window.end == first_interval.dt_utc + timedelta(minutes=15)


@pytest.mark.parametrize(
    "hours,prices,offset",
    (
        # Sums for reference (prices at indices):
        # 17: 19, 18: 17, 19: 18, 16: 14, 15: 13, 20: 14, 21: 15, 22: 17
        (None, [19], 17),
        (1,    [19], 17),
        # 2: [17..18]=36, [18..19]=35 → offset 17
        (2,    [19, 17], 17),
        # 3: [17..19]=54 wins
        (3,    [19, 17, 18], 17),
        # 4: [16..19]=68, [17..20]=68 → first wins at offset 16
        (4,    [14, 19, 17, 18], 16),
        # 5: [17..21]=83 vs [16..20]=82
        (5,    [19, 17, 18, 14, 15], 17),
        # 6: [17..22]=100 wins
        (6,    [19, 17, 18, 14, 15, 17], 17),
    ),
)
def test_find_most_expensive_window_60min(interval_60mins, hours, prices, offset):
    window = find_cheapest_window(
        interval_by_dt=interval_60mins,
        hours=hours,
        interval=SpotRateIntervalType.Hour,
        maximize=True,
    )
    assert window.prices == prices
    assert window.start == BASE_DT + timedelta(hours=offset)
    assert window.end == BASE_DT + timedelta(hours=offset + len(prices))


@pytest.mark.parametrize(
    "hours,prices,offset",
    (
        (None, [19], 17),
        # 1h = 4 slots: [16..19]=68, [17..20]=68 → first at offset 16
        (1,    [14, 19, 17, 18], 16),
        # 2h = 8 slots: [15..22]=127 wins
        (2,    [13, 14, 19, 17, 18, 14, 15, 17], 15),
    ),
)
def test_find_most_expensive_window_15min(interval_15mins, hours, prices, offset):
    window = find_cheapest_window(
        interval_by_dt=interval_15mins,
        hours=hours,
        interval=SpotRateIntervalType.QuarterHour,
        maximize=True,
    )
    assert window.prices == prices
    assert window.start == BASE_DT + timedelta(minutes=offset * 15)
    assert window.end == BASE_DT + timedelta(minutes=(offset + len(prices)) * 15)
