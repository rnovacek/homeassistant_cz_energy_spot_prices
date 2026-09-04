from typing import Callable
from random import randint
import aiohttp.client_exceptions
import asyncio
import logging
from datetime import datetime, timedelta, timezone, time
from decimal import Decimal, InvalidOperation
from typing import Any, cast, final, override
from zoneinfo import ZoneInfo

from asyncio import timeout

from attr import dataclass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.template import Template
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
    event,
)
from homeassistant.util.dt import now

from .cheapest_blocks import PriceBlockSearch, find_price_block, resolve_search_window
from .cnb_rate import CnbRate
from .const import (
    Commodity,
    Currency,
    DOMAIN,
    EnergyUnit,
    SpotRateIntervalType,
)
from .spot_rate import (
    RateByDatetime,
    RatesByInterval,
    SpotRate,
    OTEFault,
    is_unpublished_gas_price,
)

_LOGGER = logging.getLogger(__name__)

PRAGUE_TZ = ZoneInfo("Europe/Prague")

STORAGE_VERSION = 1


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
    cheapest_block_searches: list[PriceBlockSearch]
    cheapest_blocks_cross_midnight: bool = False

    def __attrs_post_init__(self) -> None:
        """Normalize direct/test construction through the persisted-data parser."""
        normalized: list[PriceBlockSearch] = []
        for raw_search in cast(
            list[PriceBlockSearch | dict[str, Any]], self.cheapest_block_searches
        ):
            if isinstance(raw_search, PriceBlockSearch):
                normalized.append(raw_search)
                continue
            search = PriceBlockSearch.from_mapping(raw_search, interval=self.interval)
            if search is not None:
                normalized.append(search)
        self.cheapest_block_searches = normalized

    def all_cheapest_blocks(self) -> list[int | None]:
        """Return all cheapest blocks, including blocks that take just one interval (it's value is None).

        Kept for backward compatibility with code that expects old-style blocks.
        """
        # Insert None first - it means cheapest interval (hour for hourly interval, 15 min block for 15min interval)
        cheapest_blocks: list[int | None] = [None]
        for search in self.cheapest_block_searches:
            block = search.legacy_block_length
            if block is None:
                continue

            if block < 1 or block > 23:
                continue

            if block == 1 and self.interval == SpotRateIntervalType.Hour:
                continue

            if block in cheapest_blocks:
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
        min_interval = None
        for dt, interval in self.interval_by_dt.items():
            if min_dt is None or dt < min_dt:
                min_dt = dt
                min_interval = interval
        return min_interval


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
        self.search_windows: dict[str, Window] = {}

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
                                # Gas templates document this timestamp as
                                # ``day``. Keep ``hour`` available as well for
                                # backwards compatibility with existing
                                # templates and the shared interval model.
                                "day": utc_hour,
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
                # Cross-midnight mode enabled for multi-hour blocks
                # First, calculate cheapest block using all of today's data
                intervals_for_cheapest_today = self._today_day.interval_by_dt.copy()

                try:
                    today_window = find_cheapest_window(
                        intervals_for_cheapest_today,
                        hours=block,
                        interval=config.interval,
                    )

                    # Check if today's cheapest block has already passed
                    block_has_passed = today_window.end <= self.now

                    if block_has_passed and self._tomorrow_day is not None:
                        # Block has passed AND tomorrow data is available
                        # Calculate with only potential cross-midnight data:
                        # Include only the last N hours of today that could create a cross-midnight window

                        # Find midnight today in local time
                        today_date = self.now.date()
                        midnight_today = datetime.combine(
                            today_date + timedelta(days=1),
                            time(0, 0, 0),
                            tzinfo=config.zoneinfo,
                        ).astimezone(timezone.utc)

                        # Calculate the earliest time that could start a cross-midnight block
                        # For a block to cross midnight, it needs to start at most N-1 hours before midnight
                        # Example: 3-hour block starting at 22:00 → 22:00, 23:00, 00:00 (crosses)
                        #          3-hour block starting at 21:00 → 21:00, 22:00, 23:00 (doesn't cross)
                        if config.interval == SpotRateIntervalType.Hour:
                            # For hourly intervals: N-hour block has N intervals
                            # Last interval is at start + (N-1) hours
                            # To cross midnight: start + (N-1) >= midnight → start >= midnight - (N-1)
                            earliest_cross_midnight_start = midnight_today - timedelta(
                                hours=block - 1
                            )
                        else:
                            # For quarter-hour intervals: N-hour block has N*4 intervals (each 15 min)
                            # Last interval is at start + (N*4-1) * 15 minutes = start + (N hours - 15 min)
                            # To cross midnight: start + (N hours - 15 min) >= midnight
                            # → start >= midnight - N hours + 15 min
                            earliest_cross_midnight_start = (
                                midnight_today
                                - timedelta(hours=block)
                                + timedelta(minutes=15)
                            )

                        # Only include intervals from today that could cross midnight
                        intervals_for_cheapest: dict[datetime, SpotRateInterval] = {
                            dt: interval
                            for dt, interval in self._today_day.interval_by_dt.items()
                            if interval.dt_utc >= earliest_cross_midnight_start
                        }
                        intervals_for_cheapest.update(self._tomorrow_day.interval_by_dt)
                        _LOGGER.debug(
                            "Cheapest %s-hour block: has passed, using %d cross-midnight intervals from today (from %s) + %d from tomorrow",
                            block,
                            len(
                                [
                                    dt
                                    for dt, interval in self._today_day.interval_by_dt.items()
                                    if interval.dt_utc >= earliest_cross_midnight_start
                                ]
                            ),
                            earliest_cross_midnight_start.astimezone(
                                config.zoneinfo
                            ).strftime("%H:%M"),
                            len(self._tomorrow_day.interval_by_dt),
                        )
                    else:
                        # Block hasn't passed OR tomorrow data not available
                        # Use all of today's data (with tomorrow if available)
                        intervals_for_cheapest = intervals_for_cheapest_today.copy()
                        if self._tomorrow_day is not None:
                            intervals_for_cheapest.update(
                                self._tomorrow_day.interval_by_dt
                            )
                            _LOGGER.debug(
                                "Cheapest %s-hour block: still valid, using all %d intervals from today + %d from tomorrow",
                                block,
                                len(self._today_day.interval_by_dt),
                                len(self._tomorrow_day.interval_by_dt),
                            )
                        else:
                            _LOGGER.debug(
                                "Cheapest %s-hour block: using all %d intervals from today",
                                block,
                                len(intervals_for_cheapest),
                            )
                except ValueError:
                    # Could not calculate today's window, use all available data
                    intervals_for_cheapest = self._today_day.interval_by_dt.copy()
                    if self._tomorrow_day is not None:
                        intervals_for_cheapest.update(self._tomorrow_day.interval_by_dt)
                    _LOGGER.debug(
                        "Cheapest %s-hour block: could not calculate today's window, using all available data",
                        block,
                    )
            else:
                # Cross-midnight disabled OR single interval (block is None)
                # Always use ALL of today's data for consistency
                intervals_for_cheapest = self._today_day.interval_by_dt.copy()
                _LOGGER.debug(
                    "Cheapest %s block: no cross_midnight, using all %d intervals from today",
                    "interval" if block is None else f"{block}-hour",
                    len(intervals_for_cheapest),
                )

            # Validate we have intervals
            if not intervals_for_cheapest:
                _LOGGER.warning(
                    "No intervals available for cheapest %s block calculation",
                    "interval" if block is None else f"{block}-hour",
                )
                continue
            try:
                window = find_cheapest_window(
                    intervals_for_cheapest,
                    hours=block,
                    interval=config.interval,
                )
                self.cheapest_windows[block] = window
            except ValueError:
                if block is None:
                    _LOGGER.error("Unable to find cheapest interval")
                else:
                    _LOGGER.error("Unable to find cheapest %s hour block", block)

        # Compute configured lowest/highest price windows.
        sorted_intervals = sorted(self.interval_by_dt.items(), key=lambda item: item[0])
        available_start = sorted_intervals[0][0] if sorted_intervals else None
        available_end = None
        interval_seconds = (
            900 if config.interval == SpotRateIntervalType.QuarterHour else 3600
        )
        if sorted_intervals:
            available_end = sorted_intervals[-1][0] + timedelta(
                seconds=interval_seconds
            )
        for search in config.cheapest_block_searches:
            window_bounds = resolve_search_window(
                search,
                self.now,
                available_end,
                available_start,
            )
            if window_bounds is None:
                _LOGGER.debug("Search %s: could not resolve window", search.id)
                continue

            window_start, window_end = window_bounds
            result = find_price_block(
                [(dt, interval.price) for dt, interval in sorted_intervals],
                window_start.astimezone(timezone.utc),
                window_end.astimezone(timezone.utc),
                search.length_hours,
                search.objective,
                interval_seconds=interval_seconds,
            )
            if result is None:
                _LOGGER.debug(
                    "Search %s: no %s price block found in window %s - %s",
                    search.id,
                    search.objective,
                    window_start,
                    window_end,
                )
                continue

            self.search_windows[search.id] = Window(
                start=result["start"],
                end=result["end"],
                prices=result["prices"],
            )

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
    """Find a legacy cheapest window using the shared block-search engine."""
    sorted_intervals = sorted(interval_by_dt.items())
    if not sorted_intervals:
        raise ValueError()
    interval_hours = 0.25 if interval == SpotRateIntervalType.QuarterHour else 1.0
    length_hours = float(hours) if hours is not None else interval_hours
    interval_delta = timedelta(hours=interval_hours)
    result = find_price_block(
        [(dt, value.price) for dt, value in sorted_intervals],
        sorted_intervals[0][0],
        sorted_intervals[-1][0] + interval_delta,
        length_hours,
        interval_seconds=int(interval_delta.total_seconds()),
    )
    if result is None:
        raise ValueError()
    return Window(result["start"], result["end"], result["prices"])


@final
class SpotRateCoordinator(DataUpdateCoordinator[RatesByInterval | None]):
    # OTE says that data for the next day should be available at 13:02 CE(S)T (Prague) time,
    # but in reality they never are. We'll start the update 13:10 with random 2 minutes jitter and
    # then try every 2 minutes until we get next day data.
    DATA_AVAILABLE_TIME = time(13, 10)
    JITTER_SECONDS = 120
    DATA_RESCHEDULE_DELAY = 120
    # Cap the exponential retry delay to one hour. Without this the delay grows
    # without bound (2**N seconds), reaching multiple days after ~20 attempts
    # of a flaky upstream service.
    MAX_RETRY_DELAY = 3600

    def __init__(
        self,
        hass: HomeAssistant,
        commodity: Commodity,
    ):
        _LOGGER.debug("SpotRateCoordinator[%s].__init__", commodity)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=None,
            name=f"Czech Energy Spot Prices [SpotRateCoordinator] for {commodity}",
        )
        self.hass = hass
        self._spot_rate = SpotRate(session=async_get_clientsession(hass))
        self._spot_rate_data: RatesByInterval | None = None
        self._update_schedule = None
        self._retry_attempt = 0
        self._commodity = commodity
        self._next_update: datetime | None = None

        # Persist last known good data so a restart of Home Assistant does not
        # leave the integration without prices until OTE is queried again
        # (especially around midnight).
        self._store: Store[dict[str, dict[str, str]]] = Store(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.spot_rates.{commodity.value}",
        )

    @staticmethod
    def _serialize(data: RatesByInterval) -> dict[str, dict[str, str]]:
        """Serialize rates dict for persistence."""
        return {
            interval.value: {
                dt.isoformat(): str(price) for dt, price in interval_data.items()
            }
            for interval, interval_data in data.items()
        }

    @staticmethod
    def _deserialize(raw: dict[str, dict[str, str]]) -> RatesByInterval:
        """Deserialize previously persisted rates."""
        return {
            SpotRateIntervalType(interval_key): {
                datetime.fromisoformat(dt_iso): Decimal(price_str)
                for dt_iso, price_str in dt_map.items()
            }
            for interval_key, dt_map in raw.items()
        }

    async def async_load_persisted(self) -> bool:
        """Load previously persisted rates so sensors have data immediately
        after a restart, even if OTE is unreachable.

        Returns True if data was loaded successfully.
        """
        if self._spot_rate_data is not None:
            return True

        raw = await self._store.async_load()
        if not raw:
            return False

        try:
            loaded = self._deserialize(raw)
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            InvalidOperation,
        ) as exc:
            _LOGGER.warning(
                "Failed to deserialize persisted spot rates for %s: %s",
                self._commodity,
                exc,
            )
            return False

        if self._commodity == Commodity.Gas:
            gas_rates = loaded.get(SpotRateIntervalType.Day)
            if gas_rates is None:
                _LOGGER.warning("Persisted gas spot rates contain no daily data")
                return False
            zero_datetimes = [
                dt
                for dt, price in gas_rates.items()
                if is_unpublished_gas_price(self._commodity, price)
            ]
            for dt in zero_datetimes:
                gas_rates.pop(dt)
            if zero_datetimes:
                _LOGGER.info(
                    "Discarded %d unpublished zero gas price(s) from persisted data",
                    len(zero_datetimes),
                )

        self._spot_rate_data = loaded
        self.async_set_updated_data(loaded)
        _LOGGER.debug(
            "SpotRateCoordinator[%s] loaded persisted data with %d intervals",
            self._commodity,
            sum(len(v) for v in loaded.values()),
        )
        return True

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
                # Update time already happened today but we don't have tomorrow data, schedule update soon
                local_target = now_prague + timedelta(
                    seconds=self.DATA_RESCHEDULE_DELAY
                )
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

        self.schedule_update(point_in_time=utc_time)
        return utc_time

    async def async_stop(self):
        """Cancel scheduled jobs."""
        _LOGGER.debug("SpotRateCoordinator[%s].async_stop", self._commodity)
        if self._update_schedule:
            self._update_schedule()
            self._update_schedule = None

    @callback
    def on_schedule(self, dt: datetime):
        _LOGGER.debug(
            "SpotRateCoordinator[%s].on_schedule called at %s", self._commodity, dt
        )

        if self._update_schedule:
            self._update_schedule()
            self._update_schedule = None

        _ = self.hass.async_create_task(self.async_request_refresh())

    async def _fetch_data(self):
        _LOGGER.debug("SpotRateCoordinator[%s]._fetch_data", self._commodity)

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

        _LOGGER.debug("SpotRateCoordinator[%s]._fetch_data_with_retry", self._commodity)
        current_delay = min(2**self._retry_attempt, self.MAX_RETRY_DELAY)
        try:
            async with timeout(30):
                data = await self._fetch_data()
                self._retry_attempt = 0
                return data

        except (
            OTEFault,
            aiohttp.client_exceptions.ClientError,
            asyncio.TimeoutError,
        ) as e:
            _LOGGER.warning(
                "Failed to update OTE prices, will retry in %d seconds: %s",
                current_delay,
                e,
            )

        except Exception:
            _LOGGER.exception(
                "OTE request failed unexpectedly, will retry in %d seconds",
                current_delay,
            )

        self._retry_attempt += 1

        self.schedule_update(delay=current_delay)

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
            noon_tomorrow = (
                now(PRAGUE_TZ).replace(hour=12, minute=0, second=0, microsecond=0)
                + timedelta(days=1)
            ).astimezone(timezone.utc)

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

    def schedule_update(
        self, *, delay: float | None = None, point_in_time: datetime | None = None
    ):
        """Schedule on_schedule to be called after delay seconds or at point_in_time."""
        if self._update_schedule:
            self._update_schedule()
            self._update_schedule = None

        if point_in_time is not None:
            _LOGGER.debug(
                "SpotRateCoordinator[%s] scheduling update at %s",
                self._commodity,
                point_in_time,
            )
            self._update_schedule = event.async_track_point_in_utc_time(
                hass=self.hass,
                action=self.on_schedule,
                point_in_time=point_in_time,
            )
            self._next_update = point_in_time
        elif delay is not None:
            _LOGGER.debug(
                "SpotRateCoordinator[%s] scheduling update in %s seconds",
                self._commodity,
                delay,
            )
            self._update_schedule = event.async_call_later(
                self.hass,
                delay=delay,
                action=self.on_schedule,
            )
            self._next_update = now() + timedelta(seconds=delay)
        else:
            raise ValueError("Either delay or point_in_time must be provided")

    @override
    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """

        _LOGGER.debug("SpotRateCoordinator[%s]._async_update_data", self._commodity)

        new_data = await self._fetch_data_with_retry()
        if new_data is None:
            # Fetch failed; preserve previously loaded/persisted data so sensors
            # do not become unavailable when OTE is temporarily down.
            if self._spot_rate_data is not None:
                _LOGGER.debug(
                    "SpotRateCoordinator[%s] fetch failed, keeping previously loaded data",
                    self._commodity,
                )
                return self._spot_rate_data
            return None

        self._spot_rate_data = new_data

        # Persist successful fetches so the data survives Home Assistant restarts.
        try:
            await self._store.async_save(self._serialize(new_data))
        except Exception:  # pragma: no cover - defensive, storage is local
            _LOGGER.exception(
                "Failed to persist spot rate data for %s", self._commodity
            )

        if not self.has_tomorrow_data() and self.is_tomorrow_data_available():
            # Tomorrow data should be available but are not => schedule update soon
            _LOGGER.info(
                "SpotRateCoordinator[%s] tomorrow data should be available in OTE but are not => rescheduling in 2 minutes",
                self._commodity,
            )
            # Try again in 2 minutes
            self.schedule_update(delay=self.DATA_RESCHEDULE_DELAY)
        else:
            # Schedule the update for tommorow
            dt = self._schedule_next_update()
            _LOGGER.info(
                "SpotRateCoordinator[%s] data updated, scheduling next update at %s",
                self._commodity,
                dt,
            )

        return self._spot_rate_data


class FxCoordinator(DataUpdateCoordinator[dict[str, Decimal] | None]):
    # Cap the exponential retry delay to one hour. See ``SpotRateCoordinator``.
    MAX_RETRY_DELAY = 3600

    def __init__(
        self,
        hass: HomeAssistant,
    ):
        """Initialize my coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=None,
            name="Czech Energy Spot Prices [FxCoordinator]",
        )

        self._cnb = CnbRate(session=async_get_clientsession(hass))
        self._retry_attempt = 0
        self._store: Store[dict[str, str]] = Store(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.fx_rates",
        )

        # Update on midnight local (hass) time
        self._update_schedule: Callable[[], None] | None = (
            event.async_track_time_change(
                hass=self.hass,
                action=self.on_schedule,
                hour=0,
                minute=0,
                second=0,
            )
        )

    @staticmethod
    def _serialize(data: dict[str, Decimal]) -> dict[str, str]:
        """Serialize currency rates for persistence."""
        return {currency: str(rate) for currency, rate in data.items()}

    @staticmethod
    def _deserialize(raw: dict[str, str]) -> dict[str, Decimal]:
        """Deserialize previously persisted currency rates."""
        return {currency: Decimal(rate) for currency, rate in raw.items()}

    async def async_load_persisted(self) -> bool:
        """Load the last known good CNB rates from Home Assistant storage."""
        if self.data is not None:
            return True

        raw = await self._store.async_load()
        if not raw:
            return False

        try:
            loaded = self._deserialize(raw)
        except (InvalidOperation, TypeError, AttributeError) as exc:
            _LOGGER.warning("Failed to deserialize persisted CNB FX rates: %s", exc)
            return False

        if not loaded:
            return False

        self.async_set_updated_data(loaded)
        _LOGGER.debug(
            "FxCoordinator loaded persisted data with %d currencies", len(loaded)
        )
        return True

    async def async_stop(self):
        """Cancel scheduled jobs."""
        if self._update_schedule:
            _LOGGER.debug("Unscheduling FX coordinator")
            self._update_schedule()
            self._update_schedule = None

    @callback
    def on_schedule(self, _dt: datetime):
        _ = self.hass.async_create_task(self.async_request_refresh())

    async def _fetch_data(self):
        _LOGGER.debug("FxCoordinator._fetch_data")

        rates = await self._cnb.get_current_rates()
        return rates

    async def _fetch_data_with_retry(self):
        _LOGGER.debug("FxCoordinator._fetch_data_with_retry")
        current_delay = min(2**self._retry_attempt, self.MAX_RETRY_DELAY)
        try:
            async with timeout(30):
                data = await self._fetch_data()
                self._retry_attempt = 0
                return data

        except (
            OTEFault,
            aiohttp.client_exceptions.ClientError,
            asyncio.TimeoutError,
        ) as e:
            _LOGGER.warning(
                "Failed to update CNB FX rates, will retry in %d seconds: %s",
                current_delay,
                e,
            )

        except Exception:
            _LOGGER.exception(
                "CNB FX request failed unexpectedly, will retry in %d seconds",
                current_delay,
            )

        self._retry_attempt += 1

        # Schedule retry without overwriting the midnight scheduler stored in
        # ``self._update_schedule``. Otherwise the original midnight callback
        # would leak (its cancel handle would be lost) and we'd also lose the
        # daily refresh after the first failure.
        event.async_call_later(
            self.hass,
            delay=current_delay,
            action=lambda dt: self.async_request_refresh(),
        )

        raise UpdateFailed("Failed to update CNB FX rates")

    @override
    async def _async_update_data(self):
        rates = await self._fetch_data_with_retry()

        try:
            await self._store.async_save(self._serialize(rates))
        except Exception:  # pragma: no cover - defensive, storage is local
            _LOGGER.exception("Failed to persist CNB FX rates")

        return rates


class EntryCoordinator(DataUpdateCoordinator[IntervalTradeRateData | None]):
    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        spot_coordinator: SpotRateCoordinator,
        fx_coordinator: FxCoordinator | None,
        config: EntryConfig,
    ):
        self._spot_coordinator = spot_coordinator
        self._fx_coordinator = fx_coordinator
        self._spot_rates = None
        self._cnb_rate = None
        self._config = config
        self._loaded_entry_data = dict(config_entry.data)
        self._loaded_entry_options = dict(config_entry.options)

        self._unsub_core: Callable[[], None] | None = (
            spot_coordinator.async_add_listener(self._source_updated)
        )
        self._unsub_fx = (
            fx_coordinator.async_add_listener(self._source_updated)
            if fx_coordinator
            else None
        )

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"Czech Energy Spot Prices [EntryCoordinator {config.unit, config.currency, config.commodity, config.interval}]",
        )

        self._unschedule: Callable[[], None] | None = event.async_track_utc_time_change(
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
        _LOGGER.debug(
            "EntryCoordinator [%s] update by fx or spot rate change",
            self._config,
        )
        data = self._compute_data()
        if data is not None:
            self.async_set_updated_data(data)

    def _compute_data(self):
        if not self._spot_coordinator.data:
            _LOGGER.debug("Spot rate data not available")
            return None
        spot_rates = self._spot_coordinator.data

        fx_rate = Decimal(1)
        if self._fx_coordinator:
            if not self._fx_coordinator.data:
                _LOGGER.debug("Currency rates not available")
                return None

            fx_rates = self._fx_coordinator.data
            eur_rate = fx_rates.get("EUR")
            if eur_rate is None:
                _LOGGER.warning(
                    "Unable to find conversion rate for EUR, skipping update to avoid publishing incorrect prices"
                )
                return None

            currency_rate = fx_rates.get(self._config.currency)
            if currency_rate is None:
                _LOGGER.warning(
                    "Unable to find conversion rate for %s, skipping update to avoid publishing incorrect prices",
                    self._config.currency,
                )
                return None

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

    def parent_config_is_current(self, config_entry: ConfigEntry) -> bool:
        """Return whether an update changed neither parent data nor options."""
        return (
            dict(config_entry.data) == self._loaded_entry_data
            and dict(config_entry.options) == self._loaded_entry_options
        )

    @callback
    def async_replace_price_block_searches(
        self, searches: list[PriceBlockSearch]
    ) -> None:
        """Recompute derived data after a subentry-only configuration change."""
        self._config.cheapest_block_searches = searches
        data = self._compute_data()
        if data is not None:
            self.async_set_updated_data(data)
