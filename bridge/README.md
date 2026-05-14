# RFID reader to HTTP bridge

Reads RFID tokens from one configured reader and forwards them to the Django
backend. Runs as a long-lived process on the kiosk host PC.

Supported modes:

| Mode     | Reader                         | Input source                         |
| -------- | ------------------------------ | ------------------------------------ |
| `serial` | Arduino Uno + RC522, 13.56 MHz | USB serial lines like `UID:A1B2C3D4` |
| `keyboard` | USB keyboard-wedge RFID reader | Keyboard tokens ended by Enter or idle flush |

## Setup

```bash
cd bridge
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # edit READER_MODE / device path / DJANGO_URL / BRIDGE_KEY
python bridge.py --list-keyboards  # optional, only for keyboard-wedge readers
python bridge.py
```

Logs to stdout. Ctrl+C to stop.

## Arduino RC522 mode

Set:

```env
READER_MODE=serial
SERIAL_PORT=/dev/ttyACM0
BAUD_RATE=9600
```

On Windows, the same Arduino mode works through pyserial with a COM port:

```env
READER_MODE=serial
SERIAL_PORT=COM3
BAUD_RATE=9600
```

Typical serial ports:

| OS      | Port                             |
| ------- | -------------------------------- |
| Linux   | `/dev/ttyACM0` or `/dev/ttyUSB0` |
| macOS   | `/dev/cu.usbmodemXXXX`           |
| Windows | `COM3`, `COM4`, ...              |

Unplug and re-plug the Arduino to spot the device name (Linux: `dmesg --follow` shows the port). Must match the port used by the Arduino IDE.

## Optional keyboard-wedge mode

Set:

```env
READER_MODE=keyboard
KEYBOARD_DEVICE=/dev/input/event0
KEYBOARD_TERMINATOR=ENTER
```

On Windows, use Raw Input device filtering instead of a Linux event path:

```env
READER_MODE=keyboard
WINDOWS_KEYBOARD_DEVICE_FILTER=<distinctive device-name substring>
KEYBOARD_TERMINATOR=ENTER
```

Run `python bridge.py --list-keyboards` to inspect candidate devices.

## Protocol

- In `serial` mode, any line matching `UID:<HEX>` is accepted.
- In `keyboard` mode, alphanumeric reader tokens are accepted after Enter or idle flush.
- Accepted tokens are uppercased and sent as:
  - `POST {DJANGO_URL}/api/scan/`
  - Header: `X-Bridge-Key: {BRIDGE_KEY}`
  - Body: `{"uid": "<UPPERCASE_TOKEN>"}`
- Other serial lines or non-alphanumeric keyboard tokens are ignored.

## Behavior

| Situation | What the bridge does |
|---|---|
| Arduino unplugged | Open fails, waits `RECONNECT_DELAY_SECONDS`, retries forever |
| Arduino disappears mid-run | Read error, logged, reconnect |
| Linux keyboard reader missing | Open fails, waits `RECONNECT_DELAY_SECONDS`, retries forever |
| Linux keyboard reader unplugged mid-run | Read error, logged, reconnect |
| Backend 5xx | Retries with exponential backoff up to `MAX_RETRIES`, then drops the tap and logs |
| Backend 4xx | Logs the error and drops the tap (no retry) |
| Unreadable line | Logged at DEBUG, skipped |

Dropped taps are NOT queued to disk in this prototype. For production, add a local SQLite queue as future work.

## Tests

```bash
pytest -q
```

Tests use `responses` to mock the backend and never open real serial or keyboard devices.
