"""
Manta command registry.

Importing this package imports every command module (which self-registers via
``BaseCommand.__init_subclass__``), in the order they should appear in the UI.
"""

from .base import BaseCommand, CommandSpec, UnknownCommand

# Import order defines the operation dropdown order.
from . import (  # noqa: F401
    compress, resize, crop, change_fps, cut, extract_mp3, remove_audio,
    replace_audio, add_subtitles, add_watermark, thumbnail, gif,
    vstack, hstack, concat, probe, transcribe,
)

COMMANDS = BaseCommand.all()
OPERATIONS = tuple(c.key for c in COMMANDS)
OPERATION_LABELS = {c.key: c.label for c in COMMANDS}
COMMAND_FILE_PRESETS = {c.key: {"inputs": c.inputs, "outputs": c.outputs} for c in COMMANDS}

# Builder tabs, in display order. ``ffmpeg`` groups the many ffmpeg commands
# behind a command dropdown; the others are single-command, focused tabs.
TAB_ORDER = ("ffmpeg", "ffprobe", "transcribe")


def commands_for_tab(tab) -> list[type[BaseCommand]]:
    return [c for c in COMMANDS if c.tab == tab]


def get_command(key):
    return BaseCommand.get(key)


def get_command_file_preset(key) -> dict:
    c = BaseCommand.get(key)
    return {"inputs": c.inputs, "outputs": c.outputs}


def get_command_input_slots(key) -> dict:
    return BaseCommand.get(key).inputs


def get_command_output_slots(key) -> dict:
    return BaseCommand.get(key).outputs
