from random import randint
import aiohttp.client_exceptions
import asyncio
from collections.abc import Sequence
import logging
from datetime import datetime, timedelta, timezone, time
from typing import cast, final, override
from zoneinfo import ZoneInfo
from decimal import Decimal

import async_timeout

from attr import dataclass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.template import Template
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
    event,
)
from homeassistant.util.dt import now

from .cnb_rate import CnbRate
from .const import Commodity, Currency, SpotRateIntervalType, EnergyUnit
from .spot_rate import (
    RateByDatetime,
    RatesByInterval,
    SpotRate,
    OTEFault,
)

logger = logging.getLogger(__name__)

PRAGUE_TZ = ZoneInfo("Europe/Prague")


def get_now(zoneinfo: timezone | ZoneInfo = timezone.utc) -> datetime:
    return now(zoneinfo)


@dataclass
class EntryConfig:
    commodity: Commodity
    interval: SpotRateIntervalType
    currency: Currency
    currency_human: str
    unit: EnergyUnit
    timezone: str
    zoneinfo: ZoneInfo
    buy_template: Template | None
    sell_template: Template | None
    cheapest_blocks: Sequence[int] | None
    cheapest_blocks_cross_midnight: bool

    def all_cheapest_blocks(self) -> list[int | None]:
        """Return all cheapest blocks, including blocks that take just one interval (it's value is None)."""
        # Insert None first - it means cheapest interval (hour for hourly interval, 15 min block for 15min interval)
        cheapest_blocks: list[int | None] = [None]
        for block in self.cheapest_blocks or []:
            try:
                block = int(block)
            except ValueError:
                logger.error("Invalid interval for cheapest blocks: %s", block)
                continue

            if block < 1 or block > 23:
                logger.error("Invalid interval for cheapest blocks: %s", block)
                continue

            if block == 1 and self.interval == SpotRateIntervalType.Hour:
                # This is covered by having `None` in the array
                continue

            if block in cheapest_blocks:
                # Prevent duplication
                continue

            cheapest_blocks.append(block)
        return cheapest_blocks


@final
class SpotRateInterval:
    def __init__(self, dt_utc: datetime, dt_local: datetime, price: Decimal):
        self.dt_utc = dt_utc
        self.dt_local = dt_local
        self.price = price

        self.most_expensive_order = 0

        # self.consecutive_sum_prices: dict[int, Decimal] = {}

        # self.cheapest_consecutive_order: dict[int, int] = {}

    @override
    def __repr__(self):
        return f"<{self.dt_utc}: {self.price}>"


@final
class SpotRateDay:
    def __init__(self):
        self.interval_by_dt: dict[datetime, SpotRateInterval] = {}

        self._interval_order: dict[datetime, int] | None = None

    def add_interval(self, interval: SpotRateInterval):
        self.interval_by_dt[interval.dt_utc] = interval

    def cheapest_interval(self) -> SpotRateInterval | None:
        cheapest_interval = None
        for interval in self.interval_by_dt.values():
            if cheapest_interval is None or cheapest_interval.price > interval.price:
                cheapest_interval = interval

        return cheapest_interval

    def most_expensive_interval(self) -> SpotRateInterval | None:
        most_expensive_interval = None
        for interval in self.interval_by_dt.values():
            if (
                most_expensive_interval is None
                or most_expensive_interval.price < interval.price
            ):
                most_expensive_interval = interval

        return most_expensive_interval

    def interval_order(self):
        if not self._interval_order:
            # Sort items by value
            sorted_items = sorted(
                self.interval_by_dt.items(), key=lambda item: item[1].price
            )

            # Extract the order (1-based)
            self._interval_order = {}
            for idx, (k, _v) in enumerate(sorted_items, start=1):
                self._interval_order[k] = idx

        return self._interval_order

    def first(self) -> SpotRateInterval | None:
        min_dt = None
        min_price = None
        for dt, price in self.interval_by_dt.items():
            if min_dt is None or min_dt < dt:
                min_dt = dt
                min_price = price
        return min_price


@final
class IntervalSpotRateData:
    def __init__(
        self,
        config: EntryConfig,
        rates: RateByDatetime,
        rate_template: Template | None,
    ) -> None:
        self.config = config
        self.now = get_now(config.zoneinfo)
        self.today_date = self.now.date()
        self.tomorrow_date = self.today_date + timedelta(days=1)

        self._today_day: SpotRateDay | None = None
        self._tomorrow_day: SpotRateDay | None = None

        self.interval_by_dt: dict[datetime, SpotRateInterval] = {}
        self._today_tomorrow_by_dt: dict[datetime, SpotRateInterval] = {}

        self.cheapest_windows: dict[int | None, Window] = {}

        # Create individual SpotRateInterval instances and compute statistics while doing that
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
            rate_hour = SpotRateInterval(
                utc_hour, utc_hour.astimezone(config.zoneinfo), rate
            )
            self.interval_by_dt[utc_hour] = rate_hour

            if rate_hour.dt_local.date() == self.today_date:
                if self._today_day is None:
                    self._today_day = SpotRateDay()
                self._today_day.add_interval(rate_hour)
                self._today_tomorrow_by_dt[rate_hour.dt_utc] = rate_hour
            elif rate_hour.dt_local.date() == self.tomorrow_date:
                if self._tomorrow_day is None:
                    self._tomorrow_day = SpotRateDay()
                self._tomorrow_day.add_interval(rate_hour)
                self._today_tomorrow_by_dt[rate_hour.dt_utc] = rate_hour

        if not self._today_day:
            return

        for block in config.all_cheapest_blocks():
            if config.cheapest_blocks_cross_midnight and block is not None:
                intervals_for_cheapest = self._today_tomorrow_by_dt
            else:
                intervals_for_cheapest = self._today_day.interval_by_dt

            try:
                window = find_cheapest_window(
                    intervals_for_cheapest,
                    hours=block,
                    interval=config.interval,
                )
                self.cheapest_windows[block] = window
            except ValueError:
                if block is None:
                    logger.error("Unable to find cheapest interval")
                else:
                    logger.error("Unable to find cheapest %s hour block", block)

            # for base_dt, rate_interval in self.interval_by_dt.items():
            #     rate = Decimal(0)
            #     for offset in range(config.cheapest_blocks[-1]):
            #         prev_dt = base_dt - timedelta(hours=offset)
            #         prev_hour = self.interval_by_dt.get(prev_dt)
            #         if not prev_hour:
            #             # Out of range, probably before yesterday
            #             continue

            #         rate += prev_hour.price

            #         if (offset + 1) in config.cheapest_blocks:
            #             rate_interval.consecutive_sum_prices[(offset + 1)] = rate

            # if not self.today_day:
            #     return

            # for consecutive in config.cheapest_blocks:
            #     sorted_today_hours = sorted(
            #         self.today_day.interval_by_dt.values(),
            #         key=lambda hour: hour.consecutive_sum_prices.get(
            #             consecutive, Decimal(0)
            #         ),
            #     )
            #     for i, rate_interval in enumerate(sorted_today_hours, 1):
            #         rate_interval.cheapest_consecutive_order[consecutive] = i

            #     if self.tomorrow_day is not None:
            #         sorted_tomorrow_hours = sorted(
            #             self.tomorrow_day.interval_by_dt.values(),
            #             key=lambda hour: hour.consecutive_sum_prices.get(
            #                 consecutive, Decimal(0)
            #             ),
            #         )
            #         for i, rate_interval in enumerate(sorted_tomorrow_hours, 1):
            #             rate_interval.cheapest_consecutive_order[consecutive] = i

    def interval_for_dt(self, dt: datetime) -> SpotRateInterval:
        if self.config.interval == SpotRateIntervalType.Day:
            # Midnight prague time
            utc_dt = (
                dt.astimezone(PRAGUE_TZ)
                .replace(hour=0, minute=0, second=0, microsecond=0)
                .astimezone(timezone.utc)
            )
        elif self.config.interval == SpotRateIntervalType.Hour:
            utc_dt = dt.astimezone(timezone.utc).replace(
                minute=0, second=0, microsecond=0
            )
        elif self.config.interval == SpotRateIntervalType.QuarterHour:
            utc_dt = dt.astimezone(timezone.utc)
            minute = int(utc_dt.minute / 15) * 15
            utc_dt = utc_dt.replace(minute=minute, second=0, microsecond=0)
        else:
            raise ValueError(f"Unknown interval {self.config.interval}")

        try:
            return self.interval_by_dt[utc_dt]
        except KeyError:
            raise LookupError(f"No hour found in data for {dt.isoformat()}")

    @property
    def current_interval(self) -> SpotRateInterval:
        return self.interval_for_dt(get_now())

    @property
    def today(self) -> SpotRateDay | None:
        return self._today_day

    @property
    def tomorrow(self) -> SpotRateDay | None:
        return self._tomorrow_day


@final
class IntervalTradeRateData:
    def __init__(
        self,
        config: EntryConfig,
        spot_rates: RateByDatetime,
        conversion_rate: Decimal,
    ) -> None:
        # Convert to different currency (EUR -> CZK using fx_rate) and unit (MWh -> kWh)
        converted_spot_rates = {
            dt: value * conversion_rate for dt, value in spot_rates.items()
        }

        self.spot_rates = IntervalSpotRateData(
            config=config,
            rates=converted_spot_rates,
            rate_template=None,
        )

        if config.buy_template is None:
            self.buy_rates = None
        else:
            self.buy_rates = IntervalSpotRateData(
                config=config,
                rates=converted_spot_rates,
                rate_template=config.buy_template,
            )

        if config.sell_template is None:
            self.sell_rates = None
        else:
            self.sell_rates = IntervalSpotRateData(
                config=config,
                rates=converted_spot_rates,
                rate_template=config.sell_template,
            )


@final
class DailySpotRateData:
    def __init__(
        self,
        rates: RateByDatetime,
        zoneinfo: ZoneInfo,
        rate_template: Template | None,
    ) -> None:
        self.now = get_now(zoneinfo)
        today = self.now.date()

        midnight_today = datetime.combine(
            date=today, time=time(hour=0), tzinfo=zoneinfo
        ).astimezone(timezone.utc)
        tomorrow = today + timedelta(days=1)
        midnight_tomorrow = datetime.combine(
            date=tomorrow, time=time(hour=0), tzinfo=zoneinfo
        ).astimezone(timezone.utc)
        yesterday = today - timedelta(days=1)
        midnight_yesterday = datetime.combine(
            date=yesterday, time=time(hour=0), tzinfo=zoneinfo
        ).astimezone(timezone.utc)

        # It's 0 when there are no data, we want None
        self._yesteday = (
            self._get_trade_rate(rates, midnight_yesterday, rate_template) or None
        )
        self._today = self._get_trade_rate(rates, midnight_today, rate_template) or None
        self._tomorrow = (
            self._get_trade_rate(rates, midnight_tomorrow, rate_template) or None
        )

    @property
    def today(self) -> Decimal:
        # When there are no data for today, we want to use yesterday's rate
        value = self._today or self._yesteday
        if value is None:
            raise LookupError("No data for today or yesterday")
        return value

    @property
    def tomorrow(self) -> Decimal | None:
        return self._tomorrow

    def _get_trade_rate(
        self,
        rates: RateByDatetime,
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
        rates: RateByDatetime,
        zoneinfo: ZoneInfo,
        buy_rate_template: Template | None,
    ) -> None:
        self.spot_rates = DailySpotRateData(rates, zoneinfo, None)
        if buy_rate_template is None:
            self.buy_rates = self.spot_rates
        else:
            self.buy_rates = DailySpotRateData(rates, zoneinfo, buy_rate_template)


@dataclass
class Window:
    start: datetime
    end: datetime
    prices: list[Decimal]


def find_cheapest_window(
    interval_by_dt: dict[datetime, SpotRateInterval],
    hours: int | None,
    interval: SpotRateIntervalType,
) -> Window:
    # window size is how many interval will fit into given X hours block
    window_size = 1
    if hours is not None:
        if interval == SpotRateIntervalType.Hour:
            window_size = hours
        else:
            window_size = hours * 4

    all_prices = [i.price for i in interval_by_dt.values()]

    min_sum = None
    min_sum_start = None
    min_sum_end = None
    min_sum_prices = None

    for i, (dt, _) in enumerate(interval_by_dt.items()):
        window = all_prices[i : (i + window_size)]
        if len(window) != window_size:
            continue

        window_sum = sum(window)
        if min_sum is None or window_sum < min_sum:
            min_sum = window_sum
            min_sum_start = dt
            if interval == SpotRateIntervalType.Hour:
                min_sum_end = dt + timedelta(hours=window_size)
            else:
                min_sum_end = dt + timedelta(minutes=window_size * 15)
            min_sum_prices = window

    if min_sum_start is None or min_sum_end is None or min_sum_prices is None:
        raise ValueError()

    return Window(
        start=min_sum_start,
        end=min_sum_end,
        prices=min_sum_prices,
    )


@final
class SpotRateCoordinator(DataUpdateCoordinator[RatesByInterval | None]):
    # OTE says that data for the next day should be available at 13:02 CE(S)T (Prague) time,
    # but in reality they never are. We'll start the update 13:10 with random 2 minutes jitter and
    # then try every 2 minutes until we get next day data.
    DATA_AVAILABLE_TIME = time(13, 10)
    JITTER_SECONDS = 120
    DATA_RESCHEDULE_DELAY = 120

    def __init__(
        self,
        hass: HomeAssistant,
        commodity: Commodity,
    ):
        logger.debug("SpotRateCoordinator[%s].__init__", commodity)
        super().__init__(
            hass,
            logger,
            name=f"Czech Energy Spot Prices [SpotRateCoordinator] for {commodity}",
        )
        self.hass = hass
        self._spot_rate = SpotRate()
        self._spot_rate_data: RatesByInterval | None = None
        self._update_schedule = None
        self._retry_attempt = 0
        self._commodity = commodity

        # TODO: persist data using
        # self._store = storage.Store(hass, STORAGE_VERSION, STORAGE_KEY)

    def _schedule_next_update(self):
        # OTE prices are published at 13:02 CE(S)T time - we need to make that independent on HA timezone,
        # so we'll use 13:02 CE(S)T, convert that to UTC and use it with local=False (that means UTC).

        # Define Prague time zone
        utc = ZoneInfo("UTC")

        # Current time in Prague
        now_prague = now(PRAGUE_TZ)

        if self.has_tomorrow_data():
            # We already have data for tomorrow, next update will be tomorrow
            local_target = datetime.combine(
                (now_prague + timedelta(days=1)).date(),
                self.DATA_AVAILABLE_TIME,
                tzinfo=PRAGUE_TZ,
            )
            # Apply jitter to prevent everyone updating at the same time
            local_target += timedelta(seconds=randint(1, self.JITTER_SECONDS))
        else:
            # We don't have data for tomorrow, next update will be today
            if self.DATA_AVAILABLE_TIME < now_prague.time():
                # Update time already happened today but we don't have tomorrow data, schedule update soon (in 5 minutes)
                local_target = now_prague + timedelta(minutes=5)
            else:
                # Update 13:02 today
                local_target = datetime.combine(
                    now_prague.date(),
                    self.DATA_AVAILABLE_TIME,
                    tzinfo=PRAGUE_TZ,
                )
                # Apply jitter to prevent everyone updating at the same time
                local_target += timedelta(seconds=randint(1, self.JITTER_SECONDS))

        # Convert to UTC (this handles DST properly)
        utc_time = local_target.astimezone(utc)

        self._update_schedule = event.async_track_point_in_utc_time(
            hass=self.hass,
            action=self.on_schedule,
            point_in_time=utc_time,
        )
        return utc_time

    async def async_stop(self):
        """Cancel scheduled jobs."""
        logger.debug("SpotRateCoordinator[%s].async_stop", self._commodity)
        if self._update_schedule:
            self._update_schedule()
            self._update_schedule = None

    @callback
    def on_schedule(self, dt: datetime):
        logger.debug(
            "SpotRateCoordinator[%s].on_schedule called at %s", self._commodity, dt
        )

        if self._update_schedule:
            self._update_schedule()
            self._update_schedule = None

        _ = self.hass.async_create_task(self.async_request_refresh())

    async def _fetch_data(self):
        logger.debug("SpotRateCoordinator[%s]._fetch_data", self._commodity)

        zoneinfo = ZoneInfo(self.hass.config.time_zone)
        start = now(zoneinfo)

        if self._commodity == Commodity.Electricity:
            rates = await self._spot_rate.get_electricity_rates(start)
        elif self._commodity == Commodity.Gas:
            rates = await self._spot_rate.get_gas_rates(start)
        else:
            raise ValueError(f"Invalid commodity {self._commodity}")

        return rates

    async def _fetch_data_with_retry(self):
        is_first_run = self.data is None

        logger.debug("SpotRateCoordinator[%s]._fetch_data_with_retry", self._commodity)
        current_delay = cast(int, 2**self._retry_attempt)
        try:
            async with async_timeout.timeout(30):
                data = await self._fetch_data()
                self._retry_attempt = 0
                return data

        except (OTEFault, aiohttp.client_exceptions.ClientError, asyncio.TimeoutError) as e:
            logger.warning(
                "Failed to update OTE prices, will retry in %d seconds: %s",
                current_delay,
                e,
            )

        except Exception:
            logger.exception(
                "OTE request failed unexpectedly, will retry in %d seconds",
                current_delay,
            )

        self._retry_attempt += 1

        self._update_schedule = event.async_call_later(
            self.hass,
            delay=current_delay,
            action=self.on_schedule,
        )

        if is_first_run:
            # Do not mark the integration as failed on first run, let it retry silently
            return None

        raise UpdateFailed("Failed to update OTE prices")

    def has_tomorrow_data(self) -> bool:
        if not self._spot_rate_data:
            return False

        if self._commodity == Commodity.Gas:
            # We have gas data for tomorrow if there is a future record
            for dt in self._spot_rate_data[SpotRateIntervalType.Day].keys():
                if dt > now():
                    return True
            return False

        else:
            # When DST changes, it might be 11 or 13 hours, but that doesn't matter
            # for just checking if tomorrow data are available
            noon_tomorrow = now(PRAGUE_TZ).replace(
                hour=12, minute=0, second=0, microsecond=0
            ) + timedelta(days=1)

            return (
                self._spot_rate_data[SpotRateIntervalType.QuarterHour].get(
                    noon_tomorrow
                )
                is not None
            )

    def is_tomorrow_data_available(self) -> bool:
        """New prices should be published on 13:10 Prague (CET or CEST) time"""
        now_cet = now(PRAGUE_TZ)
        return now_cet.time() >= self.DATA_AVAILABLE_TIME

    @override
    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        logger.debug("SpotRateCoordinator[%s]._async_update_data", self._commodity)

        self._spot_rate_data = await self._fetch_data_with_retry()
        if self._spot_rate_data is None:
            # Update failed, new update is already scheduled
            return None

        if not self.has_tomorrow_data() and self.is_tomorrow_data_available():
            # Tomorrow data should be available but are not => schedule update soon
            logger.info(
                "SpotRateCoordinator[%s] tomorrow data should be available in OTE but are not => rescheduling in 2 minutes",
                self._commodity,
            )
            self._update_schedule = event.async_call_later(
                self.hass,
                delay=self.DATA_RESCHEDULE_DELAY,  # Try again in 2 minutes
                action=self.on_schedule,
            )
        else:
            # Schedule the update for tommorow
            dt = self._schedule_next_update()
            logger.info(
                "SpotRateCoordinator[%s] data updated, scheduling next update at %s",
                self._commodity,
                dt,
            )

        return self._spot_rate_data


class FxCoordinator(DataUpdateCoordinator[dict[str, Decimal] | None]):
    def __init__(
        self,
        hass: HomeAssistant,
    ):
        """Initialize my coordinator."""
        super().__init__(
            hass,
            logger,
            name="Czech Energy Spot Prices [FxCoordinator]",
        )

        self._cnb = CnbRate()
        self._retry_attempt = 0

        # Update on midnight local (hass) time
        self._update_schedule = event.async_track_time_change(
            hass=self.hass,
            action=self.on_schedule,
            hour=0,
            minute=0,
            second=0,
        )

    async def async_stop(self):
        """Cancel scheduled jobs."""
        if self._update_schedule:
            logger.debug("Unscheduling FX coordinator")
            self._update_schedule()
            self._update_schedule = None

    @callback
    def on_schedule(self, _dt: datetime):
        _ = self.hass.async_create_task(self.async_request_refresh())

    async def _fetch_data(self):
        logger.debug("FxCoordinator._fetch_data")

        rates = await self._cnb.get_current_rates()
        return rates

    async def _fetch_data_with_retry(self):
        logger.debug("FxCoordinator._fetch_data_with_retry")
        current_delay = cast(int, 2**self._retry_attempt)
        try:
            async with async_timeout.timeout(30):
                data = await self._fetch_data()
                self._retry_attempt = 0
                return data

        except (OTEFault, aiohttp.client_exceptions.ClientError, asyncio.TimeoutError) as e:
            logger.warning(
                "Failed to update CNB FX rates, will retry in %d seconds: %s",
                current_delay,
                e,
            )

        except Exception:
            logger.exception(
                "CNB FX request failed unexpectedly, will retry in %d seconds",
                current_delay,
            )

        self._update_schedule = event.async_call_later(
            self.hass,
            delay=current_delay,
            action=lambda dt: self.async_request_refresh(),
        )

        raise UpdateFailed("Failed to update OTE prices")

    @override
    async def _async_update_data(self):
        rates = await self._fetch_data_with_retry()
        return rates


class EntryCoordinator(DataUpdateCoordinator[IntervalTradeRateData | None]):
    def __init__(
        self,
        hass: HomeAssistant,
        spot_coordinator: SpotRateCoordinator,
        fx_coordinator: FxCoordinator | None,
        config: EntryConfig,
    ):
        self._spot_coordinator = spot_coordinator
        self._fx_coordinator = fx_coordinator
        self._spot_rates = None
        self._cnb_rate = None
        self._config = config

        self._unsub_core = spot_coordinator.async_add_listener(self._source_updated)
        self._unsub_fx = (
            fx_coordinator.async_add_listener(self._source_updated)
            if fx_coordinator
            else None
        )

        super().__init__(
            hass,
            logger,
            name=f"Czech Energy Spot Prices [EntryCoordinator {config.unit, config.currency, config.commodity, config.interval}]",
        )

        self._unschedule = event.async_track_utc_time_change(
            hass,
            self.on_schedule,
            minute=[0, 15, 30, 45],
            second=0,
        )

    async def async_stop(self):
        if self._unsub_core:
            self._unsub_core()
            self._unsub_core = None

        if self._unsub_fx:
            self._unsub_fx()
            self._unsub_fx = None

        if self._unschedule:
            self._unschedule()
            self._unschedule = None

    @callback
    def _source_updated(self):
        """When spot or FX data updates → recompute derived data."""
        logger.debug(
            "EntryCoordinator [%s] update by fx or spot rate change",
            self._config,
        )
        data = self._compute_data()
        if data is not None:
            self.async_set_updated_data(data)

    def _compute_data(self):
        if not self._spot_coordinator.data:
            logger.debug("Spot rate data not available")
            return None
        spot_rates = self._spot_coordinator.data

        fx_rate = Decimal(1.0)
        if self._fx_coordinator:
            if not self._fx_coordinator.data:
                logger.debug("Currency rates not available")
                return None

            fx_rates = self._fx_coordinator.data
            eur_rate = fx_rates.get("EUR")
            if eur_rate is None:
                logger.warning("Unable to find conversion rate for EUR")
            else:
                currency_rate = fx_rates.get(self._config.currency)
                if currency_rate is None:
                    logger.warning(
                        f"Unable to find conversion rate for {self._config.currency}"
                    )
                else:
                    fx_rate = eur_rate / currency_rate

        if self._config.unit == EnergyUnit.kWh:
            conversion_rate = fx_rate / Decimal(1000)
        else:
            conversion_rate = fx_rate

        return IntervalTradeRateData(
            spot_rates=spot_rates[self._config.interval],
            config=self._config,
            conversion_rate=conversion_rate,
        )

    @override
    async def _async_update_data(self):
        return self._compute_data()

    async def on_schedule(self, _dt: datetime):
        data = self._compute_data()
        self.async_set_updated_data(data)

    @property
    def buy_template(self) -> Template | None:
        return self._config.buy_template

    @property
    def sell_template(self) -> Template | None:
        return self._config.sell_template

    @property
    def config(self) -> EntryConfig:
        return self._config
