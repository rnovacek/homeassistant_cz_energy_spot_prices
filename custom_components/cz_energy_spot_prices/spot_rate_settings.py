from dataclasses import dataclass
from zoneinfo import ZoneInfo

@dataclass
class SpotRateSettings:
    currency: str
    currency_human: str
    unit: str
    timezone: str
    zoneinfo: ZoneInfo
