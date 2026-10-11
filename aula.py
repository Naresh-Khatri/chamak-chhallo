#!/usr/bin/env python3
"""Chamak Chhallo: model-aware Linux keyboard RGB control and projection."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import time

from keyboard_profiles import F87, F87_EFFECTS, profile_for


STATE_HOME = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
BACKUPS = Path(os.environ["CHAMAK_CHHALLO_BACKUPS"]) if os.environ.get(
    "CHAMAK_CHHALLO_BACKUPS") else STATE_HOME / "chamak-chhallo/backups"
IDENTITY = F87.identity
EFFECTS = F87_EFFECTS
EFFECT_LABELS = {
    "solid": "Solid color", "rainbow": "Rainbow", "breathing": "Breathing",
    "reactive": "Reactive", "ripple": "Ripple", "starlight": "Starlight", "off": "Off",
}
ANIMATED_EFFECTS = frozenset(EFFECTS) - {"solid", "off"}
REPORT_SIZE = 520


class DeviceError(RuntimeError):
    pass


def discover_all():
    devices = []
    for entry in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        device = (entry / "device").resolve()
        try:
            props = dict(line.split("=", 1) for line in
                         (device / "uevent").read_text().splitlines() if "=" in line)
            if props.get("HID_ID") != "0003:0000258A:0000010C":
                continue
            interface = device.parent / "bInterfaceNumber"
            if interface.read_text().strip() == "01":
                devices.append(Path("/dev") / entry.name)
        except OSError:
            continue
    return devices


def discover(path=None):
    devices = discover_all()
    if path is not None:
        selected = Path(path)
        if selected not in devices:
            raise DeviceError("Select a discovered wired vendor interface from the scan command.")
        return selected
    if len(devices) != 1:
        raise DeviceError(
            f"Expected one wired RGB interface, found {len(devices)}. "
            "Connect the USB cable and select USB mode. With multiple keyboards, use --device."
        )
    return devices[0]


def packet(command, size, address=0):
    data = bytearray(REPORT_SIZE)
    data[:8] = bytes([6, command, address & 255, address >> 8, 1, 0,
                      size & 255, size >> 8])
    return data


def feature_ioctl(number, length):
    return (3 << 30) | (length << 16) | (ord("H") << 8) | number


class Keyboard:
    profile = None
    usb_parent = None

    def __init__(self, device=None):
        self.selected_device = device
        self.profile = None

    def __enter__(self):
        self.path = discover(self.selected_device)
        hid_device = (Path("/sys/class/hidraw") / self.path.name / "device").resolve()
        self.usb_parent = hid_device.parent.parent
        self.fd = os.open(self.path, os.O_RDWR | os.O_CLOEXEC)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            model = self.query(packet(0x82, 6, address=1), 14)
            if model[:8] != bytes.fromhex("06 82 01 00 01 00 06 00"):
                raise DeviceError("Unexpected identity response. No lighting writes sent.")
            self.identity = model[8:14]
            self.profile = profile_for(self.identity)
        except BaseException:
            os.close(self.fd)
            raise
        return self

    def __exit__(self, *_):
        os.close(self.fd)

    def send(self, data, *, delay=0.04):
        if len(data) != REPORT_SIZE or data[0] != 6:
            raise DeviceError("Invalid feature report.")
        if data[1] not in {0x82, 0x84, 0x8A}:
            self.require_writable()
        sent = fcntl.ioctl(self.fd, feature_ioctl(6, len(data)), bytearray(data))
        if sent != len(data):
            raise DeviceError(f"Short feature report write: {sent}/{len(data)} bytes.")
        if delay:
            time.sleep(delay)

    def send_direct(self, frame):
        self.require_writable()
        codec = self.profile.codec
        if len(frame) != codec.report_size or frame[:8] != codec.header:
            raise DeviceError("Invalid temporary RGB frame.")
        self.send(frame, delay=0)

    def require_writable(self):
        if self.profile is None or not self.profile.writable:
            model = self.profile.name if self.profile else "Unknown keyboard identity"
            raise DeviceError(f"{model} is read-only. A validated device profile is required for lighting writes.")

    def encode_colors(self, colors):
        self.require_writable()
        return self.profile.codec.encode(colors)

    def start_stream(self, config):
        self.require_writable()

    def restore_stream(self, config, palette):
        self.write_config(config)
        self.verify(config, palette)

    def identification(self):
        return {"device": str(self.path), "usb_id": "258a:010c", "transport": "wired-usb",
                "identity": self.identity.hex(" "),
                "model": self.profile.name if self.profile else "Unrecognized keyboard",
                "profile": self.profile.key if self.profile else None,
                "writable": bool(self.profile and self.profile.writable),
                "note": self.profile.note if self.profile else "Read-only. No matching identity profile."}

    def query(self, request, expected_length):
        self.send(request)
        response = bytearray(REPORT_SIZE)
        response[0] = 6
        length = fcntl.ioctl(self.fd, feature_ioctl(7, REPORT_SIZE), response)
        if length != expected_length:
            raise DeviceError(f"Expected {expected_length} response bytes, got {length}.")
        return bytes(response[:length])

    def read_config(self):
        data = self.query(packet(0x84, 128), 136)
        validate_config(data)
        return data

    def read_palette(self):
        data = self.query(packet(0x8A, 512), 520)
        validate_palette(data)
        return data

    def state(self):
        if self.profile is None or not self.profile.writable:
            return self.identification()
        config, palette = self.read_config(), self.read_palette()
        return self.describe(config, palette)

    def describe(self, config, palette):
        profile = self.profile
        effect = config[profile.effect_offset]
        result = {
            **self.identification(),
            "effect_id": effect,
            "effect": next((name for name, value in profile.effects.items() if value == effect),
                           f"onboard effect {effect}"),
            "color": "#" + palette[profile.color_offset:profile.color_offset + 3].hex(),
        }
        if 1 <= effect <= 18:
            offset = profile.parameters_offset + 2 * effect
            result.update(brightness=config[offset], speed=config[offset + 1] >> 4)
        return result

    def backup(self, config, palette):
        BACKUPS.mkdir(parents=True, exist_ok=True)
        path = BACKUPS / f"before-{time.time_ns()}.json"
        data = {"format": 1, "identity": self.identity.hex(),
                "config": config.hex(), "palette": palette.hex()}
        with path.open("x") as target:
            json.dump(data, target, indent=2)
            target.write("\n")
        if json.loads(path.read_text()) != data:
            raise DeviceError("Could not verify settings backup. No lighting writes sent.")
        return path

    def write_config(self, config):
        self.require_writable()
        validate_config(config)
        output = packet(0x04, 128)
        output[8:136] = config[8:136]
        self.send(output)

    def write_palette(self, palette):
        self.require_writable()
        validate_palette(palette)
        output = bytearray(palette)
        output[1] = 0x0A
        self.send(output)

    def verify(self, config, palette):
        actual_config, actual_palette = self.read_config(), self.read_palette()
        if actual_config != config or actual_palette != palette:
            differences = {
                "config": [i for i, pair in enumerate(zip(config, actual_config))
                           if pair[0] != pair[1]],
                "palette": [i for i, pair in enumerate(zip(palette, actual_palette))
                            if pair[0] != pair[1]],
            }
            raise DeviceError(f"Settings read-back differs at byte offsets {differences}.")

    def apply(self, effect, color, brightness, speed):
        self.require_writable()
        profile = self.profile
        color = parse_color(color)
        if effect not in profile.effects or not 0 <= brightness <= 4 or not 0 <= speed <= 4:
            raise ValueError(
                f"Choose an effect from {', '.join(EFFECTS)} and brightness/speed from 0 to 4."
            )
        config, palette = self.read_config(), self.read_palette()
        changed, colors = bytearray(config), bytearray(palette)
        changed[profile.custom_offset] = 0
        changed[profile.effect_offset] = profile.effects[effect]
        if effect != "off":
            offset = profile.parameters_offset + 2 * profile.effects[effect]
            changed[offset] = brightness
            changed[offset + 1] = (speed << 4) | (7 if effect in ANIMATED_EFFECTS else 0)
        if effect == "solid":
            colors[profile.color_offset:profile.color_offset + 3] = color
        if changed == config and colors == palette:
            return {"verified": True, "unchanged": True, **self.describe(config, palette)}
        backup = self.backup(config, palette)
        try:
            if colors != palette:
                self.write_palette(colors)
            self.write_config(changed)
            self.verify(changed, colors)
        except Exception as error:
            try:
                self.write_palette(palette)
                self.write_config(config)
                self.verify(config, palette)
            except Exception as rollback_error:
                raise DeviceError(
                    f"Apply failed: {error}. Restore also failed: {rollback_error}. "
                    f"Original settings are in {backup}."
                ) from error
            raise DeviceError(f"Apply failed: {error}. Original settings restored; backup {backup}.") from error
        return {"verified": True, "backup": str(backup), **self.describe(changed, colors)}

    def restore(self, path):
        self.require_writable()
        data = json.loads(Path(path).read_text())
        if data.get("format") != 1 or data.get("identity") != self.identity.hex():
            raise DeviceError("Backup belongs to a different keyboard identity or format.")
        config, palette = bytes.fromhex(data["config"]), bytes.fromhex(data["palette"])
        validate_config(config)
        validate_palette(palette)
        previous_config, previous_palette = self.read_config(), self.read_palette()
        backup = self.backup(previous_config, previous_palette)
        try:
            self.write_palette(palette)
            self.write_config(config)
            self.verify(config, palette)
        except Exception as error:
            raise DeviceError(
                f"Restore failed: {error}. Settings from before this restore are in {backup}."
            ) from error
        return {"verified": True, "backup": str(backup), **self.describe(config, palette)}


def validate_config(data):
    if (len(data) != 136 or data[:8] != bytes.fromhex("06 84 00 00 01 00 80 00")
            or data[134:136] != b"\x5a\xa5"):
        raise DeviceError("Invalid config response or missing end marker.")


def validate_palette(data):
    if (len(data) != 520 or data[:8] != bytes.fromhex("06 8a 00 00 01 00 00 02")
            or data[514:516] != b"\x5a\xa5"):
        raise DeviceError("Invalid color palette response or missing end marker.")


def parse_color(value):
    try:
        color = bytes.fromhex(value.removeprefix("#"))
    except ValueError as error:
        raise ValueError("Use a six-digit RGB color, such as 00c8ff.") from error
    if len(color) != 3 or len(value.removeprefix("#")) != 6:
        raise ValueError("Use a six-digit RGB color, such as 00c8ff.")
    return color


def main():
    from aula_aurora import LIVE_LABELS
    from keyboard_device import connect_keyboard, device_paths
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", type=Path, help="RGB hidraw path from scan; required with multiple interfaces")
    parser.add_argument("--model", choices=['f87'], help="Explicit paired model for a wireless receiver; wired models are detected")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("scan", help="Inspect wired keyboards and wireless receivers without changing lighting")
    actions.add_parser("info", help="Read identity and lighting settings")
    settings = actions.add_parser("set", help="Back up, apply lighting, and verify read-back")
    settings.add_argument("--effect", choices=EFFECTS, default="solid")
    settings.add_argument("--color", default="00c8ff")
    settings.add_argument("--brightness", type=int, choices=range(5), default=3)
    settings.add_argument("--speed", type=int, choices=range(5), default=2)
    restore = actions.add_parser("restore", help="Restore a settings backup")
    restore.add_argument("backup", type=Path)
    actions.add_parser("gui", help="Open the native GTK control panel")
    animation = actions.add_parser("animate", help="Stream a custom interactive animation")
    animation.add_argument("--effect", choices=[name for name in LIVE_LABELS
                                               if name not in {"video", "screen"}], default="aurora")
    animation.add_argument("--color", default="00c8ff")
    animation.add_argument("--brightness", type=int, choices=range(5), default=3)
    animation.add_argument("--speed", type=int, choices=range(5), default=2)
    animation.add_argument("--duration", type=float, default=0, help="Seconds; 0 runs until Ctrl+C")
    projection = actions.add_parser("project", help="Project local video or live screen colors onto the keys")
    sources = projection.add_mutually_exclusive_group(required=True)
    sources.add_argument("--video", type=Path, help="Local video to play on a loop")
    sources.add_argument("--screen", action="store_true", help="Stream a Wayland display through the system picker")
    projection.add_argument("--output", default=None, help="Use screenshot fallback for this Wayland display")
    projection.add_argument("--screenshots", action="store_true", help="Use screenshot fallback instead of streaming")
    projection.add_argument("--brightness", type=int, choices=range(5), default=3)
    projection.add_argument("--vividness", type=float, default=100,
                            help="Color saturation percentage, 0–300; 100 keeps source colors")
    projection.add_argument("--duration", type=float, default=0, help="Seconds; 0 runs until Ctrl+C")
    args = parser.parse_args()
    try:
        if args.action == "scan":
            results = []
            for path in device_paths():
                try:
                    with connect_keyboard(path, model=args.model) as keyboard:
                        results.append(keyboard.identification())
                except (DeviceError, OSError) as error:
                    results.append({"device": str(path), "writable": False, "error": str(error)})
            print(json.dumps(results, indent=2))
            return 0
        if args.action == "gui":
            from aula_gui import run
            return run(device=args.device, model=args.model)
        if args.action in {"animate", "project"}:
            from aula_aurora import LiveSession
            if args.duration < 0:
                raise ValueError("Duration must be zero or positive.")
            if args.action == "project":
                if args.video and args.output:
                    raise ValueError("--output is only used with --screen.")
                if args.video and args.screenshots:
                    raise ValueError("--screenshots is only used with --screen.")
                session = LiveSession("00c8ff", args.brightness, 2,
                    effect="video" if args.video else "screen",
                    vividness=args.vividness,
                    device=args.device,
                    model=args.model,
                    source=str(args.video) if args.video else
                           args.output if args.output is not None else "" if args.screenshots else None)
            else:
                session = LiveSession(args.color, args.brightness, args.speed, effect=args.effect,
                                      device=args.device, model=args.model)
            try:
                print(json.dumps(session.start(), indent=2))
                deadline = time.monotonic() + args.duration if args.duration else float("inf")
                while session.running and time.monotonic() < deadline:
                    time.sleep(0.1)
            except KeyboardInterrupt:
                pass
            finally:
                session.stop()
            return 0
        with connect_keyboard(args.device, model=args.model) as keyboard:
            if args.action == "info":
                result = keyboard.state()
            elif args.action == "set":
                result = keyboard.apply(args.effect, args.color, args.brightness, args.speed)
            else:
                result = keyboard.restore(args.backup)
        print(json.dumps(result, indent=2))
        return 0
    except (DeviceError, OSError, ValueError, ImportError, KeyError) as error:
        print(f"Chamak Chhallo: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
