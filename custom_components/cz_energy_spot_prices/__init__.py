"""Czech Spot Energy Prices"""

import logging

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT

from .const import DOMAIN, PLATFORMS
from .coordinator import SpotRateCoordinator
from .spot_rate import SpotRate


logger = logging.getLogger(__name__)

type SpotRateConfigEntry = ConfigEntry[SpotRateCoordinator]


async def options_update_listener(hass: HomeAssistant, config_entry: ConfigEntry):
    """Handle options update."""
    logger.debug('options_update_listener', config_entry.data)
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, config_entry: SpotRateConfigEntry):
    logger.debug('async_setup_entry %s data: [%s]; options: [%s]', config_entry.unique_id, config_entry.data, config_entry.options)

    spot_rate = SpotRate()
    coordinator = SpotRateCoordinator(
        hass=hass,
        spot_rate=spot_rate,
        in_eur=config_entry.data[CONF_CURRENCY] == 'EUR',
        unit=config_entry.data[CONF_UNIT_OF_MEASUREMENT],
    )

    await coordinator.async_config_entry_first_refresh()

    config_entry.runtime_data = coordinator
    config_entry.async_on_unload(config_entry.add_update_listener(options_update_listener))

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Unload config entry."""
    return await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)
