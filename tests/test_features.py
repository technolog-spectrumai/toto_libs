"""The BUILD_* flag contract in toto.features.

These are pure-function tests: resolve_features() takes a name -> raw accessor
and returns the effective build decisions. No Django, no database.
"""
import pytest

from toto.features import FeatureConfigError, resolve_features


def resolve(**env):
    """Resolve from a plain dict, the way a deploy YAML's env block does."""
    return resolve_features({k: str(v) for k, v in env.items()}.get)


# ---------------------------------------------------------------------------
# Subtractive flags (default on)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,attr", [("BUILD_GEO", "geo"), ("BUILD_KANBAN", "kanban")])
def test_subtractive_flags_default_on(name, attr):
    # A host that never names the flag keeps the feature — that is what makes
    # these safe to add to an existing deployment.
    assert getattr(resolve(), attr) is True
    assert getattr(resolve(**{name: "1"}), attr) is True


@pytest.mark.parametrize("name,attr", [("BUILD_GEO", "geo"), ("BUILD_KANBAN", "kanban")])
def test_subtractive_flags_turn_off_with_an_explicit_zero(name, attr):
    assert getattr(resolve(**{name: "0"}), attr) is False


@pytest.mark.parametrize("name,attr", [("BUILD_GEO", "geo"), ("BUILD_KANBAN", "kanban")])
def test_a_yaml_boolean_reads_as_off_even_when_it_says_true(name, attr):
    # THE trap for a default-on flag. flag() is `str(raw) == "1"`, so ONLY the
    # literal string "1" is true. Writing `BUILD_KANBAN: true` in a deploy YAML
    # therefore turns the feature OFF — the opposite of what it reads like —
    # while omitting the key entirely leaves it ON.
    assert getattr(resolve(**{name: "true"}), attr) is False
    assert getattr(resolve(**{name: "True"}), attr) is False
    assert getattr(resolve(**{name: ""}), attr) is False
    # Absent is the only way to mean "leave the default alone".
    assert getattr(resolve(), attr) is True


# ---------------------------------------------------------------------------
# kanban specifically
# ---------------------------------------------------------------------------

def test_kanban_is_independent_of_every_other_feature():
    # Nothing has a model FK into kanban, so it must neither pull anything in
    # nor be pulled in by anything. Turning the whole platform on must leave a
    # BUILD_KANBAN=0 host without kanban.
    everything = resolve(
        BUILD_STUDIO=1, BUILD_MEDIA=1, BUILD_LATEX=1, BUILD_GITVAULT=1,
        BUILD_TRAVELS=1, BUILD_MONIT=1, BUILD_KANBAN=0,
    )
    assert everything.kanban is False
    assert everything.workflows is True   # the closure still ran

    # ...and a bare host with kanban off keeps everything else off.
    bare = resolve(BUILD_KANBAN=0)
    assert bare.kanban is False
    assert bare.chat is False
    assert bare.workflows is False


def test_kanban_off_does_not_disturb_geo():
    assert resolve(BUILD_KANBAN=0).geo is True
    assert resolve(BUILD_GEO=0).kanban is True


# ---------------------------------------------------------------------------
# The one closure that raises
# ---------------------------------------------------------------------------

def test_map_features_without_geometry_are_a_hard_error():
    with pytest.raises(FeatureConfigError):
        resolve(BUILD_WEATHER=1, BUILD_GEO=0)
    with pytest.raises(FeatureConfigError):
        resolve(BUILD_TRAVELS=1, BUILD_GEO=0)
