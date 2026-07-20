from pathlib import Path
import importlib.util
import tempfile
from unittest.mock import patch
import unittest

from iphone5dualbooter.idevicerestore_rebuild import (
    PATCH_MARKER,
    _default_windows_mount_path,
    _windows_to_wsl_path,
    is_v049_patched_binary,
)


class SourcePatchTests(unittest.TestCase):
    def _load_patcher(self):
        asset = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "patch_lukezgd_idevicerestore.py"
        )
        spec = importlib.util.spec_from_file_location(
            "patch_lukezgd_idevicerestore",
            asset,
        )
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_source_patcher_adds_real_close_reopen_retry(self):
        patcher = self._load_patcher()
        sample = r'''
#include <stdlib.h>
#include <string.h>

int dfu_progress_callback(irecv_client_t client, const irecv_event_t* event)
{
    return 0;
}

int dfu_send_component(struct idevicerestore_client_t* client, plist_t build_identity, const char* component)
{
    unsigned char* data = NULL;
    uint32_t size = 0;
    info("Sending %s (%d bytes)...\n", component, size);
	irecv_error_t err = irecv_send_buffer(client->dfu->client, data, size, 1);
	if (err != IRECV_E_SUCCESS) {
		error("ERROR: Unable to send %s component: %s\\n", component, irecv_strerror(err));
		free(data);
		return -1;
	}
    free(data);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "dfu.c"
            path.write_text(sample, encoding="utf-8")
            patcher.patch_file(path)
            patched = path.read_text(encoding="utf-8")

        self.assertIn(
            "IPHONE5DUALBOOTER_DFU_RECOVERY_RECONNECT_V12",
            patched,
        )
        self.assertIn("irecv_close(client->dfu->client)", patched)
        self.assertIn("irecv_open_with_ecid(&reopened", patched)
        self.assertIn("client->dfu->client = reopened", patched)
        self.assertIn("irecv_usb_set_configuration(reopened, 1)", patched)
        self.assertIn("iBEC retry", patched)
        self.assertIn('strcmp(component, "iBEC") == 0', patched)

    def test_patcher_is_idempotent(self):
        patcher = self._load_patcher()
        sample = r'''
int dfu_progress_callback(irecv_client_t client, const irecv_event_t* event)
{
    return 0;
}
int dfu_send_component(struct idevicerestore_client_t* client, plist_t build_identity, const char* component)
{
	irecv_error_t err = irecv_send_buffer(client->dfu->client, data, size, 1);
	if (err != IRECV_E_SUCCESS) {
		error("ERROR: Unable to send %s component: %s\\n", component, irecv_strerror(err));
		free(data);
		return -1;
	}
}
'''
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "dfu.c"
            path.write_text(sample, encoding="utf-8")
            patcher.patch_file(path)
            once = path.read_text(encoding="utf-8")
            patcher.patch_file(path)
            twice = path.read_text(encoding="utf-8")
        self.assertEqual(once, twice)

    def test_current_lukezgd_source_layout_with_real_tabs_and_fixme(self):
        patcher = self._load_patcher()
        sample = """
int dfu_progress_callback(irecv_client_t client, const irecv_event_t* event)
{
\treturn 0;
}

int dfu_send_component(struct idevicerestore_client_t* client, plist_t build_identity, const char* component)
{
\tunsigned char* data = NULL;
\tuint32_t size = 0;
\tinfo("Sending %s (%d bytes)...\\n", component, size);
\t// FIXME: Did I do this right????
\tirecv_error_t err = irecv_send_buffer(client->dfu->client, data, size, 1);
\tif (err != IRECV_E_SUCCESS) {
\t\terror("ERROR: Unable to send %s component: %s\\n", component, irecv_strerror(err));
\t\tfree(data);
\t\treturn -1;
\t}
\tfree(data);
\treturn 0;
}

int dfu_enter_recovery(struct idevicerestore_client_t* client, plist_t build_identity)
{
\tif (dfu_send_component(client, build_identity, "iBEC") < 0) {
\t\terror("ERROR: Unable to send iBEC to device\\n");
\t\tirecv_close(client->dfu->client);
\t\tclient->dfu->client = NULL;
\t\treturn -1;
\t}
\treturn 0;
}
"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "dfu.c"
            path.write_text(sample, encoding="utf-8")
            patcher.patch_file(path)
            patched = path.read_text(encoding="utf-8")

        self.assertIn(patcher.PATCH_MARKER, patched)
        self.assertNotIn("// FIXME: Did I do this right????", patched)
        self.assertIn('strcmp(component, "iBEC") == 0', patched)
        self.assertIn("if (client->dfu && client->dfu->client)", patched)

    def test_binary_runtime_signature_detection(self):
        from iphone5dualbooter.idevicerestore_rebuild import (
            PATCH_RUNTIME_SIGNATURES,
        )

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "idevicerestore"
            path.write_bytes(
                b"ELF..." + b"...".join(PATCH_RUNTIME_SIGNATURES)
            )
            self.assertTrue(is_v049_patched_binary(path))


class BuildScriptTests(unittest.TestCase):
    def test_build_script_clones_lukezgd_fork(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "build_patched_idevicerestore.sh"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "https://github.com/LukeZGD/idevicerestore.git",
            script,
        )
        self.assertIn("libirecovery-dev", script)
        self.assertIn("strings", script)
        self.assertIn("idevicerestore.original", script)


class WSLPathConversionTests(unittest.TestCase):
    def test_default_mount_fallback_preserves_components(self):
        self.assertEqual(
            _default_windows_mount_path(
                r"C:\Users\ExampleUser\Downloads\Project"
            ),
            "/mnt/c/Users/ExampleUser/Downloads/Project",
        )

    def test_unc_path_is_not_guessed(self):
        self.assertIsNone(
            _default_windows_mount_path(r"\\server\share\Project")
        )

    def test_wslpath_receives_windows_path_over_stdin(self):
        class Completed:
            returncode = 0
            stdout = "/mnt/c/Users/Ace/Project\n"

        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return Completed()

        with patch(
            "iphone5dualbooter.idevicerestore_rebuild.subprocess.run",
            side_effect=fake_run,
        ):
            with patch.object(
                Path,
                "resolve",
                return_value=Path(r"C:\Users\Ace\Project"),
            ):
                converted = _windows_to_wsl_path(
                    Path("wsl.exe"),
                    "Ubuntu",
                    Path("ignored"),
                )

        self.assertEqual(converted, "/mnt/c/Users/Ace/Project")
        command, kwargs = calls[0]
        self.assertIn("--exec", command)
        self.assertIn("read -r", command[-1])
        self.assertEqual(kwargs["input"], "C:\\Users\\Ace\\Project\n")
        self.assertNotIn("C:\\Users\\Ace\\Project", command)

    def test_falls_back_when_wslpath_mangles_backslashes(self):
        class Completed:
            returncode = 1
            stdout = "wslpath: C:UsersAceProject\n"

        with patch(
            "iphone5dualbooter.idevicerestore_rebuild.subprocess.run",
            return_value=Completed(),
        ):
            with patch.object(
                Path,
                "resolve",
                return_value=Path(r"D:\Builds\Phone Tool"),
            ):
                converted = _windows_to_wsl_path(
                    Path("wsl.exe"),
                    "Ubuntu",
                    Path("ignored"),
                )

        self.assertEqual(converted, "/mnt/d/Builds/Phone Tool")


class ResilientBuildBootstrapTests(unittest.TestCase):
    def test_build_script_uses_resilient_apt_groups(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "build_patched_idevicerestore.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("apt_install_group", script)
        self.assertIn("apt_has_candidate", script)
        self.assertIn("FAST_DEPS_READY", script)
        self.assertIn("Combined install failed", script)
        self.assertIn("BUILD FAILED during stage", script)


    def test_required_packages_include_lzma_linker_development_files(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "build_patched_idevicerestore.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("liblzma-dev", script)
        self.assertIn("compiler/linker preflight", script)
        self.assertIn("COMMON_LIBS=\"-lzstd -llzma -lbz2 -ldl\"", script)
        self.assertIn("/usr/local/lib", script)
        self.assertIn("Compiler/linker preflight passed", script)

    def test_fallback_removes_duplicate_apt_transaction(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "build_patched_idevicerestore.sh"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "Skipping compile.sh's historical all-or-nothing apt transaction",
            script,
        )


class LibirecoveryCompatibilityPatchTests(unittest.TestCase):
    def _load_patcher(self):
        asset = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "patch_lukegd_libirecovery.py"
        )
        spec = importlib.util.spec_from_file_location(
            "patch_lukegd_libirecovery",
            asset,
        )
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_patches_reported_ubuntu_2604_compile_error(self):
        patcher = self._load_patcher()
        source = (
            'char cmdstr[0x100] = {0};\n'
            'snprintf(&cmdstr,sizeof(cmdstr), '
            '"setenv filesize %d",length);\n'
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "libirecovery.c"
            path.write_text(source, encoding="utf-8")
            patcher.patch_file(path)
            fixed = path.read_text(encoding="utf-8")

        self.assertNotIn("&cmdstr", fixed)
        self.assertIn("snprintf(cmdstr, sizeof(cmdstr)", fixed)
        self.assertIn("(int)length", fixed)

    def test_dependency_patcher_is_idempotent(self):
        patcher = self._load_patcher()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "libirecovery.c"
            path.write_text(patcher.FIXED_LINE + "\n", encoding="utf-8")
            patcher.patch_file(path)
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                patcher.FIXED_LINE + "\n",
            )

    def test_build_script_invokes_dependency_patcher_and_gnu17(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "build_patched_idevicerestore.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("LIBIRECOVERY_PATCHER", script)
        self.assertIn("src/libirecovery.c", script)
        self.assertIn("-std=gnu17", script)


class GCC15CompatibilityPatchTests(unittest.TestCase):
    def _load_compat(self):
        asset = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter" / "assets"
            / "patch_lukezgd_build_compat.py"
        )
        spec = importlib.util.spec_from_file_location("build_compat", asset)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_fixes_large_file_macro_and_thread_handle(self):
        module = self._load_compat()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "configure.ac").write_text(
                "if test \"$ac_cv_sys_file_offset_bits\" != 'no'; then\n"
                " LFS_CFLAGS=\"$LFS_CFLAGS -D_FILE_OFFSET_BITS=$ac_cv_sys_file_offset_bits\"\n"
                "fi\n", encoding="utf-8"
            )
            (root / "src" / "restore.c").write_text(
                '#include "restore.h"\nthread_t fdr_thread = NULL;\n',
                encoding="utf-8",
            )
            module.patch_tree(root)
            configure = (root / "configure.ac").read_text(encoding="utf-8")
            restore = (root / "src" / "restore.c").read_text(encoding="utf-8")
        self.assertIn('test -n "$ac_cv_sys_file_offset_bits"', configure)
        self.assertIn('thread_t fdr_thread = (thread_t)0;', restore)

    def test_sanitizes_generated_makefile(self):
        module = self._load_compat()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            makefile = root / "Makefile"
            makefile.write_text(
                "CFLAGS = -O2 -D_FILE_OFFSET_BITS= -Wall\n",
                encoding="utf-8",
            )
            self.assertEqual(module.sanitize_generated_makefiles(root), 1)
            self.assertIn(
                "-D_FILE_OFFSET_BITS=64",
                makefile.read_text(encoding="utf-8"),
            )


class BuildTimeProtectionTests(unittest.TestCase):
    def test_builder_pins_source_and_reuses_cached_dependencies(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter" / "assets"
            / "build_patched_idevicerestore.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("22682048240929637124781353ab3b6ee30b8dad", script)
        self.assertIn("matching_deps_ready", script)
        self.assertIn("cached matching-dependency build", script)
        self.assertIn('clean -fd -e tmp/ -e bin/', script)
        self.assertNotIn('clean -fdx -e tmp/ -e bin/', script)

    def test_builder_has_pre_and_post_compile_checks(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter" / "assets"
            / "build_patched_idevicerestore.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("sanitize_build_dir", script)
        self.assertIn("-Wno-error=int-conversion", script)
        self.assertIn('ldd "$BUILT"', script)
        self.assertIn('make -C "$directory" -j1 V=1', script)


class CompiledSignatureValidationTests(unittest.TestCase):
    def test_detector_accepts_compiled_runtime_signatures_without_source_comment(self):
        from iphone5dualbooter.idevicerestore_rebuild import (
            PATCH_RUNTIME_SIGNATURES,
            is_v049_patched_binary,
        )

        with tempfile.TemporaryDirectory() as temp:
            candidate = Path(temp) / "idevicerestore"
            candidate.write_bytes(
                b"\x7fELF..."
                + b"...".join(PATCH_RUNTIME_SIGNATURES)
            )
            self.assertTrue(is_v049_patched_binary(candidate))

    def test_detector_rejects_source_comment_marker_only(self):
        from iphone5dualbooter.idevicerestore_rebuild import (
            PATCH_MARKER,
            is_v049_patched_binary,
        )

        with tempfile.TemporaryDirectory() as temp:
            candidate = Path(temp) / "idevicerestore"
            candidate.write_bytes(b"\x7fELF..." + PATCH_MARKER)
            self.assertFalse(is_v049_patched_binary(candidate))

    def test_build_script_recovers_cache_before_source_clone(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "build_patched_idevicerestore.sh"
        ).read_text(encoding="utf-8")

        recovery = script.index(
            'Looking for a completed patched binary from an earlier attempt'
        )
        source_clone = script.index('Preparing pinned LukeZGD source')
        self.assertLess(recovery, source_clone)
        self.assertIn("binary_has_ibec_retry_patch", script)
        self.assertNotIn(
            'strings "$BUILT" | grep -Fq "$PATCH_MARKER"',
            script,
        )


class PipefailSafeValidationTests(unittest.TestCase):
    def test_build_script_avoids_strings_grep_q_pipeline(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "build_patched_idevicerestore.sh"
        ).read_text(encoding="utf-8")

        self.assertNotIn("strings \"$candidate\" | grep -Fq", script)
        self.assertIn(
            'grep -aFq -- "$PATCH_RUNTIME_STRING_1" "$candidate"',
            script,
        )

    def test_cache_recovery_occurs_before_apt_refresh(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "build_patched_idevicerestore.sh"
        ).read_text(encoding="utf-8")

        recovery = script.index(
            "Looking for a completed patched binary from an earlier attempt"
        )
        apt_refresh = script.index("Refreshing Ubuntu package information")
        self.assertLess(recovery, apt_refresh)

class RecoveryModeTransitionPatchTests(unittest.TestCase):
    def _load_patcher(self):
        asset = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "patch_lukezgd_idevicerestore.py"
        )
        spec = importlib.util.spec_from_file_location(
            "patch_lukezgd_idevicerestore_recovery",
            asset,
        )
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_recovery_client_rejects_old_dfu_mode_and_keeps_waiting(self):
        patcher = self._load_patcher()
        sample = r"""
#include "common.h"
#include "recovery.h"

int recovery_progress_callback(irecv_client_t client, const irecv_event_t* event)
{
    return 0;
}

int recovery_client_new(struct idevicerestore_client_t* client)
{
    int i = 0;
    int attempts = 20;
    irecv_client_t recovery = NULL;
    irecv_error_t recovery_error = IRECV_E_UNKNOWN_ERROR;
    if(client->recovery == NULL) {
        client->recovery = (struct recovery_client_t*)malloc(sizeof(struct recovery_client_t));
        memset(client->recovery, 0, sizeof(struct recovery_client_t));
    }
    for (i = 1; i <= attempts; i++) {
        recovery_error = irecv_open_with_ecid(&recovery, client->ecid);
        if (recovery_error == IRECV_E_SUCCESS) {
            break;
        }
        sleep(4);
    }
    if (client->srnm == NULL) {
        const struct irecv_device_info *device_info = irecv_get_device_info(recovery);
        if (device_info && device_info->srnm) {
            client->srnm = strdup(device_info->srnm);
        }
    }
    irecv_event_subscribe(recovery, IRECV_PROGRESS, &recovery_progress_callback, NULL);
    client->recovery->client = recovery;
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "recovery.c"
            path.write_text(sample, encoding="utf-8")
            patcher.patch_recovery_file(path)
            patched = path.read_text(encoding="utf-8")

        self.assertIn(patcher.RECOVERY_PATCH_MARKER, patched)
        self.assertIn("probe_mode != IRECV_K_DFU_MODE", patched)
        self.assertIn("probe_mode != IRECV_K_WTF_MODE", patched)
        self.assertIn("irecv_close(recovery)", patched)
        self.assertIn("Connected directly to post-iBEC ", patched)
        self.assertIn("recovery mode 0x%x", patched)
        self.assertIn("LIVE_HOST_USB_HANDOFF_REQUIRED", patched)
        self.assertIn("iphone5dualbooter_write_live_handoff_request", patched)
        self.assertIn("iphone5dualbooter_wait_for_live_handoff_ack", patched)
        self.assertNotIn("irecv_reset(recovery)", patched)
        self.assertIn("usleep(100000)", patched)
        self.assertIn("for (;;)", patched)



    def test_recovery_patcher_adds_complete_dfu_type_header(self):
        patcher = self._load_patcher()
        sample = r"""
#include "common.h"
#include "recovery.h"

int recovery_client_new(struct idevicerestore_client_t* client)
{
    int attempts = 20;
    irecv_client_t recovery = NULL;
    irecv_open_with_ecid(&recovery, client->ecid);
    irecv_event_subscribe(recovery, IRECV_PROGRESS, &recovery_progress_callback, NULL);
    client->recovery->client = recovery;
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "recovery.c"
            path.write_text(sample, encoding="utf-8")
            patcher.patch_recovery_file(path)
            once = path.read_text(encoding="utf-8")
            patcher.patch_recovery_file(path)
            twice = path.read_text(encoding="utf-8")

        self.assertEqual(once, twice)
        self.assertEqual(once.count('#include "dfu.h"'), 1)
        self.assertLess(
            once.index('#include "dfu.h"'),
            once.index('#include "recovery.h"'),
        )

    def test_recovery_patcher_is_idempotent(self):
        patcher = self._load_patcher()
        sample = r"""
#include "common.h"
#include "recovery.h"

int recovery_client_new(struct idevicerestore_client_t* client)
{
    int attempts = 20;
    irecv_client_t recovery = NULL;
    irecv_open_with_ecid(&recovery, client->ecid);
    irecv_event_subscribe(recovery, IRECV_PROGRESS, &recovery_progress_callback, NULL);
    client->recovery->client = recovery;
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "recovery.c"
            path.write_text(sample, encoding="utf-8")
            patcher.patch_recovery_file(path)
            once = path.read_text(encoding="utf-8")
            patcher.patch_recovery_file(path)
            twice = path.read_text(encoding="utf-8")
        self.assertEqual(once, twice)


class RecoveryBuildReuseTests(unittest.TestCase):
    def test_old_ibec_only_binary_is_rejected(self):
        from iphone5dualbooter.idevicerestore_rebuild import (
            PATCH_RUNTIME_SIGNATURES,
            is_v049_patched_binary,
        )
        with tempfile.TemporaryDirectory() as temp:
            candidate = Path(temp) / "idevicerestore"
            candidate.write_bytes(
                b"ELF" + b"...".join(PATCH_RUNTIME_SIGNATURES[:3])
            )
            self.assertFalse(is_v049_patched_binary(candidate))

    def test_build_script_patches_recovery_and_supports_incremental_make(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "build_patched_idevicerestore.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('"$SOURCE_DIR/src/recovery.c"', script)
        self.assertIn("RECOVERY_PATCH_MARKER", script)
        self.assertIn("incremental_make_if_compatible", script)
        self.assertIn("Only changed transition sources should be recompiled", script)
        self.assertIn("PATCH_RUNTIME_STRING_5", script)


class IncrementalAutotoolsRecoveryTests(unittest.TestCase):
    def test_incremental_path_regenerates_and_reconfigures_before_make(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "build_patched_idevicerestore.sh"
        ).read_text(encoding="utf-8")

        function_start = script.index("incremental_make_if_compatible()")
        function_end = script.index("\nmatching_deps_ready()", function_start)
        function = script[function_start:function_end]

        self.assertIn("autoreconf -fi", function)
        self.assertIn('"$SOURCE_DIR/configure"', function)
        self.assertLess(
            function.index("autoreconf -fi"),
            function.index('make -C "$directory"'),
        )
        self.assertLess(
            function.index('"$SOURCE_DIR/configure"'),
            function.index('make -C "$directory"'),
        )

    def test_failed_single_job_make_is_propagated(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "build_patched_idevicerestore.sh"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'if ! make -C "$directory" -j1 V=1; then',
            script,
        )
        self.assertIn(
            "refusing to validate an older binary",
            script,
        )
        self.assertIn("exit 37", script)

    def test_cleanup_preserves_ignored_autotools_outputs(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "build_patched_idevicerestore.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("git -C \"$SOURCE_DIR\" clean -fd -e tmp/ -e bin/", script)
        self.assertNotIn("git -C \"$SOURCE_DIR\" clean -fdx", script)


class PreBuildLoggingBootstrapTests(unittest.TestCase):
    def test_launcher_uses_wsl_exec_and_fixed_bootstrap(self):
        from iphone5dualbooter.idevicerestore_rebuild import (
            WSL_BUILD_BOOTSTRAP,
        )

        self.assertIn('log_file="$1"', WSL_BUILD_BOOTSTRAP)
        self.assertIn(': > "$log_file"', WSL_BUILD_BOOTSTRAP)
        self.assertIn("expected 8 build arguments", WSL_BUILD_BOOTSTRAP)
        self.assertIn('exec /bin/bash "$main_script" "$@"', WSL_BUILD_BOOTSTRAP)

    def test_bootstrap_handles_parentheses_and_spaces_without_shell_parsing(self):
        from iphone5dualbooter.idevicerestore_rebuild import (
            WSL_BUILD_BOOTSTRAP,
        )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Project (1)"
            root.mkdir()
            main = root / "main build.sh"
            output = root / "received.txt"
            log_file = root / "build.log"

            main.write_text(
                '#!/usr/bin/env bash\n'
                'printf "%s\\n" "$#" > "$1"\n'
                'shift\n'
                'printf "<%s>\\n" "$@" >> "$IPHONE5DUALBOOTER_TEST_OUTPUT"\n',
                encoding="utf-8",
            )
            main.chmod(0o755)

            arguments = [
                str(output),
                "cache path (2)",
                "patcher path",
                "45",
                "1",
                str(log_file),
                "libirecovery patcher",
                "compat patcher",
            ]
            env = dict(__import__("os").environ)
            env["IPHONE5DUALBOOTER_TEST_OUTPUT"] = str(output)

            completed = __import__("subprocess").run(
                [
                    "/bin/bash",
                    "-c",
                    WSL_BUILD_BOOTSTRAP,
                    "bootstrap-test",
                    str(log_file),
                    str(main),
                    *arguments,
                ],
                stdout=__import__("subprocess").PIPE,
                stderr=__import__("subprocess").STDOUT,
                text=True,
                env=env,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stdout)
            self.assertTrue(log_file.is_file())
            self.assertIn("Build argument count: 8", log_file.read_text())
            received = output.read_text()
            self.assertTrue(received.startswith("8\n"))
            self.assertIn("<cache path (2)>", received)

    def test_bootstrap_logs_main_script_syntax_errors(self):
        from iphone5dualbooter.idevicerestore_rebuild import (
            WSL_BUILD_BOOTSTRAP,
        )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Broken (1)"
            root.mkdir()
            main = root / "broken script.sh"
            log_file = root / "build.log"
            main.write_text("if then\\n", encoding="utf-8")

            completed = __import__("subprocess").run(
                [
                    "/bin/bash",
                    "-c",
                    WSL_BUILD_BOOTSTRAP,
                    "bootstrap-test",
                    str(log_file),
                    str(main),
                    "1", "2", "3", "4", "5", "6", "7", "8",
                ],
                stdout=__import__("subprocess").PIPE,
                stderr=__import__("subprocess").STDOUT,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 2)
            logged = log_file.read_text(encoding="utf-8")
            self.assertIn("syntax error", logged.lower())

    def test_main_script_does_not_erase_bootstrap_log(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "build_patched_idevicerestore.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("IPHONE5DUALBOOTER_LOG_INITIALIZED", script)






class LiveInProcessUSBHandoffTests(unittest.TestCase):
    def _load_patcher(self):
        asset = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "patch_lukezgd_idevicerestore.py"
        )
        spec = importlib.util.spec_from_file_location(
            "live_handoff_patcher",
            asset,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_ibec_remains_on_original_send_mode(self):
        helper = self._load_patcher().HELPER
        self.assertIn(
            "irecv_send_buffer(client->dfu->client, data, size, 1)",
            helper,
        )
        self.assertIn(
            "irecv_send_buffer(reopened, data, size, 1)",
            helper,
        )
        self.assertNotIn("DFU finalize state", helper)

    def test_recovery_closes_handles_and_waits_for_ack(self):
        recovery = self._load_patcher().RECOVERY_CLIENT_NEW
        self.assertIn("LIVE_HOST_USB_HANDOFF_REQUIRED", recovery)
        self.assertIn("IPHONE5DUALBOOTER_HOST_HANDOFF_ACK_FILE", recovery)
        self.assertIn("access(ack_path, F_OK)", recovery)
        self.assertIn("Waiting inside the same idevicerestore ", recovery)
        self.assertIn("process for Windows USB handoff", recovery)
        self.assertIn("client->dfu->client = NULL", recovery)
        self.assertNotIn("Closing idevicerestore", recovery)
        self.assertNotIn("return -1;\n        }\n\n        usleep", recovery)

    def test_recovery_resumes_same_function_after_ack(self):
        recovery = self._load_patcher().RECOVERY_CLIENT_NEW
        ack = recovery.index("Windows USB handoff acknowledged")
        reopen = recovery.index("for (;;) {", ack)
        success = recovery.index(
            "Recovery mode 0x%x after the live Windows handoff",
            reopen,
        )
        self.assertLess(ack, reopen)
        self.assertLess(reopen, success)
        self.assertIn("Continuing the same restore", recovery)

    def test_runtime_signatures_require_live_handshake(self):
        from iphone5dualbooter.idevicerestore_rebuild import (
            PATCH_RUNTIME_SIGNATURES,
        )

        self.assertEqual(len(PATCH_RUNTIME_SIGNATURES), 13)
        joined = b"\n".join(PATCH_RUNTIME_SIGNATURES)
        self.assertIn(b"LIVE_HOST_USB_HANDOFF_REQUIRED", joined)
        self.assertIn(b"same idevicerestore process", joined)
        self.assertIn(b"Windows USB handoff acknowledged", joined)

    def test_shell_exports_request_and_ack_paths(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "wsl_legacy.py"
        ).read_text(encoding="utf-8")

        self.assertIn("IPHONE5DUALBOOTER_HOST_HANDOFF_FILE", source)
        self.assertIn("IPHONE5DUALBOOTER_HOST_HANDOFF_ACK_FILE", source)
        self.assertIn("iphone5dualbooter-host-handoff-ack.txt", source)

    def test_workflow_monitors_marker_while_process_is_running(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "wsl_legacy.py"
        ).read_text(encoding="utf-8")

        monitor = source.index("while process.poll() is None:")
        marker = source.index("if not handoff_marker.is_file():", monitor)
        detach = source.index("detach_attached_apple_devices(", marker)
        ack = source.index("temporary_ack.replace(handoff_ack)", detach)
        wait = source.index("exit_code = process.wait()", ack)

        self.assertLess(monitor, marker)
        self.assertLess(marker, detach)
        self.assertLess(detach, ack)
        self.assertLess(ack, wait)
        self.assertIn("Legacy was never restarted", source)
        self.assertNotIn("Automatically relaunching Legacy", source)
        self.assertNotIn("resume_from_recovery", source)

    def test_windows_recovery_pid_is_recognized(self):
        from iphone5dualbooter.usbipd import USBIPDDevice

        recovery = USBIPDDevice(
            busid="1-7",
            hardware_id="05ac:1281",
            device="Apple Mobile Device",
            state="Shared",
        )
        self.assertEqual(recovery.mode_name, "Recovery")


class ASRValidationRecoveryPatchTests(unittest.TestCase):
    def _load_patcher(self):
        asset = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "patch_lukezgd_idevicerestore.py"
        )
        spec = importlib.util.spec_from_file_location(
            "asr_validation_recovery_patcher",
            asset,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_asr_validation_patch_adds_timeout_and_repeated_initiate(self):
        patcher = self._load_patcher()
        sample = r"""
int asr_perform_validation(asr_client_t asr, const char* filesystem)
{
    FILE* file = fopen(filesystem, "rb");
    plist_t packet = NULL;
    char* command = NULL;
    while (1) {
        asr_receive(asr, &packet);
        plist_get_string_val(
            plist_dict_get_item(packet, "Command"),
            &command
        );
        if (!strcmp(command, "OOBData")) {
            asr_handle_oob_data_request(asr, packet, file);
        } else if (!strcmp(command, "Payload")) {
            break;
        }
    }
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "asr.c"
            path.write_text(sample, encoding="utf-8")
            patcher.patch_asr_file(path)
            once = path.read_text(encoding="utf-8")
            patcher.patch_asr_file(path)
            twice = path.read_text(encoding="utf-8")

        self.assertEqual(once, twice)
        self.assertIn(patcher.ASR_PATCH_MARKER, once)
        self.assertIn("idevice_connection_receive_timeout", once)
        self.assertIn('!strcmp(command, "Initiate")', once)
        self.assertIn("ASR validation receive timed out", once)
        self.assertIn("return -2", once)

    def test_restore_asr_patch_reopens_stalled_validation(self):
        patcher = self._load_patcher()
        sample = r"""
int restore_send_filesystem(
    struct idevicerestore_client_t* client,
    idevice_t device,
    const char* filesystem)
{
    asr_client_t asr = NULL;
    asr_open_with_timeout(device, &asr);
    asr_perform_validation(asr, filesystem);
    asr_send_payload(asr, filesystem);
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "restore.c"
            path.write_text(sample, encoding="utf-8")
            patcher.patch_restore_asr_file(path)
            once = path.read_text(encoding="utf-8")
            patcher.patch_restore_asr_file(path)
            twice = path.read_text(encoding="utf-8")

        self.assertEqual(once, twice)
        self.assertIn(patcher.RESTORE_ASR_PATCH_MARKER, once)
        self.assertIn("validation_result == -2", once)
        self.assertIn("Reopening ASR validation connection", once)
        self.assertIn("validation_attempt <= 20", once)

    def test_build_script_patches_asr_and_restore_sources(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "build_patched_idevicerestore.sh"
        ).read_text(encoding="utf-8")

        self.assertIn('"$SOURCE_DIR/src/asr.c"', script)
        self.assertIn('"$SOURCE_DIR/src/restore.c"', script)
        self.assertIn("ASR_PATCH_MARKER", script)
        self.assertIn("RESTORE_ASR_PATCH_MARKER", script)

    def test_runtime_signatures_require_asr_recovery(self):
        from iphone5dualbooter.idevicerestore_rebuild import (
            PATCH_RUNTIME_SIGNATURES,
        )

        joined = b"\n".join(PATCH_RUNTIME_SIGNATURES)
        self.assertEqual(len(PATCH_RUNTIME_SIGNATURES), 13)
        self.assertIn(b"ASR validation receive timed out", joined)
        self.assertIn(b"Reopening ASR validation connection", joined)
        self.assertIn(b"ASR validation reached the Payload request", joined)


class NoSpaceWSLBuildWorkspaceTests(unittest.TestCase):
    def _script(self) -> str:
        return (
            Path(__file__).resolve().parents[1]
            / "iphone5dualbooter"
            / "assets"
            / "build_patched_idevicerestore.sh"
        ).read_text(encoding="utf-8")

    def test_source_and_build_dirs_are_not_under_windows_cache(self):
        script = self._script()

        self.assertNotIn('SOURCE_DIR="$CACHE_ROOT/source"', script)
        self.assertNotIn('FAST_BUILD_DIR="$CACHE_ROOT/fast-build"', script)
        self.assertNotIn(
            'CACHED_BUILD_DIR="$CACHE_ROOT/cached-deps-build"',
            script,
        )
        self.assertIn(
            '$HOME/.cache/iphone5dualbooter/idevicerestore-',
            script,
        )
        self.assertIn('SOURCE_DIR="$WSL_BUILD_ROOT/source"', script)

    def test_build_rejects_space_or_drvfs_workspace(self):
        script = self._script()

        self.assertIn('if [[ "$SOURCE_DIR" == *" "* ]]', script)
        self.assertIn('if [[ "$SOURCE_DIR" == /mnt/* ]]', script)
        self.assertIn("unsafe srcdir value", script)

    def test_windows_cache_receives_workspace_pointer_and_binary_copy(self):
        script = self._script()

        self.assertIn(
            '"$CACHE_ROOT/wsl-build-workspace.txt"',
            script,
        )
        self.assertIn(
            'PORTABLE_CACHE_DIR="$CACHE_ROOT/completed-binary"',
            script,
        )
        self.assertIn(
            '"$CACHE_ROOT/completed-binary/idevicerestore"',
            script,
        )

    def test_build_workspace_is_stable_across_project_folders(self):
        script = self._script()

        assignment = (
            'WSL_BUILD_ROOT="${IPHONE5DUALBOOTER_WSL_BUILD_ROOT:-'
            '$HOME/.cache/iphone5dualbooter/idevicerestore-'
            '$SOURCE_COMMIT_PIN}"'
        )
        self.assertIn(assignment, script)
        self.assertNotIn("LEGACY_ROOT", assignment)
        self.assertNotIn("CACHE_ROOT", assignment)


if __name__ == "__main__":
    unittest.main()
