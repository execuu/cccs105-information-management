"""RFID reader → Django HTTP bridge.

Supports two reader modes selected by ``READER_MODE``:

* ``serial`` reads Arduino RC522 lines of the form ``UID:<HEX>`` from a
  configured serial port.
* ``keyboard`` reads a keyboard-wedge USB reader via Linux evdev or Windows
  Raw Input and emits one token when Enter is pressed.

Both modes POST the normalized UID token to Django's scan endpoint. Network
failures retry with exponential backoff; device disconnects reconnect.
"""

from __future__ import annotations

import argparse
import ctypes
import glob
import logging
import os
import re
import select
import signal
import sys
import time
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Optional

import requests
import serial
from dotenv import load_dotenv

LOG = logging.getLogger("bridge")
READER_MODE_SERIAL = "serial"
READER_MODE_KEYBOARD = "keyboard"
SUPPORTED_READER_MODES = {READER_MODE_SERIAL, READER_MODE_KEYBOARD}
UID_LINE = re.compile(r"^UID:([0-9A-Fa-f]+)\s*$")
UID_TOKEN = re.compile(r"^[0-9A-Z]+$")

KEY_CHARS = {
    **{f"KEY_{i}": str(i) for i in range(10)},
    **{f"KEY_KP{i}": str(i) for i in range(10)},
    **{f"KEY_{chr(code)}": chr(code) for code in range(ord("A"), ord("Z") + 1)},
}
DEFAULT_KEYBOARD_TERMINATORS = ("KEY_ENTER", "KEY_KPENTER")
DEFAULT_KEYBOARD_IDLE_FLUSH_SECONDS = 0.25
WINDOWS_KEYBOARD_DEVICE_FILTER_ENV = "WINDOWS_KEYBOARD_DEVICE_FILTER"

WM_INPUT = 0x00FF
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
PM_REMOVE = 0x0001
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
RIM_TYPEKEYBOARD = 1
RIDEV_INPUTSINK = 0x00000100
HID_USAGE_PAGE_GENERIC = 0x01
HID_USAGE_GENERIC_KEYBOARD = 0x06

RI_KEY_BREAK = 0x01
RI_KEY_E0 = 0x02
RI_KEY_E1 = 0x04

VK_BACK = 0x08
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_0 = 0x30
VK_9 = 0x39
VK_A = 0x41
VK_Z = 0x5A
VK_NUMPAD0 = 0x60
VK_NUMPAD9 = 0x69


def is_windows_platform(platform: Optional[str] = None) -> bool:
    return (platform or sys.platform).lower().startswith("win")


def is_linux_platform(platform: Optional[str] = None) -> bool:
    return (platform or sys.platform).lower().startswith("linux")


@dataclass(frozen=True)
class KeyboardDeviceInfo:
    path: str
    name: str = ""
    phys: str = ""
    uniq: str = ""
    by_id_paths: tuple[str, ...] = ()

    def display_line(self) -> str:
        parts = [self.path]
        if self.name:
            parts.append(f"name={self.name}")
        if self.phys:
            parts.append(f"phys={self.phys}")
        if self.uniq:
            parts.append(f"uniq={self.uniq}")
        if self.by_id_paths:
            parts.append("by-id=" + ", ".join(self.by_id_paths))
        return " | ".join(parts)


@dataclass(frozen=True)
class Config:
    serial_port: str
    baud_rate: int
    django_url: str
    bridge_key: str
    request_timeout: float
    max_retries: int
    initial_backoff: float
    reconnect_delay: float
    reader_mode: str = READER_MODE_SERIAL
    keyboard_device: str = ""
    keyboard_terminators: tuple[str, ...] = DEFAULT_KEYBOARD_TERMINATORS
    keyboard_idle_flush_seconds: float = DEFAULT_KEYBOARD_IDLE_FLUSH_SECONDS
    keyboard_grab_device: bool = False
    windows_keyboard_device_filter: str = ""
    scanner_code: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv(os.environ.get("BRIDGE_ENV_FILE", ".env"))
        reader_mode = os.environ.get("READER_MODE", READER_MODE_SERIAL).strip().lower()
        if reader_mode not in SUPPORTED_READER_MODES:
            raise ValueError(
                "READER_MODE must be one of: "
                + ", ".join(sorted(SUPPORTED_READER_MODES))
            )
        serial_port = os.environ.get("SERIAL_PORT", "")
        keyboard_device = os.environ.get("KEYBOARD_DEVICE", "")
        windows_keyboard_device_filter = os.environ.get(
            WINDOWS_KEYBOARD_DEVICE_FILTER_ENV, ""
        )
        if reader_mode == READER_MODE_SERIAL and not serial_port:
            raise KeyError("SERIAL_PORT")
        if reader_mode == READER_MODE_KEYBOARD:
            if is_windows_platform():
                if not windows_keyboard_device_filter:
                    raise KeyError(WINDOWS_KEYBOARD_DEVICE_FILTER_ENV)
            elif is_linux_platform():
                if not keyboard_device:
                    raise KeyError("KEYBOARD_DEVICE")
            else:
                raise ValueError(
                    "keyboard reader mode is only supported on Linux and Windows"
                )
        return cls(
            reader_mode=reader_mode,
            serial_port=serial_port,
            baud_rate=int(os.environ.get("BAUD_RATE", "9600")),
            django_url=os.environ["DJANGO_URL"].rstrip("/"),
            bridge_key=os.environ["BRIDGE_KEY"],
            request_timeout=float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "5")),
            max_retries=int(os.environ.get("MAX_RETRIES", "3")),
            initial_backoff=float(os.environ.get("INITIAL_BACKOFF_SECONDS", "0.5")),
            reconnect_delay=float(os.environ.get("RECONNECT_DELAY_SECONDS", "2")),
            keyboard_device=keyboard_device,
            keyboard_terminators=parse_keyboard_terminators(
                os.environ.get("KEYBOARD_TERMINATOR", "ENTER")
            ),
            keyboard_idle_flush_seconds=float(
                os.environ.get(
                    "KEYBOARD_IDLE_FLUSH_SECONDS",
                    str(DEFAULT_KEYBOARD_IDLE_FLUSH_SECONDS),
                )
            ),
            keyboard_grab_device=parse_bool(os.environ.get("KEYBOARD_GRAB_DEVICE")),
            windows_keyboard_device_filter=windows_keyboard_device_filter,
            scanner_code=os.environ.get("SCANNER_CODE", "").strip().upper(),
        )


_shutdown = False


def _handle_signal(signum, _frame):
    global _shutdown
    LOG.info("signal %s received; shutting down", signum)
    _shutdown = True


def normalize_uid(value: str) -> Optional[str]:
    """Return a normalized reader token, or None when it is not a UID token."""
    uid = (value or "").strip().upper()
    if not uid or not UID_TOKEN.match(uid):
        return None
    return uid


def parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None or not str(value).strip():
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def parse_uid(line: str) -> Optional[str]:
    m = UID_LINE.match((line or "").strip())
    if not m:
        return None
    return normalize_uid(m.group(1))


def parse_keyboard_token(token: str) -> Optional[str]:
    return normalize_uid(token)


def _canonical_key_label(label: str) -> str:
    label = (label or "").strip().upper()
    if label and not label.startswith("KEY_"):
        label = f"KEY_{label}"
    return label


def parse_keyboard_terminators(value: str) -> tuple[str, ...]:
    labels = tuple(
        _canonical_key_label(part)
        for part in re.split(r"[\s,]+", value or "")
        if part.strip()
    )
    return labels or DEFAULT_KEYBOARD_TERMINATORS


def windows_virtual_key_to_key_label(vkey: int, flags: int = 0) -> Optional[str]:
    """Map a Windows virtual key to the labels consumed by KeyboardTokenBuffer."""
    if VK_0 <= vkey <= VK_9:
        return f"KEY_{vkey - VK_0}"
    if VK_A <= vkey <= VK_Z:
        return f"KEY_{chr(vkey)}"
    if VK_NUMPAD0 <= vkey <= VK_NUMPAD9:
        return f"KEY_KP{vkey - VK_NUMPAD0}"
    if vkey == VK_RETURN:
        return "KEY_KPENTER" if flags & RI_KEY_E0 else "KEY_ENTER"
    if vkey == VK_BACK:
        return "KEY_BACKSPACE"
    if vkey == VK_ESCAPE:
        return "KEY_ESC"
    return None


def windows_keyboard_device_matches(device_name: str, device_filter: str) -> bool:
    """Return True when any configured filter token matches a Raw Input device."""
    haystack = (device_name or "").casefold()
    filters = [
        part.strip().casefold()
        for part in re.split(r"[;,]+", device_filter or "")
        if part.strip()
    ]
    return bool(haystack and filters and any(part in haystack for part in filters))


def _is_windows_keydown(message: int, flags: int) -> bool:
    return message in {WM_KEYDOWN, WM_SYSKEYDOWN} and not flags & RI_KEY_BREAK


class KeyboardTokenBuffer:
    """Build one scanner UID from Linux key names and emit on Enter."""

    def __init__(
        self,
        terminators: tuple[str, ...] = DEFAULT_KEYBOARD_TERMINATORS,
    ) -> None:
        self.terminators = tuple(_canonical_key_label(t) for t in terminators)
        self._chars: list[str] = []

    @property
    def has_pending(self) -> bool:
        return bool(self._chars)

    def feed_key(self, label: str) -> Optional[str]:
        key = _canonical_key_label(label)
        if key in self.terminators:
            return self.flush()
        if key == "KEY_BACKSPACE":
            if self._chars:
                self._chars.pop()
            return None
        if key == "KEY_ESC":
            self._chars.clear()
            return None
        char = KEY_CHARS.get(key)
        if char is not None:
            self._chars.append(char)
        return None

    def flush(self) -> Optional[str]:
        token = "".join(self._chars)
        self._chars.clear()
        return parse_keyboard_token(token)


def post_scan(
    cfg: Config,
    uid: str,
    session: Optional[requests.Session] = None,
    sleep=time.sleep,
) -> bool:
    """POST a UID to the scan endpoint. Returns True on 2xx, False otherwise.

    Retries transient failures (network errors, 5xx) with exponential backoff.
    """
    url = f"{cfg.django_url}/api/scan/"
    headers = {"X-Bridge-Key": cfg.bridge_key, "Content-Type": "application/json"}
    s = session or requests
    delay = cfg.initial_backoff
    for attempt in range(1, cfg.max_retries + 1):
        try:
            r = s.post(
                url,
                json={"uid": uid},
                headers=headers,
                timeout=cfg.request_timeout,
            )
        except requests.RequestException as exc:
            LOG.warning("attempt %s: network error: %s", attempt, exc)
        else:
            if 200 <= r.status_code < 300:
                LOG.info("scan %s -> %s", uid, r.json().get("result"))
                return True
            if r.status_code < 500:
                LOG.error("scan %s failed (non-retryable): %s %s", uid, r.status_code, r.text)
                return False
            LOG.warning("attempt %s: backend 5xx: %s", attempt, r.status_code)
        if attempt < cfg.max_retries:
            sleep(delay)
            delay *= 2
    LOG.error("scan %s dropped after %s attempts", uid, cfg.max_retries)
    return False


def process_lines(
    lines: Iterable[str],
    cfg: Config,
    session: Optional[requests.Session] = None,
    sleep=time.sleep,
) -> int:
    """Process an iterable of serial lines. Returns the number of UIDs posted."""
    posted = 0
    for line in lines:
        uid = parse_uid(line)
        if uid is None:
            if line and line.strip():
                LOG.debug("ignoring non-UID line: %r", line.strip())
            continue
        if post_scan(cfg, uid, session=session, sleep=sleep):
            posted += 1
    return posted


def _load_evdev():
    try:
        from evdev import InputDevice, ecodes
    except ImportError as exc:
        raise RuntimeError(
            "keyboard reader mode requires evdev; install bridge requirements"
        ) from exc
    return InputDevice, ecodes


class LinuxKeyboardReader:
    def __init__(
        self,
        device_path: str,
        terminators: tuple[str, ...] = DEFAULT_KEYBOARD_TERMINATORS,
        idle_flush_seconds: float = DEFAULT_KEYBOARD_IDLE_FLUSH_SECONDS,
        grab_device: bool = False,
    ) -> None:
        self.device_path = device_path
        self.terminators = terminators
        self.idle_flush_seconds = idle_flush_seconds
        self.grab_device = grab_device

    def iter_uids(self) -> Iterable[str]:
        InputDevice, ecodes = _load_evdev()
        buffer = KeyboardTokenBuffer(self.terminators)
        device = InputDevice(self.device_path)
        LOG.info("opening keyboard reader %s (%s)", self.device_path, device.name)
        grabbed = False
        try:
            if self.grab_device:
                device.grab()
                grabbed = True
                LOG.info("keyboard reader grabbed exclusively")
            last_key_at: Optional[float] = None
            while not _shutdown:
                timeout = None
                if (
                    self.idle_flush_seconds > 0
                    and buffer.has_pending
                    and last_key_at is not None
                ):
                    elapsed = time.monotonic() - last_key_at
                    timeout = max(0, self.idle_flush_seconds - elapsed)

                ready, _, _ = select.select([device], [], [], timeout)
                if not ready:
                    uid = buffer.flush()
                    last_key_at = None
                    if uid is not None:
                        yield uid
                    continue

                for event in device.read():
                    if _shutdown:
                        break
                    if event.type != ecodes.EV_KEY or event.value != 1:
                        continue
                    label = ecodes.KEY.get(event.code, "")
                    if isinstance(label, list):
                        label = label[0] if label else ""
                    uid = buffer.feed_key(str(label))
                    if uid is not None:
                        last_key_at = None
                        yield uid
                    elif buffer.has_pending:
                        last_key_at = time.monotonic()
                    else:
                        last_key_at = None
        finally:
            if grabbed:
                device.ungrab()


@lru_cache(maxsize=1)
def _windows_raw_input_types():
    from ctypes import wintypes

    LRESULT = getattr(wintypes, "LRESULT", wintypes.LPARAM)

    class RAWINPUTDEVICE(ctypes.Structure):
        _fields_ = [
            ("usUsagePage", wintypes.USHORT),
            ("usUsage", wintypes.USHORT),
            ("dwFlags", wintypes.DWORD),
            ("hwndTarget", wintypes.HWND),
        ]

    class RAWINPUTDEVICELIST(ctypes.Structure):
        _fields_ = [
            ("hDevice", wintypes.HANDLE),
            ("dwType", wintypes.DWORD),
        ]

    class RAWINPUTHEADER(ctypes.Structure):
        _fields_ = [
            ("dwType", wintypes.DWORD),
            ("dwSize", wintypes.DWORD),
            ("hDevice", wintypes.HANDLE),
            ("wParam", wintypes.WPARAM),
        ]

    class RAWKEYBOARD(ctypes.Structure):
        _fields_ = [
            ("MakeCode", wintypes.USHORT),
            ("Flags", wintypes.USHORT),
            ("Reserved", wintypes.USHORT),
            ("VKey", wintypes.USHORT),
            ("Message", wintypes.UINT),
            ("ExtraInformation", wintypes.ULONG),
        ]

    class RAWINPUTUNION(ctypes.Union):
        _fields_ = [("keyboard", RAWKEYBOARD)]

    class RAWINPUT(ctypes.Structure):
        _fields_ = [
            ("header", RAWINPUTHEADER),
            ("data", RAWINPUTUNION),
        ]

    WNDPROC = ctypes.WINFUNCTYPE(
        LRESULT,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HCURSOR),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    return {
        "RAWINPUTDEVICE": RAWINPUTDEVICE,
        "RAWINPUTDEVICELIST": RAWINPUTDEVICELIST,
        "RAWINPUTHEADER": RAWINPUTHEADER,
        "RAWINPUT": RAWINPUT,
        "WNDCLASSW": WNDCLASSW,
        "WNDPROC": WNDPROC,
    }


def _load_windows_libraries():
    if not is_windows_platform():
        raise RuntimeError("Windows Raw Input keyboard mode requires Windows")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    return user32, kernel32


def _raise_windows_error(action: str) -> None:
    err = ctypes.get_last_error()
    try:
        detail = ctypes.FormatError(err)
    except Exception:
        detail = "Windows API call failed"
    raise OSError(err, f"{action}: {detail}")


def _configure_windows_raw_input_api(user32, kernel32, types) -> None:
    from ctypes import wintypes

    RAWINPUTDEVICE = types["RAWINPUTDEVICE"]
    RAWINPUTDEVICELIST = types["RAWINPUTDEVICELIST"]
    RAWINPUTHEADER = types["RAWINPUTHEADER"]
    WNDCLASSW = types["WNDCLASSW"]
    HRAWINPUT = getattr(wintypes, "HRAWINPUT", wintypes.HANDLE)
    LRESULT = getattr(wintypes, "LRESULT", wintypes.LPARAM)

    user32.GetRawInputDeviceList.argtypes = [
        ctypes.POINTER(RAWINPUTDEVICELIST),
        ctypes.POINTER(wintypes.UINT),
        wintypes.UINT,
    ]
    user32.GetRawInputDeviceList.restype = wintypes.UINT
    user32.GetRawInputDeviceInfoW.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.UINT),
    ]
    user32.GetRawInputDeviceInfoW.restype = wintypes.UINT
    user32.GetRawInputData.argtypes = [
        HRAWINPUT,
        wintypes.UINT,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.UINT),
        wintypes.UINT,
    ]
    user32.GetRawInputData.restype = wintypes.UINT
    user32.RegisterRawInputDevices.argtypes = [
        ctypes.POINTER(RAWINPUTDEVICE),
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.RegisterRawInputDevices.restype = wintypes.BOOL
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DefWindowProcW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.DefWindowProcW.restype = LRESULT
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.UnregisterClassW.restype = wintypes.BOOL
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    # Touch these here so linters and future refactors keep the sizes coupled.
    ctypes.sizeof(RAWINPUTHEADER)


def _get_windows_device_name(user32, h_device) -> str:
    from ctypes import wintypes

    size = wintypes.UINT(0)
    result = user32.GetRawInputDeviceInfoW(
        h_device, RIDI_DEVICENAME, None, ctypes.byref(size)
    )
    if result == ctypes.c_uint(-1).value or size.value <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(size.value)
    result = user32.GetRawInputDeviceInfoW(
        h_device, RIDI_DEVICENAME, buffer, ctypes.byref(size)
    )
    if result == ctypes.c_uint(-1).value:
        return ""
    return buffer.value


def _read_windows_raw_keyboard_event(
    user32, types, lparam
) -> Optional[tuple[str, int, int, int]]:
    from ctypes import wintypes

    RAWINPUTHEADER = types["RAWINPUTHEADER"]
    RAWINPUT = types["RAWINPUT"]

    size = wintypes.UINT(0)
    result = user32.GetRawInputData(
        lparam, RID_INPUT, None, ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER)
    )
    if result == ctypes.c_uint(-1).value or size.value <= 0:
        return None

    buffer = ctypes.create_string_buffer(size.value)
    result = user32.GetRawInputData(
        lparam,
        RID_INPUT,
        buffer,
        ctypes.byref(size),
        ctypes.sizeof(RAWINPUTHEADER),
    )
    if result == ctypes.c_uint(-1).value:
        return None

    raw = ctypes.cast(buffer, ctypes.POINTER(RAWINPUT)).contents
    if raw.header.dwType != RIM_TYPEKEYBOARD:
        return None

    keyboard = raw.data.keyboard
    device_name = _get_windows_device_name(user32, raw.header.hDevice)
    return (
        device_name,
        int(keyboard.VKey),
        int(keyboard.Flags),
        int(keyboard.Message),
    )


def list_windows_keyboard_devices() -> list[KeyboardDeviceInfo]:
    from ctypes import wintypes

    user32, kernel32 = _load_windows_libraries()
    types = _windows_raw_input_types()
    _configure_windows_raw_input_api(user32, kernel32, types)
    RAWINPUTDEVICELIST = types["RAWINPUTDEVICELIST"]

    count = wintypes.UINT(0)
    result = user32.GetRawInputDeviceList(
        None, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST)
    )
    if result == ctypes.c_uint(-1).value:
        _raise_windows_error("GetRawInputDeviceList")
    if count.value == 0:
        return []

    devices = (RAWINPUTDEVICELIST * count.value)()
    result = user32.GetRawInputDeviceList(
        devices, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST)
    )
    if result == ctypes.c_uint(-1).value:
        _raise_windows_error("GetRawInputDeviceList")

    infos: list[KeyboardDeviceInfo] = []
    for device in devices[: result if isinstance(result, int) else count.value]:
        if device.dwType != RIM_TYPEKEYBOARD:
            continue
        name = _get_windows_device_name(user32, device.hDevice)
        infos.append(KeyboardDeviceInfo(path=name, name=name))
    return infos


def _linux_by_id_paths(device_path: str) -> tuple[str, ...]:
    try:
        target = os.path.realpath(device_path)
    except OSError:
        return ()
    matches = []
    for link in glob.glob("/dev/input/by-id/*event-kbd"):
        try:
            if os.path.realpath(link) == target:
                matches.append(link)
        except OSError:
            continue
    return tuple(sorted(matches))


def list_linux_keyboard_devices() -> list[KeyboardDeviceInfo]:
    try:
        from evdev import InputDevice, ecodes, list_devices
    except ImportError as exc:
        raise RuntimeError(
            "keyboard diagnostics on Linux require evdev; install bridge requirements"
        ) from exc

    token_key_codes = {
        ecodes.KEY_ENTER,
        ecodes.KEY_KPENTER,
        *[getattr(ecodes, f"KEY_{i}") for i in range(10)],
        *[
            getattr(ecodes, f"KEY_{chr(code)}")
            for code in range(ord("A"), ord("Z") + 1)
        ],
    }
    infos: list[KeyboardDeviceInfo] = []
    for path in list_devices():
        try:
            device = InputDevice(path)
            caps = device.capabilities()
        except OSError as exc:
            LOG.debug("skipping unreadable input device %s: %s", path, exc)
            continue
        key_codes = set(caps.get(ecodes.EV_KEY, []))
        if not key_codes.intersection(token_key_codes):
            continue
        infos.append(
            KeyboardDeviceInfo(
                path=path,
                name=device.name or "",
                phys=device.phys or "",
                uniq=device.uniq or "",
                by_id_paths=_linux_by_id_paths(path),
            )
        )
    return infos


def list_keyboard_devices() -> list[KeyboardDeviceInfo]:
    if is_windows_platform():
        return list_windows_keyboard_devices()
    if is_linux_platform():
        return list_linux_keyboard_devices()
    raise RuntimeError("keyboard diagnostics are only supported on Linux and Windows")


def print_keyboard_devices() -> None:
    devices = list_keyboard_devices()
    if not devices:
        print("No keyboard-like devices found.")
        return
    for index, device in enumerate(devices, start=1):
        print(f"{index}. {device.display_line()}")


class WindowsRawInputKeyboardReader:
    def __init__(
        self,
        device_filter: str,
        terminators: tuple[str, ...] = DEFAULT_KEYBOARD_TERMINATORS,
        idle_flush_seconds: float = DEFAULT_KEYBOARD_IDLE_FLUSH_SECONDS,
    ) -> None:
        self.device_filter = device_filter
        self.terminators = terminators
        self.idle_flush_seconds = idle_flush_seconds

    def iter_uids(self) -> Iterable[str]:
        from ctypes import wintypes

        user32, kernel32 = _load_windows_libraries()
        types = _windows_raw_input_types()
        _configure_windows_raw_input_api(user32, kernel32, types)
        RAWINPUTDEVICE = types["RAWINPUTDEVICE"]
        WNDCLASSW = types["WNDCLASSW"]

        buffer = KeyboardTokenBuffer(self.terminators)
        pending: deque[str] = deque()
        last_key_at: Optional[float] = None

        def handle_key(label: str) -> None:
            nonlocal last_key_at
            uid = buffer.feed_key(label)
            if uid is not None:
                last_key_at = None
                pending.append(uid)
            elif buffer.has_pending:
                last_key_at = time.monotonic()
            else:
                last_key_at = None

        WNDPROC = types["WNDPROC"]

        @WNDPROC
        def wndproc(hwnd, message, wparam, lparam):
            if message == WM_INPUT:
                event = _read_windows_raw_keyboard_event(user32, types, lparam)
                if event is not None:
                    device_name, vkey, flags, key_message = event
                    if windows_keyboard_device_matches(
                        device_name, self.device_filter
                    ) and _is_windows_keydown(key_message, flags):
                        label = windows_virtual_key_to_key_label(vkey, flags)
                        if label is not None:
                            handle_key(label)
                return 0
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        hinstance = kernel32.GetModuleHandleW(None)
        class_name = f"RFIDBridgeRawInputWindow{os.getpid()}_{id(self)}"
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = wndproc
        window_class.hInstance = hinstance
        window_class.lpszClassName = class_name
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            _raise_windows_error("RegisterClassW")

        hwnd = None
        try:
            hwnd = user32.CreateWindowExW(
                0,
                class_name,
                "RFID Bridge Raw Input",
                0,
                0,
                0,
                0,
                0,
                None,
                None,
                hinstance,
                None,
            )
            if not hwnd:
                _raise_windows_error("CreateWindowExW")

            raw_input_device = RAWINPUTDEVICE(
                HID_USAGE_PAGE_GENERIC,
                HID_USAGE_GENERIC_KEYBOARD,
                RIDEV_INPUTSINK,
                hwnd,
            )
            if not user32.RegisterRawInputDevices(
                ctypes.byref(raw_input_device),
                1,
                ctypes.sizeof(RAWINPUTDEVICE),
            ):
                _raise_windows_error("RegisterRawInputDevices")
            LOG.info(
                "listening for Windows Raw Input keyboard scans matching %r",
                self.device_filter,
            )

            msg = wintypes.MSG()
            while not _shutdown:
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))

                while pending:
                    yield pending.popleft()

                if (
                    self.idle_flush_seconds > 0
                    and buffer.has_pending
                    and last_key_at is not None
                ):
                    elapsed = time.monotonic() - last_key_at
                    if elapsed >= self.idle_flush_seconds:
                        uid = buffer.flush()
                        last_key_at = None
                        if uid is not None:
                            yield uid
                        continue
                    sleep_for = min(0.05, self.idle_flush_seconds - elapsed)
                else:
                    sleep_for = 0.05
                time.sleep(max(0.005, sleep_for))
        finally:
            if hwnd:
                user32.DestroyWindow(hwnd)
            user32.UnregisterClassW(class_name, hinstance)


class KeyboardReader:
    def __init__(
        self,
        device_path: str,
        terminators: tuple[str, ...] = DEFAULT_KEYBOARD_TERMINATORS,
        idle_flush_seconds: float = DEFAULT_KEYBOARD_IDLE_FLUSH_SECONDS,
        grab_device: bool = False,
        windows_device_filter: str = "",
    ) -> None:
        self.device_path = device_path
        self.terminators = terminators
        self.idle_flush_seconds = idle_flush_seconds
        self.grab_device = grab_device
        self.windows_device_filter = windows_device_filter

    def iter_uids(self) -> Iterable[str]:
        if is_windows_platform():
            return WindowsRawInputKeyboardReader(
                self.windows_device_filter,
                self.terminators,
                self.idle_flush_seconds,
            ).iter_uids()
        return LinuxKeyboardReader(
            self.device_path,
            self.terminators,
            self.idle_flush_seconds,
            self.grab_device,
        ).iter_uids()


def _open_serial(cfg: Config) -> serial.Serial:
    LOG.info("opening serial port %s @ %s baud", cfg.serial_port, cfg.baud_rate)
    return serial.Serial(cfg.serial_port, cfg.baud_rate, timeout=1)


def _run_serial(cfg: Config, session: requests.Session) -> None:
    while not _shutdown:
        try:
            ser = _open_serial(cfg)
        except (serial.SerialException, OSError) as exc:
            LOG.error("serial open failed: %s — retrying in %ss", exc, cfg.reconnect_delay)
            time.sleep(cfg.reconnect_delay)
            continue

        with ser:
            LOG.info("serial open on %s", cfg.serial_port)
            try:
                while not _shutdown:
                    raw = ser.readline()
                    if not raw:
                        continue
                    try:
                        line = raw.decode("utf-8", errors="replace")
                    except Exception as exc:
                        LOG.warning("decode error: %s", exc)
                        continue
                    uid = parse_uid(line)
                    if uid is None:
                        if line.strip():
                            LOG.debug("ignoring non-UID line: %r", line.strip())
                        continue
                    post_scan(cfg, uid, session=session)
            except (serial.SerialException, OSError) as exc:
                LOG.error("serial read error: %s — reconnecting", exc)
                time.sleep(cfg.reconnect_delay)
                continue


def _run_keyboard(cfg: Config, session: requests.Session) -> None:
    if cfg.keyboard_grab_device and is_windows_platform():
        LOG.info(
            "KEYBOARD_GRAB_DEVICE is Linux-only; "
            "Windows Raw Input does not suppress keystrokes"
        )
    while not _shutdown:
        reader = KeyboardReader(
            cfg.keyboard_device,
            cfg.keyboard_terminators,
            cfg.keyboard_idle_flush_seconds,
            cfg.keyboard_grab_device,
            cfg.windows_keyboard_device_filter,
        )
        try:
            for uid in reader.iter_uids():
                if _shutdown:
                    break
                post_scan(cfg, uid, session=session)
        except OSError as exc:
            LOG.error(
                "keyboard reader error: %s — retrying in %ss",
                exc,
                cfg.reconnect_delay,
            )
            time.sleep(cfg.reconnect_delay)


def run(cfg: Optional[Config] = None) -> None:
    cfg = cfg or Config.from_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    session = requests.Session()

    if cfg.reader_mode == READER_MODE_SERIAL:
        _run_serial(cfg, session)
    elif cfg.reader_mode == READER_MODE_KEYBOARD:
        _run_keyboard(cfg, session)
    else:
        raise ValueError(f"unsupported reader mode: {cfg.reader_mode}")

    LOG.info("bridge stopped")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="RFID reader to Django HTTP bridge")
    parser.add_argument(
        "--list-keyboards",
        action="store_true",
        help="list keyboard-like reader devices for the current OS and exit",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        if args.list_keyboards:
            print_keyboard_devices()
            return 0
        run()
        return 0
    except KeyError as exc:
        LOG.error("missing required env var: %s (see .env.example)", exc)
        return 1
    except (RuntimeError, ValueError) as exc:
        LOG.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
