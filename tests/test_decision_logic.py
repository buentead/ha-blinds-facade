"""Decision-logic tests for the Blinds Facade blueprint.

The blueprint's `variables:` blocks are rendered with Jinja2 and a small
mock of the Home Assistant template functions, then the computed `desired`
state is compared with the expected one. This covers the priority cascade,
not the Home Assistant runtime (triggers, services).

Run:  pip install -r tests/requirements.txt && pytest
"""

import ast
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import jinja2
import pytest
import yaml

TZ = ZoneInfo("Europe/Zurich")
BLUEPRINT = Path(__file__).parent.parent / "blueprints" / "automation" / "blinds_facade.yaml"


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_constructor("!input", lambda loader, node: ("INPUT", loader.construct_scalar(node)))
BP = yaml.load(BLUEPRINT.read_text(encoding="utf-8"), Loader=_Loader)

ENTITIES = dict(
    covers=["cover.test"],
    scene_1=["scene.test_scene1"],
    scene_2=["scene.test_scene2"],
    enable_entity="input_boolean.enable",
    state_helper="input_select.state",
    last_cmd_helper="input_datetime.last_command",
    manual_timer="timer.manual",
    indoor_temp="sensor.indoor",
    outdoor_temp="sensor.outdoor",
    lux_sensor="sensor.lux",
    bright_sensor="binary_sensor.bright",
    weather_entity="weather.home",
)


def _defaults():
    values = {}
    for section in BP["blueprint"]["input"].values():
        for key, spec in section["input"].items():
            values[key] = spec.get("default")
    values.update(ENTITIES)
    return values


def _native(text):
    text = text.strip()
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text


def _env(now, states, attrs):
    env = jinja2.Environment()

    def to_float(value, default=None):
        try:
            return float(value)
        except (TypeError, ValueError):
            if default is None:
                raise
            return default

    def to_bool(value, default=None):
        if isinstance(value, bool):
            return value
        return {"true": True, "on": True, "false": False, "off": False}.get(str(value).lower(), default)

    env.filters.update(float=to_float, int=lambda v, d=0: int(to_float(v, d)), bool=to_bool)
    env.globals.update(
        states=lambda e: str(states.get(e, "unknown")),
        state_attr=lambda e, a: attrs.get(e, {}).get(a),
        is_state=lambda e, s: str(states.get(e)) == s,
        has_value=lambda e: states.get(e) not in (None, "unknown", "unavailable"),
        now=lambda: now,
        today_at=lambda t="00:00": dt.datetime.combine(now.date(), dt.time.fromisoformat(t), TZ),
        as_timestamp=lambda d: d.timestamp(),
        as_datetime=dt.datetime.fromisoformat,
        as_local=lambda d: d.astimezone(TZ),
        timedelta=dt.timedelta,
        none=None,
    )
    return env


def forecast(today_max, today_cond, tomorrow_cond):
    def build(now):
        day = now.date()
        mk = lambda d: dt.datetime.combine(d, dt.time(0), dt.timezone.utc).isoformat()
        return [
            {"datetime": mk(day), "temperature": today_max, "condition": today_cond},
            {"datetime": mk(day + dt.timedelta(days=1)), "temperature": 10, "condition": tomorrow_cond},
        ]
    return build


GOOD = forecast(12, "sunny", "sunny")
TOMORROW_RAIN = forecast(12, "sunny", "rainy")
HOT = forecast(28, "sunny", "sunny")
NO_FORECAST = lambda now: []


def decide(*, time, elev, azi, rising, lux, t_in, t_out, bright, current,
           fc=GOOD, last_cmd="08:00", **overrides):
    inputs = _defaults()
    inputs.update(overrides)
    now = dt.datetime(2026, 10, 28, *map(int, time.split(":")), tzinfo=TZ)
    last = dt.datetime.combine(now.date(), dt.time.fromisoformat(last_cmd), TZ).timestamp()
    states = {
        "sensor.lux": lux, "sensor.indoor": t_in, "sensor.outdoor": t_out,
        "binary_sensor.bright": bright, "input_select.state": current,
    }
    attrs = {
        "sun.sun": {"elevation": elev, "azimuth": azi, "rising": rising},
        "input_datetime.last_command": {"timestamp": last},
    }
    env = _env(now, states, attrs)

    ctx = {}
    for key, value in BP["variables"].items():
        if isinstance(value, tuple):
            ctx[key] = inputs[value[1]]
        else:
            ctx[key] = _native(env.from_string(value).render(**ctx))
    ctx["fc"] = {"weather.home": {"forecast": fc(now)}}
    ctx["trigger"] = {"id": "eval"}

    for step in BP["actions"][0]["default"]:
        for key, value in step.get("variables", {}).items():
            ctx[key] = _native(env.from_string(str(value)).render(**ctx))
    global _last_ctx
    _last_ctx = ctx
    return ctx["desired"], ctx["target"], ctx["reason"]

def would_act(**kwargs):
    """Return (desired, night_hold) from a single evaluation."""
    desired, _, _ = decide(**kwargs)
    return desired, _last_ctx["night_hold"]

CASES = [
    ("night before dawn", dict(time="05:50", elev=-12, azi=90, rising=True, lux=0, t_in=20, t_out=5, bright="off", current="closed"), "closed"),
    ("twilight but before 06:30", dict(time="06:20", elev=-4, azi=100, rising=True, lux=500, t_in=20, t_out=5, bright="off", current="closed"), "closed"),
    ("civil dawn after 06:30 opens", dict(time="07:00", elev=-4, azi=100, rising=True, lux=200, t_in=20, t_out=5, bright="off", current="closed"), "open"),
    ("1000 lx after 06:30 opens", dict(time="06:45", elev=-8, azi=95, rising=True, lux=1200, t_in=20, t_out=5, bright="off", current="closed"), "open"),
    ("bright, in window, warm → scene 1", dict(time="12:30", elev=25, azi=180, rising=True, lux=70000, t_in=22, t_out=5, bright="on", current="open"), "scene1"),
    ("bright but sun outside window", dict(time="09:00", elev=15, azi=90, rising=True, lux=70000, t_in=22, t_out=5, bright="on", current="open"), "open"),
    ("hysteresis keeps shading at 20.8 °C", dict(time="13:30", elev=24, azi=190, rising=False, lux=60000, t_in=20.8, t_out=5, bright="on", current="scene1"), "scene1"),
    ("hysteresis ends shading at 20.4 °C", dict(time="13:30", elev=24, azi=190, rising=False, lux=60000, t_in=20.4, t_out=5, bright="on", current="scene1"), "open"),
    ("cold + rain tomorrow: 22 °C stays open", dict(time="12:30", elev=25, azi=180, rising=True, lux=70000, t_in=22, t_out=5, bright="on", current="open", fc=TOMORROW_RAIN), "open"),
    ("cold + rain tomorrow: 23.2 °C shades", dict(time="12:30", elev=25, azi=180, rising=True, lux=70000, t_in=23.2, t_out=5, bright="on", current="open", fc=TOMORROW_RAIN), "scene1"),
    ("hot day morning → scene 1", dict(time="07:00", elev=-3, azi=95, rising=True, lux=800, t_in=20.5, t_out=18, bright="off", current="closed", fc=HOT), "scene1"),
    ("high sun → scene 2", dict(time="13:00", elev=55, azi=190, rising=False, lux=90000, t_in=23, t_out=24, bright="on", current="scene1"), "scene2"),
    ("below 300 lx after noon closes", dict(time="16:30", elev=2, azi=240, rising=False, lux=250, t_in=21, t_out=5, bright="off", current="open"), "closed"),
    ("evening latch stays closed at 400 lx", dict(time="16:40", elev=1, azi=242, rising=False, lux=400, t_in=21, t_out=5, bright="off", current="closed", last_cmd="16:30"), "closed"),
    ("stale morning 'closed' does not latch", dict(time="14:00", elev=20, azi=200, rising=False, lux=20000, t_in=21, t_out=5, bright="off", current="closed", last_cmd="06:00"), "open"),
    ("window wrapping through north, sun inside", dict(time="10:00", elev=30, azi=20, rising=True, lux=60000, t_in=25, t_out=24, bright="on", current="open", azimuth_min=300, azimuth_max=60), "scene1"),
    ("window wrapping through north, sun outside", dict(time="10:00", elev=30, azi=180, rising=True, lux=60000, t_in=25, t_out=24, bright="on", current="open", azimuth_min=300, azimuth_max=60), "open"),
    ("missing forecast falls back to normal target", dict(time="12:30", elev=25, azi=180, rising=True, lux=70000, t_in=22, t_out=5, bright="on", current="open", fc=NO_FORECAST), "scene1"),
]


@pytest.mark.parametrize("name,kwargs,expected", CASES, ids=[c[0] for c in CASES])
def test_decision(name, kwargs, expected):
    desired, target, reason = decide(**kwargs)
    assert desired == expected, f"{name}: got {desired} ({reason})"


NIGHT = dict(time="22:00", elev=-20, azi=300, rising=False, lux=0, t_in=21, t_out=5, bright="off")


def test_nightly_closing_enabled_by_default_closes():
    desired, hold = would_act(**NIGHT, current="open")
    assert (desired, hold) == ("closed", False)


def test_nightly_closing_disabled_holds_current_state():
    for current in ("open", "scene1", "closed"):
        desired, hold = would_act(**NIGHT, current=current, enable_nightly_closing=False)
        assert hold is True
        assert desired == current


def test_nightly_closing_disabled_does_not_affect_day():
    day = dict(time="12:30", elev=25, azi=180, rising=True, lux=70000, t_in=22, t_out=5, bright="on", current="open")
    desired, hold = would_act(**day, enable_nightly_closing=False)
    assert (desired, hold) == ("scene1", False)


def test_nightly_closing_disabled_morning_before_dawn_holds():
    early = dict(time="05:50", elev=-12, azi=90, rising=True, lux=0, t_in=20, t_out=5, bright="off", current="open")
    assert would_act(**early, enable_nightly_closing=False) == ("open", True)


def test_warm_target_only_when_cold_and_bad_tomorrow():
    base = dict(time="12:30", elev=25, azi=180, rising=True, lux=70000, t_in=22, bright="on", current="open")
    assert decide(**base, t_out=5, fc=TOMORROW_RAIN)[1] == 23
    assert decide(**base, t_out=5, fc=GOOD)[1] == 21.5
    assert decide(**base, t_out=18, fc=TOMORROW_RAIN)[1] == 21.5


def test_all_templates_parse():
    env = jinja2.Environment()

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)
        elif isinstance(node, str) and ("{{" in node or "{%" in node):
            yield node

    templates = list(walk(BP))
    assert templates
    for template in templates:
        env.parse(template)
