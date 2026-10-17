"""Exercise the wired F75 revision with the same backup and rollback contract."""

from unittest.mock import patch
from types import SimpleNamespace

import aula
from aula_aurora import LiveSession
from keyboard_profiles import F75_CD, F87, profile_for
from keyboard_layouts import F75_LAYOUT
from test_aurora import StreamingKeyboard
import test_aula


class F75OnboardTests(test_aula.SafetyTests):
    def setUp(self):
        super().setUp()
        self.keyboard.profile = F75_CD
        self.keyboard.identity = F75_CD.identity

    def test_exact_identity_enables_onboard_settings_and_streaming(self):
        self.assertIs(F75_CD, profile_for(bytes.fromhex("03 00 00 00 00 cd")))
        self.assertIsNone(profile_for(bytes.fromhex("03 00 00 00 00 ce")))
        result = self.keyboard.state()
        self.assertTrue(result["writable"])
        self.assertTrue(result["streaming_supported"])
        self.assertEqual(9, result["brightness_max"])

    def test_native_brightness_nine_round_trips_and_restores(self):
        original = self.keyboard.read_config(), self.keyboard.read_palette()
        result = self.keyboard.apply("solid", "00ffff", 9, 2)
        self.assertEqual(9, result["brightness"])
        self.assertEqual(9, self.keyboard.config[66])
        self.assertEqual("#00ffff", self.keyboard.state()["color"])
        self.keyboard.restore(result["backup"])
        self.assertEqual(original, (self.keyboard.read_config(), self.keyboard.read_palette()))

    def test_brightness_limit_is_checked_before_usb_writes(self):
        for profile, brightness in ((F75_CD, 10), (F87, 5)):
            with self.subTest(profile=profile.key):
                self.keyboard.profile = profile
                with self.assertRaises(ValueError):
                    self.keyboard.apply("solid", "ffffff", brightness, 2)
        self.assertEqual([], self.keyboard.writes)

    def test_stream_mode_preserves_unrelated_bytes_and_restores(self):
        original = self.keyboard.read_config(), self.keyboard.read_palette()
        with patch('aula.time.sleep'):
            self.keyboard.start_stream(original[0])
        allowed = {17, 18, 66, 67}
        for offset, (before, after) in enumerate(zip(original[0], self.keyboard.config)):
            if offset not in allowed:
                self.assertEqual(before, after, f'Unrelated byte {offset} changed')
        self.assertEqual((1, 21, 9, 0x47), tuple(self.keyboard.config[i] for i in (17, 18, 66, 67)))
        self.keyboard.restore_stream(*original)
        self.assertEqual(original, (self.keyboard.read_config(), self.keyboard.read_palette()))

    def test_live_session_uses_f75_frame_and_restores_on_stop(self):
        keyboard = StreamingKeyboard()
        keyboard.profile, keyboard.identity = F75_CD, F75_CD.identity
        original = keyboard.read_config(), keyboard.read_palette()
        with patch("aula_aurora.Keyboard", return_value=keyboard), \
                patch('aula_aurora.KeyInput', return_value=SimpleNamespace(devices=[], poll=lambda: (), close=lambda: None)), \
                patch('aula.time.sleep'):
            session = LiveSession("00ffff", 3, 2)
            try:
                state = session.start()
                self.assertTrue(state['streaming'])
                self.assertEqual(80, len(session.preview_colors()))
                self.assertEqual(21, keyboard.config[18])
                self.assertEqual(20, session.profile.fps)
            finally:
                session.stop()
        self.assertEqual(original, (keyboard.read_config(), keyboard.read_palette()))

    def test_f75_uses_its_own_compact_key_map_and_126_slot_frame(self):
        self.assertEqual(80, len(F75_LAYOUT))
        self.assertEqual(80, len({position[0] for position in F75_LAYOUT.values()}))
        self.assertEqual(80, F75_LAYOUT['BACKSLASH'][0])
        self.assertEqual(70, F75_LAYOUT['RIGHTSHIFT'][0])
        self.assertEqual(89, F75_LAYOUT['RIGHT'][0])
        self.assertNotIn('SYSRQ', F75_LAYOUT)
        frame = self.keyboard.encode_colors({125: b'\x12\x34\x56'})
        self.assertEqual(b'\x12\x34\x56', frame[383:386])
        with self.assertRaises(ValueError):
            self.keyboard.encode_colors({126: bytes(3)})

    def test_f75_onboard_effects_include_current_mode_and_single_color_gradient(self):
        self.assertEqual(set(range(19)), set(F75_CD.effects.values()))
        self.keyboard.config[18] = 11
        self.assertEqual('onboard-aurora', self.keyboard.state()['effect'])
        result = self.keyboard.apply('gradient', 'ffffff', 9, 3)
        self.assertEqual('gradient', result['effect'])
        self.assertEqual(0x30, self.keyboard.config[64 + 2 * 14 + 1])
