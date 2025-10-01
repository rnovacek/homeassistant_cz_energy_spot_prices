# Home Assistant Czech Energy Spot Prices

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=rnovacek&repository=homeassistant_cz_energy_spot_prices&category=integration)

Home Assistant integration that provides current Czech electricity spot prices based on [OTE](https://ote-cr.cz).

If this integration saves (or earns) you some money, you can [buy me a coffee ☕](https://github.com/sponsors/rnovacek).

## Features

- Provides real-time Czech electricity spot and gas prices from [OTE](https://ote-cr.cz).
- Supports multiple currencies (EUR, CZK) and energy units (kWh, MWh).
- Configurable templates for buy/sell prices, including VAT and distribution fees.
- Includes sensors for monitoring current, cheapest, and most expensive electricity prices.
- Compatible with Home Assistant automations for energy optimization.

### Important note

OTE (Czech market operator) uses hourly prices indexed from `1`, where:

- `1` (first hour of the day) corresponds to `00:00 - 01:00`.
- It does **not** mean `01:00 - 02:00`, as one might expect.

Keep this in mind when comparing prices reported by this integration with other sources (e.g., OTE, your electricity provider/distributor).

## Screenshot

See [Displaying a chart](#displaying-a-chart) for details.

![Screenshot](screenshot.png)

## Buy and sell prices

The integration shows just spot prices by default. If you want to also use actual prices for buying and selling (so including distribution fees, VAT, etc), you need to configure it. Use the "Configure" button in integration details and set templates for buying/selling.

Variables:
- `hour` variable to see what hour is currently being computed.
- `value` is base spot price for given hour.

### Example templates

**Electricity cost when buying**

```jinja
{% set tax_kWh = 28.30 / 1000.0 %}
{% set system_services_kWh = 212.82 / 1000.0 %}
{% set oze_kWh = 495 / 1000.0 %}
{% set low_distrib_kWh = 450.43 / 1000.0 %}
{% set high_distrib_kWh = 644.30 / 1000.0 %}
{% set operator_cost_kWh = 350.0 / 1000.0 %}
{% set vat_percent = 21 %}

{% set distrib_kWh = low_distrib_kWh %}
{% if as_local(hour).hour in [9, 12, 16, 20] %}
  {% set distrib_kWh = high_distrib_kWh %}
{% endif %}

{{ (value + distrib_kWh + tax_kWh + oze_kWh + system_services_kWh + operator_cost_kWh) * ( 100.0 + vat_percent ) / 100.0 }}
```

**Electricity cost when selling**

```jinja
{% set operator_cost_kWh = 0.25 %}
{{ value - operator_cost_kWh }}
```

## Installation

You can install the integration using HACS (preferred) or manually.

### HACS (preferred)

1. Open HACS in your Home Assistant instance.
2. Search for "Czech Energy Spot Prices" and install it.
3. Restart Home Assistant.

### Manual

1. Download the `custom_components/cz_energy_spot_prices` directory.
2. Copy it into the `custom_components` folder in your Home Assistant configuration directory.
3. Restart Home Assistant.

### Add and configure the integration

1. Go to **Settings** -> **Devices & Services** -> **Add integration**.
3. Search for "Czech Energy Spot Prices" and select it.
4. Configure the currency and energy unit.
4. (Optional) Use the "Configure" button to set templates for buy/sell prices (see above).

## Sensors

The integration provides several sensors to monitor electricity/gas prices and related data. Below is a list of available sensors and their attributes:


| Sensor | value | attributes |
| ------ | ----- | ---------- |
| **Current Spot Electricity Price** | electricity price for current hour | dictionary with timestamps as keys and spot price for given hour as values |
| **Spot Cheapest Electricity Today** | price of the cheapest electricity today | [At](#at)<br>[Hour](#hour) |
| **Spot Most Expensive Electricity Today** | price of the most expensive electricity today | [At](#at)<br>[Hour](#hour) |
| **Spot Cheapest Electricity Tomorrow** | price of the cheapest electricity tomorrow | [At](#at)<br>[Hour](#hour) |
| **Spot Most Expensive Electricity Tomorrow** | price of the most expensive electricity tomorrow | [At](#at)<br>[Hour](#hour) |
| **Current Spot Electricity Hour Order** | order of current hour when we sort hours by its price (1=cheapest, 24=most expensive) | dictionary with timestamps as keys and `order, price` as values |
| **Tomorrow Spot Electricity Hour Order** | no value | dictionary with timestamps as keys and `order, price` as values |
| **Spot Electricity Has Tomorrow Data** | `On` when data for tomorrow are loaded, `Off` otherwise | |
| **Spot Electricity Is Cheapest** | `On` when current hour has the cheapest price, `Off` otherwise | [Start](#start)<br>[Start hour](#start-hour)<br>[End](#end)<br>[End hour](#end-hour)<br>[Min](#min)<br>[Max](#max)<br>[Mean](#mean) |
| **Spot Electricity Is Cheapest `X` Hours Block** | `On` when current hour is in a block of cheapest consecutive hours, `Off` otherwise | [Start](#start)<br>[Start hour](#start-hour)<br>[End](#end)<br>[End hour](#end-hour)<br>[Min](#min)<br>[Max](#max)<br>[Mean](#mean) |

If you configure templates for buy and sell prices, there will also be similar sensors for buy/sell prices.

<!-- FIXME: add gas sensors when released -->

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

![Screenshot](./screenshot.png)

If you want to display a chart with current day (or two days if it's after noon), you can install [apexcharts-card](https://github.com/RomRider/apexcharts-card) card for Home Assistant and then use following config for it:

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
    float_precision: 2
    type: column # or "line" if you prefer
    show:
      in_header: raw
    data_generator: |
      return Object.entries(entity.attributes).map(([date, value], index) => {
        return [new Date(date).getTime() + (30 * 60 * 1000), value];
      });
```

## Find cheapest hours in selected interval

This is useful for example if you want to turn on your water heater in the afternoon and then again during the night.

### How It Works
- Define intervals as tuples `(start_hour, end_hour)` (end hour is excluded).
- The sensor will return `True` if the current hour is the cheapest in any of the defined intervals.
- Alternatively, replace the last line with `{{ min.cheapest_hours }}` to display the cheapest hours.

### Example Template Sensor

```jinja
{# Define your intervals here as tuples (hour starting the interval, hour ending the interval (excluded)) #}
{% set intervals = [
  (0, 8),
  (8, 16),
  (16, 24),
] %}

{# We need to use namespace so we can write into it in inner cycle #}
{% set min = namespace(price=None, dt=None, cheapest_hours=[]) %}
{% set cheapest_hours = [] %}


{% for interval in intervals %}
  {# Reset min price from previous runs #}
  {% set min.price = None %}

  {# Go through all the hours in the interval (end excluded) and find the hour with lowest price #}
  {% for i in range(interval[0], interval[1]) %}
     {# Get datetime of current hour in current interval #}
     {% set hour_dt = now().replace(hour=i, minute=0, second=0, microsecond=0) %}

     {# Get value for that hour #}
     {% set value = states.sensor.current_spot_electricity_hour_order.attributes.get(hour_dt.isoformat()) %}

     {# Skip if not found #}
     {% if value is not defined %}
       {% break %}
     {% endif %}

     {# value is tuple (order, price), we'll use the price #}
     {% set price = value[1] %}

     {# Min price is not set or is higher than price of current hour => store the min price and hour #}
     {% if min.price is none or price < min.price %}
        {% set min.price = price %}
        {% set min.dt = hour_dt %}
     {% endif %}
  {% endfor %}

  {# Store cheapest hour in current interval #}
  {% set min.cheapest_hours = min.cheapest_hours + [min.dt.hour] %}
{% endfor %}

{# use this to get the cheapest hours #}
{# {{ min.cheapest_hours }} #}

{# return True if current hour is in the cheapest hour of any interval #}
{{ now().hour in min.cheapest_hours }}
```

## Example automation for X cheapest hours

This automation turns on a device (e.g., a heater) during the cheapest `X` hours of the day. Replace `X` with the desired number of hours and specify the entity to control.


```yaml
alias: Turn on for cheapest X hours
trigger:
  - platform: state
    entity_id:
      - sensor.current_spot_electricity_hour_order
condition: []
action:
  - if:
      - condition: numeric_state
        entity_id: sensor.current_spot_electricity_hour_order
        below: X # Replace with amount of hours you want to have it on
    then:
      - type: turn_on
        entity_id: # Add entity you want to turn on
    else:
      - type: turn_off
        entity_id: # Turn off the entity when cheapest interval ends
    enabled: true
mode: single
```

## License

This integration is under [Apache 2.0 License](./LICENSE.txt), the same license as Home Assistant itself.

