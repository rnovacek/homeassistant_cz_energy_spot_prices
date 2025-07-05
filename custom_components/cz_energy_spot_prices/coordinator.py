import asyncio
import logging
from datetime import datetime, timedelta, timezone, time
from typing import cast, final, override
from zoneinfo import ZoneInfo
from decimal import Decimal
import random

import async_timeout

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.template import Template
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    event,
)

from .spot_rate import SpotRate, OTEFault

logger = logging.getLogger(__name__)

CONSECUTIVE_HOURS = (1, 2, 3, 4, 6, 8)


def get_now(zoneinfo: timezone | ZoneInfo = timezone.utc) -> datetime:
    return datetime.now(zoneinfo)

@final
class SpotRateHour:

    def __init__(self, dt_utc: datetime, dt_local: datetime, price: Decimal):
        self.dt_utc = dt_utc
        self.dt_local = dt_local
        self.price = price

        self.most_expensive_order = 0

        self.consecutive_sum_prices: dict[int, Decimal] = {}

        self.cheapest_consecutive_order = {i: 0 for i in CONSECUTIVE_HOURS}


@final
class SpotRateDay:
    def __init__(self):
        self.hours_by_dt: dict[datetime, SpotRateHour] = {}

        self._cheapest_hour = 0

    def add_hour(self, hour: SpotRateHour):
        self.hours_by_dt[hour.dt_utc] = hour

    def cheapest_hour(self) -> SpotRateHour | None:
        cheapest_hour = None
        for hour in self.hours_by_dt.values():
            if cheapest_hour is None or cheapest_hour.price > hour.price:
                cheapest_hour = hour

        return cheapest_hour

    def most_expensive_hour(self) -> SpotRateHour | None:
        most_expensive_hour = None
        for hour in self.hours_by_dt.values():
            if most_expensive_hour is None or most_expensive_hour.price < hour.price:
                most_expensive_hour = hour

        return most_expensive_hour


@final
class HourlySpotRateData:
    def __init__(
        self,
        rates: SpotRate.RateByDatetime,
        zoneinfo: ZoneInfo,
        rate_template: Template | None,
    ) -> None:
        self.now = get_now(zoneinfo)
        self.today_date = self.now.date()
        self.tomorrow_date = self.today_date + timedelta(days=1)

        self.today_day = SpotRateDay()
        self.tomorrow_day: SpotRateDay | None = None

        self.hours_by_dt: dict[datetime, SpotRateHour] = {}

        # Create individual SpotRateHour instances and compute statistics while doing that
        for utc_hour, rate in rates.items():
            if rate_template is not None:
                rate = Decimal(
                    cast(
                        float,
                        rate_template.async_render(
                            {
                                "value": float(rate),
                                "hour": utc_hour,
                            }
                        ),
                    )
                )
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
                prev_dt = base_dt - timedelta(hours=offset)
                prev_hour = self.hours_by_dt.get(prev_dt)
                if not prev_hour:
                    # Out of range, probably before yesterday
                    continue

                rate += prev_hour.price

                if (offset + 1) in CONSECUTIVE_HOURS:
                    hour.consecutive_sum_prices[(offset + 1)] = rate

        for consecutive in CONSECUTIVE_HOURS:
            sorted_today_hours = sorted(
                self.today_day.hours_by_dt.values(),
                key=lambda hour: hour.consecutive_sum_prices.get(
                    consecutive, Decimal(0)
                ),
            )
            for i, hour in enumerate(sorted_today_hours, 1):
                hour.cheapest_consecutive_order[consecutive] = i

            if self.tomorrow_day is not None:
                sorted_tomorrow_hours = sorted(
                    self.tomorrow_day.hours_by_dt.values(),
                    key=lambda hour: hour.consecutive_sum_prices.get(
                        consecutive, Decimal(0)
                    ),
                )
                for i, hour in enumerate(sorted_tomorrow_hours, 1):
                    hour.cheapest_consecutive_order[consecutive] = i

    def hour_for_dt(self, dt: datetime) -> SpotRateHour:
        utc_hour = dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)

        try:
            return self.hours_by_dt[utc_hour]
        except KeyError:
            raise LookupError(f'No hour found in data for {dt.isoformat()}')

    @property
    def current_hour(self) -> SpotRateHour:
        return self.hour_for_dt(get_now())

    @property
    def today(self) -> SpotRateDay:
        return self.today_day

    @property
    def tomorrow(self) -> SpotRateDay | None:
        return self.tomorrow_day


@final
class HourlyTradeRateData:
    def __init__(
        self,
        rates: SpotRate.RateByDatetime,
        zoneinfo: ZoneInfo,
        buy_rate_template: Template | None,
        sell_rate_template: Template | None,
    ) -> None:
        self.spot_rates = HourlySpotRateData(rates, zoneinfo, None)

        if buy_rate_template is None:
            self.buy_rates = self.spot_rates
        else:
            self.buy_rates = HourlySpotRateData(rates, zoneinfo, buy_rate_template)

        if sell_rate_template is None:
            self.sell_rates = self.spot_rates
        else:
            self.sell_rates = HourlySpotRateData(rates, zoneinfo, sell_rate_template)


@final
class DailySpotRateData:
    def __init__(
        self,
        rates: SpotRate.RateByDatetime,
        zoneinfo: ZoneInfo,
        rate_template: Template | None,
    ) -> None:
        self.now = get_now(zoneinfo)
        today = self.now.date()

        midnight_today = datetime.combine(date=today, time=time(hour=0), tzinfo=zoneinfo).astimezone(timezone.utc)
        tomorrow = today + timedelta(days=1)
        midnight_tomorrow = datetime.combine(date=tomorrow, time=time(hour=0), tzinfo=zoneinfo).astimezone(timezone.utc)
        yesterday = today - timedelta(days=1)
        midnight_yesterday = datetime.combine(date=yesterday, time=time(hour=0), tzinfo=zoneinfo).astimezone(timezone.utc)

        # It's 0 when there are no data, we want None
        self._yesteday = self._get_trade_rate(rates, midnight_yesterday, rate_template) or None
        self._today = self._get_trade_rate(rates, midnight_today, rate_template) or None
        self._tomorrow = self._get_trade_rate(rates, midnight_tomorrow, rate_template) or None

    @property
    def today(self) -> Decimal:
        # When there are no data for today, we want to use yesterday's rate
        value = self._today or self._yesteday
        if value is None:
            raise LookupError('No data for today or yesterday')
        return value

    @property
    def tomorrow(self) -> Decimal | None:
        return self._tomorrow

    def _get_trade_rate(
        self,
        rates: SpotRate.RateByDatetime,
        dt: datetime,
        rate_template: Template | None,
    ) -> Decimal | None:
        rate = rates.get(dt, None) or None

        if rate is not None and rate_template is not None:
            rate = Decimal(
                cast(
                    float,
                    rate_template.async_render(
                        {
                            "value": float(rate),
                            "day": dt,
                        }
                    ),
                )
            )

        return rate


@final
class DailyTradeRateData:
    def __init__(
        self,
        rates: SpotRate.RateByDatetime,
        zoneinfo: ZoneInfo,
        buy_rate_template: Template | None,
    ) -> None:
        self.spot_rates = DailySpotRateData(rates, zoneinfo, None)
        if buy_rate_template is None:
            self.buy_rates = self.spot_rates
        else:
            self.buy_rates = DailySpotRateData(rates, zoneinfo, buy_rate_template)


@final
class SpotRateData:
    def __init__(self, electricity: HourlyTradeRateData, gas: DailyTradeRateData):
        self.electricity = electricity
        self.gas = gas

    def get_now(self, zoneinfo: timezone | ZoneInfo = timezone.utc) -> datetime:
        return get_now(zoneinfo=zoneinfo)


@final
class SpotRateCoordinator(DataUpdateCoordinator[SpotRateData | None]):
    """My custom coordinator."""

    def __init__(self, hass: HomeAssistant, spot_rate: SpotRate, in_eur: bool, unit: SpotRate.EnergyUnit, electricity_buy_rate_template_code: str, electricity_sell_rate_template_code: str, gas_buy_rate_template_code: str):
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
        self._spot_rate_data = None
        self._retry_attempt = 0
        # Delays in seconds, total needs to be less than 3600 (one hour) as the `on_schedule` is scheduled once an hour
        self._retry_attempt_delays = [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]
        unique_id = f"spot_rate_{unit}_{self._in_eur}"

        self._electricity_buy_rate_template = None
        if electricity_buy_rate_template_code.strip():
            try:
                self._electricity_buy_rate_template = Template(electricity_buy_rate_template_code, hass)
            except TemplateError as e:
                logger.error("Template error in %s: %s", unique_id, e)

        self._electricity_sell_rate_template = None
        if electricity_sell_rate_template_code.strip():
            try:
                self._electricity_sell_rate_template = Template(electricity_sell_rate_template_code, hass)
            except TemplateError as e:
                logger.error("Template error in %s: %s", unique_id, e)

        self._gas_buy_rate_template = None
        if gas_buy_rate_template_code.strip():
            try:
                self._gas_buy_rate_template = Template(gas_buy_rate_template_code, hass)
            except TemplateError as e:
                logger.error("Template error in %s: %s", unique_id, e)

        # TODO: do we need to unschedule it?
        self._unschedule = event.async_track_utc_time_change(hass, self.on_schedule, minute=0, second=0)

    @callback
    def on_schedule(self, _dt: datetime):
        _ = self.hass.async_create_task(self.async_refresh())

    async def fetch_data(self):
        logger.debug('SpotRateCoordinator.fetch_data')

        zoneinfo = ZoneInfo(self.hass.config.time_zone)
        now = datetime.now(zoneinfo)

        async with async_timeout.timeout(30):
            electricity_rates, gas_rates = await asyncio.gather(
                self._spot_rate.get_electricity_rates(now, in_eur=self._in_eur, unit=self._unit),
                self._spot_rate.get_gas_rates(now, in_eur=self._in_eur, unit=self._unit),
            )
            self._retry_attempt = 0
            return SpotRateData(
                electricity=HourlyTradeRateData(electricity_rates, zoneinfo, self._electricity_buy_rate_template, self._electricity_sell_rate_template),
                gas=DailyTradeRateData(gas_rates, zoneinfo, self._gas_buy_rate_template),
            )

    def retry_maybe(self, exc_info: Exception | None=None):
        try:
            delay = self._retry_attempt_delays[self._retry_attempt]
        except IndexError:
            delay = None

        self._retry_attempt += 1
        if delay is not None:
            logger.error('OTE request failed %d times, retrying in %d seconds', self._retry_attempt, delay, exc_info=exc_info)
            _ = event.async_call_later(self.hass, delay=delay, action=self.update_data)
        else:
            logger.error('OTE request failed %d times, not retrying', self._retry_attempt, exc_info=exc_info)

    async def update_data(self, dt: datetime):
        logger.debug('SpotRateCoordinator.update_data %s', dt)
        try:
            self._spot_rate_data = await self.fetch_data()
            self.async_set_updated_data(self._spot_rate_data)

        except (OTEFault, asyncio.TimeoutError) as e:
            self.retry_maybe(exc_info=e)

        except Exception:
            logger.exception('OTE request failed unexpectedly, not retrying')

    @override
    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        logger.debug('SpotRateCoordinator._async_update_data')

        delay = 0
        if self._spot_rate_data:
            # We have some data, schedule update after a random delay to avoid all
            # users hitting the API at the same time, max delay is 2 minutes
            delay = random.randint(5, 120)
            _ = event.async_call_later(self.hass, delay=delay, action=self.update_data)
            logger.debug(f'SpotRateCoordinator.update_data scheduled in {delay} seconds')
        else:
            try:
                self._spot_rate_data = await self.fetch_data()
                logger.debug('SpotRateCoordinator._async_update_data fetched data: %s', self._spot_rate_data)
            except (OTEFault, asyncio.TimeoutError) as e:
                self.retry_maybe(exc_info=e)
            except Exception:
                logger.exception('OTE request failed unexpectedly during intial load')

        return self._spot_rate_data

    def has_electricity_buy_rate_template(self) -> bool:
        return self._electricity_buy_rate_template is not None

    def has_electricity_sell_rate_template(self) -> bool:
        return self._electricity_sell_rate_template is not None

    def has_gas_buy_rate_template(self) -> bool:
        return self._gas_buy_rate_template is not None
