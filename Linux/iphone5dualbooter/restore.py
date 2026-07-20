from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .linux_legacy import LegacyPromptFn, run_legacy_native_restore
from .models import WorkflowSettings


class RestoreBackendError(RuntimeError):
    pass


def run_restore_backend(
    settings: WorkflowSettings,
    app_root: Path,
    log: Callable[[str], None],
    legacy_prompt: LegacyPromptFn | None = None,
) -> None:
    if settings.skip_restore:
        log("Restore skipped: using an already jailbroken iOS 8.4.1 installation.")
        return
    run_legacy_native_restore(settings, app_root, log, legacy_prompt)
