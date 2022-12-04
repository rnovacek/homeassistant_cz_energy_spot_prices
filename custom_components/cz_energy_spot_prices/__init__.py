"""Czech Spot Energy Prices"""

import asyncio
import logging

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN


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
    logger.debug('async_setup_entry %s %s', config_entry.unique_id, config_entry.data)

    hass.config_entries.async_setup_platforms(config_entry, ['sensor'])

    unsub_options_update_listener = config_entry.add_update_listener(options_update_listener)

    return True
