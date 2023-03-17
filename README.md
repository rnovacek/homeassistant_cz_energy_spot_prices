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

## Sensors

| Sensor | value | attributes |
| ------ | ----- | ---------- |
| **Current Spot Electricity Price** | electricity price for current hour | dictionary with timestamps as keys and spot price for given hour as values |
| **Spot Cheapest Electricity Today** | price of the cheapest electricity today | [At](#at)<br>[Hour](#hour) |
| **Spot Most Expensive Electricity Today** | price of the most expensive electricity today | [At](#at)<br>[Hour](#hour) |
| **Spot Cheapest Electricity Tomorrow** | price of the cheapest electricity today | [At](#at)<br>[Hour](#hour) |
| **Spot Most Expensive Electricity Tomorrow** | price of the most expensive electricity tomorrow | [At](#at)<br>[Hour](#hour) |
| **Current Spot Electricity Hour Order** | order of current hour when we sort hours by it's price (1=cheapest, 24=most expensive) | dictionary with timestamps as keys and `order, price` as values |
| **Tomorrow Spot Electricity Hour Order** | no value | dictionary with timestamps as keys and `order, price` as values |
| **Spot Electricity Has Tomorrow Data** | `On` when data for tomorrow are loaded, `Off` otherwise | |
| **Spot Electricity Is Cheapest** | `On` when current hour has the cheapest price, `Off` otherwise | [Start](#start)<br>[Start hour](#start-hour)<br>[End](#end)<br>[End hour](#end-hour)<br>[Min](#min)<br>[Max](#max)<br>[Mean](#mean) |
| **Spot Electricity Is Cheapest `X` Hours Block** | `On` when current hour is in a block of cheapest consecutive hours, `Off` otherwise | [Start](#start)<br>[Start hour](#start-hour)<br>[End](#end)<br>[End hour](#end-hour)<br>[Min](#min)<br>[Max](#max)<br>[Mean](#mean) |
| **Cheapest hour within a `X` hour period** | `On` when current hour is the cheapest from larger period, `Off` otherwise | |


## Common attributes

### At

timestamp when the cheapest hour starts

### Hour

hour with the cheapest electricity (`2` means that cheapest electricity is from `2:00` till `3:00` in timezone you've configured in Home Assistant)

### Start

timestamp when consecutive block of cheapest hours starts, only available when the block is in the future

### Start hour

hour when consecutive block of cheapest hours starts, only available when the block is in the future

### End

timestamp when consecutive block of cheapest hours ends, only available when the block is in the future

### End hour

hour when consecutive block of cheapest hours ends, only available when the block is in the future

### Min

minimal price in the block, only available when the block is in the future

### Max

maximal price in the block, only available when the block is in the future

### Mean

average (mean) price in the block, only available when the block is in the future


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
