#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re
import sys


PATCH_MARKER = "IPHONE5DUALBOOTER_DFU_RECOVERY_RECONNECT_V12"
RECOVERY_PATCH_MARKER = "IPHONE5DUALBOOTER_RECOVERY_LIVE_HANDSHAKE_V7"
OLD_ASR_PATCH_MARKER = "IPHONE5DUALBOOTER_ASR_VALIDATION_RECOVERY_V1"
OLD_ASR_V2_PATCH_MARKER = "IPHONE5DUALBOOTER_ASR_VALIDATION_RECOVERY_V2"
OLD_ASR_V3_PATCH_MARKER = "IPHONE5DUALBOOTER_ASR_VALIDATION_RECOVERY_V3"
ASR_PATCH_MARKER = "IPHONE5DUALBOOTER_ASR_VALIDATION_RECOVERY_V4"
OLD_RESTORE_ASR_PATCH_MARKER = "IPHONE5DUALBOOTER_RESTORE_ASR_RETRY_V1"
RESTORE_ASR_PATCH_MARKER = "IPHONE5DUALBOOTER_RESTORE_ASR_SAME_SESSION_V2"

HELPER = r'''
/* IPHONE5DUALBOOTER_DFU_RECOVERY_RECONNECT_V12 */
static irecv_error_t iphone5dualbooter_send_ibec_with_reconnect(
    struct idevicerestore_client_t* client,
    unsigned char* data,
    uint32_t size)
{
    irecv_error_t err = IRECV_E_UNKNOWN_ERROR;
    unsigned long retry_number = 0;
    unsigned long open_failures = 0;

    /*
     * Roll back to the exact send option that previously transferred iBEC
     * successfully on this A6/WSL setup. Option 1 lets libirecovery perform
     * its normal finish/reset behavior. Do not add another ZLP/state machine.
     */
    err = irecv_send_buffer(client->dfu->client, data, size, 1);
    if (err == IRECV_E_SUCCESS) {
        info("\n[iPhone5DualBooter] iBEC upload completed using the original "
            "libirecovery finish/reset path.\n");
        return err;
    }

    info("\n[iPhone5DualBooter] Initial iBEC upload failed: %s\n",
        irecv_strerror(err));
    info("[iPhone5DualBooter] Retrying iBEC with freshly reopened DFU "
        "handles until one complete upload succeeds. Cancel Legacy to stop.\n");

    if (client->dfu && client->dfu->client) {
        irecv_close(client->dfu->client);
        client->dfu->client = NULL;
    }

    for (;;) {
        irecv_client_t reopened = NULL;
        int mode = -1;

        /* Active retry backoff only; there is no total timeout. */
        usleep(250000);

        err = irecv_open_with_ecid(&reopened, client->ecid);
        if (err != IRECV_E_SUCCESS || reopened == NULL) {
            open_failures++;
            if ((open_failures == 1) || ((open_failures % 20) == 0)) {
                info("[iPhone5DualBooter] DFU handle is not available yet; "
                    "continuing active reopen attempts (%lu).\n",
                    open_failures);
            }
            continue;
        }

        retry_number++;
        irecv_get_mode(reopened, &mode);
        info("[iPhone5DualBooter] Reopened ECID " FMT_qu
            " (mode 0x%x), iBEC retry #%lu...\n",
            client->ecid, mode, retry_number);

        irecv_event_subscribe(
            reopened,
            IRECV_PROGRESS,
            &dfu_progress_callback,
            NULL
        );
        client->dfu->client = reopened;

        if (irecv_usb_set_configuration(reopened, 1) < 0) {
            info("[iPhone5DualBooter] USB configuration 1 was not accepted; "
                "attempting the original iBEC send anyway.\n");
        }

        err = irecv_send_buffer(reopened, data, size, 1);
        if (err == IRECV_E_SUCCESS) {
            info("\n[iPhone5DualBooter] iBEC upload succeeded with the "
                "original send mode on retry #%lu.\n",
                retry_number);
            return IRECV_E_SUCCESS;
        }

        if ((retry_number <= 3) || ((retry_number % 5) == 0)) {
            info("\n[iPhone5DualBooter] iBEC retry #%lu failed: %s. "
                "Reopening DFU and trying again.\n",
                retry_number, irecv_strerror(err));
        }

        irecv_close(reopened);
        client->dfu->client = NULL;
    }
}

'''

RECOVERY_CLIENT_NEW = r'''
/* IPHONE5DUALBOOTER_RECOVERY_LIVE_HANDSHAKE_V7 */
static int iphone5dualbooter_write_live_handoff_request(
    int mode,
    unsigned long attempt)
{
    const char* request_path =
        getenv("IPHONE5DUALBOOTER_HOST_HANDOFF_FILE");
    FILE* marker = NULL;

    if (request_path == NULL || request_path[0] == '\0') {
        error("[iPhone5DualBooter] ERROR: live USB handoff request path "
            "is not configured.\n");
        return -1;
    }

    marker = fopen(request_path, "w");
    if (marker == NULL) {
        error("[iPhone5DualBooter] ERROR: could not create live USB "
            "handoff request marker: %s\n", request_path);
        return -1;
    }

    fprintf(
        marker,
        "LIVE_HOST_USB_HANDOFF_REQUIRED mode=0x%x attempt=%lu\n",
        mode,
        attempt
    );
    fclose(marker);

    info("[iPhone5DualBooter] LIVE_HOST_USB_HANDOFF_REQUIRED: iBEC is "
        "already uploaded, but WSL still exposes mode 0x%x. All Linux USB "
        "handles are closed; idevicerestore is paused while Windows performs "
        "the usbipd detach/recovery reattach.\n",
        mode);
    return 0;
}

static int iphone5dualbooter_wait_for_live_handoff_ack(void)
{
    const char* ack_path =
        getenv("IPHONE5DUALBOOTER_HOST_HANDOFF_ACK_FILE");
    unsigned long checks = 0;

    if (ack_path == NULL || ack_path[0] == '\0') {
        error("[iPhone5DualBooter] ERROR: live USB handoff ACK path "
            "is not configured.\n");
        return -1;
    }

    info("[iPhone5DualBooter] Waiting inside the same idevicerestore "
        "process for Windows USB handoff acknowledgement. Legacy remains "
        "open at the current restore step.\n");

    for (;;) {
        if (access(ack_path, F_OK) == 0) {
            unlink(ack_path);
            info("[iPhone5DualBooter] Windows USB handoff acknowledged; "
                "reopening the same ECID in Recovery mode without restarting "
                "Legacy.\n");
            return 0;
        }

        checks++;
        if ((checks % 100) == 0) {
            info("[iPhone5DualBooter] Still paused for Windows USB handoff "
                "acknowledgement (%lu checks).\n",
                checks);
        }
        usleep(100000);
    }
}

int recovery_client_new(struct idevicerestore_client_t* client)
{
    unsigned long attempt = 0;
    unsigned long recovery_attempt = 0;
    int probe_mode = -1;
    irecv_client_t recovery = NULL;
    irecv_error_t recovery_error = IRECV_E_UNKNOWN_ERROR;

    if (client->recovery == NULL) {
        client->recovery = (struct recovery_client_t*)malloc(sizeof(struct recovery_client_t));
        if (client->recovery == NULL) {
            error("ERROR: Out of memory\n");
            return -1;
        }
        memset(client->recovery, 0, sizeof(struct recovery_client_t));
    } else if (client->recovery->client != NULL) {
        irecv_close(client->recovery->client);
        client->recovery->client = NULL;
    }

    /*
     * Accept a direct transition immediately. If the same ECID still opens as
     * DFU/WTF, close every libirecovery handle before asking Windows to yank
     * the WSL virtual USB port. Do not return to Legacy and do not restart it.
     */
    for (attempt = 1; attempt <= 20; attempt++) {
        recovery = NULL;
        probe_mode = -1;
        recovery_error = irecv_open_with_ecid(&recovery, client->ecid);

        if (recovery_error == IRECV_E_SUCCESS && recovery != NULL) {
            irecv_get_mode(recovery, &probe_mode);

            if ((probe_mode != IRECV_K_DFU_MODE) &&
                (probe_mode != IRECV_K_WTF_MODE)) {
                info("[iPhone5DualBooter] Connected directly to post-iBEC "
                    "recovery mode 0x%x after %lu active attempt(s).\n",
                    probe_mode,
                    attempt);
                break;
            }

            irecv_close(recovery);
            recovery = NULL;
            break;
        }

        usleep(100000);
    }

    if (recovery == NULL) {
        /*
         * The successful iBEC retry leaves client->dfu->client pointing at the
         * reopened DFU handle. Close that handle too, otherwise usbipd detach
         * cannot reproduce the release that previously happened on process exit.
         */
        if (client->dfu != NULL && client->dfu->client != NULL) {
            irecv_close(client->dfu->client);
            client->dfu->client = NULL;
        }

        if (iphone5dualbooter_write_live_handoff_request(
                probe_mode,
                attempt) < 0) {
            return -1;
        }

        if (iphone5dualbooter_wait_for_live_handoff_ack() < 0) {
            return -1;
        }

        /*
         * Windows has detached the old WSL USB instance, observed 05ac:128x,
         * and reattached it. Stay inside this same function and reopen until
         * the ECID is genuinely available as Recovery.
         */
        for (;;) {
            recovery = NULL;
            probe_mode = -1;
            recovery_error = irecv_open_with_ecid(
                &recovery,
                client->ecid
            );

            if (recovery_error == IRECV_E_SUCCESS && recovery != NULL) {
                irecv_get_mode(recovery, &probe_mode);

                if ((probe_mode != IRECV_K_DFU_MODE) &&
                    (probe_mode != IRECV_K_WTF_MODE)) {
                    info("[iPhone5DualBooter] Connected to post-iBEC "
                        "Recovery mode 0x%x after the live Windows handoff "
                        "(open attempt %lu). Continuing the same restore.\n",
                        probe_mode,
                        recovery_attempt + 1);
                    break;
                }

                irecv_close(recovery);
                recovery = NULL;
            }

            recovery_attempt++;
            if ((recovery_attempt == 1) ||
                ((recovery_attempt % 40) == 0)) {
                info("[iPhone5DualBooter] Recovery is reattached but the "
                    "same ECID is not openable in genuine Recovery yet; "
                    "continuing live reopen attempt %lu.\n",
                    recovery_attempt);
            }
            usleep(100000);
        }
    }

    if (client->srnm == NULL) {
        const struct irecv_device_info *device_info =
            irecv_get_device_info(recovery);
        if (device_info && device_info->srnm) {
            client->srnm = strdup(device_info->srnm);
            info("INFO: device serial number is %s\n", client->srnm);
        }
    }

    irecv_event_subscribe(
        recovery,
        IRECV_PROGRESS,
        &recovery_progress_callback,
        NULL
    );
    client->recovery->client = recovery;
    return 0;
}
'''


UPLOAD_BLOCK_RE = re.compile(
    r'^(?P<indent>[ \t]*)'
    r'(?://[^\n]*\n(?P=indent))*'
    r'irecv_error_t[ \t]+err[ \t]*=[ \t]*'
    r'irecv_send_buffer\([ \t]*client->dfu->client[ \t]*,[ \t]*'
    r'data[ \t]*,[ \t]*size[ \t]*,[ \t]*1[ \t]*\)[ \t]*;[ \t]*\n'
    r'(?P=indent)if[ \t]*\([ \t]*err[ \t]*!=[ \t]*IRECV_E_SUCCESS[ \t]*\)'
    r'[ \t]*\{[ \t]*\n'
    r'(?P=indent)[ \t]+error\([^\n]*Unable[ \t]+to[ \t]+send[ \t]+%s'
    r'[ \t]+component:[ \t]+%s[^\n]*\);[ \t]*\n'
    r'(?P=indent)[ \t]+free\([ \t]*data[ \t]*\)[ \t]*;[ \t]*\n'
    r'(?P=indent)[ \t]+return[ \t]+-1[ \t]*;[ \t]*\n'
    r'(?P=indent)\}[ \t]*',
    re.MULTILINE,
)

IBEC_CALL_CLEANUP_RE = re.compile(
    r'^(?P<indent>[ \t]*)if[ \t]*\([ \t]*dfu_send_component\('
    r'[ \t]*client[ \t]*,[ \t]*build_identity[ \t]*,[ \t]*"iBEC"[ \t]*\)'
    r'[ \t]*<[ \t]*0[ \t]*\)[ \t]*\{[ \t]*\n'
    r'(?P=indent)[ \t]+error\([^\n]*Unable[ \t]+to[ \t]+send[ \t]+iBEC'
    r'[ \t]+to[ \t]+device[^\n]*\);[ \t]*\n'
    r'(?P=indent)[ \t]+irecv_close\([ \t]*client->dfu->client[ \t]*\)'
    r'[ \t]*;[ \t]*\n'
    r'(?P=indent)[ \t]+client->dfu->client[ \t]*=[ \t]*NULL[ \t]*;[ \t]*\n'
    r'(?P=indent)[ \t]+return[ \t]+-1[ \t]*;[ \t]*\n'
    r'(?P=indent)\}[ \t]*',
    re.MULTILINE,
)


def _function_region(text: str, signature: str) -> tuple[int, int]:
    start = text.find(signature)
    if start < 0:
        raise SystemExit(f"Could not find {signature} in LukeZGD dfu.c.")

    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"Could not find opening brace for {signature}.")

    depth = 0
    in_string = False
    in_char = False
    escaped = False
    index = brace
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""

        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\" and (in_string or in_char):
            escaped = True
            index += 1
            continue
        if not in_char and char == '"':
            in_string = not in_string
            index += 1
            continue
        if not in_string and char == "'":
            in_char = not in_char
            index += 1
            continue
        if not in_string and not in_char and char == "/" and next_char == "*":
            end_comment = text.find("*/", index + 2)
            if end_comment < 0:
                raise SystemExit("Unterminated block comment in dfu.c.")
            index = end_comment + 2
            continue
        if not in_string and not in_char and char == "/" and next_char == "/":
            newline = text.find("\n", index + 2)
            if newline < 0:
                return start, len(text)
            index = newline + 1
            continue
        if not in_string and not in_char:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return start, index + 1
        index += 1

    raise SystemExit(f"Could not find closing brace for {signature}.")


def _replacement(match: re.Match[str]) -> str:
    indent = match.group("indent")
    inner = indent + "\t"
    return (
        f"{indent}irecv_error_t err = IRECV_E_UNKNOWN_ERROR;\n"
        f"{indent}if (strcmp(component, \"iBEC\") == 0) {{\n"
        f"{inner}err = iphone5dualbooter_send_ibec_with_reconnect(client, data, size);\n"
        f"{indent}}} else {{\n"
        f"{inner}err = irecv_send_buffer(client->dfu->client, data, size, 1);\n"
        f"{indent}}}\n"
        f"{indent}if (err != IRECV_E_SUCCESS) {{\n"
        f"{inner}error(\"ERROR: Unable to send %s component: %s\\n\", component, irecv_strerror(err));\n"
        f"{inner}free(data);\n"
        f"{inner}return -1;\n"
        f"{indent}}}"
    )


def _cleanup_replacement(match: re.Match[str]) -> str:
    indent = match.group("indent")
    inner = indent + "\t"
    return (
        f"{indent}if (dfu_send_component(client, build_identity, \"iBEC\") < 0) {{\n"
        f"{inner}error(\"ERROR: Unable to send iBEC to device\\n\");\n"
        f"{inner}if (client->dfu && client->dfu->client) {{\n"
        f"{inner}\tirecv_close(client->dfu->client);\n"
        f"{inner}\tclient->dfu->client = NULL;\n"
        f"{inner}}}\n"
        f"{inner}return -1;\n"
        f"{indent}}}"
    )


def patch_file(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    if PATCH_MARKER in text:
        print(f"Already patched: {path}")
        return

    component_start, component_end = _function_region(
        text,
        "int dfu_send_component(",
    )
    component = text[component_start:component_end]
    component, count = UPLOAD_BLOCK_RE.subn(_replacement, component, count=1)
    if count != 1:
        location = component.find('info("Sending %s')
        nearby = component[max(0, location):][:900] if location >= 0 else component[:900]
        raise SystemExit(
            "Could not structurally match the dfu_send_component() upload block. "
            "The source layout may have changed. Nearby source:\n" + nearby
        )

    text = text[:component_start] + component + text[component_end:]

    insert_at = text.find("int dfu_send_component(")
    text = text[:insert_at] + HELPER + text[insert_at:]

    try:
        recovery_start, recovery_end = _function_region(
            text,
            "int dfu_enter_recovery(",
        )
        recovery = text[recovery_start:recovery_end]
        recovery, cleanup_count = IBEC_CALL_CLEANUP_RE.subn(
            _cleanup_replacement,
            recovery,
            count=1,
        )
        if cleanup_count == 1:
            text = text[:recovery_start] + recovery + text[recovery_end:]
    except SystemExit:
        pass

    path.write_text(text, encoding="utf-8")
    print(f"Patched: {path}")




DFU_HEADER_INCLUDE = '#include "dfu.h"'


def _ensure_recovery_dfu_header(text: str) -> tuple[str, bool]:
    """
    common.h only forward-declares struct dfu_client_t. The live handoff
    accesses client->dfu->client, so recovery.c needs dfu.h for the complete
    structure definition.
    """
    if DFU_HEADER_INCLUDE in text:
        return text, False

    recovery_include = '#include "recovery.h"'
    position = text.find(recovery_include)
    if position < 0:
        raise SystemExit(
            'Could not find #include "recovery.h" while adding dfu.h.'
        )

    return (
        text[:position]
        + DFU_HEADER_INCLUDE
        + "\n"
        + text[position:],
        True,
    )

def patch_recovery_file(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text, header_added = _ensure_recovery_dfu_header(text)

    if RECOVERY_PATCH_MARKER in text:
        if header_added:
            path.write_text(text, encoding="utf-8")
        print(f"Already recovery-patched: {path}")
        return

    start, end = _function_region(text, "int recovery_client_new(")
    original = text[start:end]
    required = (
        "int attempts = 20;",
        "irecv_open_with_ecid(&recovery, client->ecid)",
        "irecv_event_subscribe(recovery, IRECV_PROGRESS",
        "client->recovery->client = recovery;",
    )
    missing = [token for token in required if token not in original]
    if missing:
        raise SystemExit(
            "Could not verify the pinned recovery_client_new() layout. "
            "Missing tokens: " + ", ".join(missing)
        )

    text = text[:start] + RECOVERY_CLIENT_NEW + text[end:]
    path.write_text(text, encoding="utf-8")
    print(f"Recovery-mode wait patched: {path}")



ASR_VALIDATION_HELPER = r'''
/* IPHONE5DUALBOOTER_ASR_VALIDATION_RECOVERY_V1 */
#define IPHONE5DUALBOOTER_ASR_RECEIVE_TIMEOUT_MS 5000
#define IPHONE5DUALBOOTER_ASR_STALL_SECONDS 30

static int iphone5dualbooter_asr_send_validation_info(
    asr_client_t asr,
    uint64_t length)
{
    plist_t payload_info = plist_new_dict();
    plist_t packet_info = plist_new_dict();

    plist_dict_set_item(payload_info, "Port", plist_new_uint(1));
    plist_dict_set_item(payload_info, "Size", plist_new_uint(length));

    if (asr->checksum_chunks) {
        plist_dict_set_item(
            packet_info,
            "Checksum Chunk Size",
            plist_new_uint(ASR_CHECKSUM_CHUNK_SIZE)
        );
    }

    plist_dict_set_item(
        packet_info,
        "FEC Slice Stride",
        plist_new_uint(ASR_FEC_SLICE_STRIDE)
    );
    plist_dict_set_item(
        packet_info,
        "Packet Payload Size",
        plist_new_uint(ASR_PAYLOAD_PACKET_SIZE)
    );
    plist_dict_set_item(
        packet_info,
        "Packets Per FEC",
        plist_new_uint(ASR_PACKETS_PER_FEC)
    );
    plist_dict_set_item(packet_info, "Payload", payload_info);
    plist_dict_set_item(
        packet_info,
        "Stream ID",
        plist_new_uint(ASR_STREAM_ID)
    );
    plist_dict_set_item(
        packet_info,
        "Version",
        plist_new_uint(ASR_VERSION)
    );

    if (asr_send(asr, packet_info) < 0) {
        plist_free(packet_info);
        return -1;
    }

    plist_free(packet_info);
    return 0;
}

/*
 * Returns:
 *   0  received a plist
 *   1  timed out without data
 *  -1  connection/protocol failure
 */
static int iphone5dualbooter_asr_receive_timed(
    asr_client_t asr,
    plist_t* data,
    unsigned int timeout_ms)
{
    uint32_t size = 0;
    char* buffer = NULL;
    plist_t request = NULL;
    idevice_error_t device_error = IDEVICE_E_SUCCESS;

    *data = NULL;
    buffer = (char*)malloc(ASR_BUFFER_SIZE);
    if (buffer == NULL) {
        error("ERROR: Unable to allocate ASR validation receive buffer\n");
        return -1;
    }
    memset(buffer, '\0', ASR_BUFFER_SIZE);

    device_error = idevice_connection_receive_timeout(
        asr->connection,
        buffer,
        ASR_BUFFER_SIZE,
        &size,
        timeout_ms
    );

    if (device_error == IDEVICE_E_TIMEOUT ||
        (device_error == IDEVICE_E_SUCCESS && size == 0)) {
        free(buffer);
        return 1;
    }

    if (device_error != IDEVICE_E_SUCCESS) {
        error("[iPhone5DualBooter] ASR validation receive failed: %d\n",
            device_error);
        free(buffer);
        return -1;
    }

    plist_from_xml(buffer, size, &request);
    free(buffer);

    if (request == NULL) {
        error("[iPhone5DualBooter] ASR validation returned an invalid plist\n");
        return -1;
    }

    *data = request;
    return 0;
}

'''

ASR_OOB_GUARD_HELPER = r'''
/* IPHONE5DUALBOOTER_ASR_VALIDATION_RECOVERY_V4 */
#include <inttypes.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif
#ifndef MSG_DONTWAIT
#define MSG_DONTWAIT 0
#endif

/*
 * WSL/usbipd has a known 65536-vs-65535 usbmuxd boundary failure during
 * restores. Keep each userspace write small, limit queued socket data below
 * 64 KiB, and deliberately yield between bursts so the transport cannot
 * accumulate the exact failing 64 KiB packet.
 */
#define IPHONE5DUALBOOTER_ASR_OOB_CHUNK_SIZE 4096
#define IPHONE5DUALBOOTER_ASR_OOB_SOCKET_SNDBUF 16384
#define IPHONE5DUALBOOTER_ASR_OOB_BURST_BYTES 32768
#define IPHONE5DUALBOOTER_ASR_OOB_INTERCHUNK_US 2000
#define IPHONE5DUALBOOTER_ASR_OOB_BURST_PAUSE_US 10000
#define IPHONE5DUALBOOTER_ASR_OOB_POLL_MS 250
#define IPHONE5DUALBOOTER_ASR_OOB_STATUS_MS 5000

static int iphone5dualbooter_asr_send_oob_resumable_v4(
    asr_client_t asr,
    const char* data,
    uint64_t size,
    unsigned int request_number)
{
    int fd = -1;
    int requested_send_buffer = IPHONE5DUALBOOTER_ASR_OOB_SOCKET_SNDBUF;
    int actual_send_buffer = 0;
    socklen_t actual_send_buffer_size = sizeof(actual_send_buffer);
    uint64_t sent = 0;
    uint64_t next_burst_pause = IPHONE5DUALBOOTER_ASR_OOB_BURST_BYTES;
    unsigned int quiet_ms = 0;
    unsigned int last_percent = 101;

    if (idevice_connection_get_fd(asr->connection, &fd) != IDEVICE_E_SUCCESS || fd < 0) {
        error("[iPhone5DualBooter] ASR OOB request #%u could not obtain the connection fd. The current ASR session cannot continue.\n", request_number);
        return -1;
    }

    if (setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &requested_send_buffer, sizeof(requested_send_buffer)) < 0) {
        info("[iPhone5DualBooter] WSL 64 KiB OOB guard could not reduce SO_SNDBUF: %s. Continuing with paced 4 KiB writes.\n", strerror(errno));
    }
    if (getsockopt(fd, SOL_SOCKET, SO_SNDBUF, &actual_send_buffer, &actual_send_buffer_size) == 0) {
        info("[iPhone5DualBooter] WSL 64 KiB OOB guard active for request #%u: 4096-byte writes, paced 32768-byte bursts, socket send buffer %d bytes.\n", request_number, actual_send_buffer);
    } else {
        info("[iPhone5DualBooter] WSL 64 KiB OOB guard active for request #%u: 4096-byte writes with paced 32768-byte bursts.\n", request_number);
    }

    while (sent < size) {
        struct pollfd descriptor;
        size_t remaining = (size_t)(size - sent);
        size_t chunk = remaining > IPHONE5DUALBOOTER_ASR_OOB_CHUNK_SIZE
            ? IPHONE5DUALBOOTER_ASR_OOB_CHUNK_SIZE : remaining;
        int poll_result;

        descriptor.fd = fd;
        descriptor.events = POLLOUT;
        descriptor.revents = 0;
        poll_result = poll(&descriptor, 1, IPHONE5DUALBOOTER_ASR_OOB_POLL_MS);

        if (poll_result < 0) {
            if (errno == EINTR) continue;
            error("[iPhone5DualBooter] ASR OOB request #%u poll failed after %" PRIu64 "/%" PRIu64 " bytes: %s.\n", request_number, sent, size, strerror(errno));
            return -1;
        }

        if (poll_result == 0) {
            quiet_ms += IPHONE5DUALBOOTER_ASR_OOB_POLL_MS;
            if ((quiet_ms % IPHONE5DUALBOOTER_ASR_OOB_STATUS_MS) == 0) {
                info("[iPhone5DualBooter] ASR OOB request #%u is temporarily backpressured; preserving the same session and retrying from %" PRIu64 "/%" PRIu64 " bytes after %u quiet second(s). Cancel Legacy to stop.\n", request_number, sent, size, quiet_ms / 1000);
            }
            continue;
        }

        if (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL)) {
            error("[iPhone5DualBooter] ASR OOB request #%u connection became unusable at %" PRIu64 "/%" PRIu64 " bytes (poll events 0x%x). Not opening a blind replacement ASR session.\n", request_number, sent, size, descriptor.revents);
            return -1;
        }

        if (descriptor.revents & POLLOUT) {
            ssize_t written = send(fd, data + (size_t)sent, chunk, MSG_NOSIGNAL | MSG_DONTWAIT);
            if (written > 0) {
                unsigned int percent;
                sent += (uint64_t)written;
                quiet_ms = 0;
                percent = size ? (unsigned int)((sent * 100) / size) : 100;
                if (percent == 100 || last_percent == 101 || percent >= last_percent + 10) {
                    info("[iPhone5DualBooter] ASR OOB request #%u send progress: %" PRIu64 "/%" PRIu64 " bytes (%u%%).\n", request_number, sent, size, percent);
                    last_percent = percent;
                }

                usleep(IPHONE5DUALBOOTER_ASR_OOB_INTERCHUNK_US);
                if (sent >= next_burst_pause && sent < size) {
                    usleep(IPHONE5DUALBOOTER_ASR_OOB_BURST_PAUSE_US);
                    while (next_burst_pause <= sent) {
                        next_burst_pause += IPHONE5DUALBOOTER_ASR_OOB_BURST_BYTES;
                    }
                }
                continue;
            }

            if (written == 0 || errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) {
                usleep(100000);
                quiet_ms += 100;
                if ((quiet_ms % IPHONE5DUALBOOTER_ASR_OOB_STATUS_MS) < 100) {
                    info("[iPhone5DualBooter] ASR OOB request #%u send would block; keeping the same session and resuming at %" PRIu64 "/%" PRIu64 " bytes. Cancel Legacy to stop.\n", request_number, sent, size);
                }
                continue;
            }

            error("[iPhone5DualBooter] ASR OOB request #%u send failed at %" PRIu64 "/%" PRIu64 " bytes: %s. The current ASR session cannot continue.\n", request_number, sent, size, strerror(errno));
            return -1;
        }
    }
    return 0;
}

static int iphone5dualbooter_asr_handle_oob_resumable_v4(
    asr_client_t asr, plist_t packet, FILE* file, unsigned int request_number)
{
    char* oob_data = NULL;
    uint64_t oob_offset = 0, oob_length = 0;
    plist_t length_node = plist_dict_get_item(packet, "OOB Length");
    plist_t offset_node = plist_dict_get_item(packet, "OOB Offset");
    size_t read_bytes;
    int result;

    if (!length_node || plist_get_node_type(length_node) != PLIST_UINT ||
        !offset_node || plist_get_node_type(offset_node) != PLIST_UINT) {
        error("[iPhone5DualBooter] ASR OOB request #%u has invalid offset/length fields.\n", request_number);
        return -1;
    }
    plist_get_uint_val(length_node, &oob_length);
    plist_get_uint_val(offset_node, &oob_offset);
    info("[iPhone5DualBooter] ASR OOB request #%u received: offset 0x%" PRIx64 ", length %" PRIu64 " bytes.\n", request_number, oob_offset, oob_length);

    if (oob_length == 0) return 0;
    if (oob_length > (uint64_t)SIZE_MAX) return -1;
    oob_data = (char*)malloc((size_t)oob_length);
    if (!oob_data) return -1;
    if (fseeko(file, (off_t)oob_offset, SEEK_SET) < 0) { free(oob_data); return -1; }
    read_bytes = fread(oob_data, 1, (size_t)oob_length, file);
    if (read_bytes != (size_t)oob_length) { free(oob_data); return -1; }

    info("[iPhone5DualBooter] ASR OOB request #%u disk read completed; beginning WSL-safe paced same-session send.\n", request_number);
    result = iphone5dualbooter_asr_send_oob_resumable_v4(asr, oob_data, oob_length, request_number);
    free(oob_data);
    if (result == 0) info("[iPhone5DualBooter] ASR OOB request #%u completed successfully.\n", request_number);
    return result;
}

'''


ASR_VALIDATION_FUNCTION = r'''
int asr_perform_validation(asr_client_t asr, const char* filesystem)
{
    FILE* file = NULL;
    uint64_t length = 0;
    char* command = NULL;
    plist_t node = NULL;
    plist_t packet = NULL;
    unsigned int quiet_seconds = 0;
    unsigned int oob_requests = 0;

    file = fopen(filesystem, "rb");
    if (file == NULL) {
        return -1;
    }

    fseeko(file, 0, SEEK_END);
    length = ftello(file);
    fseeko(file, 0, SEEK_SET);

    if (iphone5dualbooter_asr_send_validation_info(asr, length) < 0) {
        error("ERROR: Unable to send validation packet information to ASR\n");
        fclose(file);
        return -1;
    }

    for (;;) {
        int receive_result = iphone5dualbooter_asr_receive_timed(
            asr,
            &packet,
            IPHONE5DUALBOOTER_ASR_RECEIVE_TIMEOUT_MS
        );

        if (receive_result == 1) {
            quiet_seconds +=
                IPHONE5DUALBOOTER_ASR_RECEIVE_TIMEOUT_MS / 1000;

            info("[iPhone5DualBooter] ASR validation receive timed out "
                "after %u quiet second(s); the device is still connected "
                "and the validation handshake will be retried.\n",
                quiet_seconds);

            if ((quiet_seconds % 15) == 0) {
                info("[iPhone5DualBooter] Resending ASR validation packet "
                    "information to wake the restore ramdisk.\n");
                if (iphone5dualbooter_asr_send_validation_info(
                        asr,
                        length) < 0) {
                    fclose(file);
                    return -1;
                }
            }

            if (quiet_seconds >= IPHONE5DUALBOOTER_ASR_STALL_SECONDS) {
                info("[iPhone5DualBooter] ASR validation is still quiet after %u seconds. Keeping the same ASR session open and continuing timed receives; no blind reconnect will be attempted. Cancel Legacy to stop.\n", quiet_seconds);
                quiet_seconds = 0;
            }
            continue;
        }

        if (receive_result < 0) {
            fclose(file);
            return -1;
        }

        quiet_seconds = 0;
        node = plist_dict_get_item(packet, "Command");
        if (!node || plist_get_node_type(node) != PLIST_STRING) {
            error("ERROR: Unable to find command node in validation request\n");
            plist_free(packet);
            fclose(file);
            return -1;
        }

        plist_get_string_val(node, &command);

        if (!strcmp(command, "Initiate")) {
            node = plist_dict_get_item(packet, "Checksum Chunks");
            if (node && plist_get_node_type(node) == PLIST_BOOLEAN) {
                plist_get_bool_val(node, &(asr->checksum_chunks));
            }

            free(command);
            command = NULL;
            plist_free(packet);
            packet = NULL;

            info("[iPhone5DualBooter] ASR sent a repeated Initiate request; "
                "updated checksum settings and resent packet information.\n");

            if (iphone5dualbooter_asr_send_validation_info(
                    asr,
                    length) < 0) {
                fclose(file);
                return -1;
            }
            continue;
        }

        if (!strcmp(command, "OOBData")) {
            int result = 0;
            oob_requests++;

            result = iphone5dualbooter_asr_handle_oob_resumable_v4(
                asr,
                packet,
                file,
                oob_requests
            );

            free(command);
            command = NULL;
            plist_free(packet);
            packet = NULL;

            if (result < 0) {
                fclose(file);
                return result;
            }
            continue;
        }

        if (!strcmp(command, "Payload")) {
            info("[iPhone5DualBooter] ASR validation reached the Payload "
                "request after %u completed OOB request(s). Filesystem transfer can "
                "begin.\n",
                oob_requests);

            free(command);
            command = NULL;
            plist_free(packet);
            packet = NULL;
            break;
        }

        error("ERROR: Unknown command received from ASR: %s\n",
            command ? command : "(null)");
        free(command);
        command = NULL;
        plist_free(packet);
        packet = NULL;
        fclose(file);
        return -1;
    }

    fclose(file);
    return 0;
}
'''

RESTORE_SEND_FILESYSTEM_FUNCTION = r'''
/* IPHONE5DUALBOOTER_RESTORE_ASR_SAME_SESSION_V2 */
int restore_send_filesystem(struct idevicerestore_client_t* client, idevice_t device, const char* filesystem)
{
    asr_client_t asr = NULL;
    int validation_result;

    info("About to send filesystem...\n");
    info("[iPhone5DualBooter] Opening one ASR session for validation and payload. Temporary OOB backpressure will resume inside this same session.\n");

    if (asr_open_with_timeout(device, &asr) < 0) {
        error("[iPhone5DualBooter] ERROR: the initial ASR Initiate handshake failed. No blind reconnect loop will be started.\n");
        return -1;
    }

    info("Connected to ASR\n");
    asr_set_progress_callback(asr, restore_asr_progress_cb, (void*)client);
    info("Validating the filesystem\n");
    validation_result = asr_perform_validation(asr, filesystem);
    if (validation_result < 0) {
        error("ERROR: ASR was unable to validate the filesystem on the current session\n");
        asr_free(asr);
        return -1;
    }

    info("Filesystem validated\n");
    if (asr_send_payload(asr, filesystem) < 0) {
        error("ERROR: Unable to send filesystem payload\n");
        asr_free(asr);
        return -1;
    }
    asr_free(asr);
    return 0;
}
'''



def patch_asr_file(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if ASR_PATCH_MARKER in text:
        print(f"Already ASR-v4-WSL-guard-patched: {path}")
        return

    # A cached WSL source tree may already contain V3. Remove only the V3 OOB
    # helper block before replacing asr_perform_validation(), preserving the
    # shared V1 timed-receive helper that V4 still uses.
    old_v3_comment = f"/* {OLD_ASR_V3_PATCH_MARKER} */"
    if old_v3_comment in text:
        old_start = text.index(old_v3_comment)
        validation_start, _ = _function_region(text, "int asr_perform_validation(")
        text = text[:old_start] + text[validation_start:]

    start, end = _function_region(text, "int asr_perform_validation(")
    helpers = ASR_OOB_GUARD_HELPER
    if (
        OLD_ASR_PATCH_MARKER not in text
        and OLD_ASR_V2_PATCH_MARKER not in text
    ):
        helpers = ASR_VALIDATION_HELPER + helpers
    text = text[:start] + helpers + ASR_VALIDATION_FUNCTION + text[end:]
    path.write_text(text, encoding="utf-8")
    print(f"ASR same-session resumable v4 WSL guard patched: {path}")

def patch_restore_asr_file(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if RESTORE_ASR_PATCH_MARKER in text:
        print(f"Already restore-ASR-v2-patched: {path}")
        return
    start, end = _function_region(text, "int restore_send_filesystem(")
    text = text[:start] + RESTORE_SEND_FILESYSTEM_FUNCTION + text[end:]
    path.write_text(text, encoding="utf-8")
    print(f"Restore single-ASR-session v2 patched: {path}")

def main() -> int:
    if len(sys.argv) not in (2, 3, 4, 5):
        print(
            "usage: patch_lukezgd_idevicerestore.py "
            "PATH/TO/src/dfu.c "
            "[PATH/TO/src/recovery.c "
            "[PATH/TO/src/asr.c "
            "[PATH/TO/src/restore.c]]]"
        )
        return 2

    patch_file(Path(sys.argv[1]))

    if len(sys.argv) >= 3:
        patch_recovery_file(Path(sys.argv[2]))
    if len(sys.argv) >= 4:
        patch_asr_file(Path(sys.argv[3]))
    if len(sys.argv) >= 5:
        patch_restore_asr_file(Path(sys.argv[4]))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
