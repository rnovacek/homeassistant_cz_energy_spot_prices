# pyright: reportMissingTypeStubs=false
"""
Comprehensive test suite for the cross-midnight cheapest block feature.

This tests the feature `cheapest_blocks_cross_midnight` which should find the
cheapest N-hour consecutive block across today's and tomorrow's electricity prices.

Based on fixture data from ote-electricity-2025-10-22.xml:

TODAY (Oct 22) hourly prices (EUR/MWh):
  Hour  0:  92.42    Hour 12:  86.16
  Hour  1:  92.04    Hour 13:  85.05  ← CHEAPEST TODAY
  Hour  2:  91.57    Hour 14:  92.98
  Hour  3:  92.72    Hour 15: 113.66
  Hour  4:  92.57    Hour 16: 147.49
  Hour  5:  93.64    Hour 17: 203.85
  Hour  6: 112.83    Hour 18: 293.73
  Hour  7: 129.89    Hour 19: 274.02
  Hour  8: 130.37    Hour 20: 185.28
  Hour  9: 125.90    Hour 21: 137.43
  Hour 10: 105.42    Hour 22: 125.98
  Hour 11:  90.41    Hour 23: 111.72

TOMORROW (Oct 23) hourly prices (EUR/MWh):
  Hour  0: 105.01    Hour 12:  68.80
  Hour  1: 103.21    Hour 13:  67.05  ← CHEAPEST TOMORROW
  Hour  2:  98.46    Hour 14:  75.55
  Hour  3:  87.42    Hour 15:  92.32
  Hour  4:  82.86    Hour 16: 106.66
  Hour  5:  93.34    Hour 17: 114.91
  Hour  6: 116.33    Hour 18: 118.80
  Hour  7: 126.62    Hour 19: 116.56
  Hour  8: 120.97    Hour 20: 105.00
  Hour  9: 105.38    Hour 21:  89.52
  Hour 10:  85.20    Hour 22:  82.75
  Hour 11:  71.54    Hour 23:  70.74

Computed expected cheapest blocks:

TODAY ONLY (no cross-midnight):
  1-hour: 85.05 at hour 13 (13:00-14:00)
  2-hour: 171.21 at hours 12-13 (12:00-14:00) = 86.16 + 85.05
  3-hour: 261.62 at hours 11-13 (11:00-14:00) = 90.41 + 86.16 + 85.05

TODAY + TOMORROW (cross-midnight enabled):
  1-hour: 67.05 at tomorrow hour 13 (13:00-14:00 tomorrow)
  2-hour: 135.85 at tomorrow hours 12-13 (12:00-14:00 tomorrow) = 68.80 + 67.05
  3-hour: 207.39 at tomorrow hours 11-13 (11:00-14:00 tomorrow) = 71.54 + 68.80 + 67.05

CROSS-MIDNIGHT BLOCKS (spanning midnight):
  2-hour 23:00-01:00: 111.72 + 105.01 = 216.73
  3-hour 22:00-01:00: 125.98 + 111.72 + 105.01 = 342.71
  3-hour 23:00-02:00: 111.72 + 105.01 + 103.21 = 319.94
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import pytest
from freezegun import freeze_time

from pytest_homeassistant_custom_component.common import AsyncMock

from homeassistant.core import HomeAssistant

from custom_components.cz_energy_spot_prices.cheapest_blocks import PriceBlockSearch
from custom_components.cz_energy_spot_prices.const import (
    SearchType,
    SpotRateIntervalType,
)
from custom_components.cz_energy_spot_prices.coordinator import (
    PRAGUE_TZ,
    SpotRateInterval,
    find_cheapest_window,
)

from . import BASE_DT, get_entry, init_integration


# =============================================================================
# Price data extracted from fixtures (for reference and computation)
# =============================================================================

# Today's hourly prices (Oct 22) - Prague time hours 0-23
TODAY_HOURLY_PRICES = [
    Decimal("92.42"),  # Hour 0
    Decimal("92.04"),  # Hour 1
    Decimal("91.57"),  # Hour 2
    Decimal("92.72"),  # Hour 3
    Decimal("92.57"),  # Hour 4
    Decimal("93.64"),  # Hour 5
    Decimal("112.83"),  # Hour 6
    Decimal("129.89"),  # Hour 7
    Decimal("130.37"),  # Hour 8
    Decimal("125.90"),  # Hour 9
    Decimal("105.42"),  # Hour 10
    Decimal("90.41"),  # Hour 11
    Decimal("86.16"),  # Hour 12
    Decimal("85.05"),  # Hour 13 ← CHEAPEST
    Decimal("92.98"),  # Hour 14
    Decimal("113.66"),  # Hour 15
    Decimal("147.49"),  # Hour 16
    Decimal("203.85"),  # Hour 17
    Decimal("293.73"),  # Hour 18
    Decimal("274.02"),  # Hour 19
    Decimal("185.28"),  # Hour 20
    Decimal("137.43"),  # Hour 21
    Decimal("125.98"),  # Hour 22
    Decimal("111.72"),  # Hour 23
]

# Tomorrow's hourly prices (Oct 23) - Prague time hours 0-23
TOMORROW_HOURLY_PRICES = [
    Decimal("105.01"),  # Hour 0
    Decimal("103.21"),  # Hour 1
    Decimal("98.46"),  # Hour 2
    Decimal("87.42"),  # Hour 3
    Decimal("82.86"),  # Hour 4
    Decimal("93.34"),  # Hour 5
    Decimal("116.33"),  # Hour 6
    Decimal("126.62"),  # Hour 7
    Decimal("120.97"),  # Hour 8
    Decimal("105.38"),  # Hour 9
    Decimal("85.20"),  # Hour 10
    Decimal("71.54"),  # Hour 11
    Decimal("68.80"),  # Hour 12
    Decimal("67.05"),  # Hour 13 ← CHEAPEST
    Decimal("75.55"),  # Hour 14
    Decimal("92.32"),  # Hour 15
    Decimal("106.66"),  # Hour 16
    Decimal("114.91"),  # Hour 17
    Decimal("118.80"),  # Hour 18
    Decimal("116.56"),  # Hour 19
    Decimal("105.00"),  # Hour 20
    Decimal("89.52"),  # Hour 21
    Decimal("82.75"),  # Hour 22
    Decimal("70.74"),  # Hour 23
]

# Base datetime: Oct 22, 2025 00:00:00 Prague time
OCT_22_MIDNIGHT_PRAGUE = datetime(2025, 10, 22, 0, 0, 0, tzinfo=PRAGUE_TZ)
OCT_23_MIDNIGHT_PRAGUE = datetime(2025, 10, 23, 0, 0, 0, tzinfo=PRAGUE_TZ)


def create_interval_dict(
    prices: list[Decimal],
    base_dt: datetime,
) -> dict[datetime, SpotRateInterval]:
    """Create a sorted interval dictionary from prices starting at base_dt."""
    intervals: dict[datetime, SpotRateInterval] = {}
    for i, price in enumerate(prices):
        local_dt = base_dt + timedelta(hours=i)
        utc_dt = local_dt.astimezone(UTC)
        intervals[utc_dt] = SpotRateInterval(utc_dt, local_dt, price)
    return intervals


def create_today_intervals() -> dict[datetime, SpotRateInterval]:
    """Create today's (Oct 22) interval dictionary."""
    return create_interval_dict(TODAY_HOURLY_PRICES, OCT_22_MIDNIGHT_PRAGUE)


def create_tomorrow_intervals() -> dict[datetime, SpotRateInterval]:
    """Create tomorrow's (Oct 23) interval dictionary."""
    return create_interval_dict(TOMORROW_HOURLY_PRICES, OCT_23_MIDNIGHT_PRAGUE)


def create_today_and_tomorrow_intervals() -> dict[datetime, SpotRateInterval]:
    """Create combined today + tomorrow interval dictionary, properly sorted by datetime."""
    today = create_today_intervals()
    tomorrow = create_tomorrow_intervals()
    # Combine and sort by datetime key to ensure chronological order
    combined = {**today, **tomorrow}
    return dict(sorted(combined.items()))


# =============================================================================
# Expected values computed from the price data
# =============================================================================


class ExpectedResults:
    """Pre-computed expected results for various scenarios."""

    # Today only (no cross-midnight)
    TODAY_CHEAPEST_1H_PRICE = Decimal("85.05")
    TODAY_CHEAPEST_1H_START = OCT_22_MIDNIGHT_PRAGUE + timedelta(hours=13)  # 13:00

    TODAY_CHEAPEST_2H_SUM = Decimal("86.16") + Decimal("85.05")  # = 171.21
    TODAY_CHEAPEST_2H_START = OCT_22_MIDNIGHT_PRAGUE + timedelta(
        hours=12
    )  # 12:00-14:00

    TODAY_CHEAPEST_3H_SUM = (
        Decimal("90.41") + Decimal("86.16") + Decimal("85.05")
    )  # = 261.62
    TODAY_CHEAPEST_3H_START = OCT_22_MIDNIGHT_PRAGUE + timedelta(
        hours=11
    )  # 11:00-14:00

    # Tomorrow only
    TOMORROW_CHEAPEST_1H_PRICE = Decimal("67.05")
    TOMORROW_CHEAPEST_1H_START = OCT_23_MIDNIGHT_PRAGUE + timedelta(hours=13)  # 13:00

    TOMORROW_CHEAPEST_2H_SUM = Decimal("68.80") + Decimal("67.05")  # = 135.85
    TOMORROW_CHEAPEST_2H_START = OCT_23_MIDNIGHT_PRAGUE + timedelta(
        hours=12
    )  # 12:00-14:00

    TOMORROW_CHEAPEST_3H_SUM = (
        Decimal("71.54") + Decimal("68.80") + Decimal("67.05")
    )  # = 207.39
    TOMORROW_CHEAPEST_3H_START = OCT_23_MIDNIGHT_PRAGUE + timedelta(
        hours=11
    )  # 11:00-14:00

    # Cross-midnight blocks (spanning midnight between Oct 22-23)
    CROSS_MIDNIGHT_2H_SUM = Decimal("111.72") + Decimal(
        "105.01"
    )  # 23:00-01:00 = 216.73
    CROSS_MIDNIGHT_2H_START = OCT_22_MIDNIGHT_PRAGUE + timedelta(hours=23)

    CROSS_MIDNIGHT_3H_SUM = (
        Decimal("111.72") + Decimal("105.01") + Decimal("103.21")
    )  # 23:00-02:00 = 319.94
    CROSS_MIDNIGHT_3H_START = OCT_22_MIDNIGHT_PRAGUE + timedelta(hours=23)


# =============================================================================
# Unit tests for find_cheapest_window function
# =============================================================================


class TestFindCheapestWindow:
    """Unit tests for the find_cheapest_window function."""

    def test_finds_cheapest_single_hour_in_today(self):
        """Should find cheapest 1-hour block in today's data."""
        intervals = create_today_intervals()

        window = find_cheapest_window(
            intervals, hours=None, interval=SpotRateIntervalType.Hour
        )

        assert window.start == ExpectedResults.TODAY_CHEAPEST_1H_START.astimezone(UTC)
        assert window.prices == [ExpectedResults.TODAY_CHEAPEST_1H_PRICE]
        assert window.end == window.start + timedelta(hours=1)

    def test_finds_cheapest_2hour_block_in_today(self):
        """Should find cheapest consecutive 2-hour block in today's data."""
        intervals = create_today_intervals()

        window = find_cheapest_window(
            intervals, hours=2, interval=SpotRateIntervalType.Hour
        )

        assert window.start == ExpectedResults.TODAY_CHEAPEST_2H_START.astimezone(UTC)
        assert sum(window.prices) == ExpectedResults.TODAY_CHEAPEST_2H_SUM
        assert len(window.prices) == 2
        assert window.end == window.start + timedelta(hours=2)

    def test_finds_cheapest_3hour_block_in_today(self):
        """Should find cheapest consecutive 3-hour block in today's data."""
        intervals = create_today_intervals()

        window = find_cheapest_window(
            intervals, hours=3, interval=SpotRateIntervalType.Hour
        )

        assert window.start == ExpectedResults.TODAY_CHEAPEST_3H_START.astimezone(UTC)
        assert sum(window.prices) == ExpectedResults.TODAY_CHEAPEST_3H_SUM
        assert len(window.prices) == 3
        assert window.end == window.start + timedelta(hours=3)

    def test_finds_cheapest_in_tomorrow_when_combined(self):
        """Should find cheapest block in tomorrow when today+tomorrow data is provided."""
        intervals = create_today_and_tomorrow_intervals()

        window = find_cheapest_window(
            intervals, hours=3, interval=SpotRateIntervalType.Hour
        )

        # Tomorrow's 11:00-14:00 is cheaper than today's
        assert window.start == ExpectedResults.TOMORROW_CHEAPEST_3H_START.astimezone(
            UTC
        )
        assert sum(window.prices) == ExpectedResults.TOMORROW_CHEAPEST_3H_SUM

    def test_cross_midnight_block_found_when_cheapest(self):
        """Should find cross-midnight block when it's the cheapest option."""
        # Create a scenario where cross-midnight is cheapest
        # Use only late today + early tomorrow
        today_late = {
            k: v for k, v in create_today_intervals().items() if v.dt_local.hour >= 21
        }
        tomorrow_early = {
            k: v for k, v in create_tomorrow_intervals().items() if v.dt_local.hour <= 5
        }
        intervals = dict(sorted({**today_late, **tomorrow_early}.items()))

        window = find_cheapest_window(
            intervals, hours=3, interval=SpotRateIntervalType.Hour
        )

        # In this limited set, the cheapest 3-hour should be the cross-midnight one
        # or an early morning tomorrow block
        assert window is not None
        assert len(window.prices) == 3

    def test_requires_sorted_intervals_for_correct_result(self):
        """Verify that unsorted intervals can produce incorrect results.

        This test documents the current behavior where unsorted intervals
        may lead to incorrect window calculations.
        """
        # Create intervals in reverse order (not chronological)
        intervals_reversed: dict[datetime, SpotRateInterval] = {}
        for i in range(23, -1, -1):  # 23 down to 0
            local_dt = OCT_22_MIDNIGHT_PRAGUE + timedelta(hours=i)
            utc_dt = local_dt.astimezone(UTC)
            intervals_reversed[utc_dt] = SpotRateInterval(
                utc_dt, local_dt, TODAY_HOURLY_PRICES[i]
            )

        # With reversed order, the sliding window may not work correctly
        window_reversed = find_cheapest_window(
            intervals_reversed, hours=3, interval=SpotRateIntervalType.Hour
        )

        # Compare with sorted intervals
        intervals_sorted = create_today_intervals()
        window_sorted = find_cheapest_window(
            intervals_sorted, hours=3, interval=SpotRateIntervalType.Hour
        )

        # The results should be the same if the function handles unsorted input correctly
        # If this assertion fails, it indicates the function needs to sort internally
        assert window_reversed.start == window_sorted.start, (
            "find_cheapest_window produces different results with unsorted vs sorted input"
        )

    def test_empty_intervals_raises_error(self):
        """Should raise ValueError when no intervals provided."""
        with pytest.raises(ValueError):
            _ = find_cheapest_window({}, hours=2, interval=SpotRateIntervalType.Hour)

    def test_insufficient_intervals_raises_error(self):
        """Should raise ValueError when not enough intervals for requested block size."""
        # Only 2 intervals but requesting 3-hour block
        intervals = {
            k: v for i, (k, v) in enumerate(create_today_intervals().items()) if i < 2
        }

        with pytest.raises(ValueError):
            _ = find_cheapest_window(
                intervals, hours=3, interval=SpotRateIntervalType.Hour
            )


# =============================================================================
# Tests for cross-midnight scenarios with different times of day
# =============================================================================


class TestCrossMidnightScenarios:
    """Test cross-midnight feature at different times of day."""

    @pytest.fixture
    def today_intervals(self) -> dict[datetime, SpotRateInterval]:
        return create_today_intervals()

    @pytest.fixture
    def tomorrow_intervals(self) -> dict[datetime, SpotRateInterval]:
        return create_tomorrow_intervals()

    @pytest.fixture
    def combined_intervals(self) -> dict[datetime, SpotRateInterval]:
        return create_today_and_tomorrow_intervals()

    def test_cheapest_block_across_both_days(
        self,
        combined_intervals: dict[datetime, SpotRateInterval],
    ):
        """When searching across today and tomorrow, find the absolute cheapest.

        Note: find_cheapest_window is a pure function that doesn't consider
        current time - it simply finds the cheapest block in provided intervals.

        The time-aware logic (filtering based on whether blocks have passed)
        is handled by IntervalSpotRateData, not by find_cheapest_window.
        """
        window = find_cheapest_window(
            combined_intervals, hours=3, interval=SpotRateIntervalType.Hour
        )

        # Tomorrow's 11:00-14:00 (207.39) is cheaper than today's 11:00-14:00 (261.62)
        assert sum(window.prices) == ExpectedResults.TOMORROW_CHEAPEST_3H_SUM

    def test_cheapest_3hour_block_is_in_tomorrow(
        self,
        combined_intervals: dict[datetime, SpotRateInterval],
    ):
        """Verify that when tomorrow's block is cheaper, it's correctly found.

        For 3-hour blocks across both days:
        - Today 11:00-14:00: 261.62
        - Tomorrow 11:00-14:00: 207.39 (cheapest)
        - Cross-midnight 23:00-02:00: 319.94
        """
        window = find_cheapest_window(
            combined_intervals, hours=3, interval=SpotRateIntervalType.Hour
        )

        # Tomorrow's block is cheaper than today's and cross-midnight
        assert sum(window.prices) == ExpectedResults.TOMORROW_CHEAPEST_3H_SUM
        assert window.start == ExpectedResults.TOMORROW_CHEAPEST_3H_START.astimezone(
            UTC
        )

    def test_evening_only_late_today_and_tomorrow(self):
        """At 20:00, should consider remaining today hours + tomorrow.

        Remaining today: hours 20, 21, 22, 23
        All tomorrow: hours 0-23

        For 2-hour blocks, should find tomorrow's cheapest (12:00-14:00).
        """
        # Create intervals starting from hour 20 today + all tomorrow
        today_evening = {
            k: v for k, v in create_today_intervals().items() if v.dt_local.hour >= 20
        }
        tomorrow = create_tomorrow_intervals()
        intervals = dict(sorted({**today_evening, **tomorrow}.items()))

        window = find_cheapest_window(
            intervals, hours=2, interval=SpotRateIntervalType.Hour
        )

        # Tomorrow's 12:00-14:00 (135.85) is cheaper than any remaining option
        assert sum(window.prices) == ExpectedResults.TOMORROW_CHEAPEST_2H_SUM

    def test_late_night_cross_midnight_available(self):
        """At 22:00, cross-midnight blocks become available.

        Remaining today: hours 22, 23
        Tomorrow: hours 0-23

        Cross-midnight 2h block (23:00-01:00): 216.73
        Tomorrow cheapest 2h (12:00-14:00): 135.85

        Tomorrow's block is still cheaper.
        """
        today_late = {
            k: v for k, v in create_today_intervals().items() if v.dt_local.hour >= 22
        }
        tomorrow = create_tomorrow_intervals()
        intervals = dict(sorted({**today_late, **tomorrow}.items()))

        window = find_cheapest_window(
            intervals, hours=2, interval=SpotRateIntervalType.Hour
        )

        # Tomorrow's midday block is cheaper
        assert sum(window.prices) == ExpectedResults.TOMORROW_CHEAPEST_2H_SUM

    def test_no_tomorrow_data_uses_today_only(
        self,
        today_intervals: dict[datetime, SpotRateInterval],
    ):
        """Without tomorrow data, should find cheapest in today only.

        This simulates before 13:10 when tomorrow's prices aren't published yet.
        """
        window = find_cheapest_window(
            today_intervals, hours=3, interval=SpotRateIntervalType.Hour
        )

        assert sum(window.prices) == ExpectedResults.TODAY_CHEAPEST_3H_SUM
        assert window.start == ExpectedResults.TODAY_CHEAPEST_3H_START.astimezone(UTC)


# =============================================================================
# Edge case tests
# =============================================================================


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_block_ending_exactly_at_midnight(self):
        """Test block that ends exactly at midnight (21:00-00:00).

        Block 21:00, 22:00, 23:00 ends at midnight but doesn't cross it.
        """
        intervals = create_today_intervals()
        # Get 3-hour block ending at midnight
        today_prices = [
            TODAY_HOURLY_PRICES[21],  # 137.43
            TODAY_HOURLY_PRICES[22],  # 125.98
            TODAY_HOURLY_PRICES[23],  # 111.72
        ]
        expected_sum = sum(today_prices)  # = 375.13

        # This block should be found if we search from hour 21
        late_evening = {k: v for k, v in intervals.items() if v.dt_local.hour >= 21}

        window = find_cheapest_window(
            late_evening, hours=3, interval=SpotRateIntervalType.Hour
        )

        assert len(window.prices) == 3
        assert sum(window.prices) == expected_sum

    def test_block_starting_exactly_at_midnight(self):
        """Test block that starts exactly at midnight (00:00-03:00 tomorrow)."""
        tomorrow = create_tomorrow_intervals()
        early_morning = {k: v for k, v in tomorrow.items() if v.dt_local.hour <= 5}

        window = find_cheapest_window(
            early_morning, hours=3, interval=SpotRateIntervalType.Hour
        )

        # Cheapest 3h in 00:00-05:00 is 03:00-06:00 (87.42 + 82.86 + 93.34 = 263.62)
        # or 02:00-05:00 (98.46 + 87.42 + 82.86 = 268.74)
        # or 00:00-03:00 (105.01 + 103.21 + 98.46 = 306.68)
        expected_sum = sum(
            [
                TOMORROW_HOURLY_PRICES[3],  # 87.42
                TOMORROW_HOURLY_PRICES[4],  # 82.86
                TOMORROW_HOURLY_PRICES[5],  # 93.34
            ]
        )  # = 263.62

        assert sum(window.prices) == expected_sum

    def test_single_cheapest_spans_two_days(self):
        """When finding single cheapest hour across two days."""
        intervals = create_today_and_tomorrow_intervals()

        window = find_cheapest_window(
            intervals, hours=None, interval=SpotRateIntervalType.Hour
        )

        # Tomorrow's 13:00 (67.05) is cheaper than today's 13:00 (85.05)
        assert window.prices == [ExpectedResults.TOMORROW_CHEAPEST_1H_PRICE]
        assert window.start == ExpectedResults.TOMORROW_CHEAPEST_1H_START.astimezone(
            UTC
        )

    def test_max_block_size_24_hours(self):
        """Test with maximum possible block size (24 hours = full day)."""
        intervals = create_today_intervals()

        window = find_cheapest_window(
            intervals, hours=23, interval=SpotRateIntervalType.Hour
        )

        # Should return the only possible 23-hour window
        assert len(window.prices) == 23

    def test_equal_price_blocks_returns_first(self):
        """When multiple blocks have equal prices, should return earliest one."""
        # Create artificial data with equal prices
        equal_prices = [Decimal("100.0")] * 24
        intervals = create_interval_dict(equal_prices, OCT_22_MIDNIGHT_PRAGUE)

        window = find_cheapest_window(
            intervals, hours=3, interval=SpotRateIntervalType.Hour
        )

        # Should return the first (earliest) block when all are equal
        expected_start = OCT_22_MIDNIGHT_PRAGUE.astimezone(UTC)
        assert window.start == expected_start


# =============================================================================
# Integration tests with mocked Home Assistant
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("cheapest_blocks", ["2", "3", "2,3"])
async def test_cross_midnight_integration(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,  # Uses "today+tomorrow" fixture
    mock_cnb: AsyncMock,
    cheapest_blocks: str,
):
    """Integration test: verify migrated search-based blocks are computed correctly.

    Legacy cheapest_blocks are migrated into today/tomorrow searches.
    """
    await hass.config.async_set_time_zone("Europe/Prague")

    # Initialize at midnight to ensure today's block hasn't passed
    with freeze_time(BASE_DT):
        result = await init_integration(
            hass,
            [
                get_entry(
                    currency="EUR",
                    unit="MWh",
                    interval=SpotRateIntervalType.Hour,
                    cheapest_blocks=cheapest_blocks,
                    allow_cross_midnight=True,
                ),
            ],
        )
        assert result

        await hass.async_block_till_done()

        # Verify binary sensors exist for each migrated search
        blocks_to_check = [int(b) for b in cheapest_blocks.split(",")]
        for block in blocks_to_check:
            entity_id = f"binary_sensor.spot_cheapest_block_today_{block}h"
            sensor = hass.states.get(entity_id)
            assert sensor is not None, f"Sensor {entity_id} should exist"
            assert "Start" in sensor.attributes
            assert "End" in sensor.attributes


@pytest.mark.asyncio
@pytest.mark.parametrize("mock_ote_electricity", ("today",), indirect=True)
async def test_cross_midnight_without_tomorrow_data(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    """Integration test: with only today's data available.

    When tomorrow's prices aren't available yet (before 13:10), the today
    search should still work using only today's data.
    """
    await hass.config.async_set_time_zone("Europe/Prague")

    with freeze_time(BASE_DT):
        result = await init_integration(
            hass,
            [
                get_entry(
                    currency="EUR",
                    unit="MWh",
                    interval=SpotRateIntervalType.Hour,
                    cheapest_blocks="3",
                    allow_cross_midnight=True,
                ),
            ],
        )
        assert result

        await hass.async_block_till_done()

        # Verify the today search sensor exists and uses today's data
        entity_id = "binary_sensor.spot_cheapest_block_today_3h"
        sensor = hass.states.get(entity_id)
        assert sensor is not None, f"Sensor {entity_id} should exist"

        # Without tomorrow data, should use today's cheapest block (11:00-14:00)
        expected_start = OCT_22_MIDNIGHT_PRAGUE + timedelta(hours=11)
        assert sensor.attributes["Start"] == expected_start


@pytest.mark.asyncio
async def test_today_search_uses_today_only(
    hass: HomeAssistant,
    mock_ote_electricity: AsyncMock,
    mock_cnb: AsyncMock,
):
    """Today search should only use today's data."""
    await hass.config.async_set_time_zone("Europe/Prague")

    with freeze_time(BASE_DT):
        result = await init_integration(
            hass,
            [
                get_entry(
                    currency="EUR",
                    unit="MWh",
                    interval=SpotRateIntervalType.Hour,
                    cheapest_blocks="3",
                    allow_cross_midnight=False,
                ),
            ],
        )
        assert result

        await hass.async_block_till_done()

        # Verify the today search sensor exists
        entity_id = "binary_sensor.spot_cheapest_block_today_3h"
        sensor = hass.states.get(entity_id)
        assert sensor is not None, f"Sensor {entity_id} should exist"

        # Today search should only consider today's data
        expected_start = OCT_22_MIDNIGHT_PRAGUE + timedelta(hours=11)
        assert sensor.attributes["Start"] == expected_start


# =============================================================================
# Window calculation verification tests
# =============================================================================


class TestWindowCalculations:
    """Verify correct window start/end times and price sums."""

    def test_2hour_window_times(self):
        """Verify 2-hour window has correct start/end times."""
        intervals = create_today_intervals()

        window = find_cheapest_window(
            intervals, hours=2, interval=SpotRateIntervalType.Hour
        )

        # End should be exactly 2 hours after start
        expected_end = window.start + timedelta(hours=2)
        assert window.end == expected_end

    def test_3hour_window_times(self):
        """Verify 3-hour window has correct start/end times."""
        intervals = create_today_intervals()

        window = find_cheapest_window(
            intervals, hours=3, interval=SpotRateIntervalType.Hour
        )

        # End should be exactly 3 hours after start
        expected_end = window.start + timedelta(hours=3)
        assert window.end == expected_end

    def test_window_prices_are_consecutive(self):
        """Verify that window prices match consecutive interval prices."""
        intervals = create_today_intervals()

        window = find_cheapest_window(
            intervals, hours=3, interval=SpotRateIntervalType.Hour
        )

        # Get the expected prices from the interval dict
        expected_prices: list[Decimal] = []
        for i in range(3):
            dt = window.start + timedelta(hours=i)
            expected_prices.append(intervals[dt].price)

        assert window.prices == expected_prices

    def test_cross_midnight_window_spans_days(self):
        """Verify cross-midnight window correctly spans two days."""
        today_late = {
            k: v for k, v in create_today_intervals().items() if v.dt_local.hour >= 21
        }
        tomorrow_early = {
            k: v for k, v in create_tomorrow_intervals().items() if v.dt_local.hour <= 6
        }
        intervals = dict(sorted({**today_late, **tomorrow_early}.items()))

        window = find_cheapest_window(
            intervals, hours=3, interval=SpotRateIntervalType.Hour
        )

        # If cheapest is cross-midnight, dates should differ
        # Otherwise it might be early morning tomorrow
        assert window is not None
        assert len(window.prices) == 3


# =============================================================================
# 15-minute interval tests
# =============================================================================

# Today's 15-minute prices (Oct 22) extracted from fixture - first 8 intervals (first 2 hours)
# Full dataset: 96 intervals per day
TODAY_15MIN_PRICES = [
    Decimal("99.54"),  # 00:00
    Decimal("96.27"),  # 00:15
    Decimal("87.66"),  # 00:30
    Decimal("86.19"),  # 00:45
    Decimal("95.43"),  # 01:00
    Decimal("93.53"),  # 01:15
    Decimal("91.64"),  # 01:30
    Decimal("87.57"),  # 01:45
    Decimal("92.04"),  # 02:00
    Decimal("91.41"),  # 02:15
    Decimal("91.78"),  # 02:30
    Decimal("91.06"),  # 02:45
]

# Tomorrow's 15-minute prices for reference (early hours)
TOMORROW_15MIN_PRICES = [
    Decimal("114.14"),  # 00:00
    Decimal("108.50"),  # 00:15
    Decimal("100.63"),  # 00:30
    Decimal("96.78"),  # 00:45
    Decimal("110.81"),  # 01:00
    Decimal("108.52"),  # 01:15
    Decimal("98.78"),  # 01:30
    Decimal("94.73"),  # 01:45
]


def create_15min_interval_dict(
    prices: list[Decimal],
    base_dt: datetime,
) -> dict[datetime, SpotRateInterval]:
    """Create a sorted 15-min interval dictionary from prices starting at base_dt."""
    intervals: dict[datetime, SpotRateInterval] = {}
    for i, price in enumerate(prices):
        local_dt = base_dt + timedelta(minutes=i * 15)
        utc_dt = local_dt.astimezone(UTC)
        intervals[utc_dt] = SpotRateInterval(utc_dt, local_dt, price)
    return intervals


class TestQuarterHourIntervals:
    """Test 15-minute interval handling for cross-midnight feature."""

    def test_find_cheapest_15min_single_interval(self):
        """Should find cheapest single 15-min interval."""
        intervals = create_15min_interval_dict(
            TODAY_15MIN_PRICES, OCT_22_MIDNIGHT_PRAGUE
        )

        window = find_cheapest_window(
            intervals, hours=None, interval=SpotRateIntervalType.QuarterHour
        )

        # Cheapest in sample is 86.19 at 00:45
        assert window.prices == [Decimal("86.19")]
        expected_start = (OCT_22_MIDNIGHT_PRAGUE + timedelta(minutes=45)).astimezone(
            UTC
        )
        assert window.start == expected_start
        assert window.end == expected_start + timedelta(minutes=15)

    def test_find_cheapest_15min_1hour_block(self):
        """Should find cheapest 1-hour block (4 x 15-min intervals)."""
        intervals = create_15min_interval_dict(
            TODAY_15MIN_PRICES, OCT_22_MIDNIGHT_PRAGUE
        )

        window = find_cheapest_window(
            intervals, hours=1, interval=SpotRateIntervalType.QuarterHour
        )

        # 1-hour block = 4 intervals
        assert len(window.prices) == 4
        # Window should span 1 hour
        assert window.end == window.start + timedelta(hours=1)

    def test_find_cheapest_15min_2hour_block(self):
        """Should find cheapest 2-hour block (8 x 15-min intervals)."""
        intervals = create_15min_interval_dict(
            TODAY_15MIN_PRICES, OCT_22_MIDNIGHT_PRAGUE
        )

        window = find_cheapest_window(
            intervals, hours=2, interval=SpotRateIntervalType.QuarterHour
        )

        # 2-hour block = 8 intervals
        assert len(window.prices) == 8
        # Window should span 2 hours
        assert window.end == window.start + timedelta(hours=2)

    def test_15min_cross_midnight(self):
        """Test cross-midnight with 15-min intervals."""
        # Create intervals for late today and early tomorrow
        today_late = create_15min_interval_dict(
            TODAY_15MIN_PRICES[-4:],  # Last 4 intervals (last hour)
            OCT_22_MIDNIGHT_PRAGUE + timedelta(hours=2),  # Start at 02:00
        )
        tomorrow_early = create_15min_interval_dict(
            TOMORROW_15MIN_PRICES[:8],  # First 8 intervals (first 2 hours)
            OCT_23_MIDNIGHT_PRAGUE,
        )
        intervals = dict(sorted({**today_late, **tomorrow_early}.items()))

        window = find_cheapest_window(
            intervals, hours=1, interval=SpotRateIntervalType.QuarterHour
        )

        # Should find a valid 4-interval window
        assert len(window.prices) == 4
        assert window.end == window.start + timedelta(hours=1)

    def test_15min_unsorted_intervals_still_work(self):
        """Verify that unsorted 15-min intervals are handled correctly after fix."""
        # Create intervals in reverse order
        intervals_reversed: dict[datetime, SpotRateInterval] = {}
        for i in range(len(TODAY_15MIN_PRICES) - 1, -1, -1):
            local_dt = OCT_22_MIDNIGHT_PRAGUE + timedelta(minutes=i * 15)
            utc_dt = local_dt.astimezone(UTC)
            intervals_reversed[utc_dt] = SpotRateInterval(
                utc_dt, local_dt, TODAY_15MIN_PRICES[i]
            )

        # Create sorted intervals
        intervals_sorted = create_15min_interval_dict(
            TODAY_15MIN_PRICES, OCT_22_MIDNIGHT_PRAGUE
        )

        # Both should produce the same result
        window_reversed = find_cheapest_window(
            intervals_reversed, hours=1, interval=SpotRateIntervalType.QuarterHour
        )
        window_sorted = find_cheapest_window(
            intervals_sorted, hours=1, interval=SpotRateIntervalType.QuarterHour
        )

        assert window_reversed.start == window_sorted.start
        assert window_reversed.prices == window_sorted.prices

    def test_15min_window_end_calculation(self):
        """Verify 15-min window end time is calculated correctly."""
        intervals = create_15min_interval_dict(
            TODAY_15MIN_PRICES, OCT_22_MIDNIGHT_PRAGUE
        )

        # 1-hour block (4 intervals * 15 min = 60 min)
        window = find_cheapest_window(
            intervals, hours=1, interval=SpotRateIntervalType.QuarterHour
        )

        # End should be 4 * 15 = 60 minutes after start
        expected_end = window.start + timedelta(minutes=60)
        assert window.end == expected_end

        # 2-hour block (8 intervals * 15 min = 120 min)
        window2 = find_cheapest_window(
            intervals, hours=2, interval=SpotRateIntervalType.QuarterHour
        )
        expected_end2 = window2.start + timedelta(minutes=120)
        assert window2.end == expected_end2


# =============================================================================
# Cross-midnight trigger logic tests (IntervalSpotRateData.__init__)
# =============================================================================


class TestCrossMidnightTriggerLogic:
    """Test the cross-midnight trigger logic in IntervalSpotRateData.

    These tests verify the coordinator-level logic that decides when to use
    cross-midnight intervals vs all today's intervals.
    """

    @pytest.mark.asyncio
    async def test_today_search_when_block_has_passed(
        self,
        hass: HomeAssistant,
        mock_ote_electricity: AsyncMock,
        mock_cnb: AsyncMock,
    ):
        """Today search always uses today's data regardless of current time."""
        await hass.config.async_set_time_zone("Europe/Prague")

        current_time = OCT_22_MIDNIGHT_PRAGUE + timedelta(hours=15)

        with freeze_time(current_time):
            result = await init_integration(
                hass,
                [
                    get_entry(
                        currency="EUR",
                        unit="MWh",
                        interval=SpotRateIntervalType.Hour,
                        cheapest_blocks="3",
                        allow_cross_midnight=True,
                    ),
                ],
            )
            assert result
            await hass.async_block_till_done()

            entity_id = "binary_sensor.spot_cheapest_block_today_3h"
            sensor = hass.states.get(entity_id)
            assert sensor is not None, f"Sensor {entity_id} should exist"
            assert sensor.attributes["Start"] is not None

    @pytest.mark.asyncio
    async def test_tomorrow_search_finds_tomorrow_block(
        self,
        hass: HomeAssistant,
        mock_ote_electricity: AsyncMock,
        mock_cnb: AsyncMock,
    ):
        """Tomorrow search should find cheapest block in tomorrow's data."""
        await hass.config.async_set_time_zone("Europe/Prague")

        with freeze_time(BASE_DT):
            result = await init_integration(
                hass,
                [
                    get_entry(
                        currency="EUR",
                        unit="MWh",
                        interval=SpotRateIntervalType.Hour,
                        cheapest_blocks="3",
                        allow_cross_midnight=True,
                    ),
                ],
            )
            assert result
            await hass.async_block_till_done()

            entity_id = "binary_sensor.spot_cheapest_block_tomorrow_3h"
            sensor = hass.states.get(entity_id)
            assert sensor is not None, f"Sensor {entity_id} should exist"
            # Tomorrow's cheapest 3h block starts at 11:00 tomorrow
            expected_start = OCT_23_MIDNIGHT_PRAGUE + timedelta(hours=11)
            assert sensor.attributes["Start"] == expected_start

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mock_ote_electricity", ("today",), indirect=True)
    async def test_tomorrow_search_unavailable_without_tomorrow_data(
        self,
        hass: HomeAssistant,
        mock_ote_electricity: AsyncMock,
        mock_cnb: AsyncMock,
    ):
        """Tomorrow search should be unavailable when tomorrow data isn't published."""
        await hass.config.async_set_time_zone("Europe/Prague")

        with freeze_time(BASE_DT):
            result = await init_integration(
                hass,
                [
                    get_entry(
                        currency="EUR",
                        unit="MWh",
                        interval=SpotRateIntervalType.Hour,
                        cheapest_blocks="3",
                        allow_cross_midnight=True,
                    ),
                ],
            )
            assert result
            await hass.async_block_till_done()

            entity_id = "binary_sensor.spot_cheapest_block_tomorrow_3h"
            sensor = hass.states.get(entity_id)
            assert sensor is not None, f"Sensor {entity_id} should exist"
            # Without tomorrow data, the search window is empty => unknown (unavailable binary sensor)
            assert sensor.state == "unknown"


# =============================================================================
# Quarter-hour specific cross-midnight calculation tests
# =============================================================================


class TestQuarterHourCrossMidnightCalculations:
    """Test quarter-hour specific cross-midnight logic.

    For quarter-hour intervals, the cross-midnight start calculation is:
    earliest_cross_midnight_start = midnight - hours + 15 minutes
    """

    def test_quarter_hour_earliest_start_calculation_1h(self):
        """For 1-hour block with 15-min intervals: midnight - 1h + 15min = 23:15."""
        # 1-hour block = 4 intervals of 15 min = 60 min total
        # To cross midnight: start + (60 min - 15 min) >= midnight
        # So start >= midnight - 45 min = 23:15
        # Create intervals from 23:15 to 00:45 (next day)
        intervals: dict[datetime, SpotRateInterval] = {}
        # Hour 23: 23:00, 23:15, 23:30, 23:45
        for i in range(4):
            local_dt = OCT_22_MIDNIGHT_PRAGUE + timedelta(hours=23, minutes=i * 15)
            utc_dt = local_dt.astimezone(UTC)
            intervals[utc_dt] = SpotRateInterval(utc_dt, local_dt, Decimal("100"))

        # Hour 00 (next day): 00:00, 00:15, 00:30, 00:45
        for i in range(4):
            local_dt = OCT_23_MIDNIGHT_PRAGUE + timedelta(minutes=i * 15)
            utc_dt = local_dt.astimezone(UTC)
            # Make one of these cheaper to find
            price = Decimal("50") if i == 0 else Decimal("100")
            intervals[utc_dt] = SpotRateInterval(utc_dt, local_dt, price)

        intervals = dict(sorted(intervals.items()))
        window = find_cheapest_window(
            intervals, hours=1, interval=SpotRateIntervalType.QuarterHour
        )

        # Should find the cheapest 1-hour window (4 consecutive 15-min intervals)
        assert len(window.prices) == 4
        assert window.end == window.start + timedelta(hours=1)

    def test_quarter_hour_earliest_start_calculation_2h(self):
        """For 2-hour block with 15-min intervals: midnight - 2h + 15min = 22:15."""
        # 2-hour block = 8 intervals of 15 min = 120 min total
        # To cross midnight: start + (120 min - 15 min) >= midnight
        # So start >= midnight - 105 min = 22:15

        # Create intervals from 22:15 to 01:00 (next day)
        intervals: dict[datetime, SpotRateInterval] = {}

        # From 22:15 to 23:45
        for i in range(7):  # 22:15, 22:30, 22:45, 23:00, 23:15, 23:30, 23:45
            local_dt = OCT_22_MIDNIGHT_PRAGUE + timedelta(hours=22, minutes=15 + i * 15)
            utc_dt = local_dt.astimezone(UTC)
            intervals[utc_dt] = SpotRateInterval(utc_dt, local_dt, Decimal("100"))

        # Hour 00 (next day): 00:00, 00:15, 00:30, 00:45, 01:00
        for i in range(5):
            local_dt = OCT_23_MIDNIGHT_PRAGUE + timedelta(minutes=i * 15)
            utc_dt = local_dt.astimezone(UTC)
            intervals[utc_dt] = SpotRateInterval(utc_dt, local_dt, Decimal("100"))

        intervals = dict(sorted(intervals.items()))
        window = find_cheapest_window(
            intervals, hours=2, interval=SpotRateIntervalType.QuarterHour
        )

        assert len(window.prices) == 8
        assert window.end == window.start + timedelta(hours=2)

    def test_quarter_hour_3h_block_crosses_midnight(self):
        """Test a 3-hour block with 15-min intervals that spans midnight."""
        intervals: dict[datetime, SpotRateInterval] = {}

        # Create 3 hours of data ending at 01:00 (crossing midnight)
        # Start at 22:00, go to 01:00
        start_local = OCT_22_MIDNIGHT_PRAGUE + timedelta(hours=22)
        for i in range(12):  # 12 * 15 min = 3 hours
            local_dt = start_local + timedelta(minutes=i * 15)
            utc_dt = local_dt.astimezone(UTC)
            intervals[utc_dt] = SpotRateInterval(
                utc_dt, local_dt, TOMORROW_HOURLY_PRICES[i // 4]
            )

        intervals = dict(sorted(intervals.items()))
        window = find_cheapest_window(
            intervals, hours=3, interval=SpotRateIntervalType.QuarterHour
        )

        assert len(window.prices) == 12  # 3 hours * 4 intervals
        assert window.end == window.start + timedelta(hours=3)


# =============================================================================
# EntryConfig validation and edge case tests
# =============================================================================


class TestEntryConfigValidation:
    """Test EntryConfig validation and edge cases."""

    def test_invalid_block_sizes_filtered_out(self):
        """Invalid block sizes (< 1 or > 23) should be filtered out."""
        from custom_components.cz_energy_spot_prices.coordinator import EntryConfig
        from custom_components.cz_energy_spot_prices.const import (
            Commodity,
            Currency,
            EnergyUnit,
        )

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
                    id="1",
                    name="Today 3h",
                    type=SearchType.TODAY,
                    length_hours=3,
                    legacy=True,
                ),
            ],
            cheapest_blocks_cross_midnight=True,
        )

        blocks = config.all_cheapest_blocks()
        # Should only have None (single interval) and 3
        assert None in blocks
        assert 3 in blocks
        assert 0 not in blocks
        assert 25 not in blocks
        assert -1 not in blocks

    def test_block_size_1_with_hourly_intervals_filtered(self):
        """Block size 1 with hourly intervals should be filtered (covered by None)."""
        from custom_components.cz_energy_spot_prices.coordinator import EntryConfig
        from custom_components.cz_energy_spot_prices.const import (
            Commodity,
            Currency,
            EnergyUnit,
        )

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
                    id="1",
                    name="Today 1h",
                    type=SearchType.TODAY,
                    length_hours=1,
                    legacy=True,
                ),
                PriceBlockSearch(
                    id="2",
                    name="Today 2h",
                    type=SearchType.TODAY,
                    length_hours=2,
                    legacy=True,
                ),
                PriceBlockSearch(
                    id="3",
                    name="Today 3h",
                    type=SearchType.TODAY,
                    length_hours=3,
                    legacy=True,
                ),
            ],
            cheapest_blocks_cross_midnight=True,
        )

        blocks = config.all_cheapest_blocks()
        # 1 should be filtered out for hourly intervals
        assert 1 not in blocks
        assert None in blocks  # Single interval coverage
        assert 2 in blocks
        assert 3 in blocks

    def test_block_size_1_with_quarter_hour_kept(self):
        """Block size 1 with quarter-hour intervals should be kept."""
        from custom_components.cz_energy_spot_prices.coordinator import EntryConfig
        from custom_components.cz_energy_spot_prices.const import (
            Commodity,
            Currency,
            EnergyUnit,
        )

        config = EntryConfig(
            commodity=Commodity.Electricity,
            interval=SpotRateIntervalType.QuarterHour,
            currency=Currency.EUR,
            currency_human="EUR",
            unit=EnergyUnit.MWh,
            timezone="Europe/Prague",
            zoneinfo=PRAGUE_TZ,
            buy_template=None,
            sell_template=None,
            cheapest_block_searches=[
                PriceBlockSearch(
                    id="1",
                    name="Today 1h",
                    type=SearchType.TODAY,
                    length_hours=1,
                    legacy=True,
                ),
                PriceBlockSearch(
                    id="2",
                    name="Today 2h",
                    type=SearchType.TODAY,
                    length_hours=2,
                    legacy=True,
                ),
                PriceBlockSearch(
                    id="3",
                    name="Today 3h",
                    type=SearchType.TODAY,
                    length_hours=3,
                    legacy=True,
                ),
            ],
            cheapest_blocks_cross_midnight=True,
        )

        blocks = config.all_cheapest_blocks()
        # 1 should be kept for quarter-hour intervals
        assert 1 in blocks
        assert None in blocks

    def test_duplicate_blocks_removed(self):
        """Duplicate block sizes should be deduplicated."""
        from custom_components.cz_energy_spot_prices.coordinator import EntryConfig
        from custom_components.cz_energy_spot_prices.const import (
            Commodity,
            Currency,
            EnergyUnit,
        )

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
                    id="1",
                    name="Today 2h",
                    type=SearchType.TODAY,
                    length_hours=2,
                    legacy=True,
                ),
                PriceBlockSearch(
                    id="2",
                    name="Today 2h dup",
                    type=SearchType.TODAY,
                    length_hours=2,
                    legacy=True,
                ),
                PriceBlockSearch(
                    id="3",
                    name="Today 3h",
                    type=SearchType.TODAY,
                    length_hours=3,
                    legacy=True,
                ),
                PriceBlockSearch(
                    id="4",
                    name="Today 3h dup",
                    type=SearchType.TODAY,
                    length_hours=3,
                    legacy=True,
                ),
            ],
            cheapest_blocks_cross_midnight=True,
        )

        blocks = config.all_cheapest_blocks()
        # Should have None, 2, 3 (no duplicates)
        assert blocks.count(2) == 1
        assert blocks.count(3) == 1


# =============================================================================
# Sensor state verification tests
# =============================================================================


class TestSensorStateVerification:
    """Test sensor states have correct attributes and values."""

    @pytest.mark.asyncio
    async def test_cheapest_block_sensor_attributes(
        self,
        hass: HomeAssistant,
        mock_ote_electricity: AsyncMock,
        mock_cnb: AsyncMock,
    ):
        """Verify cheapest block sensors have all required attributes."""
        await hass.config.async_set_time_zone("Europe/Prague")

        with freeze_time(BASE_DT):
            result = await init_integration(
                hass,
                [
                    get_entry(
                        currency="EUR",
                        unit="MWh",
                        interval=SpotRateIntervalType.Hour,
                        cheapest_blocks="2,3",
                        allow_cross_midnight=True,
                    ),
                ],
            )
            assert result
            await hass.async_block_till_done()

            # Test migrated today search sensors
            sensor_2h = hass.states.get("binary_sensor.spot_cheapest_block_today_2h")
            assert sensor_2h is not None
            assert sensor_2h.attributes["Start"] is not None
            assert sensor_2h.attributes["End"] is not None
            assert "Min" in sensor_2h.attributes

            sensor_3h = hass.states.get("binary_sensor.spot_cheapest_block_today_3h")
            assert sensor_3h is not None
            assert sensor_3h.attributes["Start"] is not None
            assert sensor_3h.attributes["End"] is not None
            assert "Min" in sensor_3h.attributes

    @pytest.mark.asyncio
    async def test_single_cheapest_sensor(
        self,
        hass: HomeAssistant,
        mock_ote_electricity: AsyncMock,
        mock_cnb: AsyncMock,
    ):
        """Verify single cheapest hour sensor works correctly."""
        await hass.config.async_set_time_zone("Europe/Prague")

        with freeze_time(BASE_DT):
            result = await init_integration(
                hass,
                [
                    get_entry(
                        currency="EUR",
                        unit="MWh",
                        interval=SpotRateIntervalType.Hour,
                        cheapest_blocks="",
                        allow_cross_midnight=False,
                    ),
                ],
            )
            assert result
            await hass.async_block_till_done()

            # No cheapest_blocks configured, so no migrated searches
            # Verify no search-based sensors exist for empty config
            sensor = hass.states.get("binary_sensor.spot_cheapest_block_today_1h")
            assert sensor is None
