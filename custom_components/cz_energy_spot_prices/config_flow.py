import logging
from typing import Any, Final, cast, override
from homeassistant.helpers.translation import async_get_translations
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.selector import (
    SelectSelectorConfig,
    TemplateSelector,  # pyright: ignore[reportUnknownVariableType]
    SelectSelector,  # pyright: ignore[reportUnknownVariableType]
)
from homeassistant.helpers.template import Template
from homeassistant.exceptions import TemplateError

from .const import (
    CONF_ALLOW_CROSS_MIDNIGHT,
    CONF_CHEAPEST_BLOCKS,
    DOMAIN,
    CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY,
    CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY,
    CONF_ADDITIONAL_COSTS_BUY_GAS,
    Commodity,
    SpotRateIntervalType,
)


logger = logging.getLogger(__name__)

UNITS = {
    'kWh': 'kWh',
    'MWh': 'MWh',
}

CURRENCIES = {
    'CZK': 'CZK',
    'EUR': 'EUR',
}

CONF_COMMODITY = "commodity"
ELECTRICITY = "electricity"
GAS = "gas"
COMMODITIES = {ELECTRICITY: "Electricity", GAS: "Gas"}

INTERVALS = {
    SpotRateIntervalType.Hour: "60min",
    SpotRateIntervalType.QuarterHour: "15min",
}

CONF_INTERVAL: Final = "interval"

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(
            CONF_CURRENCY,
            description="Currency",
            default="CZK",
        ): SelectSelector(SelectSelectorConfig(options=["CZK", "EUR"])),
        vol.Required(
            CONF_UNIT_OF_MEASUREMENT,
            description="Energy unit",
            default="kWh",
        ): SelectSelector(SelectSelectorConfig(options=["kWh", "MWh"])),
        vol.Required(
            CONF_COMMODITY,
            description="Commodity",
            default=ELECTRICITY,
        ): SelectSelector(
            SelectSelectorConfig(
                options=["electricity", "gas"], translation_key="commodities"
            )
        ),
    }
)

DATA_SCHEMA_ELECTRICITY = vol.Schema(
    {
        vol.Optional(
            CONF_INTERVAL,
            description="Interval for spot prices",
            default=SpotRateIntervalType.Hour,
        ): SelectSelector(
            SelectSelectorConfig(options=["60min", "15min"], translation_key="interval")
        ),
    }
)


async def async_get_localized_title(
    hass: HomeAssistant, key: str, currency: str, unit: str
) -> str:
    language = hass.config.language  # Current frontend language
    translations = await async_get_translations(
        hass,
        language,
        category="config",
        integrations=[DOMAIN],
    )

    # Key format is "component.DOMAIN.CONFIG_PATH"
    full_key = f"component.{DOMAIN}.config.create_entry.{key}"

    template = translations.get(full_key)
    if not template:
        if key == "electricity_15min":
            return f"Electricity Spot 15min Rate in {currency}/{unit}"
        elif key == "electricity_60min":
            return f"Electricity Spot 60min Rate in {currency}/{unit}"
        elif key == "gas":
            return f"Gas Spot Rate in {currency}/{unit}"
        return key

    return template.format(currency=currency, unit=unit)

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
            if user_input[CONF_COMMODITY] == ELECTRICITY:
                return await self.async_step_details()
            else:
                return await self.create()

        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_details(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input:
            self.data.update(user_input)
            return await self.create()

        return self.async_show_form(
            step_id="details",
            data_schema=DATA_SCHEMA_ELECTRICITY,
            errors=errors,
        )

    async def create(self):
        if self.data[CONF_COMMODITY] == ELECTRICITY:
            interval = cast(
                SpotRateIntervalType,
                self.data.get(CONF_INTERVAL, SpotRateIntervalType.Hour),
            )
            if interval == SpotRateIntervalType.Hour:
                title = "electricity_60min"
            else:
                title = "electricity_15min"

        else:
            title = "gas"

        return self.async_create_entry(
            title=await async_get_localized_title(
                self.hass,
                title,
                currency=cast(str, self.data[CONF_CURRENCY]),
                unit=cast(str, self.data[CONF_UNIT_OF_MEASUREMENT]),
            ),
            data=self.data,
        )


class OptionsFlowHandler(config_entries.OptionsFlowWithReload):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ):# -> FlowResult:
        """Manage the options."""
        logger.debug(
            f"OptionsFlowHandler:async_step_init user_input [{user_input}] data [{self.config_entry.data}] options [{self.config_entry.options}]"
        )

        errors: dict[str, str] = {}
        if user_input is not None:
            additional_costs_buy_electricity = cast(
                str, user_input.get(CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY) or ""
            )
            if additional_costs_buy_electricity:
                template = Template(additional_costs_buy_electricity, self.hass)
                try:
                    template.ensure_valid()
                except TemplateError:
                    errors[CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY] = "invalid_template"

            additional_costs_sell_electricity = cast(
                str, user_input.get(CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY) or ""
            )
            if additional_costs_sell_electricity:
                template = Template(additional_costs_sell_electricity, self.hass)
                try:
                    template.ensure_valid()
                except TemplateError:
                    errors[CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY] = "invalid_template"

            additional_costs_buy_gas = cast(
                str, user_input.get(CONF_ADDITIONAL_COSTS_BUY_GAS) or ""
            )
            if additional_costs_buy_gas:
                template = Template(additional_costs_buy_gas, self.hass)
                try:
                    template.ensure_valid()
                except TemplateError:
                    errors[CONF_ADDITIONAL_COSTS_BUY_GAS] = "invalid_template"

            conf_cheapest_blocks = user_input.get(CONF_CHEAPEST_BLOCKS)
            cheapest_blocks = cast(str, conf_cheapest_blocks or "")
            parts: list[int] = []
            for part in cheapest_blocks.split(","):
                try:
                    parts.append(int(part.strip()))
                except ValueError:
                    errors[CONF_CHEAPEST_BLOCKS] = "invalid_format"

            if not errors:
                return self.async_create_entry(title="", data=user_input)
        else:
            user_input = dict(self.config_entry.options)

        commodity = Commodity(self.config_entry.data.get(CONF_COMMODITY, ELECTRICITY))
        if commodity == Commodity.Gas:
            options_schema = vol.Schema(
                {
                    vol.Optional(
                        CONF_ADDITIONAL_COSTS_BUY_GAS,
                        default=user_input.get(CONF_ADDITIONAL_COSTS_BUY_GAS, ""),
                    ): TemplateSelector(),
                }
            )
        elif commodity == Commodity.Electricity:
            options_schema = vol.Schema(
                {
                    vol.Optional(
                        CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY,
                        default=user_input.get(
                            CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY, ""
                        ),
                    ): TemplateSelector(),
                    vol.Optional(
                        CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY,
                        default=user_input.get(
                            CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY, ""
                        ),
                    ): TemplateSelector(),
                    vol.Optional(
                        CONF_CHEAPEST_BLOCKS,
                        default=user_input.get(CONF_CHEAPEST_BLOCKS, ""),
                        description={
                            "name": "Cheapest consecutive hour blocks",
                            "description": (
                                "Comma-separated list of hour blocks. "
                                "For each number, a binary sensor will indicate when the "
                                "current time falls inside the cheapest consecutive hours for that block."
                            ),
                        },
                    ): cv.string,
                    vol.Optional(
                        CONF_ALLOW_CROSS_MIDNIGHT,
                        default=user_input.get(CONF_ALLOW_CROSS_MIDNIGHT, False),
                        description={
                            "name": "Allow cheapest blocks to cross midnight",
                            "description": (
                                "If enabled, cheapest consecutive-hour periods can span across days "
                                "(e.g., 23:00-01:00). "
                                "Because daily prices reset at midnight, these blocks may change "
                                "when new day data is loaded."
                            ),
                        },
                    ): cv.boolean,
                }
            )
        else:
            raise ValueError("No commodity set!")

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
            errors=errors,
        )
