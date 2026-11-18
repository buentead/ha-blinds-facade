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
| 0 | Wind lock timer is running | nothing (the wind alarm keeps the blinds open) |
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

## Wind alarm

A second blueprint, **Blinds Wind Alarm** (`blueprints/automation/blinds_wind_alarm.yaml`),
protects the blinds independently of the facades:

- When the KNX wind alarm entity reports the alarm, it starts the wind lock timer.
  It ignores area enables and manual override.
- The blinds list is **optional**. Leave it empty if KNX moves the blinds on a wind
  alarm itself – recommended, because that also works when Home Assistant is down.
  Then the lock only keeps the facade automations passive. If you select blinds, 
  Home Assistant opens them as well.
- The lock lasts for the hold time (default 2 hours). Every new alarm restarts the
  timer (and re-sends the open command if blinds are selected).
- If the alarm is still active when the timer runs out, or after a Home Assistant
  restart, the lock is extended again.
- Every Blinds Facade automation that has the timer selected as *Wind lock timer*
  stays passive while it runs and does not treat the wind movement as manual
  operation. When the lock ends, the facade applies its current target state
  (after 5 s, so an extension by the wind automation wins).

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

### 2. Blueprints

Either copy `blueprints/automation/blinds_facade.yaml` (and
`blinds_wind_alarm.yaml` if you use the wind alarm) to
`/config/blueprints/automation/`, or import them via
*Settings → Automations & scenes → Blueprints → Import blueprint* using the **raw**
URL of each file (`https://raw.githubusercontent.com/<user>/<repo>/main/blueprints/automation/...`).
A normal GitHub page URL returns HTML and fails with a YAML error.

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
helpers, the sensors and the facade's azimuth window. Select
`timer.blinds_wind_lock` as *Wind lock timer* if you use the wind alarm.

### 5. Wind alarm (once, optional)

Create one automation from **Blinds Wind Alarm**. Select the KNX wind alarm entity
and `timer.blinds_wind_lock` (from the package). Select blinds only if Home Assistant
should open them; leave the list empty if KNX handles the wind alarm.

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
