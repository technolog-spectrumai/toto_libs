"""
Back-compat shim. The real command logic now lives in ``manta/commands/``;
this module preserves the old ``factory`` API used across the app and tests.
"""

from __future__ import annotations

from .commands import (  # noqa: F401
    BaseCommand, CommandSpec, UnknownCommand,
    COMMANDS, OPERATIONS, OPERATION_LABELS, COMMAND_FILE_PRESETS,
    get_command, get_command_file_preset,
    get_command_input_slots, get_command_output_slots,
)

# Old aliases.
FFmpegCommandSpec = CommandSpec
UnknownOperation = UnknownCommand

# Operations that consume an extra (secondary) input file = >= 2 input slots.
SECONDARY_OPERATIONS = frozenset(
    k for k in OPERATIONS if len(BaseCommand.get(k).inputs) >= 2
)


class FFmpegCommandFactory:
    """Builds a :class:`CommandSpec` by delegating to the command class."""

    def build(self, operation, *, input_name, extra_input_names=None, params=None) -> CommandSpec:
        cmd = BaseCommand.get(operation)()
        return cmd.build_spec(
            input_name=input_name, extra_input_names=extra_input_names, params=params,
        )
