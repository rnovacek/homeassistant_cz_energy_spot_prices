from datetime import date, timedelta
from typing import TypedDict, cast
from zoneinfo import ZoneInfo
from decimal import Decimal
import aiohttp

from homeassistant.util.dt import utcnow


class InvalidDateError(Exception):
    """Exception raised for invalid date format in CNB API response."""

    pass


class Rate(TypedDict):
    validFor: str
    order: int
    country: str
    currency: str
    amount: int
    currencyCode: str
    rate: float


class Rates(TypedDict):
    rates: list[Rate]


class RateError(TypedDict):
    description: str
    errorCode: str
    happenedAt: str
    endPoint: str
    messageId: str


class CnbRateError(Exception):
    pass


class CnbRate:
    RATES_URL: str = "https://api.cnb.cz/cnbapi/exrates/daily"

    def __init__(self) -> None:
        self._timezone: ZoneInfo = ZoneInfo("Europe/Prague")
        self._rates: dict[str, Decimal] = {}
        self._last_checked_date: date | None = None

    async def download_rates(self, day: date) -> Rates:
        params = {"date": day.isoformat()}

        text: Rates
        async with aiohttp.ClientSession() as session:
            async with session.get(self.RATES_URL, params=params) as response:
                if response.status > 299:
                    if response.status == 400:
                        error = cast(RateError, await response.json())
                        if error.get("errorCode") == "VALIDATION_ERROR":
                            raise InvalidDateError(f"Invalid date format: {day}")

                    raise CnbRateError(
                        f"Error {response.status} while downloading rates"
                    )
                text = cast(Rates, await response.json())
        return text

    async def get_day_rates(self, day: date) -> dict[str, Decimal]:
        rates: dict[str, Decimal] = {
            "CZK": Decimal(1),
        }

        cnb_rates: Rates | None = None
        for previous_day in range(0, 7):
            try:
                cnb_rates = await self.download_rates(
                    day - timedelta(days=previous_day)
                )
                break
            except InvalidDateError:
                continue

        if not cnb_rates:
            raise CnbRateError("Could not download CNB rates for last 7 days")

        for rate in cnb_rates["rates"]:
            rates[rate["currencyCode"]] = Decimal(rate["rate"])

        return rates

    async def get_current_rates(self):
        now = utcnow()
        day = now.astimezone(self._timezone).date()

        # Update if needed
        if self._last_checked_date is None or day != self._last_checked_date:
            self._rates = await self.get_day_rates(day)
            self._last_checked_date = day

        return self._rates


if __name__ == '__main__':
    import asyncio
    cnb_rate = CnbRate()
    rates = asyncio.run(cnb_rate.get_current_rates())
    for iso, rate in rates.items():
        print(iso, rate)
