# GitHub source checkout

This repository intentionally does not include a Python virtual environment,
Apple IPSW files, SHSH blobs, downloaded Legacy iOS Kit files, cached Windows
idevicerestore binaries, WSL build caches, logs, or packaged builds.

On Windows, run `run.bat`. It creates `.venv`, installs `requirements.txt`, and
starts the app. The app downloads its required tool data into `tools/` when it
is needed.

To build the Windows application, run `build_windows.bat`.
