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

## Experimental receiver transport

The locally connected `3554:fa09` receiver has a report-ID `13` input/output collection with 20-byte reports. Its report-ID `06` feature collection is status only. `aula_wireless.py` selects by the actual descriptor and sends output reports; wired feature reports never go through this transport. F75 remains read-only.

Each report contains ID, command, count, sequence, 15 payload bytes, and a checksum equal to the sum of the first 19 bytes modulo 256. `44 01 00` requests ten native configuration fragments. Lengths are 14 bytes except the final two-byte `5a a5` marker. The connected receiver sometimes returns stale nonzero bytes beyond that final declared length. Reads validate the entire report checksum but decode only declared data; writes zero unused bytes. Two complete, stable reads and explicit `--model f87` are required before writes.

`04` writes only a native receiver snapshot, patching documented lighting fields. Fragment 0 byte 8 confirms the write; byte 14 clears the apply flag; byte 15 selects the effect; byte 17 selects color mode. Effect parameters follow the fragment offsets documented by [the F87 driver](https://github.com/marcoslor/Aula-F87-Controller/blob/main/docs/PROTOCOL.md). Each config fragment and the final `0a` save require an exact echo, followed by full read-back. Version-2 backups preserve all ten native fragments. Factory palette templates and per-key flash writes are disabled.

Temporary `88` payloads group `[R,G,B,count,indices...]`, with groups crossing 14-byte fragment boundaries. Length markers add `10` hex. Complete frames include black keys, fit at most fourteen fragments, and coarsen HSV if necessary. Pacing starts at 13 ms between reports and 4 FPS; unchanged frames are skipped for up to two seconds. No unknown idle packet is sent. The preview shows the transmitted palette.

Configuration reads have been verified on the connected F87 receiver, including ten consecutive stable reads with stale tail bytes. Lighting writes, visible streaming, and restoration have simulated coverage but still need hardware validation. Published notes disagree on parts of the streaming path, and firmware effects can compete with host colors. The driver does not force per-key mode with a wired configuration. Original native settings are restored after a streaming attempt. Palette contents cannot be read or backed up, so an existing custom per-key table blocks streaming. See [receiver streaming research](https://github.com/SibteProf/keylux/blob/main/docs/PROTOCOL.md).
