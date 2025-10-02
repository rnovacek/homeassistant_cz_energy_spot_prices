import sys
import logging
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo
from typing import Literal
from decimal import Decimal
import asyncio
import xml.etree.ElementTree as ET
from homeassistant.helpers.update_coordinator import UpdateFailed

import aiohttp

from .cnb_rate import CnbRate

logger = logging.getLogger(__name__)


QUERY_ELECTRICITY = """<?xml version="1.0" encoding="UTF-8" ?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:pub="http://www.ote-cr.cz/schema/service/public">
    <soapenv:Header/>
    <soapenv:Body>
        <pub:GetDamPricePeriodE>
            <pub:StartDate>{start}</pub:StartDate>
            <pub:EndDate>{end}</pub:EndDate>
            <pub:PeriodResolution>PT15M</pub:PeriodResolution>
        </pub:GetDamPricePeriodE>
    </soapenv:Body>
</soapenv:Envelope>
"""

QUERY_GAS = """<?xml version="1.0" encoding="UTF-8" ?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:pub="http://www.ote-cr.cz/schema/service/public">
    <soapenv:Header/>
    <soapenv:Body>
        <pub:GetImPriceG>
            <pub:StartDate>{start}</pub:StartDate>
            <pub:EndDate>{end}</pub:EndDate>
        </pub:GetImPriceG>
    </soapenv:Body>
</soapenv:Envelope>
"""

# Response example - current

# <?xml version="1.0" ?>
# <SOAP-ENV:Envelope SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/" xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">
#   <SOAP-ENV:Body>
#     <GetDamPricePeriodEResponse xmlns="http://www.ote-cr.cz/schema/service/public">
#       <Result>
#         <Item>
#           <Date>2025-10-01</Date>
#           <PeriodResolution>PT15M</PeriodResolution>
#           <PeriodIndex>1</PeriodIndex>
#           <PeriodInterval>00:00-00:15</PeriodInterval>
#           <Price>97.21</Price>
#           <HourlyPrice>85.56</HourlyPrice>
#           <VolumeTotal>868.850</VolumeTotal>
#         </Item>
#         <Item>
#           <Date>2025-10-01</Date>
#           <PeriodResolution>PT15M</PeriodResolution>
#           <PeriodIndex>2</PeriodIndex>
#           <PeriodInterval>00:15-00:30</PeriodInterval>
#           <Price>88.10</Price>
#           <HourlyPrice>85.56</HourlyPrice>
#           <VolumeTotal>852.475</VolumeTotal>
#         </Item>
#        ...
#       </Result>
#     </GetDamPricePeriodEResponse>
#   </SOAP-ENV:Body>
# </SOAP-ENV:Envelope>


class OTEFault(Exception):
    pass


class InvalidFormat(OTEFault):
    pass


class SpotRate:
    OTE_PUBLIC_URL = 'https://www.ote-cr.cz/services/PublicDataService'
    UNIT = 'MWh'

    RateByDatetime = dict[datetime, Decimal]
    EnergyUnit = Literal['kWh', 'MWh']

    def __init__(self):
        self.timezone = ZoneInfo('Europe/Prague')
        self.utc = ZoneInfo('UTC')

    def get_electricity_query(
        self,
        start: date,
        end: date,
    ) -> str:
        return QUERY_ELECTRICITY.format(
            start=start.isoformat(), end=end.isoformat(), in_eur="true"
        )

    def get_gas_query(self, start: date, end: date) -> str:
        return QUERY_GAS.format(start=start.isoformat(), end=end.isoformat())

    async def _download(self, query: str) -> str:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.OTE_PUBLIC_URL, data=query) as response:
                    return await response.text()
        except aiohttp.ClientError as e:
            raise OTEFault(f'Unable to download rates: {e}')

    def _fromstring(self, text: str):
        try:
            return ET.fromstring(text)
        except Exception as e:
            if 'Application is not available' in text:
                raise UpdateFailed('OTE Portal is currently not available!') from e
            raise UpdateFailed('Failed to parse query response.') from e

    async def noop(self) -> None:
        pass

    async def get_electricity_rates(
        self,
        start: datetime,
        in_eur: bool,
        unit: EnergyUnit,
    ) -> RateByDatetime:
        assert start.tzinfo, 'Timezone must be set'
        start_tz = start.astimezone(self.timezone)
        first_day = start_tz.date()

        # From yesterday (as we need it for longest consecutive) till tomorrow (we won't have more data anyway)
        query_start = first_day - timedelta(days=1)
        query_end = first_day + timedelta(days=1)

        rates_task = self._get_rates(
            self.get_electricity_query(
                query_start,
                query_end,
            ),
            unit,
            kind="electricity_60min",
        )

        if not in_eur:
            currency_task = CnbRate().get_current_rates()
            # Fetch both the prices and the currency rates concurrently
            rates, currency = await asyncio.gather(rates_task, currency_task)
            eur_rate = currency["EUR"]
            # Convert the rates from EUR to CZK
            converted: SpotRate.RateByDatetime = {}
            for dt, value in rates.items():
                converted[dt] = value * eur_rate
            return converted

        else:
            # Just fetch the prices
            rates = await rates_task
            return rates

    async def get_gas_rates(self, start: datetime, in_eur: bool, unit: EnergyUnit) -> RateByDatetime:
        assert start.tzinfo, 'Timezone must be set'
        start_tz = start.astimezone(self.timezone)
        first_day = start_tz.date()
        # yesteday, today and tomorrow (yesterday as we might not have today data for some time)
        query = self.get_gas_query(first_day - timedelta(days=1), first_day + timedelta(days=1))

        rates_task = self._get_rates(query, unit, kind="gas")
        if not in_eur:
            cnb_rate = CnbRate()
            rates, currency_rates = await asyncio.gather(
                rates_task,
                cnb_rate.get_current_rates(),
            )
            eur_rate = currency_rates['EUR']
            converted: SpotRate.RateByDatetime = {}
            for dt, value in rates.items():
                converted[dt] = value * eur_rate
            return converted
        else:
            rates = await rates_task

        return rates

    async def _get_rates(
        self,
        query: str,
        unit: Literal["kWh", "MWh"],
        kind: Literal[
            "electricity_legacy", "electricity_60min", "electricity_15min", "gas"
        ] = "electricity_15min",
    ) -> RateByDatetime:
        text = await self._download(query)
        root = self._fromstring(text)
        fault = root.find('.//{http://schemas.xmlsoap.org/soap/envelope/}Fault')
        if fault:
            faultstring = fault.find('faultstring')
            error = 'Unknown error'
            if faultstring is not None:
                error = faultstring.text
            else:
                error = text
            raise OTEFault(error)

        result: SpotRate.RateByDatetime = {}
        for item in root.findall('.//{http://www.ote-cr.cz/schema/service/public}Item'):
            date_el = item.find('{http://www.ote-cr.cz/schema/service/public}Date')
            if date_el is None or date_el.text is None:
                raise InvalidFormat('Item has no "Date" child or is empty')
            current_date = date.fromisoformat(date_el.text)

            if kind == "electricity_legacy":
                # Legacy API has "Hour" element - index starting from 1
                hour_el = item.find('{http://www.ote-cr.cz/schema/service/public}Hour')
                if hour_el is None or hour_el.text is None:
                    current_hour = 0
                    logger.warning('Item has no "Hour" child or is empty: %s', current_date)
                else:
                    current_hour = int(hour_el.text) - 1  # Minus 1 because OTE reports nth hour (starting with 1st) - "1" for 0:00 - 1:00
                current_minute = 0
            elif kind == "electricity_15min" or kind == "electricity_60min":
                # We can use either PeriodInterval or PeriodIndex to get the interval, but the documentation doesn't specify handling of daylight saving time
                # so we'll use PeriodIndex as that one should be safer - 92 or 100 intervals per day instead of 96 on usual days
                period_index_el = item.find(
                    "{http://www.ote-cr.cz/schema/service/public}PeriodIndex"
                )
                if period_index_el is None or not period_index_el.text:
                    logger.warning(
                        'Item has no "PeriodIndex" child or is empty: %s', current_date
                    )
                    current_hour = 0
                    current_minute = 0
                else:
                    try:
                        period_index = int(period_index_el.text)
                    except ValueError as e:
                        raise InvalidFormat(
                            f"Invalid PeriodIndex {period_index_el.text} for date {current_date}"
                        ) from e

                    if period_index < 1 or period_index > 100:
                        raise InvalidFormat(
                            f"Invalid PeriodIndex {period_index} for date {current_date}"
                        )

                    # Each period is 15 minutes, so we can calculate the hour as (period_index - 1) // 4
                    current_hour = (period_index - 1) // 4
                    current_minute = ((period_index - 1) % 4) * 15
            elif kind == "gas":
                # Gas rates doesn't have hours, skip it
                current_hour = 0
                current_minute = 0
            else:
                raise ValueError(f"Invalid kind {kind}")  # pyright: ignore[reportUnreachable]

            price_el = item.find('{http://www.ote-cr.cz/schema/service/public}Price')
            if price_el is None or price_el.text is None:
                logger.info(
                    'Item has no "Price" child or is empty: %s %s',
                    current_date,
                    current_hour,
                )
                continue
            current_price = Decimal(price_el.text)

            current_hourly_price = Decimal(0)
            if kind == "electricity_60min":
                hourly_price_el = item.find(
                    "{http://www.ote-cr.cz/schema/service/public}HourlyPrice"
                )
                if hourly_price_el is None or hourly_price_el.text is None:
                    logger.info(
                        'Item has no "HourlyPrice" child or is empty: %s %s',
                        current_date,
                        current_hour,
                    )
                    continue
                current_hourly_price = Decimal(hourly_price_el.text)

            if unit == 'kWh':
                # API returns price for MWh, we need to covert to kWh
                current_price /= Decimal(1000)
                current_hourly_price /= Decimal(1000)
            elif unit != 'MWh':
                raise ValueError(f"Invalid unit {unit}")  # pyright: ignore[reportUnreachable]

            start_of_day = datetime.combine(current_date, time(0), tzinfo=self.timezone)

            if kind != "electricity_60min":
                # Because of daylight saving time, we need to convert time to UTC
                dt = start_of_day.astimezone(self.utc) + timedelta(
                    hours=current_hour, minutes=current_minute
                )
                result[dt] = current_price
            else:
                # We need to use HourlyPrice for 60min intervals, so we need to create 4 entries for each hour
                dt = start_of_day.astimezone(self.utc) + timedelta(hours=current_hour)
                result[dt] = current_hourly_price

        return result


if __name__ == '__main__':
    spot_rate = SpotRate()
    tz = ZoneInfo('Europe/Prague')
    if len(sys.argv) >= 2:
        use_date = date.fromisoformat(sys.argv[1])
        dt = datetime.now(tz=tz).replace(year=use_date.year, month=use_date.month, day=use_date.day)
    else:
        dt = datetime.now(tz=tz)

    in_eur = False

    def print_rates(
        rates_eur: dict[datetime, Decimal], rates_czk: dict[datetime, Decimal]
    ):
        for dt, eur in rates_eur.items():
            czk = rates_czk[dt]
            print(f'{dt.isoformat():30s} {eur:10.4f} {czk:10.4f}')

    # query = spot_rate.get_electricity_query(dt - timedelta(days=1), dt + timedelta(days=1), in_eur=in_eur)
    rates_eur = asyncio.run(
        spot_rate.get_electricity_rates(dt, in_eur=False, unit="kWh")
    )
    # rates_czk = asyncio.run(spot_rate.get_electricity_rates(dt, in_eur=False, unit='kWh'))

    print('ELECTRICITY')
    for dt, eur in rates_eur.items():
        print(f"{dt.isoformat():30s} {eur:10.4f}")

    # rates_eur = asyncio.run(spot_rate.get_gas_rates(dt, in_eur=True, unit='kWh'))
    # rates_czk = asyncio.run(spot_rate.get_gas_rates(dt, in_eur=False, unit='kWh'))

    # print('GAS')
    # print_rates(rates_eur, rates_czk)
