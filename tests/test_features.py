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


# ---------------------------------------------------------------------------
# The media section: vod and ocr, independent of each other and of everything
# else. The ffmpeg half (manta, fileservices, transcription) was retired in 1.21
# — see toto_libs/limbo/*/PARKED.md — so these tests also pin down that the flags
# behind it are gone rather than merely defaulted off.
# ---------------------------------------------------------------------------

def test_vod_follows_media_when_unnamed():
    # The back-compat path, and the whole reason BUILD_MEDIA still exists: six
    # shipped profiles set it and nothing else, and they must keep playback.
    assert resolve(BUILD_MEDIA=1).vod is True
    assert resolve().vod is False


def test_vod_can_be_dropped_from_a_media_host():
    assert resolve(BUILD_MEDIA=1, BUILD_VOD=0).vod is False


def test_media_no_longer_drags_the_celery_layer():
    # What the 1.21 retirement bought. Before it, BUILD_MEDIA reached workflows
    # through fileservices' FK and so forced the realtime pip layer; zenobia_mini
    # documented that as the reason it could not be lean. vod is a template and a
    # queryset, so a media host can now be plain WSGI.
    f = resolve(BUILD_MEDIA=1)
    assert f.vod is True
    assert (f.workflows, f.realtime, f.needs_channels) == (False, False, False)


def test_ocr_stands_alone():
    # ocr used to default from the neo4j tier and to force `graph` via a closure,
    # which cost five Neo4j apps and an auto-started neo4j container for one
    # optional button that greys itself out. It is opt-in and self-contained now.
    f = resolve(BUILD_OCR=1)
    assert f.ocr is True
    assert f.tesseract is True                      # brings its own binary
    assert (f.graph, f.neo4j, f.vicuna) == (False, False, False)
    assert (f.workflows, f.realtime) == (False, False)


def test_ocr_is_not_implied_by_the_neo4j_tier_any_more():
    assert resolve(BUILD_NEO4J=1).ocr is False
    assert resolve(BUILD_GRAPH=1).ocr is False


def test_the_graph_no_longer_needs_ocr_to_be_off_or_on():
    # The reverse direction of the same closure: graph still stands up by itself.
    f = resolve(BUILD_GRAPH=1)
    assert (f.graph, f.neo4j) == (True, True)


def test_vod_and_ocr_do_not_imply_each_other():
    assert resolve(BUILD_VOD=1).ocr is False
    assert resolve(BUILD_OCR=1).vod is False


@pytest.mark.parametrize("attr", ["manta", "fileservices", "ffmpeg"])
def test_the_retired_media_fields_are_gone(attr):
    # Deliberately asserted rather than left implicit: these three were read by
    # host settings and by deploy.py's Dockerfile ARG mapping, so a stray
    # reference is a build failure, not a silent False.
    assert not hasattr(resolve(), attr)


@pytest.mark.parametrize("name", ["BUILD_MANTA", "BUILD_FILESERVICES", "INSTALL_FFMPEG"])
def test_a_retired_flag_is_ignored_rather_than_an_error(name):
    # Unlike BUILD_STUDIO, these are NOT honoured as fallbacks — the apps behind
    # them are in limbo. An old config keeps deploying; it just gets nothing for
    # that key. Failing loudly here would strand every profile that still sets it.
    f = resolve(**{name: 1})
    assert (f.media, f.vod, f.ocr) == (False, False, False)
    assert f.realtime is False


# ---------------------------------------------------------------------------
# The realtime tier, and the BUILD_STUDIO name it used to have
# ---------------------------------------------------------------------------

def test_the_realtime_tier_defaults_its_group():
    tier = resolve(BUILD_REALTIME=1)
    assert (tier.chat, tier.workflows, tier.weather) == (True, True, True)
    assert tier.realtime is True
    # A member flag still overrides the tier that offered it as a default.
    assert resolve(BUILD_REALTIME=1, BUILD_CHAT=0).chat is False


def test_realtime_is_derived_not_merely_echoed():
    # The tier is an INPUT default and also an OUTPUT: any realtime feature
    # implies the pip layer, even when the tier flag was never set. This is why
    # zenobia still installs it after the split — it keeps workflows.
    assert resolve(BUILD_WORKFLOWS=1).realtime is True
    assert resolve(BUILD_CHAT=1).realtime is True
    assert resolve(BUILD_LATEX=1).realtime is True      # via needs_channels
    assert resolve().realtime is False


def test_build_studio_is_still_honoured_as_the_old_tier_name():
    # The sibling hosts (delta, faros) vendor their own copy of this module at
    # their own pins and still say BUILD_STUDIO in their configs. Their next
    # re-vendor must not change a single build decision.
    old = resolve(BUILD_STUDIO=1)
    new = resolve(BUILD_REALTIME=1)
    assert old == new
    assert old.realtime is True
    assert (old.chat, old.workflows, old.weather) == (True, True, True)


def test_features_studio_property_still_answers():
    # Read by the sibling hosts' deploy tooling as f.studio.
    assert resolve(BUILD_REALTIME=1).studio is True
    assert resolve().studio is False
    assert resolve(BUILD_WORKFLOWS=1).studio is resolve(BUILD_WORKFLOWS=1).realtime


def test_an_explicit_build_realtime_wins_over_the_old_name():
    # Both present and disagreeing: the new name decides, so a host can retire
    # BUILD_STUDIO from its configs incrementally without a flag-day.
    assert resolve(BUILD_STUDIO=1, BUILD_REALTIME=0).realtime is False
    assert resolve(BUILD_STUDIO=0, BUILD_REALTIME=1).realtime is True
