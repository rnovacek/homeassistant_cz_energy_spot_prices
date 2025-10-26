# pyright: reportUnusedParameter=false, reportMissingTypeStubs=false
from datetime import timedelta
from typing import cast
from unittest.mock import AsyncMock, patch
from freezegun import freeze_time
from homeassistant.core import HomeAssistant
import pytest

from custom_components.cz_energy_spot_prices.const import Commodity
from custom_components.cz_energy_spot_prices.coordinator import PRAGUE_TZ, SpotRateCoordinator
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

            assert coordinator._has_tomorrow_data() == has_tomorrow

            if has_tomorrow:
                track_point_mock.assert_called_once()
                # We have tomorrow data, next update will be tomorrow 13:02
                assert track_point_mock.call_args.kwargs["point_in_time"] == (
                    BASE_DT.astimezone(PRAGUE_TZ) + timedelta(days=1)
                ).replace(hour=13, minute=2)
                call_later_mock.assert_not_called()
            else:
                # We don't have tomorrow data...
                if when == 'morning':
                    # ...next update will be today 13:02
                    track_point_mock.assert_called_once()
                    assert track_point_mock.call_args.kwargs["point_in_time"] == BASE_DT.astimezone(PRAGUE_TZ).replace(hour=13, minute=2)
                    call_later_mock.assert_not_called()
                else:
                    # ... next update will be soon (in 5 minutes)
                    call_later_mock.assert_called_once()
                    assert call_later_mock.call_args.kwargs["delay"] == 5 * 60
                    track_point_mock.assert_not_called()
