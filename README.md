# Sunflower

Make Govee LED mimic the sun.

## Setup

1) Install dependencies

```
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

2) Create `.env` with your API key

```
cp .env.example .env
```

3) Copy and edit `config.json` with your device and schedule.

```
cp config.json.example config.json
```

## Config

`config.json` fields:
- `devices`: list of `{ "device": "MAC", "model": "H6008" }`. If empty, all devices are targeted.
- `poll_interval_seconds`: how often to apply the schedule.
- `log_level`: `DEBUG`, `INFO`, `WARNING`, or `ERROR`.
- `test_blink_all`: when `true`, `--test` blinks all devices (may hit rate limits).
- `test_blink_times`: number of on/off cycles for `--test` (default 1).
- `test_blink_delay_seconds`: delay between test commands; increase to avoid rate limits.
- `test_sync_state`: set to `on` or `off` to align devices before testing.
Note: with 4 devices and a 60s delay, `--test` can take 10+ minutes.
- `schedule`: ordered anchors with `time`, `brightness`, `color_temp_k`.

## Usage

List devices:

```
python3 sun_mimic.py --list-devices
```

Test (blink devices 5 times to confirm connectivity):

```
python3 sun_mimic.py --test
```

Test a single device without syncing:

```
python3 sun_mimic.py --test --test-device A7:15:D0:C9:07:72:27:76 --no-test-sync
```

Apply once:

```
python3 sun_mimic.py --once
```

Sync all devices on/off:

```
python3 sun_mimic.py --sync-state on
```

Run continuously:

```
python3 sun_mimic.py
```

## launchd

Install the macOS service:

```
python3 sun_mimic.py --install-service
```

This installs a `launchd` agent that runs in the background and restarts automatically.
It uses a wrapper script that loads `.env` so your API key is available.

## Logs

Logs are written to `~/Library/Logs/sunflower/sun_mimic.log`.
