#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -lt 9 ]]; then
    echo "usage: $0 LEGACY_ROOT CACHE_ROOT PATCHER RETRY_SECONDS FULL_FALLBACK LOG_FILE LIBIRECOVERY_PATCHER COMPAT_PATCHER LIBIMOBILEDEVICE_TRANSPORT_PATCHER"
    exit 2
fi

LEGACY_ROOT="$1"
CACHE_ROOT="$2"
PATCHER="$3"
RETRY_SECONDS="$4"
FULL_FALLBACK="$5"
LOG_FILE="$6"
LIBIRECOVERY_PATCHER="$7"
COMPAT_PATCHER="$8"
LIBIMOBILEDEVICE_TRANSPORT_PATCHER="$9"

SOURCE_COMMIT_PIN="22682048240929637124781353ab3b6ee30b8dad"

# Autoconf/Automake reject source-directory paths containing spaces with
# "unsafe srcdir value". CACHE_ROOT is usually on /mnt/c and may contain
# folders such as "3D Objects", so all generated build state lives on WSL's
# native ext4 filesystem instead.
WSL_BUILD_ROOT="${IPHONE5DUALBOOTER_WSL_BUILD_ROOT:-$HOME/.cache/iphone5dualbooter/idevicerestore-$SOURCE_COMMIT_PIN}"
SOURCE_DIR="$WSL_BUILD_ROOT/source"
FAST_BUILD_DIR="$WSL_BUILD_ROOT/fast-build"
CACHED_BUILD_DIR="$WSL_BUILD_ROOT/cached-deps-build"
INSTALL_DIR="$WSL_BUILD_ROOT/install"

TARGET="$LEGACY_ROOT/bin/linux/x86_64/idevicerestore"
ORIGINAL="$LEGACY_ROOT/bin/linux/x86_64/idevicerestore.original"
PATCH_MARKER="IPHONE5DUALBOOTER_DFU_RECOVERY_RECONNECT_V12"
RECOVERY_PATCH_MARKER="IPHONE5DUALBOOTER_RECOVERY_LIVE_HANDSHAKE_V7"
ASR_PATCH_MARKER="IPHONE5DUALBOOTER_ASR_VALIDATION_RECOVERY_V4"
RESTORE_ASR_PATCH_MARKER="IPHONE5DUALBOOTER_RESTORE_ASR_SAME_SESSION_V2"
RESTORED_TRANSPORT_PATCH_MARKER="IPHONE5DUALBOOTER_RESTORED_TRANSPORT_V1"

COMMON_CFLAGS="-std=gnu17 -O2 -g -Wno-error=int-conversion -Wno-error=incompatible-pointer-types -Wno-error=implicit-function-declaration"
COMMON_CXXFLAGS="-std=gnu++17 -O2 -g"
COMMON_CPPFLAGS="-D_FILE_OFFSET_BITS=64"
COMMON_LDFLAGS="-Wl,--allow-multiple-definition -L/usr/local/lib -L/usr/lib/x86_64-linux-gnu -Wl,-rpath,/usr/local/lib"
COMMON_LIBS="-lzstd -llzma -lbz2 -ldl"
export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:/usr/local/lib/x86_64-linux-gnu/pkgconfig:/usr/lib/x86_64-linux-gnu/pkgconfig:${PKG_CONFIG_PATH:-}"
export LD_LIBRARY_PATH="/usr/local/lib:/usr/local/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"

PATCH_RUNTIME_STRING_1="[iPhone5DualBooter] Initial iBEC upload failed:"
PATCH_RUNTIME_STRING_2="[iPhone5DualBooter] Retrying iBEC with freshly reopened DFU handles until one complete upload succeeds"
PATCH_RUNTIME_STRING_3="[iPhone5DualBooter] iBEC upload succeeded with the original send mode"
PATCH_RUNTIME_STRING_4="[iPhone5DualBooter] LIVE_HOST_USB_HANDOFF_REQUIRED"
PATCH_RUNTIME_STRING_5="[iPhone5DualBooter] Waiting inside the same idevicerestore process"
PATCH_RUNTIME_STRING_6="[iPhone5DualBooter] Windows USB handoff acknowledged"
PATCH_RUNTIME_STRING_7="[iPhone5DualBooter] Connected to post-iBEC Recovery mode"
PATCH_RUNTIME_STRING_8="[iPhone5DualBooter] ASR validation receive timed out"
PATCH_RUNTIME_STRING_9="[iPhone5DualBooter] ERROR: the initial ASR Initiate handshake failed"
PATCH_RUNTIME_STRING_10="[iPhone5DualBooter] ASR validation reached the Payload request"
PATCH_RUNTIME_STRING_11="[iPhone5DualBooter] ASR OOB request #"
PATCH_RUNTIME_STRING_12="[iPhone5DualBooter] ASR OOB request #%u send would block"
PATCH_RUNTIME_STRING_13="[iPhone5DualBooter] ASR OOB request #%u completed successfully"
PATCH_RUNTIME_STRING_14="[iPhone5DualBooter] ASR OOB request #%u is temporarily backpressured"
PATCH_RUNTIME_STRING_15="[iPhone5DualBooter] Opening one ASR session for validation and payload"
PATCH_RUNTIME_STRING_16="[iPhone5DualBooter] ASR validation is still quiet"
PATCH_RUNTIME_STRING_17="[iPhone5DualBooter] WSL 64 KiB OOB guard active"
PATCH_RUNTIME_STRING_18="[iPhone5DualBooter] Restored transfer %s progress"
PATCH_RUNTIME_STRING_19="[iPhone5DualBooter] ERROR: restored transport timed out waiting 60 seconds"

binary_has_ibec_retry_patch() {
    local candidate="$1"
    local index=1
    local variable_name=""
    local signature=""
    local missing=0

    [[ -x "$candidate" ]] || return 1

    while [[ "$index" -le 19 ]]; do
        variable_name="PATCH_RUNTIME_STRING_${index}"
        signature="${!variable_name}"

        if ! LC_ALL=C grep -aFq -- "$signature" "$candidate"; then
            echo "Missing runtime signature #${index}: $signature"
            missing=1
        fi

        index=$((index + 1))
    done

    [[ "$missing" -eq 0 ]]
}

mkdir -p "$CACHE_ROOT" "$WSL_BUILD_ROOT" "$(dirname "$LOG_FILE")"

if [[ "$SOURCE_DIR" == *" "* ]]; then
    echo "ERROR: WSL build source path unexpectedly contains spaces: $SOURCE_DIR"
    echo "Set IPHONE5DUALBOOTER_WSL_BUILD_ROOT to a no-space WSL path."
    exit 38
fi

if [[ "$SOURCE_DIR" == /mnt/* ]]; then
    echo "ERROR: WSL build source path is still on a Windows-mounted drive: $SOURCE_DIR"
    echo "Autotools source/build files must stay on WSL's native filesystem."
    exit 38
fi

printf '%s\n' "$WSL_BUILD_ROOT" > "$CACHE_ROOT/wsl-build-workspace.txt"

if [[ "${IPHONE5DUALBOOTER_LOG_INITIALIZED:-0}" != "1" ]]; then
    : > "$LOG_FILE"
fi
exec > >(tee -a "$LOG_FILE") 2>&1

stage="startup"
report_failure() {
    status=$?
    echo
    echo "============================================================"
    echo "BUILD FAILED during stage: $stage"
    echo "Exit code: $status"
    echo "Full log: $LOG_FILE"
    echo "============================================================"
    exit "$status"
}
trap report_failure ERR

echo "============================================================"
echo "iPhone5DualBooter patched idevicerestore build v0.4.43"
echo "Pinned source commit: $SOURCE_COMMIT_PIN"
echo "Target: $TARGET"
echo "Windows project cache: $CACHE_ROOT"
echo "No-space WSL build workspace: $WSL_BUILD_ROOT"
echo "Runtime retry mode: until success or user cancellation"
echo "============================================================"

if [[ ! -f "$TARGET" ]]; then
    echo "ERROR: Legacy's idevicerestore binary is missing: $TARGET"
    exit 10
fi

BUILT=""
stage="completed-build cache recovery"
echo "[0/9] Looking for a completed patched binary from an earlier attempt..."
for cached_candidate in \
    "$CACHED_BUILD_DIR/src/idevicerestore" \
    "$FAST_BUILD_DIR/src/idevicerestore" \
    "$SOURCE_DIR/bin/idevicerestore" \
    "$CACHE_ROOT/completed-binary/idevicerestore"
do
    if binary_has_ibec_retry_patch "$cached_candidate"; then
        BUILT="$cached_candidate"
        echo "Recovered already-compiled patched binary: $BUILT"
        echo "Skipping APT refresh, source reset, and recompilation. The WSL-native cache is reusable across project folders."
        break
    fi
done

if [[ -z "$BUILT" ]]; then

apt_has_candidate() {
    local package="$1" candidate
    candidate="$(apt-cache policy "$package" 2>/dev/null | awk '/Candidate:/ {print $2; exit}')"
    [[ -n "$candidate" && "$candidate" != "(none)" ]]
}

apt_install_group() {
    local label="$1"; shift
    local -a requested=("$@") available=() unavailable=() failed=()
    local package
    echo "Checking $label packages..."
    for package in "${requested[@]}"; do
        if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
            echo "  [installed] $package"
        elif apt_has_candidate "$package"; then
            available+=("$package")
        else
            unavailable+=("$package")
            echo "  [no candidate] $package"
        fi
    done
    if ((${#available[@]})); then
        if ! sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends -o Acquire::Retries=3 "${available[@]}"; then
            echo "Combined install failed; retrying packages individually."
            sudo DEBIAN_FRONTEND=noninteractive apt-get -f install -y || true
            for package in "${available[@]}"; do
                if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
                    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends -o Acquire::Retries=3 "$package" || failed+=("$package")
                fi
            done
        fi
    fi
    if ((${#unavailable[@]} || ${#failed[@]})); then
        echo "$label package problems:"
        ((${#unavailable[@]})) && echo "  unavailable: ${unavailable[*]}"
        ((${#failed[@]})) && echo "  failed: ${failed[*]}"
        return 1
    fi
}

stage="APT repository refresh"
echo "[1/9] Refreshing Ubuntu package information..."
command -v add-apt-repository >/dev/null 2>&1 && sudo add-apt-repository -y universe >/dev/null 2>&1 || true
sudo apt-get update -o Acquire::Retries=3 || echo "WARNING: apt update failed; using existing cache."

stage="required build-tool installation"
required_packages=(
    ca-certificates git build-essential autoconf automake libtool libtool-bin
    pkg-config cmake python3 curl aria2 unzip xz-utils bzip2 autopoint
    libusb-1.0-0-dev libpng-dev libreadline-dev libzstd-dev python3-dev
    zlib1g-dev libbz2-dev liblzma-dev
)
apt_install_group "required build-tool" "${required_packages[@]}" || exit 31

stage="compiler/linker preflight"
echo "[2/9] Running compiler and linker preflight..."
probe_dir="$(mktemp -d /tmp/iphone5dualbooter-probe.XXXXXX)"
cat > "$probe_dir/probe.c" <<'EOF'
#include <sys/types.h>
int main(void) { off_t value = 0; return (int)value; }
EOF
/usr/bin/gcc $COMMON_CFLAGS $COMMON_CPPFLAGS "$probe_dir/probe.c" $COMMON_LDFLAGS $COMMON_LIBS -o "$probe_dir/probe"
"$probe_dir/probe"
rm -rf "$probe_dir"
echo "Compiler/linker preflight passed."

stage="optional fast-build dependency installation"
fast_packages=(libplist-dev libimobiledevice-dev libirecovery-dev libusbmuxd-dev libzip-dev libcurl4-openssl-dev libssl-dev)
FAST_DEPS_READY=1
apt_install_group "fast-build development" "${fast_packages[@]}" || FAST_DEPS_READY=0

stage="source clone/update"
echo "[3/9] Preparing pinned LukeZGD source..."
if [[ ! -d "$SOURCE_DIR/.git" ]]; then
    rm -rf "$SOURCE_DIR"
    git clone https://github.com/LukeZGD/idevicerestore.git "$SOURCE_DIR"
fi
git -C "$SOURCE_DIR" fetch --prune origin "$SOURCE_COMMIT_PIN" || git -C "$SOURCE_DIR" fetch --prune origin master
git -C "$SOURCE_DIR" reset --hard "$SOURCE_COMMIT_PIN"
# Preserve the expensive dependency/source cache and ignored Autotools
# outputs. `-x` previously deleted configure, aclocal.m4, missing, and the
# generated Makefile.in files required by an existing out-of-tree build.
git -C "$SOURCE_DIR" clean -fd -e tmp/ -e bin/
SOURCE_COMMIT="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
[[ "$SOURCE_COMMIT" == "$SOURCE_COMMIT_PIN" ]] || { echo "ERROR: source pin mismatch: $SOURCE_COMMIT"; exit 34; }

stage="source patches"
echo "[4/9] Applying iBEC retry and GCC 15 compatibility patches..."
python3 "$PATCHER" \
    "$SOURCE_DIR/src/dfu.c" \
    "$SOURCE_DIR/src/recovery.c" \
    "$SOURCE_DIR/src/asr.c" \
    "$SOURCE_DIR/src/restore.c"
python3 "$COMPAT_PATCHER" "$SOURCE_DIR"
grep -Fq "$PATCH_MARKER" "$SOURCE_DIR/src/dfu.c"
grep -Fq "$RECOVERY_PATCH_MARKER" "$SOURCE_DIR/src/recovery.c"
grep -Fq "$ASR_PATCH_MARKER" "$SOURCE_DIR/src/asr.c"
grep -Fq "$RESTORE_ASR_PATCH_MARKER" "$SOURCE_DIR/src/restore.c"
grep -Fq "IPHONE5DUALBOOTER_GCC15_COMPAT_V1" "$SOURCE_DIR/configure.ac"
if grep -Eq 'thread_t[[:space:]]+[A-Za-z_][A-Za-z0-9_]*[[:space:]]*=[[:space:]]*NULL' "$SOURCE_DIR/src/restore.c"; then
    echo "ERROR: incompatible thread_t = NULL initializer remains after patch."
    exit 35
fi

sanitize_build_dir() {
    local directory="$1"
    python3 "$COMPAT_PATCHER" --sanitize-makefiles "$directory"
    if grep -R --include=Makefile -nE -- '-D_FILE_OFFSET_BITS=([[:space:]]|$)' "$directory"; then
        echo "ERROR: empty _FILE_OFFSET_BITS definition remains in generated Makefiles."
        exit 36
    fi
}

configure_and_make() {
    local label="$1" directory="$2"
    echo "Configuring $label..."
    rm -rf "$directory"
    mkdir -p "$directory"

    if ! (cd "$SOURCE_DIR" && autoreconf -fi); then
        echo "ERROR: Autotools regeneration failed for $label."
        return 1
    fi

    if ! (
        cd "$directory"
        CFLAGS="$COMMON_CFLAGS" \
        CXXFLAGS="$COMMON_CXXFLAGS" \
        CPPFLAGS="$COMMON_CPPFLAGS" \
        LDFLAGS="$COMMON_LDFLAGS" \
        LIBS="$COMMON_LIBS" \
        "$SOURCE_DIR/configure" --prefix="$INSTALL_DIR" --disable-silent-rules
    ); then
        echo "ERROR: configure failed for $label."
        return 1
    fi

    sanitize_build_dir "$directory"
    echo "Building $label in parallel..."
    if ! make -C "$directory" -j"$(nproc)"; then
        echo "Parallel build failed. Retrying incrementally with one job for a precise diagnostic."
        python3 "$COMPAT_PATCHER" "$SOURCE_DIR"
        sanitize_build_dir "$directory"
        if ! make -C "$directory" -j1 V=1; then
            echo "ERROR: both parallel and single-job builds failed for $label."
            return 1
        fi
    fi
    return 0
}

incremental_make_if_compatible() {
    local label="$1" directory="$2"

    [[ -f "$directory/Makefile" ]] || return 1
    [[ -f "$directory/config.status" ]] || return 1
    # An overlaid hotfix keeps the same absolute source path. A cache copied
    # from a different folder must be reconfigured instead of building against
    # stale absolute paths embedded by Autoconf.
    grep -Fq -- "$SOURCE_DIR" "$directory/Makefile" || return 1

    echo "Reusing the existing configured idevicerestore build directory."
    echo "Regenerating deleted/stale Autotools helper files without touching cached objects..."
    if ! (cd "$SOURCE_DIR" && autoreconf -fi); then
        echo "ERROR: could not regenerate configure/missing/aclocal files."
        return 1
    fi

    echo "Refreshing the existing build configuration in place..."
    if ! (
        cd "$directory"
        CFLAGS="$COMMON_CFLAGS" \
        CXXFLAGS="$COMMON_CXXFLAGS" \
        CPPFLAGS="$COMMON_CPPFLAGS" \
        LDFLAGS="$COMMON_LDFLAGS" \
        LIBS="$COMMON_LIBS" \
        "$SOURCE_DIR/configure" --prefix="$INSTALL_DIR" --disable-silent-rules
    ); then
        echo "ERROR: in-place incremental configure failed."
        return 1
    fi

    echo "Only changed transition sources should be recompiled."
    sanitize_build_dir "$directory"
    if ! make -C "$directory" -j"$(nproc)"; then
        echo "Incremental parallel build failed; retrying with one job."
        sanitize_build_dir "$directory"
        if ! make -C "$directory" -j1 V=1; then
            echo "ERROR: incremental build failed in both parallel and single-job modes."
            return 1
        fi
    fi
    return 0
}

matching_deps_ready() {
    local -a modules=(libirecovery-1.0 libimobiledevice-1.0 libplist-2.0 libzip libcurl openssl)
    local module
    for module in "${modules[@]}"; do
        if ! pkg-config --exists "$module"; then
            echo "  [missing cached module] $module"
            return 1
        fi
    done
    return 0
}

# Skipping compile.sh's historical all-or-nothing apt transaction is handled below.
prepare_full_compile_script() {
    python3 - "$SOURCE_DIR/compile.sh" "$LIBIRECOVERY_PATCHER" "$COMPAT_PATCHER" "$LIBIMOBILEDEVICE_TRANSPORT_PATCHER" <<'PYCODE'
from pathlib import Path
import shlex
import sys

path = Path(sys.argv[1])
libirecovery_patcher = sys.argv[2]
compat_patcher = sys.argv[3]
transport_patcher = sys.argv[4]
text = path.read_text(encoding="utf-8")

text = text.replace(
    'export CC_ARGS="CC=/usr/bin/clang-14 CXX=/usr/bin/clang++-14 LD=/usr/bin/ld64.lld-14 RANLIB=/usr/bin/ranlib AR=/usr/bin/ar"',
    'export CC_ARGS="CC=/usr/bin/gcc CXX=/usr/bin/g++ LD=/usr/bin/ld RANLIB=/usr/bin/ranlib AR=/usr/bin/ar"',
)
text = text.replace(
    'export ALT_CC_ARGS="CC=/usr/bin/clang-14 CXX=/usr/bin/clang++-14 LD=/usr/bin/ld.lld-14 RANLIB=/usr/bin/ranlib AR=/usr/bin/ar"',
    'export ALT_CC_ARGS="CC=/usr/bin/gcc CXX=/usr/bin/g++ LD=/usr/bin/ld RANLIB=/usr/bin/ranlib AR=/usr/bin/ar"',
)
needle = 'export JNUM="-j$(nproc)"\n'
flags = (
    'export CFLAGS="${CFLAGS:-} -std=gnu17 -O2 -g -Wno-error=int-conversion -Wno-error=incompatible-pointer-types -Wno-error=implicit-function-declaration"\n'
    'export CXXFLAGS="${CXXFLAGS:-} -std=gnu++17 -O2 -g"\n'
    'export CPPFLAGS="${CPPFLAGS:-} -D_FILE_OFFSET_BITS=64"\n'
)
if flags not in text:
    text = text.replace(needle, needle + flags, 1)

llvm = '''if [[ $(uname -m) != "a"* ]]; then
    curl -LO https://apt.llvm.org/llvm.sh
    chmod 0755 llvm.sh
    sudo ./llvm.sh 14
fi'''
text = text.replace(llvm, 'if false; then\n    echo "LLVM install disabled; using GCC."\nfi')
text = text.replace(
    '-L/usr/lib/x86_64-linux-gnu -lzstd -llzma -lbz2',
    '-L/usr/local/lib -L/usr/lib/x86_64-linux-gnu -Wl,-rpath,/usr/local/lib -lzstd -llzma -lbz2',
)

libimobile_stage = text.find('echo \"Building libimobiledevice...\"')
if libimobile_stage < 0:
    raise SystemExit("Could not locate compile.sh libimobiledevice stage")
libimobile_autogen = text.find('./autogen.sh', libimobile_stage)
if libimobile_autogen < 0:
    raise SystemExit("Could not locate compile.sh libimobiledevice autogen stage")
transport_command = (
    'python3 ' + shlex.quote(transport_patcher) +
    ' src/property_list_service.c src/service.c\n'
)
if transport_command not in text:
    text = text[:libimobile_autogen] + transport_command + text[libimobile_autogen:]

stage = text.find('echo \"Building libirecovery...\"')
autogen = text.find('./autogen.sh $CONF_ARGS $CC_ARGS', stage)
if stage < 0 or autogen < 0:
    raise SystemExit("Could not locate compile.sh libirecovery stage")
command = 'python3 ' + shlex.quote(libirecovery_patcher) + ' src/libirecovery.c\n'
if command not in text:
    text = text[:autogen] + command + text[autogen:]

final = text.find('echo "Building idevicerestore!"')
final_autogen = text.find('./autogen.sh $ALT_CONF_ARGS', final)
if final < 0 or final_autogen < 0:
    raise SystemExit("Could not locate final idevicerestore stage")
compat = 'python3 ' + shlex.quote(compat_patcher) + ' .\n'
if compat not in text[final:final_autogen+1]:
    text = text[:final_autogen] + compat + text[final_autogen:]

start = text.find('echo "Downloading apt deps"')
end = text.find('echo "Done"', start)
if start >= 0 and end >= 0:
    end += len('echo "Done"')
    text = text[:start] + 'echo "Skipping compile.sh\'s historical all-or-nothing apt transaction; using dependencies prepared by iPhone5DualBooter"' + text[end:]


# Put static compression libraries in LIBS (after objects) rather than LDFLAGS.
text = text.replace(
    './autogen.sh $ALT_CONF_ARGS $CC_ARGS LDFLAGS="$LD_ARGS" LIBS="-ldl"',
    './autogen.sh $ALT_CONF_ARGS $CC_ARGS LDFLAGS="-Wl,--allow-multiple-definition -L/usr/local/lib -L/usr/lib/x86_64-linux-gnu -Wl,-rpath,/usr/local/lib" LIBS="-lzstd -llzma -lbz2 -ldl"',
)

path.write_text(text, encoding="utf-8")
PYCODE
}

if [[ "${IPHONE5DUALBOOTER_FORCE_PATCHED_LIBIMOBILEDEVICE:-1}" != "1" && "$FAST_DEPS_READY" == "1" ]]; then
    stage="fast idevicerestore compile"
    if configure_and_make "fast distro-library build" "$FAST_BUILD_DIR"; then
        BUILT="$FAST_BUILD_DIR/src/idevicerestore"
    fi
fi

if [[ "${IPHONE5DUALBOOTER_FORCE_PATCHED_LIBIMOBILEDEVICE:-1}" != "1" && -z "$BUILT" ]] && matching_deps_ready; then
    stage="cached matching-dependency target compile"
    echo "[5/9] Reusing matching libraries already installed by the previous long build."
    if ! incremental_make_if_compatible "cached matching-dependency build" "$CACHED_BUILD_DIR"; then
        echo "Incremental reuse was unavailable or failed; rebuilding only idevicerestore against cached dependencies."
        if ! configure_and_make "cached matching-dependency build" "$CACHED_BUILD_DIR"; then
            echo "ERROR: idevicerestore target build failed; refusing to validate an older binary."
            exit 37
        fi
    fi
    BUILT="$CACHED_BUILD_DIR/src/idevicerestore"
fi

if [[ -z "$BUILT" ]]; then
    if [[ "$FULL_FALLBACK" != "1" ]]; then
        echo "ERROR: no usable dependency set and full fallback is disabled."
        exit 32
    fi
    echo "[5b/9] Running the full matching-dependency build once."
    stage="full matching-dependency compile"
    prepare_full_compile_script
    (cd "$SOURCE_DIR" && chmod +x compile.sh && bash ./compile.sh)
    BUILT="$SOURCE_DIR/bin/idevicerestore"
fi
fi  # end compile path; cached completed binaries skip everything above

stage="binary validation"
echo "[6/9] Validating or reusing patched binary..."
[[ -x "$BUILT" ]] || { echo "ERROR: missing executable: $BUILT"; exit 20; }
if ! binary_has_ibec_retry_patch "$BUILT"; then
    echo "ERROR: one or more iBEC/live-handoff/ASR runtime signatures are missing."
    echo "Nearby iPhone5DualBooter strings in the candidate:"
    validation_strings="$CACHE_ROOT/validation-strings.txt"
    strings "$BUILT" > "$validation_strings" 2>/dev/null || true
    grep -F "[iPhone5DualBooter]" "$validation_strings" || true
    echo "Full extracted-string diagnostic: $validation_strings"
    exit 21
fi
echo "Patched iBEC/live-handoff and ASR validation-recovery signatures verified."

echo "[7/9] Checking runtime loader dependencies..."
if command -v ldd >/dev/null 2>&1 && ldd "$BUILT" | grep -q 'not found'; then
    ldd "$BUILT"
    echo "ERROR: built binary has unresolved shared libraries."
    exit 22
fi
(timeout 5 "$BUILT" --help >/tmp/iphone5dualbooter-idevicerestore-help.txt 2>&1 || true)
if grep -Eqi 'error while loading shared libraries|symbol lookup error' /tmp/iphone5dualbooter-idevicerestore-help.txt; then
    cat /tmp/iphone5dualbooter-idevicerestore-help.txt
    exit 23
fi

stage="binary installation"
echo "[8/9] Installing binary..."
[[ -f "$ORIGINAL" ]] || cp -p "$TARGET" "$ORIGINAL"
TMP_TARGET="${TARGET}.patched.tmp"
cp "$BUILT" "$TMP_TARGET"
chmod 0755 "$TMP_TARGET"
mv -f "$TMP_TARGET" "$TARGET"

PORTABLE_CACHE_DIR="$CACHE_ROOT/completed-binary"
mkdir -p "$PORTABLE_CACHE_DIR"
cp "$BUILT" "$PORTABLE_CACHE_DIR/idevicerestore"
chmod 0755 "$PORTABLE_CACHE_DIR/idevicerestore"

cat > "${TARGET}.iphone5dualbooter-patch.txt" <<EOF2
Source patch marker: $PATCH_MARKER
Binary validation: sixteen iBEC/live-handoff/ASR same-session runtime strings
Source repository: https://github.com/LukeZGD/idevicerestore
Pinned source commit: $SOURCE_COMMIT_PIN
Runtime retry variable: IPHONE5DUALBOOTER_IBEC_RETRY_SECONDS
Configured by GUI: $RETRY_SECONDS seconds
GCC compatibility: IPHONE5DUALBOOTER_GCC15_COMPAT_V1
WSL-native build workspace: $WSL_BUILD_ROOT
Windows cache pointer: $CACHE_ROOT/wsl-build-workspace.txt
EOF2

echo "[9/9] Build complete."
echo "The previous long dependency build was reused; only the idevicerestore target was refreshed and relinked."
trap - ERR
