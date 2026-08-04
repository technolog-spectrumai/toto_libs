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
# The media section, split across two packages since 1.21. toto-media (which hosts
# pin) ships vod and ocr; manta, fileservices and transcription live in
# toto-media-ops, a package NO HOST PINS. The flags for that tier still resolve, so
# a host that pins the wheel needs no library change — but nothing defaults them on,
# and BUILD_MEDIA does not default BUILD_OCR either, so asking for video never
# silently buys a tesseract apt layer.
# ---------------------------------------------------------------------------

def test_vod_follows_media_when_unnamed():
    # The back-compat path, and the whole reason BUILD_MEDIA still exists: six
    # shipped profiles set it and nothing else, and they must keep playback.
    assert resolve(BUILD_MEDIA=1).vod is True
    assert resolve().vod is False


def test_vod_can_be_dropped_from_a_media_host():
    assert resolve(BUILD_MEDIA=1, BUILD_VOD=0).vod is False


def test_media_no_longer_drags_the_celery_layer():
    # What the 1.21 split bought. Before it, BUILD_MEDIA reached workflows through
    # fileservices' FK and so forced the realtime pip layer; zenobia_mini documented
    # that as the reason it could not be lean. vod is a template and a queryset, so
    # a media host can now be plain WSGI.
    f = resolve(BUILD_MEDIA=1)
    assert f.vod is True
    assert (f.workflows, f.realtime, f.needs_channels) == (False, False, False)
    assert (f.ffmpeg, f.tesseract) == (False, False)


@pytest.mark.parametrize("flag_name", ["BUILD_MANTA", "BUILD_FILESERVICES", "BUILD_OCR"])
def test_the_heavier_media_flags_are_never_implied_by_the_media_tier(flag_name):
    # THE invariant of the split: BUILD_MEDIA is what every shipped profile sets, so
    # it must buy nothing a profile did not ask for. For manta/fileservices that means
    # not referencing a wheel the host does not pin; for ocr — which now ships in the
    # same wheel as vod — it means not pulling a tesseract apt layer into an image
    # that only wanted a video player.
    attr = {"BUILD_MANTA": "manta", "BUILD_FILESERVICES": "fileservices",
            "BUILD_OCR": "ocr"}[flag_name]
    assert getattr(resolve(BUILD_MEDIA=1), attr) is False
    assert getattr(resolve(BUILD_VOD=1), attr) is False
    assert getattr(resolve(**{flag_name: 1}), attr) is True


def test_manta_buys_the_celery_layer_but_not_workflows():
    # manta has exactly one FK (FileJob.owner -> User) and names workflows nowhere,
    # so it is deliberately NOT in the workflows closure. What it does need is
    # celery: tasks_direct.py imports it at module scope, so the image must carry
    # the realtime pip layer or it will not boot.
    f = resolve(BUILD_MANTA=1)
    assert (f.manta, f.realtime, f.ffmpeg) == (True, True, True)
    assert f.workflows is False


def test_jess_buys_the_celery_layer_but_not_workflows():
    # Same shape as manta, one step stronger: jess/tasks.py imports shared_task at
    # module scope AND its EMAIL_BACKEND calls .delay() on the request path, so a
    # BUILD_JESS=1 image without the realtime pip layer would not boot. It names
    # workflows nowhere and has no FK to WorkflowRun, so it is not in that closure.
    f = resolve(BUILD_JESS=1)
    assert (f.jess, f.realtime) == (True, True)
    assert f.workflows is False
    assert f.chat is False


def test_jess_is_opt_in_and_no_tier_turns_it_on():
    # It must not arrive with the realtime tier: a host that asked for chat has not
    # asked for its EMAIL_BACKEND to be replaced.
    assert resolve().jess is False
    assert resolve(BUILD_REALTIME=1).jess is False
    assert resolve(BUILD_JESS=0).jess is False


def test_primula_is_opt_in_and_buys_nothing_else():
    # Sheets save over a plain POST — no celery, no channels — so BUILD_PRIMULA must
    # not pull the realtime layer in, and nothing else may switch it on: what it
    # actually costs a host is the vendored Univer JS baked into the image.
    assert resolve().primula is False
    assert resolve(BUILD_PRIMULA=0).primula is False
    f = resolve(BUILD_PRIMULA=1)
    assert f.primula is True
    assert (f.realtime, f.workflows, f.editor) == (False, False, False)


def test_fileservices_forces_workflows():
    # The opposite case, and the strongest closure in the file: FileServiceRun has a
    # live FK to workflows.WorkflowRun and predefined_tasks.py imports the workflows
    # registry at module scope. Without this Django fails with fields.E300/E307.
    f = resolve(BUILD_FILESERVICES=1)
    assert (f.fileservices, f.workflows, f.realtime, f.ffmpeg) == (True, True, True, True)


def test_manta_and_fileservices_are_independent_of_each_other():
    # Established while splitting these flags, against what the old comments said:
    # manta's only two references to fileservices are a pure function in the same
    # wheel and a plugin module fileservices itself imports. So neither implies
    # the other, in either direction.
    assert resolve(BUILD_MANTA=1).fileservices is False
    assert resolve(BUILD_FILESERVICES=1).manta is False


def test_ocr_stands_alone():
    # ocr used to default from the neo4j tier and to force `graph` via a closure,
    # which cost five Neo4j apps and an auto-started neo4j container for one optional
    # button that greys itself out. It is opt-in and self-contained now.
    f = resolve(BUILD_OCR=1)
    assert f.ocr is True
    assert f.tesseract is True                      # brings its own binary
    assert (f.graph, f.neo4j, f.vicuna) == (False, False, False)
    assert (f.workflows, f.realtime, f.ffmpeg) == (False, False, False)


def test_ocr_is_not_implied_by_the_neo4j_tier_any_more():
    assert resolve(BUILD_NEO4J=1).ocr is False
    assert resolve(BUILD_GRAPH=1).ocr is False


def test_the_graph_still_stands_up_without_ocr():
    f = resolve(BUILD_GRAPH=1)
    assert (f.graph, f.neo4j) == (True, True)


def test_tesseract_no_longer_bundles_ffmpeg():
    # INSTALL_TESSERACT used to turn ffmpeg on as well (the old "OCR/media binaries"
    # bundle). Two separate features, two separate binaries.
    assert resolve(INSTALL_TESSERACT=1).ffmpeg is False
    assert resolve(INSTALL_FFMPEG=1).tesseract is False


# ---------------------------------------------------------------------------
# pdflatex and the ACE editor: two things latex used to drag along
# ---------------------------------------------------------------------------

def test_texlive_is_not_implied_by_latex_any_more():
    # It has two independent consumers — texlab compilation AND notarius
    # contract→PDF, which is a separate implementation sharing no import with
    # texlab and installed unconditionally. Defaulting it from `latex` meant a
    # host that moved texlab away silently lost notarius PDF export.
    assert resolve(BUILD_LATEX=1).texlive is False
    assert resolve(BUILD_LATEX=1, INSTALL_TEXLIVE=1).texlive is True


def test_texlive_can_be_had_without_latex_at_all():
    # The notarius-only case: no texlab, but contracts still export to PDF.
    f = resolve(INSTALL_TEXLIVE=1)
    assert (f.texlive, f.latex) == (True, False)


def test_weasyprint_is_its_own_explicit_flag():
    # HTML→PDF is a pip layer whose native libs already ship in every base image, so it
    # gates only the wheel and the feature. Off by default, on with BUILD_WEASYPRINT=1,
    # and implied by nothing else (notarius contract→PDF now, the invoice generator later).
    assert resolve().weasyprint is False
    assert resolve(BUILD_WEASYPRINT=1).weasyprint is True
    assert resolve(INSTALL_TEXLIVE=1).weasyprint is False


def test_the_editor_still_follows_latex_when_unnamed():
    # The default is unchanged, so no existing profile resolves differently.
    assert resolve(BUILD_LATEX=1).editor is True
    # BUILD_PYEDITOR is gone with the retired antaresia (1.45): setting it now
    # resolves nothing, exactly like any unknown flag.
    assert resolve(BUILD_PYEDITOR=1).editor is False
    assert resolve().editor is False


def test_the_editor_survives_latex_leaving():
    # The whole point of making it settable. toto.editor carries EIGHT file-type
    # plugins and only two are latex's; without this a host that moved LaTeX
    # elsewhere lost json/yaml/xml/csv/html/text editing too, and every vault
    # Edit link rendered as "".
    f = resolve(BUILD_EDITOR=1)
    assert f.editor is True
    assert f.latex is False


def test_the_editor_can_be_refused_even_with_latex_on():
    assert resolve(BUILD_LATEX=1, BUILD_EDITOR=0).editor is False


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


def test_canasta_buys_channels_and_nothing_else():
    # zenobia owns toto.canasta in its own portion, so this flag installs no app
    # here — it exists to put the game's table socket into the needs_channels
    # closure, and through it into `realtime`, which is what decides whether the
    # image installs requirements.realtime.txt. Get that wrong and the host names
    # daphne/channels in INSTALLED_APPS while the image ships neither.
    f = resolve(BUILD_CANASTA=1)
    assert (f.canasta, f.needs_channels, f.realtime) == (True, True, True)
    # It must NOT drag in the realtime tier's own apps: canasta has no chat by
    # design (the game forbids player-to-player talk), no workflows, no weather.
    assert (f.chat, f.workflows, f.weather) == (False, False, False)
    assert resolve().canasta is False


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
