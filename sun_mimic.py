#!/usr/bin/env python3
import argparse
import asyncio
import json
import logging
import os
import sys
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from govee_api_laggat import Govee, GoveeError, GoveeNoLearningStorage
except Exception as ex:  # pragma: no cover - import error path
    print("Missing dependency: govee_api_laggat. Install requirements first.")
    raise


LOG_DIR = Path.home() / "Library" / "Logs" / "sunflower"
DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.json"
DEFAULT_POLL_SECONDS = 60
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_COMMAND_DELAY_SECONDS = 10


@dataclass
class SchedulePoint:
    minutes: int
    brightness: int
    color_temp_k: int


class AuthError(RuntimeError):
    pass


def load_env(env_path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not env_path.exists():
        return data
    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def ensure_private_permissions(path: Path) -> None:
    if not path.exists():
        return
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def load_config(path: Path) -> Dict[str, Any]:
    ensure_private_permissions(path)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_time_to_minutes(time_str: str) -> int:
    parts = time_str.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format: {time_str}")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"Invalid time value: {time_str}")
    return hour * 60 + minute


def build_schedule(points: List[Dict[str, Any]]) -> List[SchedulePoint]:
    schedule: List[SchedulePoint] = []
    for point in points:
        schedule.append(
            SchedulePoint(
                minutes=parse_time_to_minutes(point["time"]),
                brightness=int(point["brightness"]),
                color_temp_k=int(point["color_temp_k"]),
            )
        )
    schedule.sort(key=lambda p: p.minutes)
    return schedule


def clamp(value: float, min_value: int, max_value: int) -> int:
    return int(max(min_value, min(max_value, round(value))))


def interpolate(schedule: List[SchedulePoint], now_minutes: int) -> Tuple[int, int]:
    if not schedule:
        raise ValueError("Schedule is empty")
    if now_minutes <= schedule[0].minutes:
        return schedule[0].brightness, schedule[0].color_temp_k
    if now_minutes >= schedule[-1].minutes:
        return schedule[-1].brightness, schedule[-1].color_temp_k

    for idx in range(len(schedule) - 1):
        start = schedule[idx]
        end = schedule[idx + 1]
        if start.minutes <= now_minutes <= end.minutes:
            span = end.minutes - start.minutes
            alpha = (now_minutes - start.minutes) / span if span else 0.0
            brightness = start.brightness + (end.brightness - start.brightness) * alpha
            color_temp_k = start.color_temp_k + (end.color_temp_k - start.color_temp_k) * alpha
            return clamp(brightness, 0, 100), clamp(color_temp_k, 2000, 9000)
    return schedule[-1].brightness, schedule[-1].color_temp_k


def setup_logging(log_level: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "sun_mimic.log"

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler(sys.stdout),
        ],
    )


def get_api_key(env_path: Path) -> str:
    ensure_private_permissions(env_path)
    env_data = load_env(env_path)
    api_key = env_data.get("GOVEE_API_KEY") or os.environ.get("GOVEE_API_KEY", "")
    if not api_key:
        raise RuntimeError("Missing GOVEE_API_KEY in .env or environment")
    return api_key


def get_allowed_ssids(env_path: Path) -> List[str]:
    env_data = load_env(env_path)
    raw = env_data.get("ALLOWED_SSID") or os.environ.get("ALLOWED_SSID", "")
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def get_probe_ips(env_path: Path) -> List[str]:
    env_data = load_env(env_path)
    raw = env_data.get("PROBE_IPS") or os.environ.get("PROBE_IPS", "")
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def get_probe_timeout_ms(env_path: Path) -> int:
    env_data = load_env(env_path)
    raw = env_data.get("PROBE_TIMEOUT_MS") or os.environ.get("PROBE_TIMEOUT_MS", "")
    if not raw:
        return 1000
    try:
        return max(200, int(raw))
    except ValueError:
        return 1000


def probe_ips_reachable(ips: List[str], timeout_ms: int) -> bool:
    if not ips:
        return True
    for ip in ips:
        try:
            result = subprocess.run(
                ["/sbin/ping", "-n", "-c", "1", ip],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout_ms / 1000.0,
                check=False,
            )
            if result.returncode == 0:
                return True
        except subprocess.TimeoutExpired:
            continue
    return False

def get_current_ssid() -> str:
    try:
        ports = subprocess.check_output(["networksetup", "-listallhardwareports"], text=True)
    except Exception:
        return ""

    device = ""
    for block in ports.split("\n\n"):
        if "Hardware Port: Wi-Fi" in block:
            for line in block.splitlines():
                line = line.strip()
                if line.startswith("Device:"):
                    device = line.split(":", 1)[-1].strip()
                    break
    if not device:
        return ""

    try:
        output = subprocess.check_output(["networksetup", "-getairportnetwork", device], text=True)
    except Exception:
        return ""

    if ":" not in output:
        return ""
    return output.split(":", 1)[-1].strip()


def device_matches(target: Dict[str, str], device: Any) -> bool:
    target_device = target.get("device", "").lower()
    target_model = target.get("model", "").lower()
    actual_device = str(getattr(device, "device", "")).lower()
    actual_model = str(getattr(device, "model", "")).lower()
    if target_device and target_device != actual_device:
        return False
    if target_model and target_model != actual_model:
        return False
    return True


def select_devices(config_devices: List[Dict[str, str]], hub_devices: List[Any]) -> List[Any]:
    if not config_devices:
        return hub_devices
    selected = []
    for device in hub_devices:
        if any(device_matches(target, device) for target in config_devices):
            selected.append(device)
    return selected


def is_auth_error(message: str) -> bool:
    lowered = message.lower()
    return "401" in lowered or "unauthorized" in lowered


def is_rate_limit_error(message: str) -> bool:
    lowered = message.lower()
    return "429" in lowered or "rate" in lowered


def parse_rate_limit_seconds(message: str) -> Optional[int]:
    import re

    lowered = message.lower()
    match = re.search(r"retry in\\s+([\\d.]+)\\s*(ms|s|sec|secs|seconds)?", lowered)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2) or "s"
    if unit == "ms":
        return max(1, int((value + 999) // 1000))
    return max(1, int(round(value)))


def rate_limit_wait_seconds(hub: Govee, message: Optional[str] = None, default_seconds: int = 60) -> int:
    wait_seconds = None
    if message:
        wait_seconds = parse_rate_limit_seconds(message)
    if wait_seconds is None:
        reset_seconds = getattr(hub, "rate_limit_reset_seconds", None)
        if reset_seconds:
            wait_seconds = int(reset_seconds)
        else:
            reset_at = getattr(hub, "rate_limit_reset", None)
            if reset_at:
                try:
                    wait_seconds = max(1, int(reset_at - datetime.now().timestamp()))
                except Exception:
                    wait_seconds = None
        if wait_seconds is not None and wait_seconds < 5:
            wait_seconds = default_seconds
    if wait_seconds is None:
        wait_seconds = default_seconds
    return max(1, wait_seconds)


async def handle_api_error(hub: Govee, error: Any, context: str) -> None:
    if not error:
        return
    message = str(error)
    if is_auth_error(message):
        raise AuthError("Authentication failed (401).")
    if is_rate_limit_error(message):
        wait_seconds = rate_limit_wait_seconds(hub, message)
        logging.warning("Rate limit hit during %s, waiting %ss before retry", context, wait_seconds)
        await asyncio.sleep(wait_seconds)
        return
    logging.warning("API error during %s: %s", context, message)


async def retry_call(hub: Govee, func, *args, context: str, retries: int = 3):
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            result, err = await func(*args)
            if err:
                await handle_api_error(hub, err, context)
                if is_rate_limit_error(str(err)) and attempt < retries:
                    continue
            return result, err
        except GoveeError as ex:
            message = str(ex)
            if is_auth_error(message):
                raise AuthError("Authentication failed (401).") from ex
            if is_rate_limit_error(message) and attempt < retries:
                await asyncio.sleep(rate_limit_wait_seconds(hub, message))
                continue
            last_error = ex
            await asyncio.sleep(attempt)
    if last_error:
        raise last_error
    return None, None


async def list_devices(api_key: str) -> None:
    hub = await Govee.create(api_key, learning_storage=GoveeNoLearningStorage())
    try:
        devices, err = await hub.get_devices()
        if err:
            await handle_api_error(hub, err, "get_devices")
        for device in devices or []:
            name = getattr(device, "device_name", "") or getattr(device, "name", "")
            print(
                f"device={getattr(device, 'device', '')} model={getattr(device, 'model', '')} name={name}"
            )
    finally:
        await hub.close()


async def list_states(api_key: str, config: Dict[str, Any]) -> None:
    config_devices = config.get("devices", [])
    hub = await Govee.create(api_key, learning_storage=GoveeNoLearningStorage())
    try:
        devices, err = await hub.get_devices()
        if err:
            await handle_api_error(hub, err, "get_devices")
        selected = select_devices(config_devices, devices or [])
        selected_ids = {getattr(d, "device", "") for d in selected}
        states = await hub.get_states()
        for state in states or []:
            if selected_ids and getattr(state, "device", "") not in selected_ids:
                continue
            print(
                "device={device} model={model} online={online} power={power} "
                "brightness={brightness} color_temp_k={color_temp} color={color} error={error}".format(
                    device=getattr(state, "device", ""),
                    model=getattr(state, "model", ""),
                    online=getattr(state, "online", ""),
                    power=getattr(state, "power_state", ""),
                    brightness=getattr(state, "brightness", ""),
                    color_temp=getattr(state, "color_temp", ""),
                    color=getattr(state, "color", ""),
                    error=getattr(state, "error", ""),
                )
            )
    finally:
        await hub.close()


async def blink_devices(
    hub: Govee,
    devices: List[Any],
    times: int = 5,
    delay_seconds: int = 60,
    max_retries: int = 2,
) -> None:
    if not devices:
        logging.warning("No devices to blink")
        return
    async def try_call(func, device, context: str) -> bool:
        attempts = 0
        while attempts <= max_retries:
            _, err = await func(device)
            if not err:
                return True
            message = str(err)
            if is_rate_limit_error(message):
                wait_seconds = rate_limit_wait_seconds(hub, message)
                logging.warning(
                    "Rate limit hit during %s. Waiting %ss before retry.",
                    context,
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)
                attempts += 1
                continue
            logging.warning("API error during %s: %s", context, message)
            return False
        return False

    for cycle in range(1, times + 1):
        for device in devices:
            device_id = getattr(device, "device", "")
            logging.info("Blink cycle %d/%d: OFF %s", cycle, times, device_id)
            ok = await try_call(hub.turn_off, device, "blink_off")
            if not ok:
                return
            await asyncio.sleep(delay_seconds)
        for device in devices:
            device_id = getattr(device, "device", "")
            logging.info("Blink cycle %d/%d: ON %s", cycle, times, device_id)
            ok = await try_call(hub.turn_on, device, "blink_on")
            if not ok:
                return
            await asyncio.sleep(delay_seconds)


async def sync_power_state(
    hub: Govee, devices: List[Any], state: Optional[str], delay_seconds: int
) -> None:
    if not devices or not state:
        return
    target_state = state.lower()
    if target_state not in {"on", "off"}:
        return
    logging.info("Syncing %d device(s) to %s", len(devices), target_state)
    for device in devices:
        device_id = getattr(device, "device", "")
        logging.info("Sync %s -> %s", device_id, target_state)
        if target_state == "on":
            await retry_call(hub, hub.turn_on, device, context="sync_on")
        else:
            await retry_call(hub, hub.turn_off, device, context="sync_off")
        logging.info("Sync complete %s (waiting %ss)", device_id, delay_seconds)
        await asyncio.sleep(delay_seconds)


async def apply_schedule(
    api_key: str,
    config: Dict[str, Any],
    once: bool,
    dry_run: bool,
    args_sync_state: Optional[str] = None,
    args_test_device: Optional[str] = None,
    args_no_test_sync: bool = False,
) -> None:
    schedule = build_schedule(config.get("schedule", []))
    poll_interval = int(config.get("poll_interval_seconds", DEFAULT_POLL_SECONDS))
    config_devices = config.get("devices", [])
    command_delay = int(config.get("command_delay_seconds", DEFAULT_COMMAND_DELAY_SECONDS))
    test_blink_all = bool(config.get("test_blink_all", False))
    test_blink_times = int(config.get("test_blink_times", 1))
    test_blink_delay = int(config.get("test_blink_delay_seconds", 60))
    test_sync_state = config.get("test_sync_state", "on")

    hub = await Govee.create(api_key, learning_storage=GoveeNoLearningStorage())
    try:
        devices, err = await hub.get_devices()
        if err:
            await handle_api_error(hub, err, "get_devices")
        selected = select_devices(config_devices, devices or [])
        if not selected:
            logging.warning("No devices matched configuration")
        if args_sync_state:
            await sync_power_state(hub, selected, args_sync_state, test_blink_delay)
            return
        if dry_run:
            if args_test_device:
                selected = [d for d in selected if str(getattr(d, "device", "")) == args_test_device]
            if not args_no_test_sync:
                await sync_power_state(hub, selected, test_sync_state, test_blink_delay)
            if test_blink_all:
                logging.info(
                    "Test mode: blinking %d device(s) to confirm connectivity",
                    len(selected),
                )
                await blink_devices(
                    hub,
                    selected,
                    times=test_blink_times,
                    delay_seconds=test_blink_delay,
                )
            else:
                for device in selected:
                    device_id = getattr(device, "device", "")
                    logging.info("Test mode: blinking device %s", device_id)
                    await blink_devices(
                        hub,
                        [device],
                        times=test_blink_times,
                        delay_seconds=test_blink_delay,
                    )
                    logging.info("Test mode: completed device %s", device_id)
            return
        while True:
            now = datetime.now()
            now_minutes = now.hour * 60 + now.minute
            brightness, color_temp_k = interpolate(schedule, now_minutes)
            logging.info(
                "Applying schedule at %02d:%02d: brightness=%s color_temp_k=%s",
                now.hour,
                now.minute,
                brightness,
                color_temp_k,
            )
            for device in selected:
                await retry_call(hub, hub.turn_on, device, context="turn_on")
                await asyncio.sleep(command_delay)
                await retry_call(hub, hub.set_color_temp, device, color_temp_k, context="set_color_temp")
                await asyncio.sleep(command_delay)
                await retry_call(hub, hub.set_brightness, device, brightness, context="set_brightness")
                await asyncio.sleep(command_delay)
            if once:
                return
            await asyncio.sleep(poll_interval)
    finally:
        await hub.close()


def install_service() -> None:
    import subprocess

    script_path = Path(__file__).parent / "scripts" / "install_service.sh"
    if not script_path.exists():
        raise RuntimeError("install_service.sh not found")
    subprocess.run(["/bin/sh", str(script_path)], check=True)


def uninstall_service() -> None:
    import subprocess

    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.joshuascottpaul.sunflower.plist"
    if not plist_path.exists():
        print("Service not installed.")
        return
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path)], check=False)
    plist_path.unlink(missing_ok=True)
    print(f"Uninstalled {plist_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sun Mimic - adjust brightness and color temperature over the day to mimic sunrise/sunset"
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to config.json")
    parser.add_argument("--list-devices", action="store_true", help="List devices and exit")
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show probe/IP gating status and device states (power, brightness, color temp) and exit",
    )
    parser.add_argument("--test", action="store_true", help="Log the next scheduled values without applying")
    parser.add_argument("--test-device", help="Only test a single device ID")
    parser.add_argument("--no-test-sync", action="store_true", help="Skip pre-test sync state")
    parser.add_argument("--once", action="store_true", help="Apply the schedule once and exit")
    parser.add_argument("--sync-state", choices=["on", "off"], help="Sync all devices to on/off and exit")
    parser.add_argument(
        "--install-service",
        action="store_true",
        help=(
            "Install launchd service (restart: launchctl bootout gui/$(id -u) "
            "~/Library/LaunchAgents/com.joshuascottpaul.sunflower.plist && "
            "launchctl bootstrap gui/$(id -u) "
            "~/Library/LaunchAgents/com.joshuascottpaul.sunflower.plist)"
        ),
    )
    parser.add_argument("--uninstall-service", action="store_true", help="Uninstall launchd service")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config)

    if args.install_service:
        install_service()
        return
    if args.uninstall_service:
        uninstall_service()
        return

    env_path = Path(__file__).parent / ".env"
    api_key = get_api_key(env_path)
    allowed_ssids = get_allowed_ssids(env_path)
    current_ssid = get_current_ssid()
    probe_ips = get_probe_ips(env_path)
    probe_timeout_ms = get_probe_timeout_ms(env_path)
    probe_ok = probe_ips_reachable(probe_ips, probe_timeout_ms)

    config = load_config(config_path)
    setup_logging(config.get("log_level", DEFAULT_LOG_LEVEL))

    try:
        if args.list_devices:
            asyncio.run(list_devices(api_key))
            return
        if args.status:
            print(f"ssid={current_ssid or 'unknown'} allowed={','.join(allowed_ssids) or 'any'}")
            if probe_ips:
                print(f"probe_ips={','.join(probe_ips)} probe_ok={probe_ok}")
            if allowed_ssids and current_ssid not in allowed_ssids:
                print("ssid not allowed; skipping API calls")
                return
            if probe_ips and not probe_ok:
                print("probe not reachable; skipping API calls")
                return
            asyncio.run(list_states(api_key, config))
            return
        if allowed_ssids and current_ssid not in allowed_ssids:
            logging.warning(
                "Current SSID '%s' not in allowed list. Exiting without API calls.",
                current_ssid or "unknown",
            )
            return
        if probe_ips and not probe_ok:
            logging.warning("Probe IPs not reachable. Exiting without API calls.")
            return
        if args.sync_state:
            asyncio.run(
                apply_schedule(
                    api_key,
                    config,
                    once=True,
                    dry_run=False,
                    args_sync_state=args.sync_state,
                )
            )
            return

        dry_run = bool(args.test)
        once = bool(args.once) or dry_run
        asyncio.run(
            apply_schedule(
                api_key,
                config,
                once=once,
                dry_run=dry_run,
                args_test_device=args.test_device,
                args_no_test_sync=bool(args.no_test_sync),
            )
        )
    except AuthError as ex:
        logging.error(str(ex))
        sys.exit(2)


if __name__ == "__main__":
    main()
