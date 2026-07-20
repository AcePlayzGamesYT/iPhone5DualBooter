from __future__ import annotations

from pathlib import Path
from typing import Callable

from .models import WorkflowSettings
from .wsl_legacy import (
    ExternalPwnPromptFn,
    LegacyPromptFn,
    run_legacy_native_restore,
    run_legacy_wsl_restore,
)


class RestoreBackendError(RuntimeError):
    pass


def run_restore_backend(
    settings: WorkflowSettings,
    app_root: Path,
    log: Callable[[str], None],
    legacy_prompt: LegacyPromptFn | None = None,
    external_pwn_prompt: ExternalPwnPromptFn | None = None,
) -> None:
    if settings.skip_restore:
        log("Restore skipped: using an already jailbroken iOS 8.4.1 installation.")
        return

    if settings.restore_mode == "legacy_wsl_full":
        result = run_legacy_wsl_restore(
            settings,
            app_root,
            log,
            legacy_prompt,
            external_pwn_prompt,
        )
        log(
            "Legacy iOS Kit full restore confirmed by the user. "
            f"Transcript: {result.transcript}"
        )
        return

    if settings.restore_mode == "legacy_native":
        run_legacy_native_restore(settings, app_root, log)
        return

    raise RestoreBackendError(f"Unknown restore mode: {settings.restore_mode}")
