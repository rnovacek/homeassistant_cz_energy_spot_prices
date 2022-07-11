
from typing import Any, Dict, Optional
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT

from .const import DOMAIN


UNITS = {
    'kWh': 'kWh',
    'MWh': 'MWh',
}

CURRENCIES = {
    'CZK': 'CZK',
    'EUR': 'EUR',
}

DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_CURRENCY, description='Currency', default='CZK'): vol.In(CURRENCIES),
    vol.Required(CONF_UNIT_OF_MEASUREMENT, description='Energy unit', default='kWh'): vol.In(UNITS),
})

class ExampleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}
        if user_input is not None:
            self.data = user_input
            if not errors:
                return self.async_create_entry(
                    title=f'Electricity Spot Rate in {user_input["currency"]}/{user_input["unit_of_measurement"]}',
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors,
        )
