
from typing import Any, Dict, Optional
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT, CONF_RESOURCE
from homeassistant.helpers.selector import selector

from .const import DOMAIN


DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_RESOURCE, description='Resource', default='Electricity'): selector({
        "select": {
            "options": ['Electricity'],
        },
    }),
    vol.Required(CONF_CURRENCY, description='Currency', default='CZK'): selector({
        "select": {
            "options": ['CZK', 'EUR'],
        },
    }),
    vol.Required(CONF_UNIT_OF_MEASUREMENT, description='Energy unit', default='kWh'): selector({
        "select": {
            "options": ['kWh', 'MWh'],
        },
    }),
})

class ExampleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}
        if user_input is not None:
            self.data = user_input
            if not errors:
                return self.async_create_entry(
                    title=f'{user_input["resource"]} Spot Rate in {user_input["currency"]}/{user_input["unit_of_measurement"]}',
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors,
        )
