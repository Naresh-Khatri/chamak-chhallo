"""Profile isolation and device-independent rendering without USB writes."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

import aula
from aula_aurora import Aurora, LiveSession
from aula_projection import Projection, SIZE
from aula_supernova import Supernova
from keyboard_profiles import F75, F87, WIRED_RGB, WiredRGBCodec, profile_for
from keyboard_device import DeviceOption, DeviceSelection, device_options
from test_aula import SimulatedKeyboard
from test_aurora import StreamingKeyboard


class DeviceSelectionTests(unittest.TestCase):
    wired = DeviceOption(Path('/dev/hidraw8'), 'wired-usb')
    radio = DeviceOption(Path('/dev/hidraw5'), 'wireless-2.4g')

    def test_discovery_lists_both_connections_without_opening_usb(self):
        with patch('keyboard_device.discover_all', return_value=[self.wired.path]), \
                patch('keyboard_device.discover_receivers', return_value=[self.radio.path]), \
                patch('keyboard_device.Keyboard') as wired, \
                patch('keyboard_device.WirelessKeyboard') as radio:
            self.assertEqual([self.wired, self.radio], device_options())
            wired.assert_not_called()
            radio.assert_not_called()
        self.assertIn('Wired USB', self.wired.label)
        self.assertIn('2.4G dongle', self.radio.label)

    def test_multiple_connections_wait_for_selection_instead_of_guessing(self):
        selection = DeviceSelection()
        selection.refresh([self.wired, self.radio])
        self.assertIsNone(selection.device)
        selection.choose(self.radio.path)
        self.assertEqual(self.radio.path, selection.device)
        self.assertTrue(selection.wireless)
        self.assertIsNone(selection.model)
        selection.choose_model('f87')
        self.assertEqual('f87', selection.model)

    def test_one_connection_is_selected_and_cli_selection_is_preserved(self):
        selection = DeviceSelection(model='f87')
        selection.refresh([self.radio])
        self.assertEqual((self.radio.path, 'f87'), (selection.device, selection.model))
        selection = DeviceSelection(self.radio.path, 'f87')
        selection.refresh([self.wired, self.radio])
        self.assertEqual((self.radio.path, 'f87'), (selection.device, selection.model))

    def test_model_choice_does_not_follow_a_switch_to_another_keyboard(self):
        selection = DeviceSelection(self.radio.path, 'f87')
        selection.refresh([self.wired, self.radio])
        selection.choose(self.wired.path)
        self.assertFalse(selection.wireless)
        self.assertIsNone(selection.model)
        with self.assertRaises(aula.DeviceError):
            selection.choose_model('f87')
        selection.choose(self.radio.path)
        self.assertIsNone(selection.model)
        with self.assertRaises(aula.DeviceError):
            selection.choose_model('f75')

    def test_rescan_preserves_present_selection_and_never_retargets_a_missing_device(self):
        selection = DeviceSelection(self.radio.path, 'f87')
        selection.refresh([self.wired, self.radio])
        selection.refresh([self.radio, self.wired])
        self.assertEqual((self.radio.path, 'f87'), (selection.device, selection.model))
        selection.refresh([self.wired])
        self.assertIsNone(selection.device)
        self.assertIsNone(selection.model)
        with self.assertRaises(aula.DeviceError):
            selection.choose(self.radio.path)
        self.assertIsNone(selection.device)

    def test_empty_list_and_explicit_deselection_leave_no_target(self):
        selection = DeviceSelection()
        selection.refresh([])
        self.assertIsNone(selection.device)
        selection.refresh([self.radio])
        selection.choose_model('f87')
        selection.choose(None)
        self.assertIsNone(selection.device)
        self.assertIsNone(selection.model)


class ProfileTests(unittest.TestCase):
    def test_shared_usb_id_does_not_grant_write_access(self):
        self.assertIs(F87, profile_for(aula.IDENTITY))
        self.assertIs(F75, profile_for(bytes.fromhex("03 00 00 00 03 66")))
        self.assertIsNone(profile_for(bytes.fromhex("03 00 00 00 01 ff")))
        for profile in (F75, None):
            keyboard = SimulatedKeyboard()
            keyboard.profile = profile
            with patch("aula.fcntl.ioctl") as ioctl:
                for operation in (
                    lambda: keyboard.apply("solid", "ffffff", 3, 2),
                    lambda: keyboard.restore("missing-backup.json"),
                    lambda: keyboard.write_config(keyboard.config),
                    lambda: keyboard.write_palette(keyboard.palette),
                    lambda: keyboard.send_direct(WIRED_RGB.encode({0: b"\xff\0\0"})),
                    lambda: aula.Keyboard.send(keyboard, aula.packet(4, 128)),
                ):
                    with self.assertRaisesRegex(aula.DeviceError, "read-only"):
                        operation()
                ioctl.assert_not_called()
            self.assertEqual([], keyboard.writes)

    def test_unverified_info_reads_identity_without_guessing_settings(self):
        keyboard = SimulatedKeyboard()
        keyboard.identity, keyboard.profile = F75.identity, F75
        with patch.object(keyboard, "read_config") as config, patch.object(keyboard, "read_palette") as palette:
            state = keyboard.state()
            self.assertFalse(state["writable"])
            self.assertEqual(F75.key, state["profile"])
            config.assert_not_called()
            palette.assert_not_called()

    def test_rgb_codec_rejects_wrong_indices_and_channels(self):
        for colors in ({122: bytes(3)}, {-1: bytes(3)}, {0: bytes(4)}, {0: [-1, 0, 0]}):
            with self.assertRaises(ValueError):
                WIRED_RGB.encode(colors)
        frame = WIRED_RGB.encode({75: b"\x10\x20\x30"})
        self.assertEqual(520, len(frame))
        self.assertEqual(b"\x10\x20\x30", frame[233:236])
        self.assertEqual(bytes(146), frame[374:])

    def test_renderers_work_without_a_usb_packet_or_f87_layout(self):
        layout = {"W": (0, 0, 0), "SPACE": (1, 1, 1), "BACKSPACE": (2, 2, 0)}
        source = SimpleNamespace(image=lambda: Image.new("RGB", SIZE, (20, 80, 140)))
        for renderer in (Aurora(layout=layout), Supernova(layout), Projection(layout, source)):
            renderer.press("W", 0)
            colors = renderer.render(0.5, "00c8ff", 3, 2)
            self.assertEqual({0, 1, 2}, set(colors))
            self.assertTrue(all(len(rgb) == 3 for rgb in colors.values()))
            self.assertEqual({0: bytes(3), 1: bytes(3), 2: bytes(3)},
                             renderer.render(1, "00c8ff", 0, 2))
        self.assertEqual(b"\x14\x50\x8c", Projection(layout, source).render(0, "000000", 4, 2)[0])

    def test_multiple_devices_require_selection_and_reject_unrelated_paths(self):
        paths = [Path("/dev/hidraw5"), Path("/dev/hidraw8")]
        with patch("aula.discover_all", return_value=paths):
            with self.assertRaisesRegex(aula.DeviceError, "multiple keyboards"):
                aula.discover()
            self.assertEqual(paths[1], aula.discover(paths[1]))
            with self.assertRaises(aula.DeviceError):
                aula.discover("/dev/hidraw1")

    def test_stream_uses_selected_profile_layout_and_codec(self):
        import tempfile
        layout = {"W": (0, 0, 0), "SPACE": (1, 1, 1)}
        keyboard = StreamingKeyboard()
        keyboard.profile = replace(F87, key="simulated-compact", layout=layout,
                                   codec=WiredRGBCodec(report_size=32, led_count=2, header=bytes(8)), fps=10)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(aula, "BACKUPS", Path(directory)), \
                patch("aula_aurora.Keyboard", return_value=keyboard) as constructor, \
                patch("aula_aurora.KeyInput", return_value=SimpleNamespace(devices=[], poll=lambda: (), close=lambda: None)):
            session = LiveSession("00c8ff", 3, 2, device="/dev/hidraw8")
            try:
                state = session.start()
                constructor.assert_called_once_with("/dev/hidraw8", model=None)
                self.assertEqual("simulated-compact", state["profile"])
                self.assertEqual(32, len(session.preview()))
                self.assertEqual({0, 1}, set(session.preview_colors()))
            finally:
                session.stop()


if __name__ == "__main__":
    unittest.main()
