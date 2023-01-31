import sys
import logging
from datetime import date, datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Literal
from decimal import Decimal
import asyncio
import xml.etree.ElementTree as ET

import aiohttp

logger = logging.getLogger(__name__)


QUERY = '''<?xml version="1.0" encoding="UTF-8" ?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:pub="http://www.ote-cr.cz/schema/service/public">
    <soapenv:Header/>
    <soapenv:Body>
        <pub:GetDamPriceE>
            <pub:StartDate>{start}</pub:StartDate>
            <pub:EndDate>{end}</pub:EndDate>
            <pub:InEur>{in_eur}</pub:InEur>
        </pub:GetDamPriceE>
    </soapenv:Body>
</soapenv:Envelope>
'''

# Response example
# <?xml version="1.0" ?>
# <SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
#   <SOAP-ENV:Body>
#     <GetDamPriceEResponse xmlns="http://www.ote-cr.cz/schema/service/public">
#       <Result>
#         <Item>
#           <Date>2022-11-26</Date>
#           <Hour>1</Hour>
#           <Price>5184.14</Price>
#           <Volume>4021.9</Volume>
#         </Item>
#         <Item>
#           <Date>2022-11-26</Date>
#           <Hour>2</Hour>
#           <Price>5133.71</Price>
#           <Volume>3596.0</Volume>
#         </Item>
#         ...
#       </Result>
#     </GetDamPriceEResponse>
#   </SOAP-ENV:Body>
# </SOAP-ENV:Envelope>


class OTEFault(Exception):
    pass


class InvalidFormat(OTEFault):
    pass


class SpotRate:
    ELECTRICITY_PRICE_URL = 'https://www.ote-cr.cz/services/PublicDataService'
    UNIT = 'MWh'

    RateByDatetime = Dict[datetime, Decimal]
    EnergyUnit = Literal['kWh', 'MWh']

    def __init__(self):
        self.timezone = ZoneInfo('Europe/Prague')
        self.utc = ZoneInfo('UTC')

    def get_query(self, start: date, end: date, in_eur: bool) -> str:
        return QUERY.format(start=start.isoformat(), end=end.isoformat(), in_eur='true' if in_eur else 'false')

    async def get_rates(self, start: datetime, in_eur: bool, unit: EnergyUnit) -> RateByDatetime:
        assert start.tzinfo, 'Timezone must be set'
        start_tz = start.astimezone(self.timezone)
        first_day = start_tz.date()
        # From yesterday (as we need it for longest consecutive) till tomorrow (we won't have more data anyway)
        query = self.get_query(first_day - timedelta(days=1), first_day + timedelta(days=1), in_eur=in_eur)

        return await self._get_rates(query, unit)

    async def _download(self, query: str) -> str:
        async with aiohttp.ClientSession() as session:
            async with session.post(self.ELECTRICITY_PRICE_URL, data=query) as response:
                return await response.text()

    async def _get_rates(self, query: str, unit: Literal['kWh', 'MWh']) -> RateByDatetime:
        text = await self._download(query)
        root = ET.fromstring(text)

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

            hour_el = item.find('{http://www.ote-cr.cz/schema/service/public}Hour')
            if hour_el is None or hour_el.text is None:
                raise InvalidFormat('Item has no "Hour" child or is empty')
            current_hour = int(hour_el.text) - 1  # Minus 1 because OTE reports nth hour (starting with 1st) - "1" for 0:00 - 1:00

            price_el = item.find('{http://www.ote-cr.cz/schema/service/public}Price')
            if price_el is None or price_el.text is None:
                raise InvalidFormat('Item has no "Price" child or is empty')
            current_price = Decimal(price_el.text)
            if unit == 'kWh':
                # API returns price for MWh, we need to covert to kWh
                current_price /= Decimal(1000)
            elif unit != 'MWh':
                raise ValueError(f'Invalid unit {unit}')

            start_of_day = datetime.combine(current_date, time(0), tzinfo=self.timezone)
            dt = start_of_day + timedelta(hours=current_hour)
            result[dt.astimezone(self.utc)] = current_price

        return result


if __name__ == '__main__':
    spot_rate = SpotRate()
    if len(sys.argv) >= 2:
        dt = date.fromisoformat(sys.argv[1])
    else:
        dt = date.today()

    in_eur = True

    query = spot_rate.get_query(dt - timedelta(days=1), dt + timedelta(days=1), in_eur=in_eur)
    print(asyncio.run(spot_rate._download(query)))
