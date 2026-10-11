"""Verify configuration preservation and failure handling without hardware writes."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aula
from aula_live import LatestChangeQueue


class SimulatedKeyboard(aula.Keyboard):
    def __init__(self):
        self.profile = aula.F87
        self.path = Path("/dev/simulated")
        self.identity = aula.IDENTITY
        self.config = aula.packet(0x84, 128)[:136]
        self.config[8:134] = bytes((i * 17) % 256 for i in range(126))
        self.config[17:19] = bytes([0, 12])
        self.config[134:136] = b"\x5a\xa5"
        self.palette = aula.packet(0x8A, 512)
        self.palette[8:514] = bytes((i * 11) % 256 for i in range(506))
        self.palette[514:516] = b"\x5a\xa5"
        self.writes = []
        self.fail_next_config = False

    def read_config(self):
        aula.validate_config(self.config)
        return bytes(self.config)

    def read_palette(self):
        aula.validate_palette(self.palette)
        return bytes(self.palette)

    def send(self, data):
        self.writes.append(bytes(data))
        if data[1] == 4:
            if self.fail_next_config:
                self.fail_next_config = False
                raise OSError("simulated USB write failure")
            self.config[8:136] = data[8:136]
        elif data[1] == 0x0A:
            self.palette[8:] = data[8:]
        else:
            raise AssertionError("Unexpected write command")


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        override = patch.object(aula, "BACKUPS", Path(self.directory.name) / "backups")
        override.start()
        self.addCleanup(override.stop)
        self.keyboard = SimulatedKeyboard()

    def test_change_preserves_unrelated_settings_and_can_restore(self):
        config, palette = self.keyboard.read_config(), self.keyboard.read_palette()
        result = self.keyboard.apply("solid", "00c8ff", 3, 2)
        allowed_config = {17, 18, 66, 67}
        allowed_palette = {29, 30, 31}
        for offset, (before, after) in enumerate(zip(config, self.keyboard.config)):
            if offset not in allowed_config:
                self.assertEqual(before, after, f"Unrelated config byte {offset} changed")
        for offset, (before, after) in enumerate(zip(palette, self.keyboard.palette)):
            if offset not in allowed_palette:
                self.assertEqual(before, after, f"Unrelated palette byte {offset} changed")
        self.assertTrue(result["verified"])
        self.keyboard.restore(result["backup"])
        self.assertEqual(config, self.keyboard.read_config())
        self.assertEqual(palette, self.keyboard.read_palette())

    def test_failed_apply_rolls_back_palette_and_config(self):
        config, palette = self.keyboard.read_config(), self.keyboard.read_palette()
        self.keyboard.fail_next_config = True
        with self.assertRaisesRegex(aula.DeviceError, "Original settings restored"):
            self.keyboard.apply("solid", "00c8ff", 3, 2)
        self.assertEqual(config, self.keyboard.read_config())
        self.assertEqual(palette, self.keyboard.read_palette())

    def test_new_animations_preserve_palette_and_other_effect_settings(self):
        for name, effect_id in [("breathing", 2), ("reactive", 12), ("ripple", 7), ("starlight", 8)]:
            with self.subTest(effect=name):
                keyboard = SimulatedKeyboard()
                config, palette = keyboard.read_config(), keyboard.read_palette()
                result = keyboard.apply(name, "00c8ff", 3, 2)
                self.assertEqual(effect_id, keyboard.config[18])
                offset = 64 + 2 * effect_id
                self.assertEqual(bytes([3, 0x27]), keyboard.config[offset:offset + 2])
                self.assertEqual(palette, keyboard.read_palette())
                self.assertEqual(name, result["effect"])
                allowed = {17, 18, offset, offset + 1}
                for position, (before, after) in enumerate(zip(config, keyboard.config)):
                    if position not in allowed:
                        self.assertEqual(before, after, f"Unrelated config byte {position} changed")
                keyboard.restore(result["backup"])
                self.assertEqual(config, keyboard.read_config())
                self.assertEqual(palette, keyboard.read_palette())

    def test_bad_read_end_marker_prevents_any_lighting_write(self):
        self.keyboard.config[-1] = 0
        with self.assertRaises(aula.DeviceError):
            self.keyboard.apply("solid", "00c8ff", 3, 2)
        self.assertEqual([], self.keyboard.writes)

    def test_invalid_input_prevents_any_lighting_write(self):
        for color in ["red", "ff", "ffffff00", "00 c8 ff"]:
            with self.subTest(color=color), self.assertRaises(ValueError):
                self.keyboard.apply("solid", color, 3, 2)
        self.assertEqual([], self.keyboard.writes)

    def test_read_back_mismatch_is_reported(self):
        wanted = bytearray(self.keyboard.read_config())
        wanted[18] ^= 1
        with self.assertRaisesRegex(aula.DeviceError, "read-back differs"):
            self.keyboard.verify(wanted, self.keyboard.read_palette())

    def test_repeated_settings_do_not_write_or_create_another_backup(self):
        self.keyboard.apply("solid", "00c8ff", 3, 2)
        self.keyboard.writes.clear()
        before = list(aula.BACKUPS.iterdir())
        result = self.keyboard.apply("solid", "00c8ff", 3, 2)
        self.assertTrue(result["unchanged"])
        self.assertEqual([], self.keyboard.writes)
        self.assertEqual(before, list(aula.BACKUPS.iterdir()))

    def test_other_keyboard_identity_rejects_backup_before_writing(self):
        backup = self.keyboard.backup(self.keyboard.read_config(), self.keyboard.read_palette())
        self.keyboard.identity = b"\x03\0\0\0\0\xa9"
        with self.assertRaisesRegex(aula.DeviceError, "different keyboard"):
            self.keyboard.restore(backup)
        self.assertEqual([], self.keyboard.writes)


class LiveUpdateTests(unittest.TestCase):
    def test_first_edit_dispatches_immediately_and_burst_finishes_on_latest(self):
        writes = []
        queue = LatestChangeQueue(writes.append)
        queue.submit("red")
        self.assertEqual(["red"], writes)
        queue.submit("green")
        queue.submit("blue")
        self.assertEqual(["red"], writes)
        queue.complete()
        self.assertEqual(["red", "blue"], writes)
        self.assertTrue(queue.busy)
        queue.complete()
        self.assertFalse(queue.busy)
        queue.submit("cyan")
        self.assertEqual(["red", "blue", "cyan"], writes)

    def test_new_edit_while_latest_is_sending_is_not_lost(self):
        writes = []
        queue = LatestChangeQueue(writes.append)
        queue.submit("red")
        queue.submit("green")
        queue.complete()
        queue.submit("blue")
        queue.complete()
        self.assertEqual(["red", "green", "blue"], writes)
        queue.complete()
        self.assertFalse(queue.busy)


if __name__ == "__main__":
    unittest.main()
