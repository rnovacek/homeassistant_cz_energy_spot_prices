from collections.abc import Mapping
from decimal import Decimal
import logging
from enum import StrEnum
from typing import Any, cast, override

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import (
    DailySpotRateData,
    DailyTradeRateData,
    HourlySpotRateData,
    HourlyTradeRateData,
    SpotRateCoordinator,
    SpotRateData,
)
from .spot_rate_settings import SpotRateSettings

logger = logging.getLogger(__name__)


class Trade(StrEnum):
    SPOT = "Spot"
    BUY = "Buy"
    SELL = "Sell"


class SpotRateSensorMixin(CoordinatorEntity[SpotRateCoordinator]):
    hass: HomeAssistant
    _settings: SpotRateSettings
    _trade: Trade
    _value: Decimal | int | None
    _attr: Mapping[str, Any] | None
    _attr_has_entity_name: bool = True
    _attr_unique_id: str | None
    _attr_translation_key: str | None
    _attr_icon: str | None

    coordinator: SpotRateCoordinator

    def __init__(
        self,
        hass: HomeAssistant,
        settings: SpotRateSettings,
        coordinator: SpotRateCoordinator,
        trade: Trade,
    ):
        super().__init__(coordinator)
        self.hass = hass
        self._settings = settings
        self._trade = trade

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
        self, _rate_data: SpotRateData
    ) -> HourlyTradeRateData | DailyTradeRateData:
        raise NotImplementedError()

    def _get_trade_rates(self, rate_data: SpotRateData):
        utility_rate_data = self._get_utility_rate_data(rate_data)
        match self._trade:
            case Trade.SPOT:
                return utility_rate_data.spot_rates
            case Trade.BUY:
                return utility_rate_data.buy_rates
            case Trade.SELL:
                if isinstance(utility_rate_data, DailyTradeRateData):
                    # For gas, we only have daily rates
                    raise ValueError(
                        f"Trade type '{self._trade}' is not applicable for daily rates."
                    )

                return utility_rate_data.sell_rates

    def update(self, _rate_data: SpotRateData | None) -> None:
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


class ElectricitySpotRateSensorMixin(SpotRateSensorMixin):
    @override
    def _get_utility_rate_data(self, rate_data: SpotRateData):
        return rate_data.electricity

    @override
    def _get_trade_rates(self, rate_data: SpotRateData) -> HourlySpotRateData:
        return cast(HourlySpotRateData, super()._get_trade_rates(rate_data))


class GasSpotRateSensorMixin(SpotRateSensorMixin):
    @override
    def _get_utility_rate_data(self, rate_data: SpotRateData):
        return rate_data.gas

    @override
    def _get_trade_rates(self, rate_data: SpotRateData) -> DailySpotRateData:
        return cast(DailySpotRateData, super()._get_trade_rates(rate_data))
