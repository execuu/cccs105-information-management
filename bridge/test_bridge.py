import requests
import responses
import pytest

import bridge
from bridge import (
    Config,
    KeyboardTokenBuffer,
    RI_KEY_E0,
    normalize_uid,
    parse_bool,
    parse_keyboard_token,
    parse_uid,
    post_scan,
    process_lines,
    windows_keyboard_device_matches,
    windows_virtual_key_to_key_label,
)


def make_cfg(**overrides) -> Config:
    defaults = dict(
        serial_port="/dev/null",
        baud_rate=9600,
        django_url="http://example.test",
        bridge_key="k",
        request_timeout=1.0,
        max_retries=3,
        initial_backoff=0.01,
        reconnect_delay=0.01,
    )
    defaults.update(overrides)
    return Config(**defaults)


def set_base_env(monkeypatch, tmp_path, *, reader_mode="serial"):
    monkeypatch.chdir(tmp_path)
    for name in (
        "READER_MODE",
        "SERIAL_PORT",
        "BAUD_RATE",
        "KEYBOARD_DEVICE",
        "KEYBOARD_TERMINATOR",
        "KEYBOARD_IDLE_FLUSH_SECONDS",
        "KEYBOARD_GRAB_DEVICE",
        "WINDOWS_KEYBOARD_DEVICE_FILTER",
        "DJANGO_URL",
        "BRIDGE_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("READER_MODE", reader_mode)
    monkeypatch.setenv("SERIAL_PORT", "")
    monkeypatch.setenv("KEYBOARD_DEVICE", "")
    monkeypatch.setenv("WINDOWS_KEYBOARD_DEVICE_FILTER", "")
    monkeypatch.setenv("DJANGO_URL", "http://example.test")
    monkeypatch.setenv("BRIDGE_KEY", "k")


class TestParseUid:
    def test_valid(self):
        assert parse_uid("UID:ab12\n") == "AB12"

    def test_valid_trailing_spaces(self):
        assert parse_uid("UID:DEADBEEF  \n") == "DEADBEEF"

    def test_ready_line_ignored(self):
        assert parse_uid("READY:rfid_reader\n") is None

    def test_blank(self):
        assert parse_uid("") is None

    def test_invalid_chars(self):
        assert parse_uid("UID:ZZZZ") is None


class TestNormalizeUid:
    def test_strips_and_uppercases_alphanumeric_tokens(self):
        assert normalize_uid("  abcd1234 \n") == "ABCD1234"

    def test_rejects_blank_tokens(self):
        assert normalize_uid("   ") is None

    def test_rejects_punctuation(self):
        assert normalize_uid("12-34") is None


class TestParseKeyboardToken:
    def test_accepts_bare_reader_token(self):
        assert parse_keyboard_token("0001234567\n") == "0001234567"

    def test_accepts_alphanumeric_reader_token(self):
        assert parse_keyboard_token("ab12cd") == "AB12CD"

    def test_does_not_accept_arduino_prefix(self):
        assert parse_keyboard_token("UID:AB12") is None


class TestParseBool:
    def test_accepts_true_values(self):
        assert parse_bool("true") is True
        assert parse_bool("1") is True
        assert parse_bool("yes") is True

    def test_accepts_false_values(self):
        assert parse_bool("false", default=True) is False
        assert parse_bool("0", default=True) is False
        assert parse_bool("no", default=True) is False

    def test_empty_uses_default(self):
        assert parse_bool("", default=True) is True
        assert parse_bool(None, default=False) is False


class TestConfigFromEnv:
    def test_serial_mode_accepts_windows_com_port(self, monkeypatch, tmp_path):
        set_base_env(monkeypatch, tmp_path, reader_mode="serial")
        monkeypatch.setattr(bridge.sys, "platform", "win32")
        monkeypatch.setenv("SERIAL_PORT", "COM3")

        cfg = Config.from_env()

        assert cfg.reader_mode == "serial"
        assert cfg.serial_port == "COM3"

    def test_scanner_code_is_not_required(self, monkeypatch, tmp_path):
        set_base_env(monkeypatch, tmp_path, reader_mode="serial")
        monkeypatch.setenv("SERIAL_PORT", "COM3")

        cfg = Config.from_env()

        assert cfg.scanner_code == ""

    def test_linux_keyboard_mode_requires_keyboard_device(self, monkeypatch, tmp_path):
        set_base_env(monkeypatch, tmp_path, reader_mode="keyboard")
        monkeypatch.setattr(bridge.sys, "platform", "linux")

        with pytest.raises(KeyError) as exc:
            Config.from_env()

        assert exc.value.args == ("KEYBOARD_DEVICE",)

    def test_windows_keyboard_mode_requires_device_filter(self, monkeypatch, tmp_path):
        set_base_env(monkeypatch, tmp_path, reader_mode="keyboard")
        monkeypatch.setattr(bridge.sys, "platform", "win32")

        with pytest.raises(KeyError) as exc:
            Config.from_env()

        assert exc.value.args == ("WINDOWS_KEYBOARD_DEVICE_FILTER",)

    def test_windows_keyboard_mode_does_not_require_linux_device(
        self, monkeypatch, tmp_path
    ):
        set_base_env(monkeypatch, tmp_path, reader_mode="keyboard")
        monkeypatch.setattr(bridge.sys, "platform", "win32")
        monkeypatch.setenv("WINDOWS_KEYBOARD_DEVICE_FILTER", "VID_1234")

        cfg = Config.from_env()

        assert cfg.keyboard_device == ""
        assert cfg.windows_keyboard_device_filter == "VID_1234"


class TestKeyboardTokenBuffer:
    def test_emits_one_uid_when_enter_is_pressed(self):
        buf = KeyboardTokenBuffer()
        emitted = [
            buf.feed_key("KEY_0"),
            buf.feed_key("KEY_1"),
            buf.feed_key("KEY_A"),
            buf.feed_key("KEY_B"),
            buf.feed_key("KEY_ENTER"),
        ]
        assert emitted == [None, None, None, None, "01AB"]

    def test_keypad_digits_and_keypad_enter_are_supported(self):
        buf = KeyboardTokenBuffer()
        emitted = [
            buf.feed_key("KEY_KP1"),
            buf.feed_key("KEY_KP2"),
            buf.feed_key("KEY_KPENTER"),
        ]
        assert emitted[-1] == "12"

    def test_backspace_removes_last_character(self):
        buf = KeyboardTokenBuffer()
        buf.feed_key("KEY_1")
        buf.feed_key("KEY_2")
        buf.feed_key("KEY_BACKSPACE")
        assert buf.feed_key("KEY_ENTER") == "1"

    def test_blank_enter_does_not_emit(self):
        buf = KeyboardTokenBuffer()
        assert buf.feed_key("KEY_ENTER") is None

    def test_flush_emits_pending_uid_without_enter(self):
        buf = KeyboardTokenBuffer()
        buf.feed_key("KEY_2")
        buf.feed_key("KEY_3")
        buf.feed_key("KEY_1")
        assert buf.flush() == "231"
        assert buf.flush() is None


class TestWindowsKeyboardMapping:
    def test_maps_digits_letters_and_controls_to_existing_key_labels(self):
        assert windows_virtual_key_to_key_label(0x31) == "KEY_1"
        assert windows_virtual_key_to_key_label(0x41) == "KEY_A"
        assert windows_virtual_key_to_key_label(0x0D) == "KEY_ENTER"
        assert windows_virtual_key_to_key_label(0x08) == "KEY_BACKSPACE"
        assert windows_virtual_key_to_key_label(0x1B) == "KEY_ESC"

    def test_maps_keypad_digits_and_extended_enter(self):
        assert windows_virtual_key_to_key_label(0x60) == "KEY_KP0"
        assert windows_virtual_key_to_key_label(0x69) == "KEY_KP9"
        assert windows_virtual_key_to_key_label(0x0D, flags=RI_KEY_E0) == "KEY_KPENTER"

    def test_ignores_non_token_keys(self):
        assert windows_virtual_key_to_key_label(0x10) is None


class TestWindowsKeyboardDeviceFilter:
    def test_matches_case_insensitive_substrings(self):
        device_name = r"\\?\HID#VID_1234&PID_ABCD#7&33A#0#{keyboard}"

        assert windows_keyboard_device_matches(device_name, "vid_1234")
        assert windows_keyboard_device_matches(device_name, "PID_ABCD")

    def test_matches_any_semicolon_or_comma_separated_filter(self):
        device_name = r"\\?\HID#VID_1234&PID_ABCD#7&33A#0#{keyboard}"

        assert windows_keyboard_device_matches(device_name, "FC Reader; VID_1234")
        assert windows_keyboard_device_matches(device_name, "FC Reader, PID_ABCD")

    def test_rejects_blank_or_unmatched_filters(self):
        device_name = r"\\?\HID#VID_1234&PID_ABCD#7&33A#0#{keyboard}"

        assert not windows_keyboard_device_matches(device_name, "")
        assert not windows_keyboard_device_matches(device_name, "VID_9999")


class TestPostScan:
    @responses.activate
    def test_posts_uid_with_header(self):
        cfg = make_cfg()
        responses.post(
            "http://example.test/api/scan/",
            json={"result": "checked_in"},
            status=200,
        )
        assert post_scan(cfg, "AB12") is True
        req = responses.calls[0].request
        assert req.headers["X-Bridge-Key"] == "k"
        import json
        assert json.loads((req.body or b"").decode()) == {"uid": "AB12"}

    @responses.activate
    def test_non_retryable_4xx(self):
        cfg = make_cfg()
        responses.post(
            "http://example.test/api/scan/", json={"detail": "nope"}, status=403
        )
        assert post_scan(cfg, "AB12") is False
        assert len(responses.calls) == 1  # no retry

    @responses.activate
    def test_retries_5xx_then_succeeds(self):
        cfg = make_cfg()
        responses.post("http://example.test/api/scan/", status=500)
        responses.post("http://example.test/api/scan/", status=500)
        responses.post(
            "http://example.test/api/scan/",
            json={"result": "checked_in"},
            status=200,
        )
        slept = []
        assert post_scan(cfg, "AB12", sleep=slept.append) is True
        assert len(responses.calls) == 3
        assert slept == [0.01, 0.02]  # exponential

    @responses.activate
    def test_gives_up_after_max_retries(self):
        cfg = make_cfg(max_retries=2)
        responses.post("http://example.test/api/scan/", status=500)
        responses.post("http://example.test/api/scan/", status=500)
        slept = []
        assert post_scan(cfg, "AB12", sleep=slept.append) is False
        assert len(responses.calls) == 2

    def test_network_error_retries_and_fails(self):
        cfg = make_cfg(max_retries=2)
        session = requests.Session()

        class Boom:
            def post(self, *a, **kw):
                raise requests.ConnectionError("nope")

        slept = []
        assert post_scan(cfg, "AB12", session=Boom(), sleep=slept.append) is False
        assert len(slept) == 1


class TestProcessLines:
    @responses.activate
    def test_mixed_stream(self):
        cfg = make_cfg()
        responses.post(
            "http://example.test/api/scan/",
            json={"result": "checked_in"},
            status=200,
        )
        lines = [
            "READY:rfid_reader\n",
            "garbage",
            "UID:AB12\n",
            "",
            "UID:CD34\n",
        ]
        assert process_lines(lines, cfg, sleep=lambda s: None) == 2
        assert len(responses.calls) == 2
