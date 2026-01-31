# Sunflower

Make Govee LED mimic the sun.

## Setup (new users)

Recommended location on macOS (avoids launchd permission issues):
- `/Users/you/Applications/sunflower`

If you already cloned to Desktop, move it first:

```
mv ~/Desktop/sunflower ~/Applications/sunflower
```

Run all commands below from the repo directory:

```
cd ~/Applications/sunflower
```

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

4) List devices and paste device IDs into `config.json`

```
python3 sun_mimic.py --list-devices
```

5) Run a single-device test

```
python3 sun_mimic.py --test --test-device YOUR_DEVICE_ID --no-test-sync
```

## Config

`config.json` fields:
- `devices`: list of `{ "device": "MAC", "model": "H6008" }`. If empty, all devices are targeted.
- `poll_interval_seconds`: how often to apply the schedule.
- `command_delay_seconds`: delay between API commands to reduce rate limits.
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

## launchd (run in background)

Install the macOS service (requires the repo not on Desktop):

```
python3 sun_mimic.py --install-service
```

This installs a `launchd` agent that runs in the background and restarts automatically.
It uses a wrapper script that loads `.env` so your API key is available.

Verify the service is running:

```
launchctl print gui/$(id -u)/com.joshuascottpaul.sunflower
tail -n 50 ~/Library/Logs/sunflower/sun_mimic.launchd.log
tail -n 50 ~/Library/Logs/sunflower/sun_mimic.launchd.err
```

## Troubleshooting

- Service won’t start on Desktop: move the repo to a non-protected path like `/Users/you/Applications/sunflower` and reinstall the service.
- Missing dependency in logs: recreate the venv and reinstall dependencies.
  - `python3 -m venv .venv`
  - `. .venv/bin/activate`
  - `pip install -r requirements.txt`
  - Note: if you moved the repo, you must recreate the venv in the new location.
- View logs:
  - `tail -n 50 ~/Library/Logs/sunflower/sun_mimic.launchd.log`
  - `tail -n 50 ~/Library/Logs/sunflower/sun_mimic.launchd.err`
- Hitting 429s: increase `command_delay_seconds` and/or `poll_interval_seconds`.

## Future work: 429 mitigation strategies

If the Govee API rate limits (429) are frequent, consider these approaches:

1) Increase `command_delay_seconds` to slow per-device commands.
   - Pros: simple config change.
   - Cons: per-cycle updates become slow, so the schedule lags.

2) Increase `poll_interval_seconds` to run fewer cycles.
   - Pros: fewer total calls.
   - Cons: the sun curve updates less often (stepwise changes).

3) Add thresholded updates (code change).
   - Only send updates when brightness/color temp changes exceed a threshold.
   - Pros: best balance of smoothness vs. API usage.
   - Cons: requires tuning and implementation work.

## Logs

Logs are written to `~/Library/Logs/sunflower/sun_mimic.log`.
