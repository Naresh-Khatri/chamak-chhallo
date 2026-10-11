"""Exercise live frame rendering and cleanup using a simulated keyboard."""

import math
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import aula
from aula_aurora import Aurora, AuroraSession, KeyInput, LAYOUT, LiveSession
from aula_supernova import Supernova
from test_aula import SimulatedKeyboard


class FrameTests(unittest.TestCase):
    def test_frame_packs_physical_keys_and_leaves_unused_leds_off(self):
        frame = Aurora().frame(0.5, "00c8ff", 3, 2)
        self.assertEqual(520, len(frame))
        self.assertEqual(bytes.fromhex("06 08 00 00 01 00 7a 01"), frame[:8])
        mapped = {position[0] for position in LAYOUT.values()}
        self.assertEqual(87, len(mapped))
        self.assertEqual(14, LAYOUT["W"][0])
        self.assertEqual(75, LAYOUT["BACKSLASH"][0])
        for index in range(122):
            if index not in mapped:
                self.assertEqual(b"\0\0\0", frame[8 + index * 3:11 + index * 3])
        self.assertEqual(bytes(146), frame[374:])

    def test_zero_brightness_and_motion(self):
        animation = Aurora()
        self.assertEqual(bytes(512), animation.frame(1, "00c8ff", 0, 2)[8:])
        self.assertNotEqual(animation.frame(1, "00c8ff", 3, 2),
                            animation.frame(2, "00c8ff", 3, 2))

    def test_press_flashes_at_the_pressed_key_then_expires(self):
        animation = Aurora()
        baseline = animation.frame(1, "00c8ff", 4, 2)
        animation.press("W", 1)
        changed = animation.frame(1, "00c8ff", 4, 2)
        offset = 8 + LAYOUT["W"][0] * 3
        self.assertGreater(sum(changed[offset:offset + 3]), sum(baseline[offset:offset + 3]))
        far = 8 + LAYOUT["PAUSE"][0] * 3
        self.assertEqual(baseline[far:far + 3], changed[far:far + 3])
        self.assertEqual(Aurora().frame(4, "00c8ff", 4, 2), animation.frame(4, "00c8ff", 4, 2))
        self.assertEqual(0, len(animation.ripples))

    def test_bounded_reactions_store_only_time_and_position(self):
        animation = Aurora()
        for number in range(100):
            animation.press("W", number / 100)
        self.assertEqual(32, len(animation.ripples))
        self.assertTrue(all(isinstance(value, (float, int))
                            for ripple in animation.ripples for value in ripple))

    def test_input_ignores_release_and_auto_repeat(self):
        reader = KeyInput.__new__(KeyInput)
        reader.ev_key = 1
        reader.names = {17: "W"}
        reader.devices = [SimpleNamespace(read=lambda: iter([
            SimpleNamespace(type=1, code=17, value=0),
            SimpleNamespace(type=1, code=17, value=2),
        ]))]
        self.assertEqual(set(), reader.poll())
        reader.devices = [SimpleNamespace(read=lambda: iter([
            SimpleNamespace(type=1, code=17, value=1),
            SimpleNamespace(type=1, code=17, value=1),
        ]))]
        self.assertEqual({"W"}, reader.poll())


class StreamingKeyboard(SimulatedKeyboard):
    def __init__(self):
        super().__init__()
        self.frames = []
        self.black_seen = threading.Event()
        self.fail_frame = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def send_direct(self, frame):
        if self.fail_frame:
            raise OSError("simulated direct-frame failure")
        self.frames.append(bytes(frame))
        if not any(frame[8:]):
            self.black_seen.set()


class NoKeys:
    devices = []

    def __init__(self, **_):
        pass

    def poll(self):
        return set()

    def close(self):
        pass


class SessionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for override in [patch.object(aula, "BACKUPS", Path(directory.name) / "backups"),
                         patch("aula_aurora.KeyInput", NoKeys)]:
            override.start()
            self.addCleanup(override.stop)
        self.keyboard = StreamingKeyboard()
        override = patch("aula_aurora.Keyboard", return_value=self.keyboard)
        override.start()
        self.addCleanup(override.stop)

    def test_live_changes_skip_flash_writes_and_restore_on_stop(self):
        config, palette = self.keyboard.read_config(), self.keyboard.read_palette()
        session = AuroraSession("00c8ff", 3, 2)
        try:
            result = session.start()
            self.assertTrue(result["streaming"])
            self.assertEqual([], self.keyboard.writes)
            session.update("ff0088", 0, 4)
            self.assertTrue(self.keyboard.black_seen.wait(1))
            self.assertEqual([], self.keyboard.writes)
            self.assertEqual(1, len(list(aula.BACKUPS.iterdir())))
        finally:
            session.stop()
        self.assertFalse(session.running)
        self.assertEqual([4], [report[1] for report in self.keyboard.writes])
        self.assertEqual(config, self.keyboard.read_config())
        self.assertEqual(palette, self.keyboard.read_palette())

    def test_frame_failure_restores_settings_and_reports_the_backup(self):
        config, palette = self.keyboard.read_config(), self.keyboard.read_palette()
        self.keyboard.fail_frame = True
        session = AuroraSession("00c8ff", 3, 2)
        with self.assertRaisesRegex(aula.DeviceError, "Original settings backup"):
            session.start()
        session.thread.join(1)
        self.assertFalse(session.running)
        self.assertEqual(config, self.keyboard.read_config())
        self.assertEqual(palette, self.keyboard.read_palette())

    def test_invalid_live_parameters_leave_the_previous_values(self):
        session = AuroraSession("00c8ff", 3, 2)
        with self.assertRaises(ValueError):
            session.update("invalid", 3, 2)
        self.assertEqual(("00c8ff", 3, 2), session.settings)

    def test_supernova_uses_shared_transport_and_restores_on_stop(self):
        config, palette = self.keyboard.read_config(), self.keyboard.read_palette()
        session = LiveSession("ff0088", 3, 2, effect="supernova")
        try:
            result = session.start()
            self.assertEqual("supernova", result["effect"])
            self.assertTrue(result["streaming"])
            self.assertTrue(self.keyboard.frames)
            self.assertEqual([], self.keyboard.writes)
        finally:
            session.stop()
        self.assertEqual(config, self.keyboard.read_config())
        self.assertEqual(palette, self.keyboard.read_palette())
        self.assertEqual([4], [report[1] for report in self.keyboard.writes])

    def test_unknown_live_effect_is_rejected_before_any_usb_operation(self):
        with self.assertRaises(ValueError):
            LiveSession("00c8ff", 3, 2, effect="unknown")
        self.assertEqual([], self.keyboard.frames)
        self.assertEqual([], self.keyboard.writes)
        self.assertFalse(aula.BACKUPS.exists())


class SupernovaTests(unittest.TestCase):
    def test_galaxy_moves_and_zero_brightness_blanks_all_leds(self):
        animation = Supernova(LAYOUT)
        first = animation.frame(0, "00c8ff", 3, 2)
        self.assertNotEqual(first, animation.frame(1, "00c8ff", 3, 2))
        animation.press("SPACE", 1)
        self.assertEqual(bytes(512), animation.frame(1.4, "00c8ff", 0, 2)[8:])
        self.assertEqual(520, len(first))
        self.assertEqual(bytes.fromhex("06 08 00 00 01 00 7a 01"), first[:8])
        mapped = {position[0] for position in LAYOUT.values()}
        for index in range(122):
            if index not in mapped:
                self.assertEqual(bytes(3), first[8 + index * 3:11 + index * 3])
        self.assertEqual(bytes(146), first[374:])

    def test_key_launches_a_comet_from_its_physical_position(self):
        animation = Supernova(LAYOUT)
        before = animation.frame(0, "00c8ff", 3, 2)
        animation.press("W", 0)
        after = animation.frame(0, "00c8ff", 3, 2)
        index, x, y = LAYOUT["W"]
        offset = 8 + index * 3
        self.assertGreater(sum(after[offset:offset + 3]), sum(before[offset:offset + 3]))
        particle = animation.particles(0, 2)[0]
        self.assertAlmostEqual(x, particle[0])
        self.assertAlmostEqual(y, particle[1])
        moved = animation.particles(0.5, 2)[0]
        self.assertNotEqual(particle[:2], moved[:2])

    def test_space_and_accumulated_typing_trigger_blasts(self):
        animation = Supernova(LAYOUT)
        for _ in range(11):
            animation.press("W", 0)
        self.assertFalse(animation.blasts)
        animation.press("W", 0)
        self.assertEqual([0], list(animation.blasts))
        self.assertLess(animation.energy, 0.2)
        animation.press("SPACE", 0.1)
        self.assertEqual([0, 0.1], list(animation.blasts))

    def test_blast_light_moves_outward_across_the_keyboard(self):
        radii = []
        for age in (0.2, 0.8):
            background = Supernova(LAYOUT).frame(age, "00c8ff", 3, 2)
            animation = Supernova(LAYOUT)
            animation.blasts.append(0)
            frame = animation.frame(age, "00c8ff", 3, 2)
            weighted_radius, total_light = 0, 0
            for index, x, y in LAYOUT.values():
                offset = 8 + index * 3
                light = sum(frame[offset:offset + 3]) - sum(background[offset:offset + 3])
                radius = math.hypot(x - animation.center[0], (y - animation.center[1]) * 1.6)
                weighted_radius += radius * light
                total_light += light
            self.assertGreater(total_light, 0)
            radii.append(weighted_radius / total_light)
        self.assertGreater(radii[1], radii[0] + 2)

    def test_backspace_reverses_spin_without_jumping_phase_or_resetting_speed(self):
        animation = Supernova(LAYOUT)
        animation.frame(1, "00c8ff", 3, 4)
        phase = animation.phase
        animation.press("BACKSPACE", 1)
        self.assertEqual(phase, animation.phase)
        self.assertEqual(-1, animation.direction)
        animation.advance(2)
        self.assertAlmostEqual(0, animation.phase)
        animation.press("BACKSPACE", 2)
        animation.advance(3)
        self.assertAlmostEqual(phase, animation.phase)

    def test_reactions_are_bounded_numeric_and_expire(self):
        animation = Supernova(LAYOUT)
        for _ in range(100):
            animation.press("SPACE", 0)
        self.assertEqual(32, len(animation.comets))
        self.assertEqual(4, len(animation.blasts))
        self.assertTrue(all(isinstance(value, (int, float))
                            for comet in animation.comets for value in comet))
        animation.frame(5, "00c8ff", 3, 2)
        self.assertFalse(animation.comets)
        self.assertFalse(animation.blasts)
        self.assertEqual(0, animation.energy)


if __name__ == "__main__":
    unittest.main()
