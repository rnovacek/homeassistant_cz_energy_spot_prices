



from datetime import UTC, datetime, timedelta
from decimal import Decimal
import pytest
from custom_components.cz_energy_spot_prices.const import SpotRateIntervalType
from custom_components.cz_energy_spot_prices.coordinator import PRAGUE_TZ, SpotRateInterval, find_cheapest_window


BASE_DT = datetime(2025, 1, 1, 0, tzinfo=PRAGUE_TZ)

@pytest.fixture
def interval_one_value():
    interval_by_dt: dict[datetime, SpotRateInterval] = {}
    for dt, price in [
        (BASE_DT, Decimal(10)),
    ]:
        dt_utc = dt.astimezone(UTC)
        interval_by_dt[dt_utc] = SpotRateInterval(dt_utc, dt, price)

    return interval_by_dt


# 10th is the cheapest
TEST_PRICES = [
    #     60min  15min
    10,  #  0:00  0:00
    12,  #  1:00  0:15
    14,  #  2:00  0:30
    11,  #  3:00  0:45
    13,  #  4:00  1:00
    15,  #  5:00  1:15
    9,  #   6:00  1:30
    11,  #  7:00  1:45
    3,  #   8:00  2:00                                           < cheapest 3-block <
    1,  #   9:00  2:15  < cheapest interval  <                   <                  <
    2,  #  10:00  2:30                       < cheapest 2-block  <                  <
    4,  #  11:00  2:45                                                              < cheapest 4-block
    10,  # 12:00  3:00
    12,  # 13:00  3:15
    14,  # 14:00  3:30
    13,  # 15:00  3:45
    14,  # 16:00  4:00
    19,  # 17:00  4:15
    17,  # 18:00  4:30
    18,  # 19:00  4:45
    14,  # 20:00  5:00
    15,  # 21:00  5:15
    17,  # 22:00  5:30
    11,  # 23:00  5:45
]


@pytest.fixture
def interval_60mins():
    interval_by_dt: dict[datetime, SpotRateInterval] = {}
    prices = TEST_PRICES
    for i, price in enumerate(prices):
        dt = BASE_DT + timedelta(hours=i)
        dt_utc = dt.astimezone(UTC)
        interval_by_dt[dt_utc] = SpotRateInterval(dt_utc, dt, Decimal(price))

    return interval_by_dt


@pytest.fixture
def interval_15mins():
    interval_by_dt: dict[datetime, SpotRateInterval] = {}
    prices = TEST_PRICES
    for i, price in enumerate(prices):
        dt = BASE_DT + timedelta(minutes=i * 15)
        dt_utc = dt.astimezone(UTC)
        interval_by_dt[dt_utc] = SpotRateInterval(dt_utc, dt, Decimal(price))

    return interval_by_dt

def test_find_cheapest_window_no_data():
    with pytest.raises(ValueError):
        _ = find_cheapest_window({}, hours=None, interval=SpotRateIntervalType.Hour)

    with pytest.raises(ValueError):
        _ = find_cheapest_window({}, hours=2, interval=SpotRateIntervalType.Hour)

    with pytest.raises(ValueError):
        _ = find_cheapest_window({}, hours=None, interval=SpotRateIntervalType.QuarterHour)

    with pytest.raises(ValueError):
        _ = find_cheapest_window({}, hours=2, interval=SpotRateIntervalType.QuarterHour)


def test_find_cheapest_window_not_enough_data(interval_one_value: dict[datetime, SpotRateInterval]):
    with pytest.raises(ValueError):
        _ = find_cheapest_window(
            interval_by_dt=interval_one_value, hours=2, interval=SpotRateIntervalType.Hour
        )

    with pytest.raises(ValueError):
        _ = find_cheapest_window(
            interval_by_dt=interval_one_value, hours=1, interval=SpotRateIntervalType.QuarterHour
        )


@pytest.mark.parametrize('hours,interval', [
    (None, SpotRateIntervalType.Hour),
    (1, SpotRateIntervalType.Hour),
    (None, SpotRateIntervalType.QuarterHour),
])
def test_find_cheapest_window_one_interval(
    interval_one_value: dict[datetime, SpotRateInterval],
    hours: int | None,
    interval: SpotRateIntervalType,
):
    first_interval = list(interval_one_value.values())[0]

    window = find_cheapest_window(
        interval_by_dt=interval_one_value, hours=hours, interval=interval
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
        (None, [1], 9),
        (1, [1], 9),
        (2, [1, 2], 9),
        (3, [3, 1, 2], 8),
        (4, [3, 1, 2, 4], 8),
        (5, [3, 1, 2, 4, 10], 8),
        (6, [9, 11, 3, 1, 2, 4], 6),
    ),
)
def test_find_cheapest_window_60min(
    interval_60mins: dict[datetime, SpotRateInterval],
    hours: int | None,
    prices: list[Decimal],
    offset: int,
):
    window = find_cheapest_window(
        interval_by_dt=interval_60mins, hours=hours, interval=SpotRateIntervalType.Hour,
    )
    assert window.prices == prices
    assert window.start == BASE_DT + timedelta(hours=offset)
    assert window.end == BASE_DT + timedelta(hours=offset + len(prices))


@pytest.mark.parametrize(
    "hours,prices,offset",
    (
        (None, [1], 9),
        (1, [3, 1, 2, 4], 8),
        (2, [9, 11, 3, 1, 2, 4, 10, 12], 6),
    ),
)
def test_find_cheapest_window_15min(
    interval_15mins: dict[datetime, SpotRateInterval],
    hours: int | None,
    prices: list[Decimal],
    offset: int,
):
    window = find_cheapest_window(
        interval_by_dt=interval_15mins,
        hours=hours,
        interval=SpotRateIntervalType.QuarterHour,
    )
    assert window.prices == prices
    assert window.start == BASE_DT + timedelta(minutes=offset * 15)
    assert window.end == BASE_DT + timedelta(minutes=(offset + len(prices)) * 15)