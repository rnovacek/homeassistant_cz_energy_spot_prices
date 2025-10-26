# pyright: reportUnusedParameter=false, reportMissingTypeStubs=false

from collections.abc import Generator
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch
from homeassistant.const import CONF_CURRENCY, CONF_UNIT_OF_MEASUREMENT
import pytest

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cz_energy_spot_prices.config_flow import CONF_COMMODITY
from custom_components.cz_energy_spot_prices.const import DOMAIN


@pytest.fixture(autouse=True)
async def auto_enable_custom_integrations(enable_custom_integrations: bool):
    """Enable custom integrations defined in the test dir."""
    yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Mock a config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Cz Spot",
        unique_id="0123456789",
        data={
            CONF_COMMODITY: "electricity",
            CONF_CURRENCY: "CZK",
            CONF_UNIT_OF_MEASUREMENT: "kWh",
        },
        minor_version=1,
    )


@pytest.fixture
def mock_ote_electricity(request: pytest.FixtureRequest) -> Generator[MagicMock]:
    param: str = getattr(request, "param", "today+tomorrow")
    filename = (
        "ote-electricity-2025-10-22-today.xml"
        if param == "today"
        else "ote-electricity-2025-10-22.xml"
    )
    with open(Path(__file__).parent / "fixtures" / filename) as f:
        data = f.read()

    with (
        patch(
            "custom_components.cz_energy_spot_prices.spot_rate.SpotRate._download",
            autospec=True,
        ) as mock_client,
    ):
        mock_client.return_value = data
        mock_client.param = param
        yield mock_client

@pytest.fixture
def mock_cnb() -> Generator[MagicMock]:
    with open(Path(__file__).parent / "fixtures" / "cnb-2025-10-22.json") as f:
        data = cast(dict[str, Any], json.load(f))

    with (
        patch(
            "custom_components.cz_energy_spot_prices.cnb_rate.CnbRate.download_rates",
            autospec=True,
        ) as mock_client,
    ):
        mock_client.return_value = data
        yield mock_client