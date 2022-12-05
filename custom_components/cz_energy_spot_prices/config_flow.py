import logging
from typing import Any, Dict, Optional
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT
from homeassistant.helpers.selector import TemplateSelector

from .const import DOMAIN, ADDITIONAL_COSTS_BUY, ADDITIONAL_COSTS_SELL, CHEAPEST_CONSECUTIVE_HOURS_BUY


logger = logging.getLogger(__name__)

UNITS = {
    'kWh': 'kWh',
    'MWh': 'MWh',
}

CURRENCIES = {
    'CZK': 'CZK',
    'EUR': 'EUR',
}

OPTIONS_SCHEMA = vol.Schema({
    vol.Optional(ADDITIONAL_COSTS_BUY, description='Additional costs when buying', default='0.0'): TemplateSelector(),
    vol.Optional(CHEAPEST_CONSECUTIVE_HOURS_BUY, description='Hours of cheapest consecutive prices for buying', default=''): str,
    vol.Optional(ADDITIONAL_COSTS_SELL, description='Additional costs when selling', default='0.0'): TemplateSelector(),
})

DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_CURRENCY, description='Currency', default='CZK'): vol.In(CURRENCIES),
    vol.Required(CONF_UNIT_OF_MEASUREMENT, description='Energy unit', default='kWh'): vol.In(UNITS),
})


class ExampleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}
        if user_input is not None:
            self.data = user_input
            if not errors:
                return self.async_create_entry(
                    title=f'Electricity Spot Rate in {user_input[CONF_CURRENCY]}/{user_input[CONF_UNIT_OF_MEASUREMENT]}',
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors,
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ):# -> FlowResult:
        """Manage the options."""
        logger.debug(
            f"OptionsFlowHandler:async_step_init user_input [{user_input}] data [{self.config_entry.data}]"
        )
        if user_input is not None:
            if CONF_CURRENCY in self.config_entry.data:
                user_input[CONF_CURRENCY] = self.config_entry.data[CONF_CURRENCY]
            if CONF_UNIT_OF_MEASUREMENT in self.config_entry.data:
                user_input[CONF_UNIT_OF_MEASUREMENT] = self.config_entry.data[CONF_UNIT_OF_MEASUREMENT]

            self.hass.config_entries.async_update_entry(
                self.config_entry, data=user_input, options=self.config_entry.options
            )
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=OPTIONS_SCHEMA,
        )
