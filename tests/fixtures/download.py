import asyncio
from datetime import date, timedelta
import json
from typing import Literal
from pathlib import Path
import xml.dom.minidom

from custom_components.cz_energy_spot_prices.spot_rate import SpotRate
from custom_components.cz_energy_spot_prices.cnb_rate import CnbRate

base_date = date(2025, 10, 26)

async def run_spot(spot_rate: SpotRate, resource: Literal["electricity", "gas"]):
    if resource == 'electricity':
        query = spot_rate.get_electricity_query(
            base_date - timedelta(days=1), base_date + timedelta(days=1)
        )
    else:
        query = spot_rate.get_gas_query(base_date - timedelta(days=1), base_date + timedelta(days=1))

    text = await spot_rate._download(query)  # pyright: ignore[reportPrivateUsage]

    filename = Path(__file__).parent / f"ote-{resource}-{base_date.isoformat()}.xml"

    dom = xml.dom.minidom.parseString(text)
    pretty = dom.toprettyxml()

    with open(filename, 'w') as f:
        _ = f.write(pretty)


async def run_cnb(cnb_rate: CnbRate):
    rates = await cnb_rate.download_rates(base_date)
    filename = Path(__file__).parent / f"cnb-{base_date.isoformat()}.json"

    with open(filename, 'w') as f:
        json.dump(rates, f, indent=4)


async def run_all(spot_rate: SpotRate, cnb_rate: CnbRate):
    await run_spot(spot_rate, "electricity")
    await run_spot(spot_rate, "gas")
    await run_cnb(cnb_rate)

spot_rate = SpotRate()
cnb_rate = CnbRate()
asyncio.run(run_all(spot_rate, cnb_rate))
