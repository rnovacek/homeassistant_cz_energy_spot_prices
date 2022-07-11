from datetime import date, datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo
from typing import Dict, List
from typing_extensions import TypedDict, NotRequired
import asyncio

import aiohttp

class AxisDef(TypedDict):
    decimals: int
    legend: str
    short: NotRequired[bool]
    step: int
    tooltip: NotRequired[str]

class AxisData(TypedDict):
    x: AxisDef
    y: AxisDef
    y2: AxisDef


class DataPoint(TypedDict):
    x: str
    y: float

class DataLine(TypedDict):
    bold: bool
    colour: str
    point: List[DataPoint]
    title: str
    tooltip: str
    tooltipDecimalsY: int
    type: str
    useTooltip: bool
    useY2: bool


class ChartDatapoints(TypedDict):
    dataLine: List[DataLine]

class ChartGraph(TypedDict):
    fullscreen: bool
    title: str
    zoom: bool


class ChartData(TypedDict):
    axis: AxisData
    data: ChartDatapoints
    graph: ChartGraph


class SpotRate:
    ELECTRICITY_PRICE_URL = 'https://www.ote-cr.cz/en/short-term-markets/electricity/day-ahead-market/@@chart-data'
    CURRENCY = 'EUR'
    UNIT = 'MWh'

    def __init__(self):
        self.timezone = ZoneInfo('Europe/Prague')
        self.utc = ZoneInfo('UTC')

    async def get_two_days_rates(self, start: datetime) -> Dict[str, float]:
        assert start.tzinfo, 'Timezone must be set'
        start_tz = start.astimezone(self.timezone)
        first_day = start_tz.date()
        day1_rates, day2_rates = await asyncio.gather(
            self.get_day_rates(day=first_day),
            self.get_day_rates(day=first_day + timedelta(days=1))
        )
        day1_rates.update(day2_rates)
        return day1_rates

    async def get_day_rates(self, day: date) -> Dict[str, float]:
        chart_data = await self.get_day_chart_data(day)
        rate_by_dt: Dict[str, float] = {}

        start_of_day = datetime.combine(day, time(0), tzinfo=self.timezone)
        for line in chart_data['data']['dataLine']:
            if line['tooltip'] == 'Price':
                for point in line['point']:
                    hour = int(point['x']) - 1
                    dt = start_of_day + timedelta(hours=hour)
                    rate_by_dt[dt.astimezone(self.utc).isoformat()] = float(point['y'])

        return rate_by_dt

    async def get_day_chart_data(self, day: date) -> ChartData:
        params = {
            'report_date': day.isoformat(),
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(self.ELECTRICITY_PRICE_URL, params=params) as response:
                return await response.json()


if __name__ == '__main__':
    spot_rate = SpotRate()
    import json
    print(json.dumps(asyncio.run(spot_rate.get_two_days_rates(datetime.now(timezone.utc))), indent=4))
