"""Template failures must affect only the corresponding trade's entities."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from freezegun import freeze_time
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
import pytest

from custom_components.cz_energy_spot_prices.binary_sensor import (
    ConsecutiveCheapestElectricitySensor,
    SearchBasedCheapestElectricitySensor,
)
from custom_components.cz_energy_spot_prices.cheapest_blocks import PriceBlockSearch
from custom_components.cz_energy_spot_prices.const import (
    CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY,
    CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY,
    Commodity,
    Currency,
    EnergyUnit,
    SearchType,
    SpotRateIntervalType,
)
from custom_components.cz_energy_spot_prices.coordinator import (
    EntryConfig,
    EntryCoordinator,
    PRAGUE_TZ,
)
from custom_components.cz_energy_spot_prices.sensor import (
    _get_electricity_sensors,
    _get_gas_sensors,
)
from custom_components.cz_energy_spot_prices.spot_rate_mixin import Trade

from . import BASE_DT, get_entry, get_rate


@pytest.mark.parametrize("trade", [Trade.BUY, Trade.SELL])
@pytest.mark.parametrize(
    "interval", [SpotRateIntervalType.Hour, SpotRateIntervalType.QuarterHour]
)
async def test_historical_template_failure_isolated_and_recovers(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    trade: Trade,
    interval: SpotRateIntervalType,
) -> None:
    """An invalid yesterday price must not freeze today's spot or other trade."""
    await hass.config.async_set_time_zone("Europe/Prague")
    current = datetime(2026, 11, 12, 12, tzinfo=UTC)
    start = current.replace(hour=0) - timedelta(days=1)
    interval_minutes = 60 if interval == SpotRateIntervalType.Hour else 15
    rates = {
        start + timedelta(minutes=i * interval_minutes): Decimal(i - 10)
        for i in range(3 * 24 * 60 // interval_minutes)
    }
    search = PriceBlockSearch(
        id="test", name="Test", type=SearchType.TODAY, length_hours=1
    )
    config = EntryConfig(
        commodity=Commodity.Electricity,
        interval=interval,
        currency=Currency.EUR,
        currency_human="EUR",
        unit=EnergyUnit.MWh,
        timezone="Europe/Prague",
        zoneinfo=PRAGUE_TZ,
        buy_template=Template("{{ value + 1 }}", hass),
        sell_template=Template("{{ value - 1 }}", hass),
        cheapest_block_searches=[search],
    )
    source = MagicMock()
    source.data = {interval: rates}
    coordinator = EntryCoordinator(hass, get_entry(interval=interval), source, None, config)
    with patch(
        "custom_components.cz_energy_spot_prices.coordinator.get_now", return_value=current
    ):
        try:
            await coordinator.on_schedule(current)
            entities = {
                entity_trade: [
                    *_get_electricity_sensors(hass, coordinator, "test", entity_trade),
                    ConsecutiveCheapestElectricitySensor(
                        hass=hass,
                        coordinator=coordinator,
                        device_id="test",
                        trade=entity_trade,
                        hours=None,
                    ),
                    SearchBasedCheapestElectricitySensor(
                        hass=hass,
                        coordinator=coordinator,
                        device_id="test",
                        trade=entity_trade,
                        search=search,
                    ),
                ]
                for entity_trade in Trade
            }
            assert all(entity.available for group in entities.values() for entity in group)
            # Today's values all render correctly; only yesterday is outside coverage.
            template = Template(
                "{{ none if as_local(hour).day == 11 else value + 1 }}", hass
            )
            if trade == Trade.BUY:
                config.buy_template = template
            else:
                config.sell_template = template
            await coordinator.on_schedule(current + timedelta(minutes=interval_minutes))
            assert coordinator.data is not None
            for entity_trade, group in entities.items():
                for entity in group:
                    entity.update(coordinator.data)
                    assert entity.available == (entity_trade != trade)
            assert f"{trade.lower()}" in caplog.text
            assert start.isoformat() in caplog.text
            assert "None" in caplog.text
            assert template.template in caplog.text
            # Correcting the template restores the existing entities.
            config.buy_template = Template("{{ value + 1 }}", hass)
            config.sell_template = Template("{{ value - 1 }}", hass)
            coordinator._source_updated()
            for group in entities.values():
                for entity in group:
                    entity.update(coordinator.data)
                    assert entity.available
        finally:
            await coordinator.async_stop()


@pytest.mark.parametrize(
    "expression",
    [
        "none",
        "'not a price'",
        "''",
        "'   '",
        "'None'",
        "'1,23'",
        "true",
        "false",
        "[]",
        "[1, 2]",
        "{}",
        "{'price': 1}",
        "(1, 2)",
        "'NaN'",
        "'-NaN'",
        "'sNaN'",
        "'Infinity'",
        "'-Infinity'",
        "1e309",
        "-1e309",
        "missing_price",
        "missing_price + 1",
        "1 / 0",
        "value +",
    ],
)
@pytest.mark.parametrize("commodity", [Commodity.Electricity, Commodity.Gas])
async def test_invalid_template_results_are_unavailable(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    expression: str,
    commodity: Commodity,
) -> None:
    """Bad results and render errors must not escape scheduled/source updates."""
    current = datetime(2026, 11, 12, 12, tzinfo=PRAGUE_TZ)
    interval = (
        SpotRateIntervalType.Day if commodity == Commodity.Gas else SpotRateIntervalType.Hour
    )
    midnight = current.replace(hour=0).astimezone(UTC)
    rates = {midnight + timedelta(days=i): Decimal("0") for i in range(2)}
    if commodity == Commodity.Electricity:
        rates[current.astimezone(UTC)] = Decimal("-1")
    config = EntryConfig(
        commodity=commodity,
        interval=interval,
        currency=Currency.EUR,
        currency_human="EUR",
        unit=EnergyUnit.MWh,
        timezone="Europe/Prague",
        zoneinfo=PRAGUE_TZ,
        buy_template=Template("{{ " + expression + " }}", hass),
        sell_template=None,
        cheapest_block_searches=[],
    )
    source = MagicMock()
    source.data = {interval: rates}
    coordinator = EntryCoordinator(hass, get_entry(), source, None, config)
    with patch(
        "custom_components.cz_energy_spot_prices.coordinator.get_now", return_value=current
    ):
        try:
            coordinator._source_updated()
            factory = (
                _get_gas_sensors if commodity == Commodity.Gas else _get_electricity_sensors
            )
            spot = factory(hass, coordinator, "test", Trade.SPOT)
            buy = factory(hass, coordinator, "test", Trade.BUY)
            assert all(entity.available for entity in spot)
            assert all(not entity.available for entity in buy)
            assert f"Invalid {commodity} buy price template" in caplog.text
            assert midnight.isoformat() in caplog.text
            assert config.buy_template.template in caplog.text
            config.buy_template = Template("{{ value }}", hass)
            await coordinator.on_schedule(current)
            assert coordinator.data is not None
            assert coordinator.data.buy_rates is not None
            assert {
                dt: interval.price
                for dt, interval in coordinator.data.buy_rates.interval_by_dt.items()
            } == rates
            for entity in buy:
                entity.update(coordinator.data)
                assert entity.available
        finally:
            await coordinator.async_stop()


@pytest.mark.parametrize("failed_trade", [Trade.BUY, Trade.SELL])
async def test_template_failure_publishes_unavailable_and_recovers(
    hass: HomeAssistant,
    mock_ote_electricity: MagicMock,
    mock_cnb: MagicMock,
    failed_trade: Trade,
) -> None:
    """HA states reflect trade-specific failure, continued spot updates and recovery."""
    await hass.config.async_set_time_zone("Europe/Prague")
    entry = get_entry(
        currency="CZK", unit="kWh", interval=SpotRateIntervalType.QuarterHour,
        cheapest_block_searches=[{
            "id": "test", "name": "Test", "type": "today", "length_hours": 1,
            "price_type": failed_trade.lower(),
        }],
    )
    entry.add_to_hass(hass)
    option = (
        CONF_ADDITIONAL_COSTS_BUY_ELECTRICITY if failed_trade == Trade.BUY
        else CONF_ADDITIONAL_COSTS_SELL_ELECTRICITY
    )
    hass.config_entries.async_update_entry(entry, options={
        **entry.options,
        option: "{{ value + 1 if is_state('binary_sensor.tariff_available', 'on') else none }}",
    })
    with freeze_time(BASE_DT):
        hass.states.async_set("binary_sensor.tariff_available", "on")
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        affected_ids = [
            state.entity_id for state in hass.states.async_all()
            if f".{failed_trade.lower()}_" in state.entity_id
            or f"_{failed_trade.lower()}_" in state.entity_id
        ]
        assert len(affected_ids) >= 9
        for entity_id in affected_ids:
            assert hass.states.get(entity_id).state != "unavailable"

    coordinator = entry.runtime_data
    for minutes, tariff_available in [(15, "off"), (30, "off"), (45, "on")]:
        next_interval = BASE_DT + timedelta(minutes=minutes)
        with freeze_time(next_interval):
            hass.states.async_set("binary_sensor.tariff_available", tariff_available)
            await coordinator.on_schedule(next_interval)
            await hass.async_block_till_done()
            for entity_id in affected_ids:
                state = hass.states.get(entity_id)
                assert state is not None
                if tariff_available == "off":
                    assert state.state == "unavailable"
                else:
                    assert state.state != "unavailable"
            spot = hass.states.get("sensor.current_spot_electricity_price_15min")
            assert spot is not None
            expected = {15: 96.27, 30: 87.66, 45: 86.19}[minutes] * get_rate("CZK", "kWh")
            assert float(spot.state) == pytest.approx(expected, abs=0.00005)
            other_trade = "sell" if failed_trade == Trade.BUY else "buy"
            other = hass.states.get(f"sensor.current_{other_trade}_electricity_price_15min")
            assert other is not None
            offset = -1 if other_trade == "sell" else 10
            assert float(other.state) == pytest.approx(expected + offset, abs=0.00005)
