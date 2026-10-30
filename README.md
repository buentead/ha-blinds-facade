# Blinds Facade (KNX scenes)

A Home Assistant automation blueprint for venetian blinds that are driven through
**KNX scenes**. One automation instance controls the blinds of **one facade of one
room** – a room with windows to the east, south and west gets three instances.

KNX sets the slat angle inside the scene, while Home Assistant's KNX cover only knows
percentages. This blueprint therefore never drives to a percentage for shading; it
calls `scene.turn_on` with a scene per blind instead.

## How it works

On every relevant event (sensor change, timer, every 5 minutes) the blueprint
recomputes the **target state** of the facade. It only acts when the target differs
from the state it last set. After a restart it simply recomputes, so it cannot get
stuck or replay old commands.

Priorities – the first matching rule wins:

| # | Rule | Result |
|---|------|--------|
| 1 | Area enable is off | nothing |
| 2 | Manual override timer is running | nothing |
| 3 | Night | `closed` if *Enable nightly closing* is on, otherwise nothing |
| 4 | Hot day: today's forecast > 25 °C and good weather, room ≥ 20 °C | shading |
| 5 | Sun bright, sun within the facade's azimuth window, room > target | shading |
| 6 | Otherwise | `open` |

**Shading** uses scene 1 while the sun elevation is below 40° and scene 2 from 40° on.

**Night** (closing only if *Enable nightly closing* is on – default. When it is off,
the automation does nothing at night and leaves the blinds where they are.)
- Before solar noon it stays night until it is at least 06:30 **and** either civil
  dawn (sun elevation ≥ −6°) or ≥ 1000 lx is reached.
- After solar noon it becomes night at civil dusk (< −6°) or below 300 lx. Once the
  blinds were closed after 12:00 they stay closed for the evening, even if the
  illuminance rises again.

**Target temperature** is 21.5 °C, or 23 °C if it is colder than 15 °C outside and
tomorrow's forecast is not good weather (good = `sunny`, `partlycloudy`,
`clear-night`). This lets the sun heat the room further before shading starts.

**Hysteresis**
- Shading ends when the room drops below target − 1 °C, when the sun leaves the
  azimuth window, or when it has been below 40k lx for 30 minutes.
- Hot-day shading ends below 19 °C.

**Manual override**: a position or slat change that happens more than 120 s after the
last own command is treated as manual operation and pauses the automation for 60 min.
When the timer finishes, the current target state is applied again.

All thresholds are blueprint inputs.

Every movement is written to the logbook of the state helper with its reason, e.g.
`open → scene1: Shading (indoor 22.0 °C, target 21.5 °C, sun 25.0°/180°)`.

## Requirements

- Home Assistant 2024.10 or newer
- KNX covers plus one KNX scene per blind and shading level
- An illuminance sensor (e.g. KNX weather station), room and outdoor temperature sensors
- A weather entity with a daily forecast (`weather.get_forecasts`), e.g. MeteoSwiss
- The `sun.sun` entity (default)

## Installation

### 1. Package (once)

1. Copy `packages/blinds.yaml` to `/config/packages/blinds.yaml`.
2. Enable packages in `configuration.yaml` if you have not done so yet:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

3. In the package, replace `sensor.weather_station_illuminance` with your
   illuminance sensor. This creates `binary_sensor.blinds_sun_bright`:
   - on after ≥ 55k lx for 10 min
   - off after < 40k lx for 30 min
4. Restart Home Assistant.

### 2. Blueprint

Either copy `blueprints/automation/blinds_facade.yaml` to
`/config/blueprints/automation/`, or import it from your Git hosting via
*Settings → Automations & scenes → Blueprints → Import blueprint* using the URL of
that file.

### 3. Helpers per room facade

The package contains one example set. Copy it for every room facade, or create the
helpers in the UI:

| Helper | Example | Notes |
|--------|---------|-------|
| `input_select` | `blinds_j_e3_01_south_state` | Options exactly `open`, `closed`, `scene1`, `scene2` |
| `input_datetime` | `blinds_j_e3_01_south_last_command` | Date **and** time |
| `timer` | `blinds_j_e3_01_south_manual` | `restore: true` |

You also need an enable entity per area (`input_boolean`, `switch` or
`binary_sensor`). One area can contain several rooms.

### 4. Automation per room facade

Create an automation from the blueprint and select the blinds, their scenes, the
helpers, the sensors and the facade's azimuth window.

## Tests

The decision logic (the priority cascade) is covered by tests that render the
blueprint's templates outside Home Assistant:

```bash
pip install -r tests/requirements.txt
pytest
```

When you add a rule, add a test case to `tests/test_decision_logic.py`.

## Known limitations

- Manual operation within the travel time window after an own command is not
  detected. KNX feedback cannot be told apart from the feedback of our own command.
- If the indoor temperature sensor is unavailable, the automation does nothing.
- The hot-day rule applies all day by default. Enable *Hot-day rule only until solar
  noon* to limit it to the morning.

## Contributing

Issues and pull requests are welcome. Please read the
[Code of Conduct](CODE_OF_CONDUCT.md) first.

## License

[MIT](LICENSE) © 2026 Adrian Bünter
