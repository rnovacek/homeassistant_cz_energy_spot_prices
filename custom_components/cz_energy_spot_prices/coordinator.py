import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import async_timeout

from homeassistant.core import HomeAssistant

from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
    event,
)

from .spot_rate import SpotRate, OTEFault

logger = logging.getLogger(__name__)


class SpotRateCoordinator(DataUpdateCoordinator[SpotRate.RateByDatetime]):
    """My custom coordinator."""

    def __init__(self, hass: HomeAssistant, spot_rate: SpotRate, in_eur: bool, unit: SpotRate.EnergyUnit):
        """Initialize my coordinator."""
        logger.debug('SpotRateCoordinator.__init__')
        super().__init__(
            hass,
            logger,
            name="Czech Energy Spot Prices",
        )
        self._spot_rate = spot_rate
        self._in_eur = in_eur
        self._unit: SpotRate.EnergyUnit = unit

        # TODO: do we need to unschedule it?
        self._unschedule = event.async_track_utc_time_change(hass, lambda dt: hass.async_create_task(self.async_refresh()), minute=0, second=0)

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        logger.debug('SpotRateCoordinator._async_update_data')
        now = datetime.now(ZoneInfo(self.hass.config.time_zone))

        try:
            # Note: asyncio.TimeoutError and aiohttp.ClientError are already
            # handled by the data update coordinator.
            async with async_timeout.timeout(10):
                return await self._spot_rate.get_two_days_rates(now, in_eur=self._in_eur, unit=self._unit)
        except OTEFault as err:
            raise UpdateFailed(f"Error communicating with API: {err}")
