from collections.abc import Mapping
from decimal import Decimal
import logging
from enum import StrEnum
from typing import Any, override

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity


from .coordinator import (
    DailyTradeRateData,
    EntryCoordinator,
    IntervalTradeRateData,
)

logger = logging.getLogger(__name__)


class Trade(StrEnum):
    SPOT = "Spot"
    BUY = "Buy"
    SELL = "Sell"


class SpotRateSensorMixin(CoordinatorEntity[EntryCoordinator]):
    hass: HomeAssistant
    _trade: Trade
    _value: Decimal | int | None
    _attr: Mapping[str, Any] | None
    _attr_has_entity_name: bool = True
    _attr_unique_id: str | None
    _attr_translation_key: str | None
    _attr_icon: str | None

    coordinator: EntryCoordinator

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EntryCoordinator,
        device_id: str,
        trade: Trade,
    ):
        super().__init__(coordinator)
        self.hass = hass
        self._trade = trade
        self._device_id = device_id

        self._value = None
        self._attr = None
        self._attr_available = False

        self.update(self.coordinator.data)

    @override
    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.update(self.coordinator.data)
        super()._handle_coordinator_update()

    def _get_utility_rate_data(
        self, _rate_data: IntervalTradeRateData
    ) -> IntervalTradeRateData | DailyTradeRateData:
        raise NotImplementedError()

    def _get_trade_rates(self, rate_data: IntervalTradeRateData):
        match self._trade:
            case Trade.SPOT:
                return rate_data.spot_rates
            case Trade.BUY:
                return rate_data.buy_rates
            case Trade.SELL:
                if not rate_data.sell_rates:
                    # For gas, we only have daily rates
                    raise ValueError(
                        f"Trade type '{self._trade}' is not applicable for daily rates."
                    )

                return rate_data.sell_rates

    def update(self, _rate_data: IntervalTradeRateData | None) -> None:
        raise NotImplementedError()

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self._value

    @property
    @override
    def extra_state_attributes(self):  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return other attributes of the sensor."""
        return self._attr
