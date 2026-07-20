#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

MARKER = "IPHONE5DUALBOOTER_RESTORED_TRANSPORT_V1"


def function_region(text: str, signature: str) -> tuple[int, int]:
    start = text.find(signature)
    if start < 0:
        raise RuntimeError(f"Could not find function: {signature}")
    brace = text.find("{", start)
    if brace < 0:
        raise RuntimeError(f"Could not find opening brace: {signature}")
    depth = 0
    i = brace
    in_string = False
    in_char = False
    escape = False
    while i < len(text):
        c = text[i]
        if escape:
            escape = False
        elif c == "\\":
            escape = True
        elif in_string:
            if c == '"':
                in_string = False
        elif in_char:
            if c == "'":
                in_char = False
        elif c == '"':
            in_string = True
        elif c == "'":
            in_char = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
        i += 1
    raise RuntimeError(f"Unterminated function: {signature}")

PROPERTY_LIST_SEND = r'''/* IPHONE5DUALBOOTER_RESTORED_TRANSPORT_V1 */
#define IPHONE5DUALBOOTER_RESTORED_CHUNK_SIZE 4096U
#define IPHONE5DUALBOOTER_RESTORED_BURST_SIZE 32768U
#define IPHONE5DUALBOOTER_RESTORED_INTERCHUNK_US 2000U
#define IPHONE5DUALBOOTER_RESTORED_BURST_PAUSE_US 10000U

static const char* iphone5dualbooter_plist_transfer_name(plist_t plist)
{
    static const char* keys[] = {
        "KernelCacheFile", "NORData", "BasebandData", "DeviceTreeFile",
        "SystemImageData", "RootTicketData", "FirmwareData", "FUDData",
        "PersonalizedData", "Data", NULL
    };
    unsigned int i;
    if (!plist || plist_get_node_type(plist) != PLIST_DICT) {
        return "restored plist";
    }
    for (i = 0; keys[i] != NULL; i++) {
        if (plist_dict_get_item(plist, keys[i]) != NULL) {
            return keys[i];
        }
    }
    return "restored plist";
}

static property_list_service_error_t internal_plist_send(property_list_service_client_t client, plist_t plist, int binary)
{
    property_list_service_error_t res = PROPERTY_LIST_SERVICE_E_UNKNOWN_ERROR;
    char *content = NULL;
    uint32_t length = 0;
    uint32_t nlen = 0;
    uint32_t bytes = 0;
    uint32_t offset = 0;
    uint32_t next_report = 0;
    uint32_t next_burst = IPHONE5DUALBOOTER_RESTORED_BURST_SIZE;
    const char* transfer_name = iphone5dualbooter_plist_transfer_name(plist);

    if (!client || (client && !client->parent) || !plist) {
        return PROPERTY_LIST_SERVICE_E_INVALID_ARG;
    }
    if (binary) {
        plist_to_bin(plist, &content, &length);
    } else {
        plist_to_xml(plist, &content, &length);
    }
    if (!content || length == 0) {
        return PROPERTY_LIST_SERVICE_E_PLIST_ERROR;
    }

    nlen = htobe32(length);
    if (service_send(client->parent, (const char*)&nlen, sizeof(nlen), &bytes) != SERVICE_E_SUCCESS || bytes != sizeof(nlen)) {
        debug_info("ERROR: sending plist length failed");
        free(content);
        return PROPERTY_LIST_SERVICE_E_MUX_ERROR;
    }

    fprintf(stderr, "[iPhone5DualBooter] Restored transfer %s: %u-byte serialized plist; using 4096-byte writes, paced 32768-byte bursts, socket send buffer below 64 KiB.\n", transfer_name, length);

    while (offset < length) {
        uint32_t remaining = length - offset;
        uint32_t chunk = remaining > IPHONE5DUALBOOTER_RESTORED_CHUNK_SIZE ? IPHONE5DUALBOOTER_RESTORED_CHUNK_SIZE : remaining;
        service_error_t serr;
        unsigned int backpressure_ms = 0;
        for (;;) {
            bytes = 0;
            serr = service_send(client->parent, content + offset, chunk, &bytes);
            if (serr == SERVICE_E_SUCCESS && bytes > 0) {
                break;
            }
            if (serr == SERVICE_E_TIMEOUT || bytes == 0) {
                backpressure_ms += 250;
                if ((backpressure_ms % 5000) == 0) {
                    fprintf(stderr, "[iPhone5DualBooter] Restored transfer %s is temporarily backpressured at offset %u/%u; retrying the same %u-byte chunk after %u quiet second(s).\n", transfer_name, offset, length, chunk, backpressure_ms / 1000);
                }
                if (backpressure_ms >= 60000) {
                    fprintf(stderr, "[iPhone5DualBooter] ERROR: restored transfer %s timed out after 60 seconds with no offset advancement at %u/%u bytes (next chunk %u bytes).\n", transfer_name, offset, length, chunk);
                    free(content);
                    return PROPERTY_LIST_SERVICE_E_MUX_ERROR;
                }
                usleep(250000);
                continue;
            }
            fprintf(stderr, "[iPhone5DualBooter] ERROR: restored transfer %s failed at offset %u/%u (requested %u bytes, sent %u, service error %d).\n", transfer_name, offset, length, chunk, bytes, serr);
            free(content);
            return PROPERTY_LIST_SERVICE_E_MUX_ERROR;
        }
        offset += bytes;
        if (offset >= next_report || offset == length) {
            unsigned int percent = length ? (unsigned int)(((uint64_t)offset * 100U) / length) : 100U;
            fprintf(stderr, "[iPhone5DualBooter] Restored transfer %s progress: %u/%u bytes (%u%%).\n", transfer_name, offset, length, percent);
            next_report = offset + (length / 10U > 4096U ? length / 10U : 4096U);
        }
        usleep(IPHONE5DUALBOOTER_RESTORED_INTERCHUNK_US);
        if (offset >= next_burst) {
            usleep(IPHONE5DUALBOOTER_RESTORED_BURST_PAUSE_US);
            while (next_burst <= offset) {
                next_burst += IPHONE5DUALBOOTER_RESTORED_BURST_SIZE;
            }
        }
    }

    fprintf(stderr, "[iPhone5DualBooter] Restored transfer %s completed successfully (%u bytes).\n", transfer_name, length);
    debug_plist(plist);
    res = PROPERTY_LIST_SERVICE_E_SUCCESS;
    free(content);
    return res;
}
'''

SERVICE_SEND = r'''/* IPHONE5DUALBOOTER_RESTORED_TRANSPORT_V1 */
#include <errno.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/time.h>

#define IPHONE5DUALBOOTER_SERVICE_SEND_TIMEOUT_MS 60000
#define IPHONE5DUALBOOTER_SERVICE_SOCKET_SNDBUF 32768
#define IPHONE5DUALBOOTER_SERVICE_CALL_TIMEOUT_SEC 5

service_error_t service_send(service_client_t client, const char* data, uint32_t size, uint32_t *sent)
{
    service_error_t res = SERVICE_E_UNKNOWN_ERROR;
    uint32_t bytes = 0;
    int fd = -1;
    int requested_send_buffer = IPHONE5DUALBOOTER_SERVICE_SOCKET_SNDBUF;
    struct timeval send_timeout;
    struct pollfd descriptor;
    int poll_result;

    if (!client || (client && !client->connection) || !data || (size == 0)) {
        return SERVICE_E_INVALID_ARG;
    }

    if (idevice_connection_get_fd(client->connection, &fd) == IDEVICE_E_SUCCESS && fd >= 0) {
        setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &requested_send_buffer, sizeof(requested_send_buffer));
        send_timeout.tv_sec = IPHONE5DUALBOOTER_SERVICE_CALL_TIMEOUT_SEC;
        send_timeout.tv_usec = 0;
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &send_timeout, sizeof(send_timeout));
        descriptor.fd = fd;
        descriptor.events = POLLOUT;
        descriptor.revents = 0;
        poll_result = poll(&descriptor, 1, IPHONE5DUALBOOTER_SERVICE_SEND_TIMEOUT_MS);
        if (poll_result == 0) {
            fprintf(stderr, "[iPhone5DualBooter] ERROR: restored transport timed out waiting 60 seconds for a writable USB socket before offset advancement (next write %u bytes).\n", size);
            if (sent) *sent = 0;
            return SERVICE_E_TIMEOUT;
        }
        if (poll_result < 0) {
            fprintf(stderr, "[iPhone5DualBooter] ERROR: restored transport poll failed: %s.\n", strerror(errno));
            if (sent) *sent = 0;
            return SERVICE_E_MUX_ERROR;
        }
        if (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL)) {
            fprintf(stderr, "[iPhone5DualBooter] ERROR: restored transport socket became unusable (poll events 0x%x).\n", descriptor.revents);
            if (sent) *sent = 0;
            return SERVICE_E_MUX_ERROR;
        }
    }

    res = idevice_to_service_error(idevice_connection_send(client->connection, data, size, &bytes));
    if (res != SERVICE_E_SUCCESS) {
        if (res == SERVICE_E_TIMEOUT || errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) {
            fprintf(stderr, "[iPhone5DualBooter] Restored transport is temporarily backpressured; retrying the same %u-byte write without advancing the offset.\n", size);
            if (sent) *sent = 0;
            return SERVICE_E_TIMEOUT;
        }
        fprintf(stderr, "[iPhone5DualBooter] ERROR: restored transport write failed (requested %u bytes, sent %u, service error %d, errno %d: %s).\n", size, bytes, res, errno, strerror(errno));
    }
    if (sent) {
        *sent = bytes;
    }
    return res;
}
'''


def patch_property_list(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print(f"Already paced restored-plist patched: {path}")
        return
    # Required for uint64_t and usleep/fprintf on older snapshots.
    includes = "#include <stdint.h>\n#include <stdio.h>\n#include <unistd.h>\n"
    anchor = "#include <stdlib.h>\n"
    if includes not in text:
        text = text.replace(anchor, anchor + includes, 1)
    start, end = function_region(text, "static property_list_service_error_t internal_plist_send(")
    text = text[:start] + PROPERTY_LIST_SEND + text[end:]
    path.write_text(text, encoding="utf-8")
    print(f"Paced restored plist callback patched: {path}")


def patch_service(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print(f"Already restored service timeout patched: {path}")
        return
    start, end = function_region(text, "service_error_t service_send(")
    text = text[:start] + SERVICE_SEND + text[end:]
    path.write_text(text, encoding="utf-8")
    print(f"Restored service socket timeout/backpressure patched: {path}")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: patch_libimobiledevice_restored_transport.py PATH/src/property_list_service.c PATH/src/service.c")
        return 2
    patch_property_list(Path(sys.argv[1]))
    patch_service(Path(sys.argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
