"""Wireless lighting using receiver snapshots, with no palette templates."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time

import aula
from aula import DeviceError, parse_color
from aula_wireless import (
    REPORT_SIZE, Receiver, config_equivalent, config_payload, encode_rgb,
    report, validate_report, validate_rgb,
)
from keyboard_profiles import F75_CD, F87


WIRELESS_PROFILES = {'f87': F87, 'f75': F75_CD}
WIRELESS_STREAM_LIMITS = {'f87': (4, 196, None), 'f75': (8, 112, 64)}


def config_key(frames):
    payload = bytearray(config_payload(frames))
    for offset in (3, 9, 10, 12):
        payload[offset] = 0
    payload[58:94] = bytes(36)
    return hashlib.sha256(payload).hexdigest()


def effect_parameter(effect):
    if 1 <= effect <= 6:
        return 4, 7 + (effect - 1) * 2
    if 7 <= effect <= 13:
        return 5, 5 + (effect - 7) * 2
    if 14 <= effect <= 18:
        return 6, 5 + (effect - 14) * 2
    raise ValueError("No wireless parameters for this effect.")


class WirelessRGBCodec:
    def __init__(self, *, max_payload=196, report_rate=None):
        self.max_payload = max_payload
        self.report_rate = report_rate

    def encode(self, colors):
        return encode_rgb(colors, max_payload=self.max_payload)[0]


def wireless_profile(model):
    base = WIRELESS_PROFILES[model]
    fps, max_payload, report_rate = WIRELESS_STREAM_LIMITS[model]
    codec = WirelessRGBCodec(max_payload=max_payload, report_rate=report_rate)
    return replace(base, key=f'aula-{model}-wireless',
                   name=f'AULA {model.upper()} · selected wireless model',
                   codec=codec if base.streaming_supported else None, fps=fps,
                   note='Experimental wireless support. Model selected by the user; the receiver does not identify the paired keyboard.')


class WirelessKeyboard(Receiver):
    def __init__(self, device=None, *, model=None):
        super().__init__(device)
        if model is not None and model not in WIRELESS_PROFILES:
            raise ValueError("Select the F75 or F87 wireless model.")
        self.model = model
        self.profile = None
        self.usb_parent = None
        self.validated = False
        self.receiver_key = None
        self.rendered_colors = None
        self.last_rgb = None
        self.last_rgb_time = None
        self.next_rgb_at = 0

    def __enter__(self):
        self.validated = False
        self.profile = None
        self.stream_touched = False
        self.next_rgb_at = 0
        super().__enter__()
        try:
            hid_device = (Path('/sys/class/hidraw') / self.path.name / 'device').resolve()
            self.usb_parent = hid_device.parent.parent
            first = self.read_config()
            time.sleep(0.05)
            second = self.read_config()
            if not config_equivalent(first, second):
                raise DeviceError("Wireless settings changed between reads. Retry while the keyboard is idle.")
            self.receiver_key = config_key(first)
            if self.model in WIRELESS_PROFILES:
                self.profile = wireless_profile(self.model)
                self.validated = True
        except BaseException:
            super().__exit__()
            raise
        return self

    def require_writable(self):
        if not self.validated or self.profile is None:
            raise DeviceError("Wireless lighting requires --model f75 or --model f87 and two valid receiver config reads. "
                              "Select the model of the paired keyboard.")

    def send(self, frame):
        validate_report(frame)
        if frame[1] != 0x44:
            self.require_writable()
            if frame[1] not in (0x04, 0x0A, 0x88):
                raise DeviceError("Unsupported wireless write command. Palette and per-key flash writes are disabled.")
        self._write(frame)

    def identification(self):
        return {"device": str(self.path), "usb_id": "3554:fa09", "transport": "wireless-2.4g",
                "identity": None, "model": self.profile.name if self.profile else 'AULA-family wireless receiver',
                "profile": self.profile.key if self.profile else None,
                "writable": self.validated, "selected_model": self.model,
                "streaming_supported": bool(self.profile and self.profile.streaming_supported),
                "brightness_max": self.profile.brightness_max if self.profile else 4,
                "target_fps": self.profile.fps if self.profile else None,
                "supported_effects": list(self.profile.effects) if self.profile else [],
                "note": self.profile.note if self.profile else
                        'Paired model cannot be detected. Select F75 or F87 to match your keyboard.'}

    def describe(self, config, palette=None):
        config_payload(config)
        effect = config[0][15]
        names = self.profile.effects if self.profile else {}
        state = {**self.identification(), 'effect_id': effect,
                 'effect': next((name for name, value in names.items() if value == effect),
                                f'onboard effect {effect}'),
                 'color': None, 'palette_readback': False,
                 'note': self.identification()['note'] + ' Saved palette colors cannot be read over this link.'}
        if 1 <= effect <= 18:
            sequence, offset = effect_parameter(effect)
            state.update(brightness=config[sequence][offset], speed=config[sequence][offset + 1] >> 4)
        return state

    def state(self):
        return self.describe(self.read_config())

    def read_palette(self):
        return None

    def backup(self, config, palette=None):
        if palette is not None:
            raise DeviceError('A wired palette cannot be used with the receiver.')
        config_payload(config)
        aula.BACKUPS.mkdir(parents=True, exist_ok=True)
        path = aula.BACKUPS / f'before-wireless-{time.time_ns()}.json'
        data = {'format': 2, 'transport': 'wireless-2.4g', 'usb_id': '3554:fa09',
                'model': self.model, 'receiver_key': config_key(config),
                'config': [frame.hex() for frame in config]}
        with path.open('x') as target:
            json.dump(data, target, indent=2)
            target.write('\n')
        if json.loads(path.read_text()) != data:
            raise DeviceError('Could not verify wireless settings backup.')
        return path

    def _echo(self, frame, *, optional=False):
        self.send(frame)
        deadline = time.monotonic() + 0.5
        while True:
            response = self.read(deadline)
            if response is None:
                if optional:
                    return
                raise DeviceError(f'No echo for wireless command {frame[1]:02x}, sequence {frame[3]}.')
            if response == frame:
                return
            if response[:4] == frame[:4]:
                raise DeviceError('Wireless acknowledgement differs from the sent payload.')

    def write_config(self, config):
        self.require_writable()
        if config_key(config) != self.receiver_key:
            raise DeviceError('Wireless configuration does not match this receiver snapshot.')
        self.drain()
        for sequence, source in enumerate(config):
            payload = bytearray(source[4:5 + source[4]])
            if sequence == 0:
                payload[4] = 1
            self._echo(report(0x04, 10, sequence, payload))
        # Some receivers apply save without an echo. Read-back is mandatory
        # even when the save is acknowledged; config fragments still need echoes.
        self._echo(report(0x0A, 1, 0, b'\x04\x07'), optional=True)
        time.sleep(0.4)
        self.verify(config)
        self.last_rgb = None

    def verify(self, config, palette=None):
        if palette is not None:
            raise DeviceError('Wireless palette verification is unavailable.')
        if not config_equivalent(config, self.read_config()):
            raise DeviceError('Wireless configuration read-back differs from the requested settings.')

    def start_stream(self, config):
        self.require_writable()
        if not self.profile.streaming_supported:
            raise DeviceError('Live streaming is not enabled for this keyboard revision.')
        if config_key(config) != self.receiver_key:
            raise DeviceError('Receiver configuration changed since connection. Reconnect before streaming.')
        if config[0][15] == 21:
            raise DeviceError('Choose a built-in effect on the keyboard before streaming. '
                              'The existing custom per-key table cannot be backed up over the receiver.')
        if self.model == 'f75':
            payload = bytearray(config_payload(config))
            # Patch only this receiver's own lighting fields. USB snapshots
            # and factory templates must never be sent through the radio.
            payload[9] = 1
            payload[10] = 21
            payload[58] = self.profile.brightness_max
            payload[59] = 0x47
            changed = tuple(report(0x44, 10, sequence, bytes((len(chunk),)) + chunk)
                            for sequence, start in enumerate(range(0, 128, 14))
                            for chunk in (payload[start:start + 14],))
            self.stream_touched = True
            self.write_config(changed)

    def encode_colors(self, colors):
        self.require_writable()
        if not self.profile.streaming_supported:
            raise DeviceError('Live streaming is not enabled for this keyboard revision.')
        frame, self.rendered_colors = encode_rgb(colors, max_payload=self.profile.codec.max_payload)
        return frame

    def _pace_rgb(self, report_count):
        rate = self.profile.codec.report_rate
        if rate is None:
            return
        time.sleep(max(0, self.next_rgb_at - time.monotonic()))
        self.next_rgb_at = time.monotonic() + report_count / rate

    def send_direct(self, data):
        self.require_writable()
        if not self.profile.streaming_supported:
            raise DeviceError('Live streaming is not enabled for this keyboard revision.')
        validate_rgb(data)
        now = time.monotonic()
        if (self.last_rgb == data and self.last_rgb_time is not None
                and now - self.last_rgb_time < self.profile.frame_keepalive):
            return False
        self._pace_rgb(len(data) // REPORT_SIZE)
        self.stream_touched = True
        for start in range(0, len(data), REPORT_SIZE):
            self.send(data[start:start + REPORT_SIZE])
            # discard echoes and input; never record keystrokes
            self.drain()
        self.last_rgb, self.last_rgb_time = data, time.monotonic()
        return True

    def restore_stream(self, config, palette):
        if getattr(self, 'stream_touched', False):
            self.write_config(config)
        self.verify(config, palette)

    def apply(self, effect, color, brightness, speed):
        self.require_writable()
        parse_color(color)
        if effect == 'solid':
            if not self.profile.streaming_supported:
                raise DeviceError('Custom wireless colors are not enabled for this keyboard revision. '
                                  'Use a built-in effect, or connect with wired USB for solid colors.')
            raise DeviceError('Wireless custom colors use temporary streaming. '
                              'Use the panel or animate --effect wireless-solid.')
        if effect not in self.profile.effects or not 0 <= brightness <= self.profile.brightness_max or not 0 <= speed <= 4:
            raise ValueError(f'Choose a supported effect, brightness from 0 to {self.profile.brightness_max}, '
                             'and speed from 0 to 4.')
        original = self.read_config()
        changed = [bytearray(frame) for frame in original]
        effect_id = self.profile.effects[effect]
        changed[0][15] = effect_id
        changed[0][17] = 1 if effect != 'off' else 3
        if effect != 'off':
            sequence, offset = effect_parameter(effect_id)
            changed[sequence][offset] = brightness
            changed[sequence][offset + 1] = speed << 4 | (0 if effect == 'gradient' else 7)
        changed = tuple(report(0x44, 10, sequence, frame[4:19]) for sequence, frame in enumerate(changed))
        if config_equivalent(original, changed):
            return {'verified': True, 'unchanged': True, **self.describe(original)}
        backup = self.backup(original)
        try:
            self.write_config(changed)
            self.verify(changed)
        except Exception as error:
            try:
                self.write_config(original)
                self.verify(original)
            except Exception as rollback:
                raise DeviceError(f'Wireless apply failed: {error}. Restore failed: {rollback}. Backup: {backup}') from error
            raise DeviceError(f'Wireless apply failed: {error}. Original settings restored. Backup: {backup}') from error
        return {'verified': True, 'backup': str(backup), **self.describe(changed)}

    def restore(self, path):
        self.require_writable()
        data = json.loads(Path(path).read_text())
        if (data.get('format') != 2 or data.get('transport') != 'wireless-2.4g'
                or data.get('model') != self.model or data.get('receiver_key') != self.receiver_key):
            raise DeviceError('Backup belongs to a different transport, model, or receiver configuration.')
        config = tuple(bytes.fromhex(frame) for frame in data['config'])
        if config_key(config) != self.receiver_key:
            raise DeviceError('Wireless backup fingerprint is invalid.')
        backup = self.backup(self.read_config())
        self.write_config(config)
        self.verify(config)
        return {'verified': True, 'backup': str(backup), **self.describe(config)}
