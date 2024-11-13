import logging
from enum import StrEnum

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import SpotRateCoordinator, SpotRateData
from .spot_rate_settings import SpotRateSettings

logger = logging.getLogger(__name__)


class Trade(StrEnum):
    SPOT = 'Spot'
    BUY = 'Buy'
    SELL = 'Sell'


class SpotRateSensorMixin(CoordinatorEntity):
    _attr_has_entity_name = True

    coordinator: SpotRateCoordinator

    def __init__(self, hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator, trade: Trade):
        super().__init__(coordinator)
        self.hass = hass
        self._settings = settings
        self._trade = trade

        self._value = None
        self._attr = None
        self._available = False

        self.update(self.coordinator.data)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.update(self.coordinator.data)
        super()._handle_coordinator_update()

    def _get_utility_rate_data(self, rate_data: SpotRateData):
        raise NotImplementedError()

    def _get_trade_rates(self, rate_data: SpotRateData):
        utility_rate_data = self._get_utility_rate_data(rate_data)
        match self._trade:
            case Trade.SPOT:
                return utility_rate_data.spot_rates
            case Trade.BUY:
                return utility_rate_data.buy_rates
            case Trade.SELL:
                return utility_rate_data.sell_rates

    def update(self, rates_by_datetime: SpotRateData):
        raise NotImplementedError()

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self._value

    @property
    def extra_state_attributes(self):
        """Return other attributes of the sensor."""
        return self._attr

    @property
    def available(self):
        """Return True if entity is available."""
        return self._available


class ElectricitySpotRateSensorMixin(SpotRateSensorMixin):
    def _get_utility_rate_data(self, rate_data: SpotRateData):
        return rate_data.electricity


class GasSpotRateSensorMixin(SpotRateSensorMixin):
    def _get_utility_rate_data(self, rate_data: SpotRateData):
        return rate_data.gas
