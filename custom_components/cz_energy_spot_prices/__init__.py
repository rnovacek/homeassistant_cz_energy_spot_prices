"""Czech Spot Energy Prices"""

import logging

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT, STATE_ON, STATE_OFF
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, PLATFORMS
from .coordinator import SpotRateCoordinator
from .spot_rate import SpotRate


logger = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Setup our skeleton component."""
    hass.data[DOMAIN] = {}
    return True


async def options_update_listener(hass: HomeAssistant, config_entry: ConfigEntry):
    """Handle options update."""
    logger.debug('options_update_listener', config_entry.data)
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    logger.debug('async_setup_entry %s data: [%s]; options: [%s]', config_entry.unique_id, config_entry.data, config_entry.options)

    spot_rate = SpotRate()
    coordinator = SpotRateCoordinator(
        hass=hass,
        spot_rate=spot_rate,
        in_eur=config_entry.data[CONF_CURRENCY] == 'EUR',
        unit=config_entry.data[CONF_UNIT_OF_MEASUREMENT],
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][config_entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    hass_data = dict(config_entry.data)
    # Registers update listener to update config entry when options are updated.
    unsub_options_update_listener = config_entry.add_update_listener(options_update_listener)
    # Store a reference to the unsubscribe function to cleanup if an entry is unloaded.
    hass_data["unsub_options_update_listener"] = unsub_options_update_listener
    hass.data[DOMAIN][config_entry.entry_id] = hass_data

    config_entry.async_on_unload(config_entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Unload config entry."""
    return await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Reload config entry."""
    logger.debug('async_reload_entry %s data: %s; options: %s', config_entry.unique_id, config_entry.data, config_entry.options)

    await hass.config_entries.async_reload(config_entry.entry_id)
