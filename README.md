# Home Assistant Czech Energy Spot Prices

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=rnovacek&repository=homeassistant_cz_energy_spot_prices&category=integration)

Home Assistant integration that provides current Czech electricity spot prices based on [OTE](https://ote-cr.cz).

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
    float_precision: 2
    type: column # or "line" if you prefer
    show:
      in_header: raw
    data_generator: |
      return Object.entries(entity.attributes).map(([date, value], index) => {
        return [new Date(date).getTime(), value];
      });
```

## Find cheapest hours in selected interval

Add this as a [template sensor](https://www.home-assistant.io/integrations/template/). Change the intervals on lines 3-5, the sensor will be on when
cheapest hour in any of the intervals is active. Or you can replace the last line with `{{ min.cheapest_hours }}` to display the cheapest hours.

This is useful for example if you want to turn on your water heater in the afternoon and then again during the night.

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
