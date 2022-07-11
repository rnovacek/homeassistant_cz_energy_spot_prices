# Home Assistant Czech Energy Spot Prices

Home Assistant integration that provides current Czech electricity spot prices based on [OTE](ote-cr.cz).

You can select an energy unit between kWh and MWh when configuring the integration. OTE prices are in EUR, but you can also select to use CZK (Czech Koruna) as a currency for displayed prices (based on ČNB rate for given day).

## Installation

1. Copy `custom_components/cz_energy_spot_prices` directory into your `custom_components` in your configuration directory.
2. Restart Home Assistant
3. Open Settings -> Devices & Services -> Integration
4. Search for "Czech Energy Spot Prices" and click the search result
5. Configure Currency and Unit of energy
6. Submit

## Usage

You can use created sensor (usually called `sensor.current_spot_electricity_price`) to get current hourly price in selected unit and currency.

The sensor also has currently valid (current day, and if it's after noon (12pm) then also next day) spot prices as attributes on the sensor.

## Displaying a chart

If you want you display a chart with current day (or two days if it's after noon), you can install [apexcharts-card](https://github.com/RomRider/apexcharts-card) card for Home Assistant and then use following config for it:

```yaml
type: custom:apexcharts-card
header:
  show: true
  show_states: true
  colorize_states: true
graph_span: 2d
span:
  start: day
now:
  show: true
  label: Now
series:
  - entity: sensor.current_spot_electricity_price
    type: column # or "line" if you prefer
    show:
      in_header: raw
    data_generator: |
      return Object.entries(entity.attributes).map(([date, value], index) => {
        return [new Date(date).getTime(), value];
      });
```
