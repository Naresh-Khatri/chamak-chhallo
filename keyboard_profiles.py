"""Model identities, capabilities, settings offsets, and wired RGB encoding."""

from dataclasses import dataclass
from types import MappingProxyType

from keyboard_layouts import F87_LAYOUT


@dataclass(frozen=True)
class WiredRGBCodec:
    report_size: int = 520
    led_count: int = 122
    header: bytes = bytes.fromhex("06 08 00 00 01 00 7a 01")

    def encode(self, colors):
        if len(self.header) != 8 or 8 + self.led_count * 3 > self.report_size:
            raise ValueError("Invalid RGB codec dimensions.")
        frame = bytearray(self.report_size)
        frame[:8] = self.header
        for index, rgb in colors.items():
            if not isinstance(index, int) or not 0 <= index < self.led_count:
                raise ValueError(f"LED index {index} is outside this device's frame.")
            rgb = bytes(rgb)
            if len(rgb) != 3:
                raise ValueError("Each LED requires three RGB bytes.")
            frame[8 + index * 3:11 + index * 3] = rgb
        return frame


WIRED_RGB = WiredRGBCodec()
F87_EFFECTS = MappingProxyType({
    "off": 0, "solid": 1, "breathing": 2, "rainbow": 3,
    "ripple": 7, "starlight": 8, "reactive": 12,
})


@dataclass(frozen=True)
class KeyboardProfile:
    key: str
    name: str
    identity: bytes
    writable: bool
    layout: object = None
    codec: object = None
    fps: int = 25
    effects: object = None
    custom_offset: int = 17
    effect_offset: int = 18
    parameters_offset: int = 64
    color_offset: int = 29
    note: str = ""


F87 = KeyboardProfile(
    "aula-f87-0b", "AULA F87 family · tested identity 0b",
    bytes.fromhex("03 00 00 00 01 0b"), True,
    layout=MappingProxyType(F87_LAYOUT), codec=WIRED_RGB, effects=F87_EFFECTS,
    note="Wired lighting verified on the owner's keyboard. Retail revision is unconfirmed.",
)
F75 = KeyboardProfile(
    "aula-f75-0366", "AULA F75 · reported identity 03 66",
    bytes.fromhex("03 00 00 00 03 66"), False,
    note="Read-only recognition. Settings offsets, LED map, and streaming need hardware validation.",
)
PROFILES = MappingProxyType({profile.identity: profile for profile in (F87, F75)})


def profile_for(identity):
    return PROFILES.get(bytes(identity))
