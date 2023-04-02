import pytest

from homeassistant.core import HomeAssistant

from custom_components.cz_energy_spot_prices.sensor import SpotRateElectricitySensor, Settings


@pytest.fixture
async def hass():
    h = HomeAssistant()
    h.config.time_zone = 'Europe/Prague'
    return h

