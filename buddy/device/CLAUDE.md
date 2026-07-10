# buddy/device — hardware quirks

Read this before writing keyboard-handling code for any app in `apps/`.

## MatrixKeyboard.get_key() does not report keys the way the silkscreen suggests

`from hardware import MatrixKeyboard` is a frozen firmware module (no
source on the filesystem — `hardware.__file__` points into `.frozen`).
Its behavior can only be learned empirically, by probing a real device.
Confirmed quirks on the Cardputer-Adv, UIFlow2 v2.4.8:

- **ESC reports as `0x60` (backtick), not `0x1B`.** Verified live by
  polling `kb.get_key()` while physically pressing ESC — it returned
  `96` every time, never `27`. This is the same "silkscreen label !=
  electrical scancode" phenomenon as the arrow cluster below; it just
  hadn't been caught before because every other app also accepts `Q`
  as a fallback exit key, which masked it. `mine_tale.py` was the
  first app to rely on ESC alone and that's how this surfaced.
- **Enter reports as `0x0A` (LF), not `0x0D` (CR).** Accept both.
- **The arrow cluster reports its unshifted punctuation glyph**, not a
  special arrow code: `;` (up), `,` (left), `.` (down), `/` (right).
- **Return type is an int** for special/control keys (ASCII code) and
  either an int or a length-1 string for printables, depending on
  firmware build — always guard with `isinstance(k, int)` before
  comparing to a control code, then fall through to a string compare
  for printables. Don't assume `get_key()` always returns the same
  type.

If you add a new special-key check, don't trust the "obvious" ASCII
code — probe it on hardware first:

```bash
python3 scripts/repl_run.py --port /dev/cu.usbmodemXXXX --script "
from hardware import MatrixKeyboard
import time
kb = MatrixKeyboard()
print('press the key now')
end = time.ticks_add(time.ticks_ms(), 8000)
while time.ticks_diff(end, time.ticks_ms()) > 0:
    kb.tick()
    k = kb.get_key()
    if k is not None:
        print('GOT', type(k), repr(k))
    time.sleep_ms(30)
" --settle 9
```
(Give the person at the keyboard a moment to read the prompt and press
the key before the window closes — the script and the human aren't
synchronized.)

## Required pattern for every app

- **Never make ESC the only exit key.** Always accept `Q`/`q` too —
  see `_is_exit()` in `apps/mine_tale.py` or `_intent_for_key()` in
  `apps/claude_buddy.py` for the reference shape.
- **Wrap the main loop in `try/finally`**, with the `finally` clearing
  the screen and calling `machine.reset()`. Without it, any exception
  before your exit key check leaves the app frozen with no path back
  to the launcher — indistinguishable from "the exit key doesn't
  work."

## Don't forget when adding a new app

- Add it to `DEFAULT_FILES` in `scripts/push.py` — otherwise a plain
  `push.py --port ...` (no `--files`) silently skips it.
- Add it to the device tree in `README.md`.
