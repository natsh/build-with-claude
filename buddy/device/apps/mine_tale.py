"""Mine-Tale — an Undertale-style micro-RPG with a Minecraft heart.

One mini level, end to end:

  1. Title screen (pixel creeper + logo).
  2. Overworld: walk a small cave (WASD / arrow cluster), find the
     Creeper, bump into it to start an encounter.
  3. Battle, Undertale-rules / Minecraft-flavor:
       MINE  — timing-bar attack (stop the cursor near the center)
       TALK  — befriend the Creeper (two talks make it spareable)
       ITEM  — eat bread, restore HP (x2 per run)
       SPARE — ends the fight peacefully once befriended
     Between your turns the Creeper attacks: your SOUL (the red
     heart) dodges falling gravel and TNT inside the battle box.
  4. Victory (pacifist or miner route) -> demo-complete screen.

App contract (shared by every app in this bundle):
  1. draw to the 240x135 LCD (M5.Lcd),
  2. loop reading the keyboard (MatrixKeyboard),
  3. exit with machine.reset() to return to the launcher menu.

Keyboard quirks (see buddy/device/CLAUDE.md): ESC reports as 0x60,
Enter as 0x0A, the arrow cluster as `;` `,` `.` `/`. Q always exits.
"""

import random
import time

import M5
import machine
from hardware import MatrixKeyboard

_LCD = M5.Lcd
_W = 240
_H = 135

# ---- suite palette
_BLACK = 0x000000
_ORANGE = 0xCC785C
_CREAM = 0xF0EEE6
_DARK = 0x1F1F1F
_GRAY = 0x777777
_WHITE = 0xFFFFFF
_RED = 0xE43B44
_YELLOW = 0xF7D51D
_HPGREEN = 0x3FD435

# ---- Minecraft-flavored colors
_STONE = 0x8A8A8A
_STONE_DK = 0x606060
_DIRT = 0x3B2A18
_DIRT_LT = 0x51402A
_DIAMOND = 0x4AEDD9
_CREEP = 0x44A838
_CREEP_DK = 0x2E7D27
_SKIN = 0xC68863
_HAIR = 0x4A2F17
_EYE = 0x3A2FA8
_TNT = 0xC33B2F

# ---------------------------------------------------------------- sprites
# 8x8 pixel maps, drawn scaled. '.' = transparent.

_SPR_CREEPER = (
    "gggggggg",
    "gGgggGgg",
    "gXXggXXg",
    "gXXggXXg",
    "gggXXggg",
    "ggXXXXgg",
    "ggXXXXgg",
    "ggXggXgg",
)
_PAL_CREEPER = {"g": _CREEP, "G": _CREEP_DK, "X": _BLACK}

_SPR_PLAYER = (
    "hhhhhhhh",
    "hhhhhhhh",
    "hssssssh",
    "ssssssss",
    "swesseWs",
    "ssssssss",
    "ssmmmmss",
    "ssssssss",
)
_PAL_PLAYER = {"h": _HAIR, "s": _SKIN, "w": _WHITE, "W": _WHITE,
               "e": _EYE, "E": _EYE, "m": _HAIR}

_SPR_HEART = (
    ".XX..XX.",
    "XXXXXXXX",
    "XXXXXXXX",
    "XXXXXXXX",
    ".XXXXXX.",
    "..XXXX..",
    "...XX...",
    "........",
)
_PAL_HEART = {"X": _RED}


def _sprite(spr, pal, x, y, scale):
    for r, row in enumerate(spr):
        c = 0
        n = len(row)
        while c < n:
            ch = row[c]
            if ch == ".":
                c += 1
                continue
            # Run-length within the row: adjacent same-color pixels
            # collapse into one fillRect (fewer SPI transactions).
            c2 = c + 1
            while c2 < n and row[c2] == ch:
                c2 += 1
            _LCD.fillRect(x + c * scale, y + r * scale,
                          (c2 - c) * scale, scale, pal[ch])
            c = c2


# ---------------------------------------------------------------- input

class _Quit(Exception):
    """Raised anywhere to unwind to run()'s finally (-> launcher)."""
    pass


def _intent(k):
    """Normalize MatrixKeyboard output to a gameplay intent or None."""
    if k is None:
        return None
    if isinstance(k, int):
        if k in (0x1B, 0x60):        # ESC (0x60 on this hardware)
            return "exit"
        if k in (0x0A, 0x0D):        # Enter (0x0A on this hardware)
            return "confirm"
        if 0x20 <= k <= 0x7E:
            k = chr(k)
        else:
            return None
    if not isinstance(k, str) or not k:
        return None
    ch = k.lower()
    if ch == "q":
        return "exit"
    if ch in ("w", ";"):
        return "up"
    if ch in ("s", "."):
        return "down"
    if ch in ("a", ","):
        return "left"
    if ch in ("d", "/"):
        return "right"
    if ch in ("\r", "\n", " ", "e"):
        return "confirm"
    if ch == "r":
        return "restart"
    return None


def _poll(kb):
    """One keyboard tick -> intent. Raises _Quit on the exit keys."""
    kb.tick()
    i = _intent(kb.get_key())
    if i == "exit":
        raise _Quit()
    return i


def _flush(kb):
    """Swallow any buffered keypresses (e.g. the Enter that got us
    to this phase) so they don't leak into the next one."""
    for _ in range(3):
        kb.tick()
        kb.get_key()
        time.sleep_ms(30)


def _wait_confirm(kb):
    while True:
        if _poll(kb) == "confirm":
            _flush(kb)
            return
        time.sleep_ms(40)


# ---------------------------------------------------------------- text

def _center(text, y, color, bg=_BLACK, size=1):
    _LCD.setTextSize(size)
    _LCD.setTextColor(color, bg)
    _LCD.drawString(text, (_W - _LCD.textWidth(text)) // 2, y)


# ---------------------------------------------------------------- title

def _title(kb):
    _LCD.fillScreen(_BLACK)
    _sprite(_SPR_CREEPER, _PAL_CREEPER, (_W - 40) // 2, 8, 5)
    _center("MINE-TALE", 56, _ORANGE, size=3)
    _center("a creeper blocks your path", 88, _CREAM)
    # dirt strip along the bottom for the Minecraft of it all
    _LCD.fillRect(0, _H - 18, _W, 18, _DIRT)
    for i in range(0, _W, 12):
        _LCD.fillRect(i + (i // 12 % 3) * 3, _H - 14, 3, 3, _DIRT_LT)
    _center("ENTER start    Q quit", 104, _GRAY)
    _wait_confirm(kb)


# ---------------------------------------------------------------- overworld

_TILE = 16
_MAP_Y = 22
_MAP = (
    "###############",
    "#.....#....*..#",
    "#.##..#..###..#",
    "#..#.....#....#",
    "#*.#..##.#.##.#",
    "#......#.....C#",
    "###############",
)
_START = (1, 1)


def _draw_tile(cx, cy):
    x = cx * _TILE
    y = _MAP_Y + cy * _TILE
    t = _MAP[cy][cx]
    if t == "#":
        _LCD.fillRect(x, y, _TILE, _TILE, _STONE)
        # mortar cracks, deterministic per tile so redraws are stable
        _LCD.fillRect(x, y + 7, _TILE, 1, _STONE_DK)
        off = (cx * 7 + cy * 5) % 10
        _LCD.fillRect(x + off, y, 1, 7, _STONE_DK)
        _LCD.fillRect(x + (off + 5) % 14, y + 8, 1, 8, _STONE_DK)
    else:
        _LCD.fillRect(x, y, _TILE, _TILE, _DIRT)
        _LCD.fillRect(x + (cx * 5 + cy * 3) % 12, y + (cx + cy * 7) % 12,
                      2, 2, _DIRT_LT)
        if t == "*":
            for dx, dy in ((3, 3), (9, 5), (5, 10), (11, 11)):
                _LCD.fillRect(x + dx, y + dy, 2, 2, _DIAMOND)


def _draw_map(state):
    _LCD.fillScreen(_BLACK)
    _LCD.fillRect(0, 0, _W, 20, _DARK)
    _LCD.fillRect(0, 20, _W, 1, _ORANGE)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_ORANGE, _DARK)
    _LCD.drawString("MINE-TALE", 6, 5)
    hp = "HP {}/20".format(state["hp"])
    _LCD.setTextColor(_CREAM, _DARK)
    _LCD.drawString(hp, _W - 6 - _LCD.textWidth(hp), 5)
    for cy in range(len(_MAP)):
        for cx in range(len(_MAP[0])):
            _draw_tile(cx, cy)
    if not state["won"]:
        cx, cy = state["creeper"]
        _sprite(_SPR_CREEPER, _PAL_CREEPER, cx * _TILE, _MAP_Y + cy * _TILE, 2)
    px, py = state["pos"]
    _sprite(_SPR_PLAYER, _PAL_PLAYER, px * _TILE, _MAP_Y + py * _TILE, 2)


def _dialog(kb, lines):
    """Undertale-style text box overlaid on the bottom of the map."""
    bx, by, bw, bh = 4, 86, _W - 8, 46
    _LCD.fillRect(bx, by, bw, bh, _BLACK)
    _LCD.fillRect(bx, by, bw, 2, _WHITE)
    _LCD.fillRect(bx, by + bh - 2, bw, 2, _WHITE)
    _LCD.fillRect(bx, by, 2, bh, _WHITE)
    _LCD.fillRect(bx + bw - 2, by, 2, bh, _WHITE)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_CREAM, _BLACK)
    y = by + 7
    for ln in lines:
        _LCD.drawString(ln, bx + 8, y)
        y += 12
    _LCD.setTextColor(_GRAY, _BLACK)
    _LCD.drawString("[ENTER]", bx + bw - 54, by + bh - 12)
    _flush(kb)
    _wait_confirm(kb)


def _overworld(kb, state):
    """Walk the cave. Returns when the player bumps the Creeper."""
    _draw_map(state)
    if not state["intro_done"]:
        _dialog(kb, ("* You mined too deep and woke",
                     "  something up. Find it.",
                     "  (WASD / arrows move)"))
        state["intro_done"] = True
        _draw_map(state)
    moves = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
    while True:
        i = _poll(kb)
        d = moves.get(i)
        if d:
            px, py = state["pos"]
            nx, ny = px + d[0], py + d[1]
            t = _MAP[ny][nx]
            if (nx, ny) == state["creeper"] and not state["won"]:
                _dialog(kb, ("* A wild CREEPER blocks",
                             "  the way!"))
                return
            if t != "#":
                state["pos"] = (nx, ny)
                _draw_tile(px, py)
                _sprite(_SPR_PLAYER, _PAL_PLAYER,
                        nx * _TILE, _MAP_Y + ny * _TILE, 2)
        elif i == "confirm" and state["won"]:
            return
        time.sleep_ms(40)


# ---------------------------------------------------------------- battle

# battle box (Undertale's white rectangle)
_BOX_X = 30
_BOX_Y = 48
_BOX_W = 180
_BOX_H = 52

_MENU = ("MINE", "TALK", "ITEM", "SPARE")

_TALKS = (
    ("* You tell the Creeper it has",
     "  ssstyle. It seems flattered."),
    ("* You hum a C418 song. The",
     "  Creeper sways. It trusts you!"),
    ("* You are already friends.", ""),
)

_ENEMY_TURNS = (
    ("* The Creeper sways from",
     "  ssside to ssside."),
    ("* Gravel rains from the",
     "  cave roof."),
    ("* The Creeper whispers:",
     "  thisss is fine."),
)


def _box_border(color):
    _LCD.fillRect(_BOX_X - 2, _BOX_Y - 2, _BOX_W + 4, 2, color)
    _LCD.fillRect(_BOX_X - 2, _BOX_Y + _BOX_H, _BOX_W + 4, 2, color)
    _LCD.fillRect(_BOX_X - 2, _BOX_Y - 2, 2, _BOX_H + 4, color)
    _LCD.fillRect(_BOX_X + _BOX_W, _BOX_Y - 2, 2, _BOX_H + 4, color)


def _box_text(lines, color=_CREAM):
    _LCD.fillRect(_BOX_X, _BOX_Y, _BOX_W, _BOX_H, _BLACK)
    _LCD.setTextSize(1)
    _LCD.setTextColor(color, _BLACK)
    y = _BOX_Y + 8
    for ln in lines:
        if ln:
            _LCD.drawString(ln, _BOX_X + 8, y)
        y += 13


def _enemy_hp_bar(b):
    x, y, w = 90, 40, 60
    _LCD.fillRect(x, y, w, 5, _DARK)
    fill = (w * b["ehp"]) // b["ehp_max"]
    if fill > 0:
        _LCD.fillRect(x, y, fill, 5, _HPGREEN)


def _player_strip(b):
    y = _BOX_Y + _BOX_H + 6
    _LCD.fillRect(0, y, _W, 12, _BLACK)
    _LCD.setTextSize(1)
    name_col = _YELLOW if b["friend"] >= 2 else _CREAM
    _LCD.setTextColor(name_col, _BLACK)
    _LCD.drawString("STEVE LV1", 8, y + 1)
    _LCD.setTextColor(_CREAM, _BLACK)
    _LCD.drawString("HP", 84, y + 1)
    bx, bw = 102, 60
    _LCD.fillRect(bx, y + 1, bw, 9, _DARK)
    fill = (bw * b["hp"]) // 20
    if fill > 0:
        _LCD.fillRect(bx, y + 1, fill, 9, _YELLOW)
    t = "{}/20  bread x{}".format(b["hp"], b["bread"])
    _LCD.drawString(t, bx + bw + 6, y + 1)


def _menu_row(sel):
    y = _H - 15
    _LCD.fillRect(0, y - 2, _W, 17, _BLACK)
    _LCD.setTextSize(1)
    for i, label in enumerate(_MENU):
        x = 6 + i * 59
        if i == sel:
            _LCD.fillRect(x, y - 2, 55, 15, _ORANGE)
            _LCD.setTextColor(_BLACK, _ORANGE)
        else:
            _LCD.setTextColor(_ORANGE, _BLACK)
        _LCD.drawString(label, x + 6, y + 1)


def _draw_battle(b, lines):
    _LCD.fillScreen(_BLACK)
    _sprite(_SPR_CREEPER, _PAL_CREEPER, (_W - 32) // 2, 4, 4)
    _enemy_hp_bar(b)
    _box_border(_WHITE)
    _box_text(lines)
    _player_strip(b)
    _menu_row(b["sel"])


def _menu_phase(b, kb, lines):
    """Left/right + Enter over MINE TALK ITEM SPARE."""
    _draw_battle(b, lines)
    _flush(kb)
    while True:
        i = _poll(kb)
        if i == "left" and b["sel"] > 0:
            b["sel"] -= 1
            _menu_row(b["sel"])
        elif i == "right" and b["sel"] < 3:
            b["sel"] += 1
            _menu_row(b["sel"])
        elif i == "confirm":
            _flush(kb)
            return _MENU[b["sel"]]
        time.sleep_ms(40)


def _attack_phase(b, kb):
    """Undertale's timing bar: cursor sweeps, Enter stops it.
    Damage scales with distance from center. Returns damage dealt."""
    _box_text(("* Stop the pick at the",
               "  center! [ENTER]"))
    bar_x = _BOX_X + 10
    bar_w = _BOX_W - 20
    bar_y = _BOX_Y + 32
    _LCD.fillRect(bar_x, bar_y, bar_w, 12, _DARK)
    cx = bar_x + bar_w // 2
    _LCD.fillRect(cx - 4, bar_y, 8, 12, _HPGREEN)   # sweet spot
    _flush(kb)
    pos = 0
    prev = -1
    while pos < bar_w - 3:
        # erase previous cursor (repaint strip segment underneath)
        if prev >= 0:
            under = _HPGREEN if abs(bar_x + prev - cx) <= 4 else _DARK
            _LCD.fillRect(bar_x + prev, bar_y, 3, 12, under)
        _LCD.fillRect(bar_x + pos, bar_y, 3, 12, _WHITE)
        prev = pos
        if _poll(kb) == "confirm":
            off = abs((bar_x + pos) - cx)
            half = bar_w // 2
            dmg = 8 - (7 * off) // half   # 8 at center -> 1 at edge
            return dmg if dmg > 1 else 1
        pos += 4
        time.sleep_ms(25)
    return 0   # never pressed: whiff


def _hit_flash(b, dmg):
    """Show the creeper taking a hit + damage number."""
    sx = (_W - 32) // 2
    _LCD.fillRect(sx, 4, 32, 32, _BLACK)
    time.sleep_ms(80)
    _sprite(_SPR_CREEPER, _PAL_CREEPER, sx, 4, 4)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_RED, _BLACK)
    _LCD.drawString(str(dmg), sx + 38, 14)
    _enemy_hp_bar(b)
    time.sleep_ms(350)
    _LCD.fillRect(sx + 38, 14, 20, 10, _BLACK)


def _dodge_phase(b, kb):
    """The Creeper's turn: move the SOUL, dodge gravel and TNT.
    Runs a fixed 110 frames (~4.5 s). Mutates b['hp']."""
    _box_text(random.choice(_ENEMY_TURNS), color=_GRAY)
    time.sleep_ms(900)
    _LCD.fillRect(_BOX_X, _BOX_Y, _BOX_W, _BOX_H, _BLACK)
    _flush(kb)

    hx = _BOX_X + _BOX_W // 2 - 4
    hy = _BOX_Y + _BOX_H - 14
    step = 7
    shots = []            # [x, y, speed, size, color]
    inv = 0               # invulnerability frames after a hit
    for frame in range(110):
        if frame % 11 == 0:
            size = 5
            speed = 2
            color = _STONE
            if random.randint(0, 3) == 0:     # occasional TNT: faster
                color = _TNT
                speed = 4
            shots.append([_BOX_X + 2 + random.randint(0, _BOX_W - 10),
                          _BOX_Y + 2, speed, size, color])

        i = _poll(kb)
        if i in ("left", "right", "up", "down"):
            _LCD.fillRect(hx, hy, 8, 8, _BLACK)
            if i == "left":
                hx -= step
            elif i == "right":
                hx += step
            elif i == "up":
                hy -= step
            else:
                hy += step
            if hx < _BOX_X + 2:
                hx = _BOX_X + 2
            if hx > _BOX_X + _BOX_W - 10:
                hx = _BOX_X + _BOX_W - 10
            if hy < _BOX_Y + 2:
                hy = _BOX_Y + 2
            if hy > _BOX_Y + _BOX_H - 10:
                hy = _BOX_Y + _BOX_H - 10

        alive = []
        for s in shots:
            _LCD.fillRect(s[0], s[1], s[3], s[3], _BLACK)
            s[1] += s[2]
            if s[1] + s[3] >= _BOX_Y + _BOX_H - 1:
                continue
            _LCD.fillRect(s[0], s[1], s[3], s[3], s[4])
            alive.append(s)
            if inv == 0 and s[0] < hx + 7 and s[0] + s[3] > hx and \
                    s[1] < hy + 7 and s[1] + s[3] > hy:
                b["hp"] -= 4
                if b["hp"] < 0:
                    b["hp"] = 0
                inv = 20
                _player_strip(b)
                if b["hp"] == 0:
                    return
        shots = alive

        if inv > 0:
            inv -= 1
            if inv % 4 < 2:          # flash the soul while invulnerable
                _LCD.fillRect(hx, hy, 8, 8, _BLACK)
            else:
                _sprite(_SPR_HEART, _PAL_HEART, hx, hy, 1)
        else:
            _sprite(_SPR_HEART, _PAL_HEART, hx, hy, 1)
        time.sleep_ms(40)
    _LCD.fillRect(_BOX_X, _BOX_Y, _BOX_W, _BOX_H, _BLACK)


def _game_over(kb):
    """Returns 'retry' (Enter/R). Q/ESC quits via _poll."""
    _LCD.fillScreen(_BLACK)
    _center("GAME OVER", 30, _RED, size=3)
    _center("* You cannot give up just yet...", 70, _CREAM)
    _center("ENTER retry    Q quit", 100, _GRAY)
    _flush(kb)
    while True:
        i = _poll(kb)
        if i in ("confirm", "restart"):
            return "retry"
        time.sleep_ms(40)


def _battle(kb, state):
    """One encounter. Returns 'mine' or 'spare' (the winning route).
    Retries internally on death; raises _Quit if the player quits."""
    while True:
        b = {"hp": state["hp"], "ehp": 24, "ehp_max": 24,
             "bread": state["bread"], "friend": 0, "sel": 0}
        lines = ("* CREEPER attacks you!", "  It looks nervous.")
        while True:
            act = _menu_phase(b, kb, lines)
            if act == "MINE":
                dmg = _attack_phase(b, kb)
                if dmg == 0:
                    lines = ("* Your pick whiffs entirely.", "")
                else:
                    b["ehp"] -= dmg
                    if b["ehp"] < 0:
                        b["ehp"] = 0
                    _hit_flash(b, dmg)
                    lines = ("* You hit for {} damage!".format(dmg), "")
                if b["ehp"] == 0:
                    state["hp"] = b["hp"]
                    return "mine"
            elif act == "TALK":
                t = _TALKS[min(b["friend"], 2)]
                b["friend"] += 1
                lines = t
                if b["friend"] == 2:
                    _player_strip(b)   # name turns yellow: spareable
                    _box_text(t, color=_YELLOW)
                    time.sleep_ms(700)
            elif act == "ITEM":
                if b["bread"] > 0:
                    b["bread"] -= 1
                    b["hp"] = min(20, b["hp"] + 8)
                    _player_strip(b)
                    lines = ("* You eat the bread.",
                             "  Recovered 8 HP!")
                else:
                    lines = ("* Your inventory is empty.", "")
            elif act == "SPARE":
                if b["friend"] >= 2:
                    state["hp"] = b["hp"]
                    state["bread"] = b["bread"]
                    return "spare"
                lines = ("* The Creeper hissses.",
                         "  It doesn't trust you yet.")
            _box_text(lines)
            time.sleep_ms(600)
            _dodge_phase(b, kb)
            if b["hp"] == 0:
                _game_over(kb)
                state["hp"] = 20        # fresh retry
                break                    # rebuild b, restart battle


def _victory(kb, state, route):
    _LCD.fillScreen(_BLACK)
    if route == "spare":
        _sprite(_SPR_CREEPER, _PAL_CREEPER, (_W - 40) // 2, 6, 5)
        _center("YOU WON!", 52, _YELLOW, size=2)
        _center("* The Creeper is your friend.", 76, _CREAM)
        _center("* It gives you 2 emeralds.", 89, _CREAM)
    else:
        _center("YOU WON!", 20, _YELLOW, size=2)
        _center("* The Creeper crumbles", 52, _CREAM)
        _center("  into gravel. +30 XP", 65, _CREAM)
        _center("* ...maybe next time, TALK?", 85, _GRAY)
    _center("ENTER continue", 110, _GRAY)
    _flush(kb)
    _wait_confirm(kb)

    _LCD.fillScreen(_BLACK)
    # size 2: at size 3 this is ~270 px wide and clips off the panel
    _center("DEMO COMPLETE", 34, _ORANGE, size=2)
    _center("Mine-Tale: mini level 1 of ?", 64, _CREAM)
    _center("R play again    Q menu", 96, _GRAY)
    while True:
        if _poll(kb) == "restart":
            return
        time.sleep_ms(40)


# ---------------------------------------------------------------- main

def run():
    try:
        _LCD.setFont(_LCD.FONTS.DejaVu9)
    except Exception:
        pass
    kb = MatrixKeyboard()
    # Swallow the Enter keypress that launched us.
    time.sleep_ms(400)
    try:
        while True:                      # one loop = one full playthrough
            state = {"pos": _START, "creeper": (13, 5), "hp": 20,
                     "bread": 2, "won": False, "intro_done": False}
            _title(kb)
            _overworld(kb, state)
            route = _battle(kb, state)
            state["won"] = True
            _victory(kb, state, route)
    except _Quit:
        pass
    finally:
        # Mirror the other apps' exit protocol: clear the screen
        # before the soft reset so the launcher doesn't briefly
        # flash the last frame of this app.
        try:
            _LCD.fillScreen(_BLACK)
        except Exception as e:
            print("mine_tale: clear warning:", e)
        time.sleep_ms(200)
        machine.reset()


run()
