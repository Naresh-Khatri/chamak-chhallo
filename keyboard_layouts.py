"""Physical key positions and LED indices, separate from renderers and USB."""

def make_layout():
    layout = {}

    def row(names, indices, columns, height):
        for name, index, column in zip(names.split(), indices, columns, strict=True):
            layout[name] = (index, column, height)

    row("ESC F1 F2 F3 F4 F5 F6 F7 F8 F9 F10 F11 F12 SYSRQ SCROLLLOCK PAUSE",
        [0, 12, 18, 24, 30, 36, 42, 48, 54, 60, 66, 72, 78, 84, 90, 96],
        [0, 2, 3, 4, 5, 6.5, 7.5, 8.5, 9.5, 11, 12, 13, 14, 15.5, 16.5, 17.5], 0)
    row("GRAVE 1 2 3 4 5 6 7 8 9 0 MINUS EQUAL BACKSPACE INSERT HOME PAGEUP",
        [1, 7, 13, 19, 25, 31, 37, 43, 49, 55, 61, 67, 73, 79, 85, 91, 97],
        [*range(13), 13.5, 15.5, 16.5, 17.5], 1.5)
    row("TAB Q W E R T Y U I O P LEFTBRACE RIGHTBRACE BACKSLASH DELETE END PAGEDOWN",
        [2, 8, 14, 20, 26, 32, 38, 44, 50, 56, 62, 68, 74, 75, 86, 92, 98],
        [0.25, *[i + 1.5 for i in range(12)], 13.75, 15.5, 16.5, 17.5], 2.5)
    row("CAPSLOCK A S D F G H J K L SEMICOLON APOSTROPHE ENTER",
        [3, 9, 15, 21, 27, 33, 39, 45, 51, 57, 63, 69, 81],
        [0.375, *[i + 1.75 for i in range(11)], 13.375], 3.5)
    row("LEFTSHIFT Z X C V B N M COMMA DOT SLASH RIGHTSHIFT UP",
        [4, 10, 16, 22, 28, 34, 40, 46, 52, 58, 64, 82, 94],
        [0.625, *[i + 2.25 for i in range(10)], 13.125, 16.5], 4.5)
    row("LEFTCTRL LEFTMETA LEFTALT SPACE RIGHTALT FN COMPOSE RIGHTCTRL LEFT DOWN RIGHT",
        [5, 11, 17, 35, 53, 59, 65, 83, 89, 95, 101],
        [0.125, 1.375, 2.625, 6.375, 10.125, 11.375, 12.625, 13.875, 15.5, 16.5, 17.5], 5.5)
    return layout


F87_LAYOUT = make_layout()


def make_f75_layout():
    """F75's 80 keys, with the column-major indices reported by its matrix."""
    layout = {}

    def row(names, indices, widths, height):
        left = 0
        for name, index, width in zip(names.split(), indices, widths, strict=True):
            layout[name] = (index, left + width / 2 - 0.5, height)
            left += width

    names = "ESC F1 F2 F3 F4 F5 F6 F7 F8 F9 F10 F11 F12"
    columns = [0, 1.9, 2.9, 3.9, 4.9, 6.3, 7.3, 8.3, 9.3, 10.7, 11.7, 12.7, 13.7]
    layout.update({name: (index, column, 0) for name, index, column in
                   zip(names.split(), [0, *range(12, 79, 6)], columns, strict=True)})
    row("GRAVE 1 2 3 4 5 6 7 8 9 0 MINUS EQUAL BACKSPACE DELETE",
        [1, 7, 13, 19, 25, 31, 37, 43, 49, 55, 61, 67, 73, 79, 85],
        [*([1] * 13), 2, 1], 1.5)
    row("TAB Q W E R T Y U I O P LEFTBRACE RIGHTBRACE BACKSLASH PAGEUP",
        [2, 8, 14, 20, 26, 32, 38, 44, 50, 56, 62, 68, 74, 80, 86],
        [1.5, *([1] * 12), 1.5, 1], 2.5)
    row("CAPSLOCK A S D F G H J K L SEMICOLON APOSTROPHE ENTER PAGEDOWN",
        [3, 9, 15, 21, 27, 33, 39, 45, 51, 57, 63, 69, 81, 87],
        [1.75, *([1] * 11), 2.25, 1], 3.5)
    row("LEFTSHIFT Z X C V B N M COMMA DOT SLASH RIGHTSHIFT UP END",
        [4, 10, 16, 22, 28, 34, 40, 46, 52, 58, 64, 70, 82, 88],
        [2.25, *([1] * 10), 1.75, 1, 1], 4.5)
    row("LEFTCTRL LEFTMETA LEFTALT SPACE FN RIGHTCTRL LEFT DOWN RIGHT",
        [5, 11, 17, 35, 53, 59, 77, 83, 89],
        [1.25, 1.25, 1.25, 6.25, 1.25, 1.75, 1, 1, 1], 5.5)
    return layout


F75_LAYOUT = make_f75_layout()
