# Protocol notes

These notes describe the F87-family identity tested for this project. They do not establish support for other models or receivers.

## How the wired protocol works

All messages use report ID `06` and 520-byte feature reports. GET_FEATURE responses
can be shorter than the buffer:

| Operation | Command | Response |
| --- | --- | --- |
| Query identity | `82`, address 1, length 6 | 14 bytes |
| Read config | `84`, address 0, length 128 | 136 bytes |
| Write config | `04`, address 0, length 128 | Read back using `84` |
| Read color palette | `8a`, address 0, length 512 | 520 bytes |
| Write color palette | `0a`, address 0, length 512 | Read back using `8a` |

Offsets below are absolute offsets in the response, including its eight-byte header:

- Config byte 17 selects custom per-key mode; 18 selects the onboard effect.
- Effect 0 = Off, 1 = Solid color, 2 = Breathing, 3 = Rainbow,
  7 = Ripple, 8 = Starlight, 12 = Reactive.
- Effect parameters start at `64 + 2 * effect`: brightness followed by speed/flags.
  The high nibble stores speed. Low nibble 0 selects single color; 7 selects rainbow.
- Palette bytes 29–31 store the primary RGB color used by Solid color.
- Config end marker: bytes 134–135. Palette end marker: bytes 514–515. Both are `5a a5`.

The controller discovers the hidraw node through sysfs each time it connects.
It does not assume `/dev/hidraw5` will stay the same after reconnection.

## Temporary RGB frames

The tested F87 uses header `06 08 00 00 01 00 7a 01` in a 520-byte report. Interleaved RGB starts at byte 8, using 122 LED slots. The header is preserved exactly as tested; unused slots and padding stay zero. Physical indices are in `keyboard_layouts.py`. Frames stream at 25 FPS without saving a configuration for each frame.

## References and other transports

- [F87 implementation](https://github.com/Ahorts/aula-f87pro) and [wired protocol notes](https://github.com/veysiemrah/aula-rgb-controller/blob/main/tools/protocol_notes.md).
- [Aula-Manager](https://github.com/free-soldier28/Aula-Manager/blob/main/docs/PROTOCOL.md) documents the reported F75 identity and a different wireless transport.
- [F75 streaming research](https://github.com/SibteProf/keylux/blob/main/docs/PROTOCOL.md) reports its own settings and pacing requirements.
- [Linux hidraw](https://docs.kernel.org/hid/hidraw.html), [ScreenCast portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html), and [GStreamer appsink](https://gstreamer.freedesktop.org/documentation/app/appsink.html).

The reported `3554:fa09` receiver uses 20-byte output reports with report ID `13`, checksums, fragmentation, and acknowledgments. It is not supported here; do not send wired feature reports through it. F75 recognition remains read-only until hardware validation.
