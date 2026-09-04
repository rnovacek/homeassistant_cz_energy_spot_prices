import logging
import uuid
from typing import Any, Final, cast, override
from homeassistant.helpers.translation import async_get_translations
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigSubentry,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.selector import (
    SelectSelectorConfig,
    TemplateSelector,
    SelectSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TimeSelector,
)
from homeassistant.helpers.template import Template
from homeassistant.exceptions import TemplateError

from .cheapest_blocks import (
    format_search_subentry_title,
    legacy_block_length,
    validate_search_definition,
)
from .const import (
    CONF_PRICE_TYPE,
    CONF_SEARCH_OBJECTIVE,
    DOMAIN,
    PRICE_BLOCK_SUBENTRY_TYPE,
    SearchObjective,
    SearchType,
    CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY,
    CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY,
    CONF_ADDITIONAL_COSTS_BUY_GAS,
    Commodity,
    SpotRateIntervalType,
)


_LOGGER = logging.getLogger(__name__)

SUPPORTED_SEARCH_TYPES: Final = (
    SearchType.TODAY,
    SearchType.TOMORROW,
    SearchType.FIXED,
)

UNITS = {
    "kWh": "kWh",
    "MWh": "MWh",
}

CURRENCIES = {
    "CZK": "CZK",
    "EUR": "EUR",
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
    VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        super().__init__()
        self.data: dict[str, Any] = {}

    @override
    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return OptionsFlowHandler()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the price-block flow, including its friendly gas rejection."""
        return {PRICE_BLOCK_SUBENTRY_TYPE: PriceBlockSubentryFlowHandler}

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


class OptionsFlowHandler(config_entries.OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ):  # -> FlowResult:
        """Manage the options."""
        _LOGGER.debug(
            f"OptionsFlowHandler:async_step_init user_input [{user_input}] data [{self.config_entry.data}] options [{self.config_entry.options}]"
        )

        return self.async_show_menu(
            step_id="init",
            menu_options=["configure_templates"],
        )

    async def async_step_configure_templates(
        self, user_input: dict[str, Any] | None = None
    ):
        """Manage template options."""
        errors: dict[str, str] = {}
        options = dict(self.config_entry.options)

        if user_input is not None:
            price_types_in_use = {
                subentry.data.get(CONF_PRICE_TYPE, "spot")
                for subentry in self.config_entry.subentries.values()
                if subentry.subentry_type == PRICE_BLOCK_SUBENTRY_TYPE
            }
            additional_costs_buy_electricity = cast(
                str, user_input.get(CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY) or ""
            )
            if additional_costs_buy_electricity:
                template = Template(additional_costs_buy_electricity, self.hass)
                try:
                    template.ensure_valid()
                except TemplateError:
                    errors[CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY] = "invalid_template"
            elif (
                CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY in user_input
                and "buy" in price_types_in_use
            ):
                errors[CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY] = (
                    "template_used_by_price_block"
                )

            additional_costs_sell_electricity = cast(
                str, user_input.get(CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY) or ""
            )
            if additional_costs_sell_electricity:
                template = Template(additional_costs_sell_electricity, self.hass)
                try:
                    template.ensure_valid()
                except TemplateError:
                    errors[CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY] = "invalid_template"
            elif (
                CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY in user_input
                and "sell" in price_types_in_use
            ):
                errors[CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY] = (
                    "template_used_by_price_block"
                )

            additional_costs_buy_gas = cast(
                str, user_input.get(CONF_ADDITIONAL_COSTS_BUY_GAS) or ""
            )
            if additional_costs_buy_gas:
                template = Template(additional_costs_buy_gas, self.hass)
                try:
                    template.ensure_valid()
                except TemplateError:
                    errors[CONF_ADDITIONAL_COSTS_BUY_GAS] = "invalid_template"

            if not errors:
                new_options = {
                    **options,
                    **{k: v for k, v in user_input.items() if v is not None},
                }
                return self.async_create_entry(title="", data=new_options)
        else:
            user_input = options

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
                }
            )
        else:
            raise ValueError("No commodity set!")

        return self.async_show_form(
            step_id="configure_templates",
            data_schema=options_schema,
            errors=errors,
        )


class PriceBlockSubentryFlowHandler(ConfigSubentryFlow):
    """Add and reconfigure one price-block search."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the period for a new price-block search."""
        if self._get_entry().data.get(CONF_COMMODITY, ELECTRICITY) != ELECTRICITY:
            return self.async_abort(reason="price_blocks_not_supported_for_gas")
        return self.async_show_menu(
            step_id="user",
            menu_options=["add_today", "add_tomorrow", "add_fixed"],
        )

    async def async_step_add_today(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a search limited to today."""
        return await self._async_handle_search(
            "add_today", SearchType.TODAY, user_input
        )

    async def async_step_add_tomorrow(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a search limited to tomorrow."""
        return await self._async_handle_search(
            "add_tomorrow", SearchType.TOMORROW, user_input
        )

    async def async_step_add_fixed(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a search limited to a fixed time window."""
        return await self._async_handle_search(
            "add_fixed", SearchType.FIXED, user_input
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure an existing price-block search."""
        subentry = self._get_reconfigure_subentry()
        search_type = _search_type_from_value(
            subentry.data.get("type"), SearchType.TODAY
        )
        return await self._async_handle_search(
            "reconfigure",
            search_type,
            user_input,
            subentry,
        )

    async def _async_handle_search(
        self,
        step_id: str,
        search_type: SearchType,
        user_input: dict[str, Any] | None,
        subentry: ConfigSubentry | None = None,
    ) -> SubentryFlowResult:
        """Validate and create or update one price-block subentry."""
        entry = self._get_entry()
        errors: dict[str, str] = {}
        selected_search_type = search_type
        price_type_options = _price_type_options(entry)
        existing_searches = [
            {
                **configured.data,
                "id": configured.unique_id,
                "name": configured.data.get("name", configured.title),
            }
            for configured in entry.subentries.values()
            if configured.subentry_type == PRICE_BLOCK_SUBENTRY_TYPE
        ]

        if user_input is not None:
            selected_search_type = _search_type_from_value(
                user_input.get("type"), search_type
            )
            price_type = user_input.get(CONF_PRICE_TYPE, "spot")
            if price_type not in price_type_options:
                errors[CONF_PRICE_TYPE] = "unsupported_price_type"
                price_type = "spot"

            search_id = (
                str(subentry.unique_id or subentry.subentry_id)
                if subentry is not None
                else str(uuid.uuid4())
            )
            search: dict[str, Any] = {
                "id": search_id,
                "type": selected_search_type.value,
                "name": str(user_input.get("name", "")).strip(),
                "length_hours": user_input.get("length_hours"),
                CONF_PRICE_TYPE: price_type,
                CONF_SEARCH_OBJECTIVE: _search_objective_from_value(
                    user_input.get(CONF_SEARCH_OBJECTIVE)
                ).value,
            }
            if (
                subentry is not None
                and subentry.data.get("legacy") is True
                and legacy_block_length(search["length_hours"]) is not None
            ):
                # This marker keeps the released N-hour compatibility entity
                # alive. It is internal metadata, not an editable form field.
                search["legacy"] = True
            if selected_search_type == SearchType.FIXED:
                search["start_time"] = user_input.get("start_time")
                search["end_time"] = user_input.get("end_time")

            errors.update(
                validate_search_definition(
                    search,
                    existing_searches,
                    search_id if subentry is not None else None,
                    SpotRateIntervalType(
                        entry.data.get(CONF_INTERVAL, SpotRateIntervalType.Hour)
                    ),
                )
            )
            if not errors:
                if subentry is None:
                    return self.async_create_entry(
                        title=format_search_subentry_title(search),
                        data=search,
                        unique_id=search_id,
                    )
                return self.async_update_and_abort(
                    entry,
                    subentry,
                    title=format_search_subentry_title(search),
                    data=search,
                    unique_id=search_id,
                )

        defaults = _subentry_search_defaults(
            selected_search_type,
            user_input,
            subentry,
            price_type_options,
        )
        schema_fields: dict[vol.Marker, Any] = {
            vol.Required("name", default=defaults["name"]): cv.string,
            vol.Required(
                CONF_PRICE_TYPE, default=defaults[CONF_PRICE_TYPE]
            ): SelectSelector(
                SelectSelectorConfig(
                    options=price_type_options,
                    translation_key="price_type",
                )
            ),
            vol.Required(
                CONF_SEARCH_OBJECTIVE,
                default=defaults[CONF_SEARCH_OBJECTIVE],
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[objective.value for objective in SearchObjective],
                    translation_key="search_objective",
                )
            ),
            vol.Required(
                "length_hours", default=defaults["length_hours"]
            ): NumberSelector(_length_selector_config(entry)),
        }
        if subentry is not None:
            schema_fields[vol.Required("type", default=defaults["type"])] = (
                SelectSelector(
                    SelectSelectorConfig(
                        options=[value.value for value in SUPPORTED_SEARCH_TYPES],
                        translation_key="search_type",
                    )
                )
            )
        if selected_search_type == SearchType.FIXED:
            schema_fields[
                vol.Required("start_time", default=defaults["start_time"])
            ] = TimeSelector()
            schema_fields[vol.Required("end_time", default=defaults["end_time"])] = (
                TimeSelector()
            )

        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(schema_fields),
            errors=errors,
        )


def _subentry_search_defaults(
    search_type: SearchType,
    user_input: dict[str, Any] | None,
    subentry: ConfigSubentry | None,
    price_type_options: list[str],
) -> dict[str, Any]:
    """Return safe defaults for a price-block subentry form."""
    stored = dict(subentry.data) if subentry is not None else {}
    source = user_input if user_input is not None else stored
    selected_type = _search_type_from_value(source.get("type"), search_type)
    default_name = {
        SearchType.TODAY: "Today",
        SearchType.TOMORROW: "Tomorrow",
        SearchType.FIXED: "Time window",
    }[selected_type]
    price_type = source.get(CONF_PRICE_TYPE, "spot")
    if price_type not in price_type_options:
        price_type = "spot"
    return {
        "name": source.get(
            "name", subentry.title if subentry is not None else default_name
        ),
        "type": selected_type.value,
        "length_hours": source.get("length_hours", 1.0),
        CONF_PRICE_TYPE: price_type,
        CONF_SEARCH_OBJECTIVE: _search_objective_from_value(
            source.get(CONF_SEARCH_OBJECTIVE)
        ).value,
        "start_time": source.get("start_time", "20:00"),
        "end_time": source.get("end_time", "06:00"),
    }


def _search_type_from_value(value: Any, default: SearchType) -> SearchType:
    """Return a valid search type from stored or submitted form data."""
    try:
        search_type = SearchType(value)
    except (TypeError, ValueError):
        return default
    if search_type not in SUPPORTED_SEARCH_TYPES:
        return default
    return search_type


def _search_objective_from_value(value: Any) -> SearchObjective:
    """Return a valid search objective, defaulting old searches to lowest."""
    try:
        return SearchObjective(value)
    except (TypeError, ValueError):
        return SearchObjective.LOWEST


def _length_selector_config(
    config_entry: config_entries.ConfigEntry,
) -> NumberSelectorConfig:
    """Return an interval-aware block length selector config."""
    interval = SpotRateIntervalType(
        config_entry.data.get(CONF_INTERVAL, SpotRateIntervalType.Hour)
    )
    step = 0.25 if interval == SpotRateIntervalType.QuarterHour else 1.0
    return NumberSelectorConfig(
        mode=NumberSelectorMode.BOX,
        step=step,
        min=step,
        max=24,
        unit_of_measurement="h",
    )


def _price_type_options(config_entry: config_entries.ConfigEntry) -> list[str]:
    """Return price types that can produce custom block sensors."""
    options = ["spot"]
    if config_entry.options.get(CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY):
        options.append("buy")
    if config_entry.options.get(CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY):
        options.append("sell")
    return options
