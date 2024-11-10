import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import SpotRateCoordinator, SpotRateData
from .spot_rate_settings import SpotRateSettings

logger = logging.getLogger(__name__)


class SpotRateSensorMixin(CoordinatorEntity):
    _attr_has_entity_name = True

    coordinator: SpotRateCoordinator

    def __init__(self, hass: HomeAssistant, settings: SpotRateSettings, coordinator: SpotRateCoordinator):
        super().__init__(coordinator)
        self.hass = hass
        self._settings = settings

        self._value = None
        self._attr = None
        self._available = False

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.update(self.coordinator.data)
        self.async_write_ha_state()

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
