# pyright: reportMissingTypeStubs=false

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, cast

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    SOURCE_USER,
    ConfigSubentry,
    SubentryFlowContext,
)
from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cz_energy_spot_prices.cheapest_blocks import (
    format_search_subentry_title,
    validate_search_definition,
)
from custom_components.cz_energy_spot_prices.config_flow import (
    CONF_COMMODITY,
    CONF_INTERVAL,
    ConfigFlow,
)
from custom_components.cz_energy_spot_prices.const import (
    CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY,
    CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY,
    CONF_PRICE_TYPE,
    CONF_SEARCH_OBJECTIVE,
    DOMAIN,
    PRICE_BLOCK_SUBENTRY_TYPE,
    SearchObjective,
    SearchType,
    SpotRateIntervalType,
)


def _entry(
    options: dict[str, Any] | None = None,
    *,
    commodity: str = "electricity",
    interval: SpotRateIntervalType = SpotRateIntervalType.Hour,
) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Spot prices",
        data={
            CONF_COMMODITY: commodity,
            CONF_CURRENCY: "EUR",
            CONF_UNIT_OF_MEASUREMENT: "MWh",
            CONF_INTERVAL: interval,
        },
        options=options or {},
        version=2,
        minor_version=1,
    )


def _schema_keys(result: Mapping[str, Any]) -> list[str]:
    """Return form schema keys in rendered order."""
    return [str(getattr(key, "schema", key)) for key in result["data_schema"].schema]


def _selector_options(result: Mapping[str, Any], field: str) -> list[str]:
    """Return selector options for one rendered field."""
    for key, validator in result["data_schema"].schema.items():
        if str(getattr(key, "schema", key)) == field:
            return list(validator.config["options"])
    raise AssertionError(f"{field} selector not found")


async def _start_add_flow(hass: HomeAssistant, entry: MockConfigEntry):
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, PRICE_BLOCK_SUBENTRY_TYPE),
        context=SubentryFlowContext(source=SOURCE_USER),
    )


def _add_subentry(
    hass: HomeAssistant, entry: MockConfigEntry, search: dict[str, Any]
) -> ConfigSubentry:
    subentry = ConfigSubentry(
        data=MappingProxyType(search),
        subentry_type=PRICE_BLOCK_SUBENTRY_TYPE,
        title=format_search_subentry_title(search),
        unique_id=str(search["id"]),
    )
    assert hass.config_entries.async_add_subentry(entry, subentry)
    return subentry


def test_validate_search_definition_rejects_invalid_today_length_without_crashing():
    """Test malformed lengths return a validation error for today searches."""
    errors = validate_search_definition(
        {
            "type": SearchType.TODAY,
            "name": "Bad length",
            "length_hours": "not-a-number",
        }
    )
    assert errors["length_hours"] == "invalid_number"


async def test_options_flow_only_manages_parent_templates(hass: HomeAssistant):
    """Search CRUD is exposed through subentries, not parent options."""
    entry = _entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert list(result["menu_options"]) == ["configure_templates"]


async def test_options_flow_preserves_templates_used_by_price_blocks(
    hass: HomeAssistant,
):
    """A template cannot be removed while a subentry depends on its prices."""
    buy_template = "{{ value + 1 }}"
    sell_template = "{{ value - 1 }}"
    entry = _entry(
        {
            CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY: buy_template,
            CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY: sell_template,
        }
    )
    entry.add_to_hass(hass)
    for price_type in ("buy", "sell"):
        _add_subentry(
            hass,
            entry,
            {
                "id": f"{price_type}-search",
                "type": SearchType.TODAY,
                "name": f"{price_type.title()} search",
                "length_hours": 1.0,
                CONF_PRICE_TYPE: price_type,
                CONF_SEARCH_OBJECTIVE: SearchObjective.LOWEST,
            },
        )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "configure_templates"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY: "",
            CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY: sell_template,
        },
    )
    assert result["type"] == "form"
    assert result["errors"] == {
        CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY: "template_used_by_price_block"
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY: buy_template,
            CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY: "",
        },
    )
    assert result["type"] == "form"
    assert result["errors"] == {
        CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY: "template_used_by_price_block"
    }
    assert entry.options == {
        CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY: buy_template,
        CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY: sell_template,
    }


def test_subentry_flow_is_registered_for_a_friendly_gas_error():
    """Both pickers resolve, while the gas flow explains the limitation."""
    assert PRICE_BLOCK_SUBENTRY_TYPE in ConfigFlow.async_get_supported_subentry_types(
        _entry()
    )
    assert PRICE_BLOCK_SUBENTRY_TYPE in ConfigFlow.async_get_supported_subentry_types(
        _entry(commodity="gas")
    )


async def test_subentry_flow_rejects_gas_with_friendly_error(hass: HomeAssistant):
    """Gas selection ends with a translated explanation instead of invalid handler."""
    entry = _entry(commodity="gas")
    entry.add_to_hass(hass)

    result = await _start_add_flow(hass, entry)

    assert result["type"] == "abort"
    assert result["reason"] == "price_blocks_not_supported_for_gas"


async def test_subentry_flow_adds_tomorrow_search(hass: HomeAssistant):
    """A native subentry flow creates one independently managed search."""
    entry = _entry()
    entry.add_to_hass(hass)
    result = await _start_add_flow(hass, entry)
    assert cast(list[str], result["menu_options"]) == [
        "add_today",
        "add_tomorrow",
        "add_fixed",
    ]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "add_tomorrow"}
    )
    assert _schema_keys(result) == [
        "name",
        CONF_PRICE_TYPE,
        CONF_SEARCH_OBJECTIVE,
        "length_hours",
    ]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Tomorrow plan",
            CONF_PRICE_TYPE: "spot",
            CONF_SEARCH_OBJECTIVE: SearchObjective.LOWEST,
            "length_hours": 2.0,
        },
    )
    assert result["type"] == "create_entry"
    subentry = next(iter(entry.subentries.values()))
    assert subentry.title == "Tomorrow plan · Tomorrow · Lowest Spot · 2 h"
    assert subentry.unique_id
    assert subentry.data["type"] == SearchType.TOMORROW


async def test_subentry_fixed_search_validation(hass: HomeAssistant):
    """Fixed-window forms use existing validation and interval-aware lengths."""
    entry = _entry(interval=SpotRateIntervalType.QuarterHour)
    entry.add_to_hass(hass)
    result = await _start_add_flow(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "add_fixed"}
    )
    assert _schema_keys(result)[-2:] == ["start_time", "end_time"]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Night",
            CONF_PRICE_TYPE: "spot",
            CONF_SEARCH_OBJECTIVE: SearchObjective.LOWEST,
            "length_hours": 1.25,
            "start_time": "22:00",
            "end_time": "22:00",
        },
    )
    assert result["type"] == "form"
    assert result["errors"]["end_time"] == "start_equals_end"


async def test_subentry_reconfigure_preserves_identity(hass: HomeAssistant):
    """Editing keeps opaque identity while allowing a readable title change."""
    entry = _entry()
    entry.add_to_hass(hass)
    subentry = _add_subentry(
        hass,
        entry,
        {
            "id": "stable-search-id",
            "type": SearchType.FIXED,
            "name": "Night",
            "length_hours": 2.0,
            CONF_PRICE_TYPE: "spot",
            CONF_SEARCH_OBJECTIVE: SearchObjective.LOWEST,
            "start_time": "22:00",
            "end_time": "06:00",
            "legacy": True,
        },
    )
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, PRICE_BLOCK_SUBENTRY_TYPE),
        context=SubentryFlowContext(
            source=SOURCE_RECONFIGURE,
            subentry_id=subentry.subentry_id,
        ),
    )
    assert _schema_keys(result)[-3:] == ["type", "start_time", "end_time"]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Morning",
            CONF_PRICE_TYPE: "spot",
            CONF_SEARCH_OBJECTIVE: SearchObjective.HIGHEST,
            "length_hours": 1.0,
            "type": SearchType.TODAY,
        },
    )
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    updated = entry.subentries[subentry.subentry_id]
    assert updated.unique_id == "stable-search-id"
    assert updated.title == "Morning · Today · Highest Spot · 1 h"
    assert updated.data["type"] == SearchType.TODAY
    assert "start_time" not in updated.data
    assert updated.data["legacy"] is True


async def test_subentry_price_types_follow_parent_templates(hass: HomeAssistant):
    """Searches offer calculated price types configured on the parent."""
    entry = _entry(
        {
            CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY: "{{ value + 1 }}",
            CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY: "{{ value - 1 }}",
        }
    )
    entry.add_to_hass(hass)
    result = await _start_add_flow(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "add_today"}
    )
    assert _selector_options(result, CONF_PRICE_TYPE) == ["spot", "buy", "sell"]


async def test_subentry_rejects_duplicate_title(hass: HomeAssistant):
    """Search names remain unique within one parent entry."""
    entry = _entry()
    entry.add_to_hass(hass)
    _add_subentry(
        hass,
        entry,
        {
            "id": "existing",
            "type": SearchType.TODAY,
            "name": "Plan",
            "length_hours": 1.0,
        },
    )
    result = await _start_add_flow(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "add_today"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "plan",
            CONF_PRICE_TYPE: "spot",
            CONF_SEARCH_OBJECTIVE: SearchObjective.LOWEST,
            "length_hours": 1.0,
        },
    )
    assert result["type"] == "form"
    assert result["errors"]["name"] == "duplicate_name"
