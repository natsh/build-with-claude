"""Mine-Tale — a mash-up of Undertale and Minecraft (WIP).

Right now this is just a hello-world placeholder so the app shows up
in the launcher and boots cleanly. The actual game gets built in a
later session. Keeping the shell minimal but functional: it draws a
title screen and exits back to the launcher on ESC.

App contract (shared by every app in this bundle):
  1. draw to the 240x135 LCD (M5.Lcd),
  2. loop reading the keyboard (MatrixKeyboard),
  3. exit with machine.reset() to return to the launcher menu.
"""

import time

import M5
import machine
from hardware import MatrixKeyboard

_LCD = M5.Lcd

# Shared palette (0xRRGGBB) so the app looks native to the suite.
_BLACK = 0x000000
_ORANGE = 0xCC785C
_CREAM = 0xF0EEE6
_GRAY = 0x777777

_W = 240
_H = 135


def _draw_title():
    _LCD.fillScreen(_BLACK)

    # Title, centered and bold.
    _LCD.setTextSize(3)
    _LCD.setTextColor(_ORANGE, _BLACK)
    title = "MINE-TALE"
    _LCD.drawString(title, (_W - _LCD.textWidth(title)) // 2, 34)

    # Placeholder line — where the game will live.
    _LCD.setTextSize(1)
    _LCD.setTextColor(_CREAM, _BLACK)
    sub = "hello world"
    _LCD.drawString(sub, (_W - _LCD.textWidth(sub)) // 2, 74)

    # Hint strip.
    _LCD.setTextColor(_GRAY, _BLACK)
    hint = "ESC  back to menu"
    _LCD.drawString(hint, (_W - _LCD.textWidth(hint)) // 2, _H - 14)


def run():
    try:
        _LCD.setFont(_LCD.FONTS.DejaVu9)
    except Exception:
        pass

    kb = MatrixKeyboard()
    # Swallow the Enter keypress that launched us.
    time.sleep_ms(400)

    _draw_title()

    while True:
        kb.tick()
        k = kb.get_key()  # int ASCII code, or None
        if k == 0x1B:  # ESC
            break
        time.sleep_ms(40)

    # Return to the launcher.
    _LCD.fillScreen(_BLACK)
    time.sleep_ms(200)
    machine.reset()


run()
