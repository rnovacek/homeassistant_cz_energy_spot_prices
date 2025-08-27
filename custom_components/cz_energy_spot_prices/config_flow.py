import logging
from typing import Any, cast, override
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT
from homeassistant.helpers.selector import TemplateSelector  # pyright: ignore[reportUnknownVariableType]
from homeassistant.helpers.template import Template
from homeassistant.exceptions import TemplateError

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
    def __init__(self) -> None:
        """Initialize the config flow."""
        super().__init__()
        self.data: dict[str, Any] = {}

    @override
    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return OptionsFlowHandler()

    @override
    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
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
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ):# -> FlowResult:
        """Manage the options."""
        logger.debug(
            f"OptionsFlowHandler:async_step_init user_input [{user_input}] data [{self.config_entry.data}] options [{self.config_entry.options}]"
        )
        options_schema = vol.Schema({
            vol.Optional(
                ADDITIONAL_COSTS_BUY_ELECTRICITY,
                default=self.config_entry.options.get(ADDITIONAL_COSTS_BUY_ELECTRICITY, ''),
            ): TemplateSelector(),
            vol.Optional(
                ADDITIONAL_COSTS_SELL_ELECTRICITY,
                default=self.config_entry.options.get(ADDITIONAL_COSTS_SELL_ELECTRICITY, ''),
            ): TemplateSelector(),
            vol.Optional(
                ADDITIONAL_COSTS_BUY_GAS,
                default=self.config_entry.options.get(ADDITIONAL_COSTS_BUY_GAS, ''),
            ): TemplateSelector(),
        })

        errors = {}
        if user_input is not None:
            additional_costs_buy_electricity = cast(
                str, user_input.get(ADDITIONAL_COSTS_BUY_ELECTRICITY) or ""
            )
            if additional_costs_buy_electricity:
                template = Template(additional_costs_buy_electricity, self.hass)
                try:
                    template.ensure_valid()
                except TemplateError:
                    errors[ADDITIONAL_COSTS_BUY_ELECTRICITY] = 'invalid_template'

            additional_costs_sell_electricity = cast(
                str, user_input.get(ADDITIONAL_COSTS_SELL_ELECTRICITY) or ""
            )
            if additional_costs_sell_electricity:
                template = Template(additional_costs_sell_electricity, self.hass)
                try:
                    template.ensure_valid()
                except TemplateError:
                    errors[ADDITIONAL_COSTS_SELL_ELECTRICITY] = 'invalid_template'

            additional_costs_buy_gas = cast(
                str, user_input.get(ADDITIONAL_COSTS_BUY_GAS) or ""
            )
            if additional_costs_buy_gas:
                template = Template(additional_costs_buy_gas, self.hass)
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
