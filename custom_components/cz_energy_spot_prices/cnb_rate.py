from datetime import date, datetime, timezone
from typing import Dict
from zoneinfo import ZoneInfo
from decimal import Decimal
import aiohttp


class CnbRate:
    RATES_URL = 'https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/denni_kurz.txt'

    def __init__(self) -> None:
        self._timezone = ZoneInfo('Europe/Prague')
        self._rates: Dict[str, Decimal] = {}
        self._last_checked_date = None

    async def download_rates(self, day: date) -> str:
        params = {
            'date': day.strftime('%d.%m.%Y')
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(self.RATES_URL, params=params) as response:
                text = await response.text()
        return text

    async def get_day_rates(self, day: date) -> Dict[str, Decimal]:
        rates: Dict[str, Decimal] = {
            'CZK': Decimal(1),
        }

        text = await self.download_rates(day)
        lines = text.split('\n')
        # First two lines are just headers, skip them
        for line in lines[2:]:
            if not line:
                # last line is empty
                continue
            coutry, currency, amount, iso, rate = line.split('|')
            rates[iso] = Decimal(rate.replace(',', '.'))
        return rates

    async def get_current_rates(self):
        now = datetime.now(timezone.utc)
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