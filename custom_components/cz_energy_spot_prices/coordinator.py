import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal
from typing import Dict, Union, Optional, Sequence, List

import async_timeout

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
    event,
)

from .spot_rate import SpotRate, OTEFault

logger = logging.getLogger(__name__)

CONSECUTIVE_HOURS = (1, 2, 3, 4, 6, 8)

class SpotRateHour:

    def __init__(self, dt_utc: datetime, dt_local: datetime, price: Decimal):
        self.dt_utc = dt_utc
        self.dt_local = dt_local
        self.price = price

        self.most_expensive_order = 0

        self._consecutive_sum_prices: Dict[int, Decimal] = {}

        self.cheapest_consecutive_order = {i: 0 for i in CONSECUTIVE_HOURS}


class SpotRateDay:
    def __init__(self):
        self.hours_by_dt: Dict[datetime, SpotRateHour] = {}

        self._cheapest_hour = 0

    def add_hour(self, hour: SpotRateHour):
        self.hours_by_dt[hour.dt_utc] = hour

    def cheapest_hour(self) -> Optional[SpotRateHour]:
        cheapest_hour = None
        for hour in self.hours_by_dt.values():
            if cheapest_hour is None or cheapest_hour.price > hour.price:
                cheapest_hour = hour

        return cheapest_hour

    def most_expensive_hour(self) -> Optional[SpotRateHour]:
        most_expensive_hour = None
        for hour in self.hours_by_dt.values():
            if most_expensive_hour is None or most_expensive_hour.price < hour.price:
                most_expensive_hour = hour

        return most_expensive_hour


class SpotRateData:
    def __init__(self, rates: SpotRate.RateByDatetime, zoneinfo: ZoneInfo) -> None:
        utc = ZoneInfo('UTC')
        self.now = self.get_now(zoneinfo)
        self.today_date = self.now.date()
        self.tomorrow_date = self.today_date + timedelta(days=1)

        self.today_day = SpotRateDay()
        self.tomorrow_day: Optional[SpotRateDay] = None

        self.hours_by_dt: Dict[datetime, SpotRateHour] = {}

        # Create individual SpotRateHour instances and compute statistics while doing that
        for utc_hour, rate in rates.items():
            rate_hour = SpotRateHour(utc_hour, utc_hour.astimezone(zoneinfo), rate)
            self.hours_by_dt[utc_hour] = rate_hour

            if rate_hour.dt_local.date() == self.today_date:
                self.today_day.add_hour(rate_hour)
            elif rate_hour.dt_local.date() == self.tomorrow_date:
                if self.tomorrow_day is None:
                    self.tomorrow_day = SpotRateDay()
                self.tomorrow_day.add_hour(rate_hour)

        for base_dt, hour in self.hours_by_dt.items():
            rate = Decimal(0)
            for offset in range(CONSECUTIVE_HOURS[-1]):
                prev_dt = (base_dt.astimezone(utc) - timedelta(hours=offset)).astimezone(zoneinfo)
                prev_hour = self.hours_by_dt.get(prev_dt)
                if not prev_hour:
                    # Out of range, probably before yesterday
                    continue

                rate += prev_hour.price

                if (offset + 1) in CONSECUTIVE_HOURS:
                    hour._consecutive_sum_prices[(offset + 1)] = rate

        for hour in self.hours_by_dt.values():
            logger.info('HOUR %s %s %s %s', hour.dt_local, hour.dt_utc, hour.price, hour._consecutive_sum_prices)

        logger.info('today_day %s', self.today_day.hours_by_dt.keys())

        for consecutive in CONSECUTIVE_HOURS:
            for i, hour in enumerate(sorted(self.today_day.hours_by_dt.values(), key=lambda hour: hour._consecutive_sum_prices[consecutive]), 1):
                hour.cheapest_consecutive_order[consecutive] = i

            if self.tomorrow_day is not None:
                for i, hour in enumerate(sorted(self.tomorrow_day.hours_by_dt.values(), key=lambda hour: hour._consecutive_sum_prices[consecutive]), 1):
                    hour.cheapest_consecutive_order[consecutive] = i

    def get_now(self, zoneinfo: Union[timezone, ZoneInfo] = timezone.utc) -> datetime:
        return datetime.now(zoneinfo)

    def hour_for_dt(self, dt: datetime) -> SpotRateHour:
        utc_hour = dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)

        try:
            return self.hours_by_dt[utc_hour]
        except KeyError:
            raise LookupError(f'No hour found in data for {dt.isoformat()}')

    @property
    def current_hour(self) -> SpotRateHour:
        return self.hour_for_dt(self.get_now())

    @property
    def today(self) -> SpotRateDay:
        return self.today_day

    @property
    def tomorrow(self) -> Optional[SpotRateDay]:
        return self.tomorrow_day


class SpotRateCoordinator(DataUpdateCoordinator[SpotRateData]):
    """My custom coordinator."""

    def __init__(self, hass: HomeAssistant, spot_rate: SpotRate, in_eur: bool, unit: SpotRate.EnergyUnit):
        """Initialize my coordinator."""
        logger.debug('SpotRateCoordinator.__init__')
        super().__init__(
            hass,
            logger,
            name="Czech Energy Spot Prices",
        )
        self.hass = hass
        self._spot_rate = spot_rate
        self._in_eur = in_eur
        self._unit: SpotRate.EnergyUnit = unit

        # TODO: do we need to unschedule it?
        self._unschedule = event.async_track_utc_time_change(hass, self.on_schedule, minute=0, second=0)

    @callback
    def on_schedule(self, dt):
        self.hass.async_create_task(self.async_refresh())

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        logger.debug('SpotRateCoordinator._async_update_data')
        zoneinfo = ZoneInfo(self.hass.config.time_zone)
        now = datetime.now(zoneinfo)

        try:
            # Note: asyncio.TimeoutError and aiohttp.ClientError are already
            # handled by the data update coordinator.
            async with async_timeout.timeout(10):
                rates = await self._spot_rate.get_rates(now, in_eur=self._in_eur, unit=self._unit)
                return SpotRateData(rates, zoneinfo)

        except OTEFault as err:
            raise UpdateFailed(f"Error communicating with API: {err}")
