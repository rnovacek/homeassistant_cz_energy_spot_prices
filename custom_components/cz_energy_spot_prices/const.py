from homeassistant.const import Platform

DOMAIN = "cz_energy_spot_prices"

PLATFORMS = [
	Platform.BINARY_SENSOR,
	Platform.SENSOR,
]


ADDITIONAL_COSTS_BUY_ELECTRICITY = 'additional_costs_buy_electricity'
ADDITIONAL_COSTS_SELL_ELECTRICITY = 'additional_costs_sell_electricity'
ADDITIONAL_COSTS_BUY_GAS = 'additional_costs_buy_gas'
