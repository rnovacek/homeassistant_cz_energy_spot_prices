# pyright: reportUnusedParameter=false, reportMissingTypeStubs=false
from datetime import datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, patch
from freezegun import freeze_time
from homeassistant.core import HomeAssistant
import pytest

from custom_components.cz_energy_spot_prices.const import Commodity
from custom_components.cz_energy_spot_prices.coordinator import SpotRateCoordinator
from . import BASE_DT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mock_ote_electricity", ("today", "today+tomorrow"), indirect=True
)
@pytest.mark.parametrize('when', ('morning', 'evening'))
async def test_update_schedule(hass: HomeAssistant, mock_ote_electricity: AsyncMock, when: str):
    has_tomorrow = cast(str, mock_ote_electricity.param) != "today"
    coordinator = SpotRateCoordinator(hass, Commodity.Electricity)
    with (
        patch(
            "homeassistant.helpers.update_coordinator.event.async_track_point_in_utc_time",
        ) as track_point_mock,
        patch(
            "homeassistant.helpers.update_coordinator.event.async_call_later"
        ) as call_later_mock,
    ):
        with freeze_time(BASE_DT + timedelta(hours=12 if when == 'morning' else 14, minutes=30)):
            await coordinator.async_refresh()

            assert coordinator.has_tomorrow_data() == has_tomorrow

            today_update_time = BASE_DT + timedelta(
                hours=SpotRateCoordinator.DATA_AVAILABLE_TIME.hour,
                minutes=SpotRateCoordinator.DATA_AVAILABLE_TIME.minute,
            )
            tomorrow_update_time = today_update_time + timedelta(days=1)

            if has_tomorrow:
                track_point_mock.assert_called_once()
                # We have tomorrow data, next update will be tomorrow 13:10 + jitter
                scheduled_time = cast(
                    datetime, track_point_mock.call_args.kwargs["point_in_time"]
                )
                assert (
                    scheduled_time - tomorrow_update_time
                ).total_seconds() < SpotRateCoordinator.JITTER_SECONDS
                call_later_mock.assert_not_called()
            else:
                # We don't have tomorrow data...
                if when == 'morning':
                    # ...next update will be today 13:10 + jitter
                    track_point_mock.assert_called_once()
                    scheduled_time = cast(
                        datetime, track_point_mock.call_args.kwargs["point_in_time"]
                    )
                    assert (
                        scheduled_time - today_update_time
                    ).total_seconds() < SpotRateCoordinator.JITTER_SECONDS
                    call_later_mock.assert_not_called()
                else:
                    # ... next update will be soon (in 2 minutes)
                    call_later_mock.assert_called_once()
                    assert (
                        call_later_mock.call_args.kwargs["delay"]
                        == SpotRateCoordinator.DATA_RESCHEDULE_DELAY
                    )
                    track_point_mock.assert_not_called()
