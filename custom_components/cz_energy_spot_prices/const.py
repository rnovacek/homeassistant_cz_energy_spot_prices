from enum import StrEnum
from homeassistant.const import Platform

DOMAIN = "cz_energy_spot_prices"

SPOT_ELECTRICTY_COORDINATOR = "spot_electricity_coordinator"
SPOT_GAS_COORDINATOR = "spot_gas_coordinator"
FX_COORDINATOR = "fx_coordinator"
ENTRY_COORDINATOR = "entry_coordinator"

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
]


CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY = "additional_costs_buy_electricity"
CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY = "additional_costs_sell_electricity"
CONF_ADDITIONAL_COSTS_BUY_GAS = "additional_costs_buy_gas"
CONF_CHEAPEST_BLOCKS = "cheapest_blocks"
CONF_ALLOW_CROSS_MIDNIGHT = "allow_cross_midnight"

GLOBAL_ELECTRICITY_SENSOR_FLAG = "global_electricity_binary_sensor_created"
GLOBAL_GAS_SENSOR_FLAG = "global_gas_binary_sensor_created"


class SpotRateIntervalType(StrEnum):
    QuarterHour = "15min"
    Hour = "60min"
    Day = "1day"


class Commodity(StrEnum):
    Electricity = "electricity"
    Gas = "gas"


class Currency(StrEnum):
    EUR = "EUR"
    CZK = "CZK"


class EnergyUnit(StrEnum):
    kWh = "kWh"
    MWh = "MWh"
