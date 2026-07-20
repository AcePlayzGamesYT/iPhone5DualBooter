#!/bin/bash
set -Eeuo pipefail
cd "$(dirname "$0")"
exec ./run_macos.command
