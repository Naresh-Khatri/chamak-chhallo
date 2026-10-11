"""Wireless framing and config validation without receiver or keyboard writes."""

from collections import deque
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import aula
from aula import DeviceError
from aula_aurora import LiveSession
from aula_wireless import Receiver, config_equivalent, config_payload, encode_rgb, report, report_sizes, validate_report, validate_rgb
from keyboard_profiles import F87
from wireless_keyboard import WirelessKeyboard, WirelessRGBCodec, config_key


def configuration(payload=None):
    if payload is None:
        payload = bytes(range(126)) + b"\x5a\xa5"
    return tuple(report(0x44, 10, seq, bytes((len(chunk),)) + chunk)
                 for seq, start in enumerate(range(0, 128, 14))
                 for chunk in (payload[start:start + 14],))


class SimulatedReceiver(Receiver):
    def __init__(self, frames):
        super().__init__()
        self.pending = deque(frames)
        self.sent = []

    def drain(self):
        pass

    def send(self, frame):
        self.sent.append(frame)

    def read(self, deadline):
        return self.pending.popleft() if self.pending else None


class WirelessReadTests(unittest.TestCase):
    def test_read_request_matches_documented_bytes(self):
        self.assertEqual(bytes.fromhex("13 44 01 00") + bytes(15) + b"\x58", report(0x44, 1, 0))
        with self.assertRaises(ValueError):
            report(0x44, 1, 0, bytes(16))

    def test_descriptor_separates_rgb_output_from_status_feature(self):
        descriptor = bytes.fromhex("06 02 ff 09 02 a1 01 85 13 15 00 26 ff 00 "
                                   "75 08 95 13 09 02 81 00 09 02 91 00 c0 "
                                   "06 04 ff 09 02 a1 01 85 06 09 02 15 00 "
                                   "26 ff 00 75 08 95 07 b1 02 c0")
        sizes = report_sizes(descriptor)
        self.assertEqual(20, sizes['input', 0x13])
        self.assertEqual(20, sizes['output', 0x13])
        self.assertEqual(8, sizes['feature', 6])
        self.assertNotIn(('output', 6), sizes)
        with self.assertRaises(ValueError):
            report_sizes(descriptor + b"\x76\x01")

    def test_config_read_accepts_reordering_and_identical_duplicates(self):
        frames = configuration()
        receiver = SimulatedReceiver([report(0x44, 1, 0), frames[4], frames[4], *reversed(frames)])
        self.assertEqual(frames, receiver.read_config())
        self.assertEqual([report(0x44, 1, 0)], receiver.sent)
        self.assertEqual(bytes(range(126)) + b"\x5a\xa5", config_payload(frames))

    def test_missing_or_conflicting_fragments_are_rejected(self):
        frames = configuration()
        with self.assertRaisesRegex(DeviceError, "9/10"):
            SimulatedReceiver(frames[:-1]).read_config()
        changed = bytearray(frames[0][4:19])
        changed[2] ^= 1
        with self.assertRaisesRegex(DeviceError, "Conflicting duplicate"):
            SimulatedReceiver([frames[0], report(0x44, 10, 0, changed)]).read_config()

    def test_config_tail_ignores_stale_bytes_but_still_checks_checksum_and_marker(self):
        frames = configuration()
        stale = (*frames[:-1], report(0x44, 10, 9, b'\x02\x5a\xa5' + bytes(range(1, 13))))
        self.assertEqual(config_payload(frames), config_payload(stale))
        self.assertTrue(config_equivalent(frames, stale))
        self.assertEqual(stale, SimulatedReceiver(stale).read_config())
        damaged = bytearray(stale[-1])
        damaged[7] ^= 1
        with self.assertRaisesRegex(DeviceError, 'checksum'):
            config_payload((*frames[:-1], damaged))
        with self.assertRaisesRegex(DeviceError, 'end marker'):
            config_payload((*frames[:-1], report(0x44, 10, 9, b'\x02\x5a\x00' + bytes(range(1, 13)))))

    def test_checksums_end_markers_lengths_and_sequence_are_required(self):
        frames = configuration()
        corrupted = bytearray(frames[0])
        corrupted[6] ^= 1
        with self.assertRaisesRegex(DeviceError, "checksum"):
            validate_report(corrupted)
        with self.assertRaises(DeviceError):
            validate_report(frames[0][:-1])
        cases = [configuration(bytes(128)), frames[:-1], tuple(reversed(frames)),
                 (*frames[:9], report(0x44, 10, 9, b"\x03\x5a\xa5\0"))]
        for invalid in cases:
            with self.assertRaises(DeviceError):
                config_payload(invalid)

    def test_unvalidated_receiver_refuses_every_write_command(self):
        receiver = Receiver()
        with patch.object(receiver, '_write') as write:
            for command in (0x04, 0x09, 0x02, 0x0A, 0x88):
                with self.assertRaises(DeviceError):
                    receiver.send(report(command, 1, 0))
            write.assert_not_called()


class WirelessFrameTests(unittest.TestCase):
    def test_complete_solid_frame_crosses_fragments_and_preserves_black(self):
        colors = {index: b'\x00\xff\xff' for index in range(126)}
        colors[125] = bytes(3)
        packed, actual = encode_rgb(colors)
        self.assertEqual(colors, actual)
        self.assertEqual(colors, validate_rgb(packed))
        self.assertEqual(10 * 20, len(packed))
        self.assertEqual(colors, validate_rgb(WirelessRGBCodec().encode(colors)))

    def test_dense_frame_is_complete_bounded_and_never_rounds_lit_keys_to_black(self):
        colors = {index: bytes((index * 31 % 256, index * 53 % 256, index * 79 % 256))
                  for index in range(126)}
        colors[1] = b'\x01\x00\x00'
        packed, actual = encode_rgb(colors)
        self.assertLessEqual(len(packed), 14 * 20)
        self.assertEqual(set(colors), set(actual))
        self.assertEqual(actual, validate_rgb(packed))
        for index, rgb in colors.items():
            self.assertEqual(bool(any(rgb)), bool(any(actual[index])))

    def test_bad_fragments_groups_and_indices_are_rejected(self):
        packed, _ = encode_rgb({index: bytes(3) for index in range(87)})
        for invalid in (packed[:-1], packed[20:], b'', report(0x88, 1, 0, b'\x15\0\0\0\x02\x01'),
                        report(0x88, 1, 0, b'\x16\0\0\0\x02\x01\x01')):
            with self.assertRaises(DeviceError):
                validate_rgb(invalid)
        for invalid in ({126: bytes(3)}, {-1: bytes(3)}, {0: bytes(4)}, {0: [-1, 0, 0]}):
            with self.assertRaises(ValueError):
                encode_rgb(invalid)


class SimulatedWirelessKeyboard(WirelessKeyboard):
    def __init__(self):
        super().__init__(model='f87')
        payload = bytearray(range(126)) + b'\x5a\xa5'
        payload[10] = 3
        self.config = configuration(payload)
        self.receiver_key = config_key(self.config)
        self.validated = True
        self.profile = replace(F87, codec=WirelessRGBCodec(), fps=4)
        self.path = Path('/dev/hidraw-test')
        self.sent = []
        self.pending = deque()
        self.staged = []
        self.fail_echo = False
        self.fail_rgb = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def drain(self):
        self.pending.clear()

    def read_config(self):
        return self.config

    def read(self, deadline):
        return self.pending.popleft() if self.pending else None

    def _write(self, frame):
        self.sent.append(frame)
        if frame[1] == 0x88 and self.fail_rgb:
            raise OSError('simulated partial-frame failure')
        if frame[1] == 4:
            self.staged.append(frame)
        if frame[1] == 0x0A:
            self.config = tuple(report(0x44, 10, seq, item[4:19])
                                for seq, item in enumerate(self.staged[-10:]))
        if not self.fail_echo:
            self.pending.append(frame)


class WirelessSafetyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for override in (patch.object(aula, 'BACKUPS', Path(directory.name)),
                         patch('wireless_keyboard.time.sleep')):
            override.start()
            self.addCleanup(override.stop)
        self.keyboard = SimulatedWirelessKeyboard()

    def test_no_model_or_unstable_snapshot_prevents_all_writes(self):
        keyboard = WirelessKeyboard()
        with patch.object(keyboard, '_write') as write:
            for command in (4, 9, 2, 10, 0x88):
                with self.assertRaises(DeviceError):
                    keyboard.send(report(command, 1, 0))
            write.assert_not_called()
        first = self.keyboard.config
        payload = bytearray(config_payload(first))
        payload[20] ^= 1
        with patch('aula_wireless.Receiver.__enter__'), patch('aula_wireless.Receiver.__exit__'), \
                patch.object(self.keyboard, 'read_config', side_effect=[first, configuration(payload)]):
            with self.assertRaisesRegex(DeviceError, 'changed between reads'):
                WirelessKeyboard.__enter__(self.keyboard)
        self.assertEqual([], self.keyboard.sent)

    def test_onboard_change_preserves_unrelated_settings_and_restores_native_backup(self):
        original = config_payload(self.keyboard.config)
        result = self.keyboard.apply('breathing', 'ffffff', 3, 2)
        changed = config_payload(self.keyboard.config)
        allowed = {3, 9, 10, 12, 60, 61}
        self.assertTrue(all(a == b for index, (a, b) in enumerate(zip(original, changed)) if index not in allowed))
        self.assertEqual((2, 3, 0x27), (changed[10], changed[60], changed[61]))
        self.assertEqual({4, 10}, {frame[1] for frame in self.keyboard.sent})
        self.assertEqual(bytes.fromhex('13 0a 01 00 04 07') + bytes(13) + b'\x29', self.keyboard.sent[-1])
        self.assertEqual(2, json.loads(Path(result['backup']).read_text())['format'])
        self.keyboard.restore(result['backup'])
        restored = config_payload(self.keyboard.config)
        self.assertTrue(all(a == b for index, (a, b) in enumerate(zip(original, restored)) if index not in {3, 9}))

    def test_foreign_settings_wired_backups_and_palette_writes_are_refused(self):
        payload = bytearray(config_payload(self.keyboard.config))
        payload[14] ^= 1
        with self.assertRaises(DeviceError):
            self.keyboard.write_config(configuration(payload))
        for command in (9, 2):
            with self.assertRaises(DeviceError):
                self.keyboard.send(report(command, 1, 0))
        for data in ({'format': 1}, {'format': 2, 'transport': 'wired-usb'},
                     {'format': 2, 'transport': 'wireless-2.4g', 'model': 'f75'}):
            path = aula.BACKUPS / 'invalid.json'
            path.write_text(json.dumps(data))
            with self.assertRaises(DeviceError):
                self.keyboard.restore(path)
        self.assertEqual([], self.keyboard.sent)

    def test_missing_ack_stops_at_first_fragment(self):
        self.keyboard.fail_echo = True
        with self.assertRaisesRegex(DeviceError, 'No echo'):
            self.keyboard.write_config(self.keyboard.config)
        self.assertEqual(1, len(self.keyboard.sent))

    def test_config_write_zeroes_unused_tail_without_changing_settings(self):
        original = self.keyboard.config
        stale = (*original[:-1], report(0x44, 10, 9, b'\x02\x5a\xa5' + bytes(range(1, 13))))
        self.keyboard.config = stale
        backup = self.keyboard.backup(stale)
        self.assertEqual(stale[-1].hex(), json.loads(backup.read_text())['config'][-1])
        self.keyboard.write_config(stale)
        self.keyboard.verify(stale)
        last_config_write = self.keyboard.sent[-2]
        self.assertEqual(b'\x02\x5a\xa5', last_config_write[4:7])
        self.assertEqual(bytes(12), last_config_write[7:19])

    def test_failed_verification_rolls_back_before_reporting_failure(self):
        original = self.keyboard.config
        verify = self.keyboard.verify
        with patch.object(self.keyboard, 'verify', side_effect=[DeviceError('read-back mismatch'), None]):
            with self.assertRaisesRegex(DeviceError, 'Original settings restored'):
                self.keyboard.apply('rainbow', 'ffffff', 2, 1)
        verify(original)

    def test_unchanged_frames_skip_writes_and_partial_frame_still_restores(self):
        original = self.keyboard.config
        self.keyboard.start_stream(original)
        frame = self.keyboard.encode_colors({index: b'\x00\xff\xff' for index in range(87)})
        self.assertTrue(self.keyboard.send_direct(frame))
        sent = len(self.keyboard.sent)
        self.assertFalse(self.keyboard.send_direct(frame))
        self.assertEqual(sent, len(self.keyboard.sent))
        self.keyboard.fail_rgb = True
        with self.assertRaises(OSError):
            self.keyboard.send_direct(self.keyboard.encode_colors({0: bytes(3)}))
        self.keyboard.restore_stream(original, None)
        self.keyboard.verify(original)

    def test_custom_table_cannot_be_backed_up_and_cannot_start_stream(self):
        payload = bytearray(config_payload(self.keyboard.config))
        payload[10] = 21
        with self.assertRaisesRegex(DeviceError, 'custom per-key table'):
            self.keyboard.start_stream(configuration(payload))
        self.assertEqual([], self.keyboard.sent)


class WirelessIntegrationTests(unittest.TestCase):
    def test_selected_receiver_never_uses_wired_commands_and_requires_selection_if_ambiguous(self):
        import keyboard_device
        wired, wireless = Path('/dev/hidraw-wired'), Path('/dev/hidraw-wireless')
        with patch.object(keyboard_device, 'discover_all', return_value=[wired]), \
                patch.object(keyboard_device, 'discover_receivers', return_value=[wireless]), \
                patch.object(keyboard_device, 'Keyboard') as usb, \
                patch.object(keyboard_device, 'WirelessKeyboard') as radio:
            with self.assertRaisesRegex(DeviceError, '2 RGB interfaces'):
                keyboard_device.connect_keyboard(model='f87')
            keyboard_device.connect_keyboard(wireless, model='f87')
            radio.assert_called_once_with(wireless, model='f87')
            usb.assert_not_called()
            keyboard_device.connect_keyboard(wired)
            usb.assert_called_once_with(wired)
            with self.assertRaises(DeviceError):
                keyboard_device.connect_keyboard('/dev/hidraw-unrelated')

    def test_wireless_live_color_uses_native_codec_and_restores_on_stop(self):
        keyboard = SimulatedWirelessKeyboard()
        original = keyboard.config
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(aula, 'BACKUPS', Path(directory)), \
                patch('aula_aurora.Keyboard', return_value=keyboard) as factory, \
                patch('wireless_keyboard.time.sleep'):
            session = LiveSession('00ffff', 3, 2, effect='wireless-solid', model='f87')
            try:
                state = session.start()
                self.assertEqual('wireless-2.4g', state['transport'])
                self.assertEqual(4, session.profile.fps)
                self.assertEqual(87, len(session.preview_colors()))
                self.assertEqual({b'\x00\xbf\xbf'}, set(session.preview_colors().values()))
                self.assertFalse(state['typing_reactions'])
            finally:
                session.stop()
            factory.assert_called_once_with(None, model='f87')
            keyboard.verify(original)
            self.assertEqual({4, 10, 0x88}, {frame[1] for frame in keyboard.sent})


if __name__ == '__main__':
    unittest.main()
