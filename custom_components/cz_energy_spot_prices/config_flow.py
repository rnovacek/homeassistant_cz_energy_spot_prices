import logging
from typing import Any, Dict, Optional
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT
from homeassistant.helpers.selector import TemplateSelector
from homeassistant.helpers.template import Template, TemplateError

from .const import DOMAIN, ADDITIONAL_COSTS_BUY_ELECTRICITY, ADDITIONAL_COSTS_SELL_ELECTRICITY, ADDITIONAL_COSTS_BUY_GAS


logger = logging.getLogger(__name__)

UNITS = {
    'kWh': 'kWh',
    'MWh': 'MWh',
}

CURRENCIES = {
    'CZK': 'CZK',
    'EUR': 'EUR',
}

DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_CURRENCY, description='Currency', default='CZK'): vol.In(CURRENCIES),  # type: ignore
    vol.Required(CONF_UNIT_OF_MEASUREMENT, description='Energy unit', default='kWh'): vol.In(UNITS),  # type: ignore
})


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
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
        self.options = dict(config_entry.options)
        logger.debug('OptionsFlowHandler.__init__ %s; data [%s]; options [%s]', config_entry.unique_id, config_entry.data, config_entry.options)

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ):# -> FlowResult:
        """Manage the options."""
        logger.debug(
            f"OptionsFlowHandler:async_step_init user_input [{user_input}] data [{self.config_entry.data}] options [{self.config_entry.options}]"
        )
        options_schema = vol.Schema({
            vol.Optional(
                ADDITIONAL_COSTS_BUY_ELECTRICITY,
                description='Additional costs when buying electricity',
                default=self.config_entry.options.get(ADDITIONAL_COSTS_BUY_ELECTRICITY, ''),
            ): TemplateSelector(),
            vol.Optional(
                ADDITIONAL_COSTS_SELL_ELECTRICITY,
                description='Additional costs when selling electricity',
                default=self.config_entry.options.get(ADDITIONAL_COSTS_SELL_ELECTRICITY, ''),
            ): TemplateSelector(),
            vol.Optional(
                ADDITIONAL_COSTS_BUY_GAS,
                description='Additional costs when buying gas',
                default=self.config_entry.options.get(ADDITIONAL_COSTS_BUY_GAS, ''),
            ): TemplateSelector(),
        })

        errors = {}
        if user_input is not None:
            additional_costs_buy_electricity = user_input.get(ADDITIONAL_COSTS_BUY_ELECTRICITY) or ''
            if additional_costs_buy_electricity:
                template = Template(additional_costs_buy_electricity)
                try:
                    template.ensure_valid()
                except TemplateError:
                    errors[ADDITIONAL_COSTS_BUY_ELECTRICITY] = 'invalid_template'

            additional_costs_sell_electricity = user_input.get(ADDITIONAL_COSTS_SELL_ELECTRICITY) or ''
            if additional_costs_sell_electricity:
                template = Template(additional_costs_sell_electricity)
                try:
                    template.ensure_valid()
                except TemplateError:
                    errors[ADDITIONAL_COSTS_SELL_ELECTRICITY] = 'invalid_template'

            additional_costs_buy_gas = user_input.get(ADDITIONAL_COSTS_BUY_GAS) or ''
            if additional_costs_buy_gas:
                template = Template(additional_costs_buy_gas)
                try:
                    template.ensure_valid()
                except TemplateError:
                    errors[ADDITIONAL_COSTS_BUY_GAS] = 'invalid_template'

            if not errors:
                return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
        )
