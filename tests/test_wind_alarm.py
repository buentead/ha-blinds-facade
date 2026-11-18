"""Tests for the wind alarm: the Blinds Wind Alarm blueprint and the wind lock
in the Blinds Facade blueprint.

Like test_decision_logic.py, the templates are rendered with Jinja2 and a small
mock of the Home Assistant template functions.
"""

import datetime as dt
from pathlib import Path

import pytest
import yaml

from test_decision_logic import BP as FACADE, TZ, _Loader, _defaults, _env, _native

WIND = yaml.load(
    (Path(__file__).parent.parent / "blueprints" / "automation" / "blinds_wind_alarm.yaml").read_text(encoding="utf-8"),
    Loader=_Loader,
)

NOW = dt.datetime(2026, 11, 18, 14, 0, tzinfo=TZ)
WIND_TIMER = "timer.blinds_wind_lock"


def _render_vars(variables, inputs, env, ctx=None):
    ctx = dict(ctx or {})
    for key, value in variables.items():
        if isinstance(value, tuple):
            ctx[key] = inputs[value[1]]
        else:
            ctx[key] = _native(env.from_string(str(value)).render(**ctx))
    return ctx


def _truthy(text):
    return str(text).strip() in ("True", "true")


# ---------------------------------------------------------------------------
# Blinds Wind Alarm blueprint
# ---------------------------------------------------------------------------

def _wind_inputs(**overrides):
    values = {key: spec.get("default") for key, spec in WIND["blueprint"]["input"].items()}
    values.update(
        wind_sensor="binary_sensor.wind_alarm",
        covers=["cover.a", "cover.b", "cover.c"],
        wind_timer=WIND_TIMER,
    )
    values.update(overrides)
    return values


def _wind_ctx(alarm, trigger_id, **overrides):
    states = {"binary_sensor.wind_alarm": alarm}
    env = _env(NOW, states, {})
    ctx = _render_vars(WIND["variables"], _wind_inputs(**overrides), env)
    ctx["trigger"] = {"id": trigger_id}
    return env, ctx


def _wind_proceeds(alarm, trigger_id):
    env, ctx = _wind_ctx(alarm, trigger_id)
    condition = WIND["actions"][0]
    assert condition["condition"] == "template"
    return _truthy(env.from_string(condition["value_template"]).render(**ctx))


def test_default_hold_time_is_two_hours():
    assert WIND["blueprint"]["input"]["hold_minutes"]["default"] == 120
    _, ctx = _wind_ctx("on", "alarm")
    assert ctx["hold_duration"] == "02:00:00"


@pytest.mark.parametrize("minutes,expected", [(1, "00:01:00"), (90, "01:30:00"), (1440, "24:00:00")])
def test_hold_duration_format(minutes, expected):
    _, ctx = _wind_ctx("on", "alarm", hold_minutes=minutes)
    assert ctx["hold_duration"] == expected


def test_triggers():
    triggers = {t["id"]: t for t in WIND["triggers"]}
    assert triggers["alarm"]["trigger"] == "state"
    assert triggers["alarm"]["to"] == ("INPUT", "alarm_state")
    assert triggers["expired"]["event_type"] == "timer.finished"
    assert triggers["expired"]["event_data"]["entity_id"] == ("INPUT", "wind_timer")
    assert triggers["start"]["trigger"] == "homeassistant"


def test_new_alarm_always_opens_and_restarts_timer():
    # A repeated alarm while the timer is already running must restart it.
    assert _wind_proceeds("on", "alarm")


@pytest.mark.parametrize("trigger_id", ["expired", "start"])
def test_lock_extended_only_while_alarm_still_active(trigger_id):
    assert _wind_proceeds("on", trigger_id)
    assert not _wind_proceeds("off", trigger_id)


def _open_step():
    """Index and step of the optional 'open the blinds' block."""
    for i, step in enumerate(WIND["actions"]):
        if "if" in step and step["then"][0].get("action") == "cover.open_cover":
            return i, step
    raise AssertionError("open block not found")


def test_timer_started_before_blinds_move():
    actions = [step.get("action") for step in WIND["actions"]]
    open_index, _ = _open_step()
    assert actions.index("timer.start") < open_index
    start = WIND["actions"][actions.index("timer.start")]
    assert start["target"]["entity_id"] == ("INPUT", "wind_timer")
    assert start["data"]["duration"] == "{{ hold_duration }}"
    # The timer is started unconditionally, so the lock works without blinds.
    assert "if" not in start and "condition" not in start


def test_blinds_list_is_optional():
    assert WIND["blueprint"]["input"]["covers"]["default"] == []


@pytest.mark.parametrize("covers,opens", [([], False), (["cover.a"], True), (["cover.a", "cover.b", "cover.c"], True)])
def test_blinds_opened_only_when_selected(covers, opens):
    env, ctx = _wind_ctx("on", "alarm", covers=covers)
    _, step = _open_step()
    assert _truthy(env.from_string(step["if"][0]["value_template"]).render(**ctx)) is opens


def test_wind_alarm_ignores_enable_and_manual_override():
    names = set(WIND["blueprint"]["input"])
    assert not names & {"enable_entity", "manual_timer"}


def test_logbook_message():
    env, ctx = _wind_ctx("on", "alarm")
    step = next(s for s in WIND["actions"] if s.get("action") == "logbook.log")
    message = " ".join(env.from_string(step["data"]["message"]).render(**ctx).split())
    assert message == "Wind alarm – 3 blinds opened, lock for 120 min."


def test_logbook_message_without_blinds():
    env, ctx = _wind_ctx("on", "alarm", covers=[])
    step = next(s for s in WIND["actions"] if s.get("action") == "logbook.log")
    message = " ".join(env.from_string(step["data"]["message"]).render(**ctx).split())
    assert message == "Wind alarm – blinds left to KNX, lock for 120 min."


# ---------------------------------------------------------------------------
# Wind lock in the Blinds Facade blueprint
# ---------------------------------------------------------------------------

def _facade(wind_state, wind_lock_timer=WIND_TIMER, trigger=None, last_cmd_age=600):
    inputs = _defaults()
    inputs["wind_lock_timer"] = wind_lock_timer
    states = {
        WIND_TIMER: wind_state, "timer.manual": "idle", "input_boolean.enable": "on",
        "sensor.indoor": 21, "input_select.state": "open",
    }
    attrs = {"input_datetime.last_command": {"timestamp": NOW.timestamp() - last_cmd_age}}
    env = _env(NOW, states, attrs)
    ctx = _render_vars(FACADE["variables"], inputs, env)
    ctx["trigger"] = trigger or {"id": "eval"}
    return env, ctx


def _default_gate_passes(env, ctx):
    """Evaluate the template conditions of the default branch up to the forecast call."""
    for step in FACADE["actions"][0]["default"]:
        if step.get("action") == "weather.get_forecasts":
            return True
        if step.get("condition") == "template":
            if not _truthy(env.from_string(step["value_template"]).render(**ctx)):
                return False
    raise AssertionError("forecast step not found")


def _manual_detected(env, ctx):
    branch = FACADE["actions"][0]["choose"][0]
    template = branch["sequence"][0]["value_template"]
    return _truthy(env.from_string(template).render(**ctx))


class _State:
    def __init__(self, position):
        self.state = "open"
        self.attributes = {"current_position": position}


def _position_change():
    return {"id": "manual", "entity_id": "cover.test", "attribute": "current_position",
            "from_state": _State(0), "to_state": _State(100)}


def test_facade_passive_while_wind_lock_active():
    env, ctx = _facade("active")
    assert not _default_gate_passes(env, ctx)


def test_facade_runs_when_wind_lock_idle():
    env, ctx = _facade("idle")
    assert _default_gate_passes(env, ctx)


def test_facade_runs_without_wind_lock_configured():
    env, ctx = _facade("active", wind_lock_timer="")
    assert _default_gate_passes(env, ctx)


def test_wind_opening_is_not_detected_as_manual_operation():
    env, ctx = _facade("active", trigger=_position_change())
    assert not _manual_detected(env, ctx)


def test_manual_operation_still_detected_without_wind_lock():
    env, ctx = _facade("idle", trigger=_position_change())
    assert _manual_detected(env, ctx)


def test_facade_reapplies_target_when_wind_lock_ends():
    trigger = next(t for t in FACADE["triggers"] if t.get("id") == "wind_end")
    assert trigger["event_type"] == "timer.finished"
    assert trigger["event_data"]["entity_id"] == ("INPUT", "wind_lock_timer")

    default = FACADE["actions"][0]["default"]
    # A short delay comes before the lock check, so a restart by the wind
    # automation (alarm still active) wins the race.
    first = default[0]
    assert first["if"][0] == {"condition": "trigger", "id": "wind_end"}
    assert first["then"][0]["delay"] == "00:00:05"
    lock_check = default[1]
    assert "wind_lock_timer" in lock_check["value_template"]

    # The final move condition treats wind_end like a forced re-apply.
    final = next(s for s in default if s.get("condition") == "template"
                 and "desired != current" in s["value_template"])
    env, ctx = _facade("idle", trigger={"id": "wind_end"})
    ctx.update(night_hold=False, desired="open", current="open")
    assert _truthy(env.from_string(final["value_template"]).render(**ctx))
