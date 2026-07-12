"""Mine-Tale — an Undertale-style micro-RPG with a Minecraft heart.

One mini level, end to end:

  1. Title screen (pixel creeper + logo).
  2. Overworld: walk three cave rooms (WASD / arrow cluster); a random
     encounter (Creeper or Zombie) can strike after any step, until
     ten of them have been cleared and the boss door unseals.
  3. Battle, Undertale-rules / Minecraft-flavor:
       MINE  — timing-bar attack (stop the cursor near the center)
       TALK  — befriend the enemy (two talks make it spareable)
       ITEM  — eat bread, restore HP (x2 per run)
       SPARE — ends the fight peacefully once befriended
     Between your turns the enemy attacks: your SOUL (the red heart)
     dodges a generalized set of bullet-hell patterns inside the
     battle box.
  4. Boss: the Villager guards the deep door. How you treated the
     ten miners along the way (mined vs. spared) decides which of
     three boss fights and endings you get: pacifist, neutral, or
     genocide.
  5. Victory -> demo-complete screen.

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
_SHIRT = 0x2B50C8
_STRIPE = 0xC838C8
_TNT = 0xC33B2F
_ZOMB = 0x3E8948
_ZOMB_DK = 0x2C6635
_EMER = 0x17DD62
_ROBE = 0x7A5230
_NOSE = 0xB07850

# ---- dim (telegraph) variants of the above, used by the dodge engine
_DIM_TNT = 0x611D17
_DIM_EMER = 0x0B6E31
_DIM_ZOMB = 0x1F4424

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

_SPR_ZOMBIE = (
    "zzzzzzzz",
    "zZzzzzZz",
    "zXXzzXXz",
    "zzzzzzzz",
    "zzzXXzzz",
    "zzXzzXzz",
    "zzXXXXzz",
    "zZzzzzZz",
)
_PAL_ZOMBIE = {"z": _ZOMB, "Z": _ZOMB_DK, "X": _BLACK}

_SPR_PLAYER = (
    ".hhhhhh.",  # bob hair
    "hhhhhhhh",
    "hffffffh",
    "hfLffLfh",  # closed eyes
    ".ffLLff.",
    "fbbbbbbf",  # shirt
    ".pppppp.",  # shirt stripe
    "..b..b..",
)
_PAL_PLAYER = {"h": _HAIR, "f": _SKIN, "L": _BLACK,
               "b": _SHIRT, "p": _STRIPE}

_SPR_VILLAGER = (
    "..bbbb..",
    ".ffffff.",
    "bfLffLfb",
    ".ffnnff.",
    ".ffnnff.",
    "..fnnf..",
    ".rrrrrr.",
    ".rrrrrr.",
)
_PAL_VILLAGER = {"b": _HAIR, "f": _SKIN, "L": _BLACK,
                 "n": _NOSE, "r": _ROBE}

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
    _center("something stalks these caves", 88, _CREAM)
    # dirt strip along the bottom for the Minecraft of it all
    _LCD.fillRect(0, _H - 18, _W, 18, _DIRT)
    for i in range(0, _W, 12):
        _LCD.fillRect(i + (i // 12 % 3) * 3, _H - 14, 3, 3, _DIRT_LT)
    _center("ENTER start    Q quit", 104, _GRAY)
    _wait_confirm(kb)


# ---------------------------------------------------------------- overworld

_TILE = 16
_MAP_Y = 22
_MAPS = (
    (   # room 0: the entry cave
        "###############",
        "#.....#....*..#",
        "#.##..#..###..#",
        "#..#.....#....D",
        "#*.#..##.#.##.#",
        "#......#......#",
        "###############",
    ),
    (   # room 1: the deep cave
        "###############",
        "#..*....#.....#",
        "#.###...#..##.#",
        "D...#.......#.D",
        "#.#...##..#.#.#",
        "#.#....*..#...#",
        "###############",
    ),
    (   # room 2: the boss hall
        "###############",
        "#.............#",
        "#..*.......*..#",
        "D......B......#",
        "#.............#",
        "#.............#",
        "###############",
    ),
)
# stepping into a door: (room, x, y) -> (room, x, y) landing tile
_DOORS = {
    (0, 14, 3): (1, 1, 3),
    (1, 0, 3): (0, 13, 3),
    (1, 14, 3): (2, 1, 3),
    (2, 0, 3): (1, 13, 3),
}
_START = (1, 1)


def _draw_tile(m, cx, cy):
    x = cx * _TILE
    y = _MAP_Y + cy * _TILE
    t = m[cy][cx]
    if t == "D":
        _LCD.fillRect(x, y, _TILE, _TILE, _BLACK)   # cave opening
    elif t == "#":
        _LCD.fillRect(x, y, _TILE, _TILE, _STONE)
        # mortar cracks, deterministic per tile so redraws are stable
        _LCD.fillRect(x, y + 7, _TILE, 1, _STONE_DK)
        off = (cx * 7 + cy * 5) % 10
        _LCD.fillRect(x + off, y, 1, 7, _STONE_DK)
        _LCD.fillRect(x + (off + 5) % 14, y + 8, 1, 8, _STONE_DK)
    else:
        # plain floor (also covers 'B', the boss tile: it's just dirt
        # with the Villager standing on it)
        _LCD.fillRect(x, y, _TILE, _TILE, _DIRT)
        _LCD.fillRect(x + (cx * 5 + cy * 3) % 12, y + (cx + cy * 7) % 12,
                      2, 2, _DIRT_LT)
        if t == "*":
            for dx, dy in ((3, 3), (9, 5), (5, 10), (11, 11)):
                _LCD.fillRect(x + dx, y + dy, 2, 2, _DIAMOND)


def _draw_map(state):
    m = _MAPS[state["room"]]
    _LCD.fillScreen(_BLACK)
    _LCD.fillRect(0, 0, _W, 20, _DARK)
    _LCD.fillRect(0, 20, _W, 1, _ORANGE)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_ORANGE, _DARK)
    _LCD.drawString("MINE-TALE", 6, 5)
    hp = "HP {}/20".format(state["hp"])
    hp_w = _LCD.textWidth(hp)
    hp_x = _W - 6 - hp_w
    _LCD.setTextColor(_CREAM, _DARK)
    _LCD.drawString(hp, hp_x, 5)
    cleared = "{}/10".format(state["kills"] + state["spares"])
    _LCD.drawString(cleared, hp_x - 8 - _LCD.textWidth(cleared), 5)
    for cy in range(len(m)):
        for cx in range(len(m[0])):
            _draw_tile(m, cx, cy)
    if state["room"] == 2:
        for cy in range(len(m)):
            bx = m[cy].find("B")
            if bx != -1:
                _sprite(_SPR_VILLAGER, _PAL_VILLAGER,
                        bx * _TILE, _MAP_Y + cy * _TILE, 2)
                break
    px, py = state["pos"]
    _sprite(_SPR_PLAYER, _PAL_PLAYER, px * _TILE, _MAP_Y + py * _TILE, 2)


def _alert(px, py):
    """Red '!' pops over the player's head: encounter!"""
    x = px * _TILE + _TILE // 2 - 2
    y = _MAP_Y + py * _TILE - 12
    _LCD.fillRect(x, y, 4, 7, _RED)      # bar of the '!'
    _LCD.fillRect(x, y + 9, 4, 3, _RED)  # dot of the '!'
    time.sleep_ms(600)


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
    """Walk the caves. Returns 'fight' on a random encounter or
    'boss' when the player steps onto the Villager's tile."""
    _draw_map(state)
    m = _MAPS[state["room"]]
    if not state["intro_done"]:
        _dialog(kb, ("* You mined too deep and woke",
                     "  something up. It hunts you now.",
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
            door = _DOORS.get((state["room"], nx, ny))
            if door:
                cleared = state["kills"] + state["spares"]
                if door[0] == 2 and cleared < 10:
                    n = 10 - cleared
                    _dialog(kb, ("* The door is sealed by some",
                                 "  force. {} presences remain..."
                                 .format(n)))
                    _draw_map(state)
                else:
                    state["room"] = door[0]
                    state["pos"] = (door[1], door[2])
                    m = _MAPS[state["room"]]
                    _draw_map(state)
            elif m[ny][nx] != "#":
                state["pos"] = (nx, ny)
                _draw_tile(m, px, py)
                _sprite(_SPR_PLAYER, _PAL_PLAYER,
                        nx * _TILE, _MAP_Y + ny * _TILE, 2)
                if m[ny][nx] == "B":
                    return "boss"
                left = state["left_c"] + state["left_z"]
                if left > 0 and random.randint(1, 20) == 1:
                    _alert(nx, ny)
                    r = random.randint(1, left)
                    kind = "creeper" if r <= state["left_c"] else "zombie"
                    state["enemy"] = kind
                    return "fight"
        time.sleep_ms(40)


# ---------------------------------------------------------------- battle

# battle box (Undertale's white rectangle)
_BOX_X = 30
_BOX_Y = 48
_BOX_W = 180
_BOX_H = 52

_MENU = ("MINE", "TALK", "ITEM", "SPARE")

_TALKS_CREEPER = (
    ("* You tell the Creeper it has",
     "  ssstyle. It seems flattered."),
    ("* You hum a C418 song. The",
     "  Creeper sways. It trusts you!"),
    ("* You are already friends.", ""),
)

_TURNS_CREEPER = (
    ("* The Creeper sways from",
     "  ssside to ssside."),
    ("* Gravel rains from the",
     "  cave roof."),
    ("* The Creeper whispers:",
     "  thisss is fine."),
)

_TALKS_ZOMBIE = (
    ("* You compliment its groan.",
     "  It groans back, pleased."),
    ("* You offer a carrot. The",
     "  Zombie sniffs it, confused."),
    ("* The Zombie groans warmly.",
     "  You're basically friends."),
)

_TURNS_ZOMBIE = (
    ("* The Zombie shuffles",
     "  toward the surface."),
    ("* Arrows whistle from",
     "  its skeleton friends."),
    ("* The Zombie moans:",
     "  brains... or was it bread?"),
)

_ENEMIES = {
    "creeper": {
        "name": "CREEPER", "spr": _SPR_CREEPER, "pal": _PAL_CREEPER,
        "ehp": 24, "dmg": 4, "patterns": ("gravel", "blast"),
        "talks": _TALKS_CREEPER, "turns": _TURNS_CREEPER,
        "intro": ("* CREEPER attacks you!", "  It looks nervous."),
    },
    "zombie": {
        "name": "ZOMBIE", "spr": _SPR_ZOMBIE, "pal": _PAL_ZOMBIE,
        "ehp": 28, "dmg": 4, "patterns": ("arms", "arrows", "hail"),
        "talks": _TALKS_ZOMBIE, "turns": _TURNS_ZOMBIE,
        "intro": ("* ZOMBIE lurches closer!", "  It groans hungrily."),
    },
}


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
    e = b["e"]
    _LCD.fillScreen(_BLACK)
    _sprite(e["spr"], e["pal"], (_W - 32) // 2, 4, 4)
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
    """Show the enemy taking a hit + damage number."""
    e = b["e"]
    sx = (_W - 32) // 2
    _LCD.fillRect(sx, 4, 32, 32, _BLACK)
    time.sleep_ms(80)
    _sprite(e["spr"], e["pal"], sx, 4, 4)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_RED, _BLACK)
    _LCD.drawString(str(dmg), sx + 38, 14)
    _enemy_hp_bar(b)
    time.sleep_ms(350)
    _LCD.fillRect(sx + 38, 14, 20, 10, _BLACK)


# ------------------------------------------------------- dodge engine

# Shot = [x, y, dx, dy, w, h, color, tele, life]
#   tele  > 0: telegraphed (dim outline, no movement/collision), counts
#              down to 0 (then the shot goes solid and active).
#   life  > 0: counts down every active frame; dies at 0.
#   life == -1: lives until it has fully left the battle box.

_DIM = {_TNT: _DIM_TNT, _EMER: _DIM_EMER, _ZOMB: _DIM_ZOMB}


def _erase_shot(s):
    _LCD.fillRect(s[0], s[1], s[4], s[5], _BLACK)


def _draw_shot(s):
    x, y, w, h, color, tele = s[0], s[1], s[4], s[5], s[6], s[7]
    if tele > 0:
        dim = _DIM.get(color, _GRAY)
        _LCD.fillRect(x, y, w, 1, dim)
        _LCD.fillRect(x, y + h - 1, w, 1, dim)
        _LCD.fillRect(x, y, 1, h, dim)
        _LCD.fillRect(x + w - 1, y, 1, h, dim)
    else:
        _LCD.fillRect(x, y, w, h, color)


def _spawn_gravel(frame, shots, hx, hy):
    if frame % 11 == 0:
        color = _STONE
        dy = 2
        if random.randint(1, 4) == 1:      # 25% TNT
            color = _TNT
            dy = 4
        x = _BOX_X + 2 + random.randint(0, _BOX_W - 10)
        shots.append([x, _BOX_Y + 2, 0, dy, 5, 5, color, 0, -1])


def _spawn_blast(frame, shots, hx, hy):
    if frame % 35 == 0:
        qw = _BOX_W // 2
        qh = _BOX_H // 2
        coords = ((_BOX_X, _BOX_Y), (_BOX_X + qw, _BOX_Y),
                  (_BOX_X, _BOX_Y + qh), (_BOX_X + qw, _BOX_Y + qh))
        safe = random.randint(0, 3)
        for i in range(4):
            if i == safe:
                continue
            qx, qy = coords[i]
            shots.append([qx + 1, qy + 1, 0, 0, qw - 2, qh - 2,
                          _TNT, 14, 8])


def _spawn_arms(frame, shots, hx, hy):
    if frame % 20 == 0:
        y = _BOX_Y + 2 + random.randint(0, _BOX_H - 10)
        w, h = 50, 8
        if random.randint(0, 1) == 0:
            x = _BOX_X + 1
            dx = 2
        else:
            x = _BOX_X + _BOX_W - 1 - w
            dx = -2
        shots.append([x, y, dx, 0, w, h, _ZOMB, 6, 30])


def _spawn_arrows(frame, shots, hx, hy):
    if frame % 8 == 0:
        y = _BOX_Y + 2 + random.randint(0, _BOX_H - 4)
        if random.randint(0, 1) == 0:
            x = _BOX_X + 1
            dx = 5
        else:
            x = _BOX_X + _BOX_W - 9
            dx = -5
        shots.append([x, y, dx, 0, 8, 2, _CREAM, 0, -1])


def _spawn_hail(frame, shots, hx, hy):
    if frame % 7 == 0:
        x = _BOX_X + 2 + random.randint(0, _BOX_W - 6)
        dy = random.randint(3, 5)
        shots.append([x, _BOX_Y + 2, 0, dy, 4, 4, _STONE, 0, -1])


def _spawn_shard_h(frame, shots, hx, hy):
    if frame % 26 == 0:
        dx = 4 if random.randint(0, 1) == 0 else -4
        x = _BOX_X + 1 if dx > 0 else _BOX_X + _BOX_W - 7
        gap_y = _BOX_Y + 2 + random.randint(0, _BOX_H - 4 - 20)
        top_h = gap_y - (_BOX_Y + 2)
        if top_h > 0:
            shots.append([x, _BOX_Y + 2, dx, 0, 6, top_h, _EMER, 0, -1])
        bot_y = gap_y + 20
        bot_h = (_BOX_Y + _BOX_H - 2) - bot_y
        if bot_h > 0:
            shots.append([x, bot_y, dx, 0, 6, bot_h, _EMER, 0, -1])


def _spawn_shard_v(frame, shots, hx, hy):
    if frame % 26 == 0:
        dy = 3 if random.randint(0, 1) == 0 else -3
        y = _BOX_Y + 1 if dy > 0 else _BOX_Y + _BOX_H - 4
        gap_x = _BOX_X + 2 + random.randint(0, _BOX_W - 4 - 24)
        left_w = gap_x - (_BOX_X + 2)
        if left_w > 0:
            shots.append([_BOX_X + 2, y, 0, dy, left_w, 3, _EMER, 0, -1])
        gx2 = gap_x + 24
        right_w = (_BOX_X + _BOX_W - 2) - gx2
        if right_w > 0:
            shots.append([gx2, y, 0, dy, right_w, 3, _EMER, 0, -1])


def _spawn_blaster(frame, shots, hx, hy):
    if frame % 30 == 0:
        if random.randint(0, 1) == 0:
            y = _BOX_Y + 2 + random.randint(0, _BOX_H - 14)
            shots.append([_BOX_X + 1, y, 0, 0, _BOX_W - 2, 12,
                          _EMER, 16, 7])
        else:
            x = _BOX_X + 2 + random.randint(0, _BOX_W - 16)
            shots.append([x, _BOX_Y + 1, 0, 0, 14, _BOX_H - 2,
                          _EMER, 16, 7])


def _spawn_emeralds(frame, shots, hx, hy):
    if frame % 18 == 0:
        x = _BOX_X + 2 + random.randint(0, _BOX_W - 7)
        shots.append([x, _BOX_Y + 2, 0, 1, 5, 5, _EMER, 0, -1])


def _spawn_mix(frame, shots, hx, hy):
    if frame % 60 < 30:
        _spawn_blaster(frame, shots, hx, hy)
    else:
        _spawn_shard_h(frame, shots, hx, hy)


_PATTERNS = {
    "gravel": (_spawn_gravel, 110),
    "blast": (_spawn_blast, 110),
    "arms": (_spawn_arms, 110),
    "arrows": (_spawn_arrows, 110),
    "hail": (_spawn_hail, 100),
    "shard_h": (_spawn_shard_h, 130),
    "shard_v": (_spawn_shard_v, 130),
    "blaster": (_spawn_blaster, 140),
    "emeralds": (_spawn_emeralds, 60),
    "mix": (_spawn_mix, 150),
}

# Populated in run() if the frozen MatrixKeyboard module exposes a
# held-key-state accessor; _dodge doesn't require it (autorepeat
# already produces a smooth glide) but can lean on it if available.
_KS = None


def _dodge(kb, b, pattern, frames, dmg):
    """Generic bullet-hell dodge phase shared by regular and boss
    battles. `pattern` selects a spawner from _PATTERNS; `frames` <= 0
    uses that pattern's default duration. Mutates b['hp']. Q/ESC
    exits via _Quit, same as every other phase."""
    spawner, default_frames = _PATTERNS[pattern]
    if frames <= 0:
        frames = default_frames
    _LCD.fillRect(_BOX_X, _BOX_Y, _BOX_W, _BOX_H, _BLACK)
    _flush(kb)

    hx = _BOX_X + _BOX_W // 2 - 4
    hy = _BOX_Y + _BOX_H - 14
    _sprite(_SPR_HEART, _PAL_HEART, hx, hy, 1)
    hdir = None
    ht = 0
    shots = []
    inv = 0
    for frame in range(frames):
        i = _poll(kb)
        if i in ("left", "right", "up", "down"):
            hdir = i
            ht = 6                 # held-key latch: refreshed by autorepeat

        moved_heart = False
        if ht > 0:
            ht -= 1
            _LCD.fillRect(hx, hy, 8, 8, _BLACK)
            if hdir == "left":
                hx -= 3
            elif hdir == "right":
                hx += 3
            elif hdir == "up":
                hy -= 3
            else:
                hy += 3
            if hx < _BOX_X + 2:
                hx = _BOX_X + 2
            if hx > _BOX_X + _BOX_W - 10:
                hx = _BOX_X + _BOX_W - 10
            if hy < _BOX_Y + 2:
                hy = _BOX_Y + 2
            if hy > _BOX_Y + _BOX_H - 10:
                hy = _BOX_Y + _BOX_H - 10
            moved_heart = True

        alive = []
        for s in shots:
            dx, dy = s[2], s[3]
            static = (dx == 0 and dy == 0)
            if s[7] > 0:                       # telegraphed
                s[7] -= 1
                if s[7] == 0:
                    _erase_shot(s)
                    _draw_shot(s)               # now solid
                alive.append(s)
                continue
            moved = False
            if not static:
                _erase_shot(s)
                s[0] += dx
                s[1] += dy
                moved = True
            x, y, w, h = s[0], s[1], s[4], s[5]
            off_box = (x + w <= _BOX_X or x >= _BOX_X + _BOX_W or
                       y + h <= _BOX_Y or y >= _BOX_Y + _BOX_H)
            if s[8] > 0:
                s[8] -= 1
                dead = s[8] <= 0
            else:
                dead = off_box
            if dead:
                _erase_shot(s)
                continue
            if moved:
                _draw_shot(s)
            if inv == 0 and x < hx + 8 and x + w > hx and \
                    y < hy + 8 and y + h > hy:
                b["hp"] -= dmg
                if b["hp"] < 0:
                    b["hp"] = 0
                inv = 20
                _player_strip(b)
            alive.append(s)
        shots = alive

        if b["hp"] == 0:
            _LCD.fillRect(hx, hy, 8, 8, _BLACK)
            _LCD.fillRect(_BOX_X, _BOX_Y, _BOX_W, _BOX_H, _BLACK)
            return

        if inv > 0:
            inv -= 1
            if not moved_heart:
                _LCD.fillRect(hx, hy, 8, 8, _BLACK)
            if inv % 4 >= 2:            # flash the soul while invulnerable
                _sprite(_SPR_HEART, _PAL_HEART, hx, hy, 1)
        elif moved_heart:
            _sprite(_SPR_HEART, _PAL_HEART, hx, hy, 1)

        before = len(shots)
        spawner(frame, shots, hx, hy)
        for s in shots[before:]:
            _draw_shot(s)

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


def _battle(kb, state, kind):
    """One regular encounter against `kind` ('creeper' or 'zombie').
    Returns 'mine' or 'spare' (the winning route). Retries internally
    on death (state's counters are untouched by a retry); raises
    _Quit if the player quits."""
    e = _ENEMIES[kind]
    while True:
        b = {"hp": state["hp"], "ehp": e["ehp"], "ehp_max": e["ehp"],
             "bread": state["bread"], "friend": 0, "sel": 0, "e": e}
        lines = e["intro"]
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
                    state["bread"] = b["bread"]
                    return "mine"
            elif act == "TALK":
                t = e["talks"][min(b["friend"], 2)]
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
                lines = ("* The {} isn't won over.".format(e["name"]),
                         "  It doesn't trust you yet.")
            _box_text(lines)
            time.sleep_ms(600)
            _box_text(random.choice(e["turns"]), color=_GRAY)
            time.sleep_ms(900)
            _dodge(kb, b, random.choice(e["patterns"]), 0, e["dmg"])
            if b["hp"] == 0:
                _game_over(kb)
                state["hp"] = 20        # fresh retry
                break                    # rebuild b, restart battle


# ------------------------------------------------------------ boss battle

_BOSS = {"name": "VILLAGER", "spr": _SPR_VILLAGER, "pal": _PAL_VILLAGER}

_BOSS_EHP = {"pacifist": 12, "neutral": 40, "genocide": 60}

# route -> tuple of (dialog_lines, pattern, frames, dmg), one per turn
_BOSS_SCRIPT = {
    "pacifist": (
        (("* Villager: Hrmm! A friend!",
          "  Let's... pretend fight?"), "emeralds", 60, 1),
        (("* Villager: Catch! Free",
          "  samples! Hrng hrng!"), "emeralds", 60, 1),
        (("* Villager: Okay I'm tired.",
          "  Just SPARE me already."), "emeralds", 45, 1),
    ),
    "neutral": (
        (("* You spared some.",
          "  You mined some. Hrmm."), "gravel", 100, 4),
        (("* The Villager juggles",
          "  emerald shards."), "shard_h", 110, 4),
        (("* Villager: Which side are",
          "  you really on?"), "shard_v", 110, 4),
        (("* The Villager watches",
          "  you carefully."), "blaster", 110, 4),
    ),
    "genocide": (
        (("* Villager: You killed them",
          "  ALL. No trades for you."), "blaster", 140, 5),
        (("* Emerald light floods",
          "  the chamber."), "shard_h", 130, 5),
        (("* Villager: HRMM. You're",
          "  still standing?"), "shard_v", 130, 5),
        (("* The air itself turns",
          "  emerald green."), "mix", 150, 5),
        (("* Villager: I could do",
          "  this all day."), "blaster", 140, 5),
    ),
}

_BOSS_TALKS = {
    "pacifist": (
        ("* Villager: Wanna trade?",
         "  One emerald for ALL your bread."),
        ("* Villager: Hrmm, bad deal?",
         "  Two emeralds, then. Final offer."),
        ("* Villager: You're funny.",
         "  I like you. Hrmm hrmm."),
    ),
    "neutral": (
        ("* Villager: Hrmm. You're",
         "  not what I expected."),
        ("* Villager: ...you might",
         "  be alright, actually."),
        ("* Villager: I'm warming",
         "  up to you. Slowly."),
    ),
    "genocide": (
        ("* Villager: Save it.", ""),
        ("* Villager: Hrmm. No.", ""),
        ("* Villager: Words won't",
         "  fix this."),
    ),
}


def _route(state):
    """Which boss fight the ten cleared miners earned."""
    if state["spares"] == 10:
        return "pacifist"
    if state["kills"] == 10:
        return "genocide"
    return "neutral"


def _villager_sidestep():
    """Genocide-only: the Villager dodges a MINE while dodges remain."""
    sx = (_W - 32) // 2
    _LCD.fillRect(sx, 4, 32, 32, _BLACK)
    _sprite(_SPR_VILLAGER, _PAL_VILLAGER, sx + 20, 4, 4)
    time.sleep_ms(200)
    _LCD.fillRect(sx + 20, 4, 32, 32, _BLACK)
    _sprite(_SPR_VILLAGER, _PAL_VILLAGER, sx, 4, 4)


def _boss_battle(kb, state, route):
    """The Villager fight. `route` ('pacifist' | 'neutral' |
    'genocide') was decided in run() from the kill/spare tally and
    picks the whole script + ending. Reuses _menu_phase/_attack_phase/
    _hit_flash/_dodge/_player_strip like a regular battle. Returns
    'spare' or 'mine'."""
    ehp_max = _BOSS_EHP[route]
    script = _BOSS_SCRIPT[route]
    while True:
        b = {"hp": state["hp"], "ehp": ehp_max, "ehp_max": ehp_max,
             "bread": state["bread"], "friend": 0, "sel": 0, "e": _BOSS}
        b["dodges"] = 3 if route == "genocide" else 0
        b["turn"] = 0
        b["phase2"] = False
        lines = ("* VILLAGER blocks the door.", "  Hrmm...?")
        while True:
            act = _menu_phase(b, kb, lines)
            if act == "MINE":
                dmg = _attack_phase(b, kb)
                if route == "genocide" and b["dodges"] > 0:
                    _villager_sidestep()
                    b["dodges"] -= 1
                    lines = ("* The Villager steps aside.",
                             "  Hrmm. Too slow.")
                elif dmg == 0:
                    lines = ("* Your pick whiffs entirely.", "")
                else:
                    b["ehp"] -= dmg
                    if b["ehp"] < 0:
                        b["ehp"] = 0
                    _hit_flash(b, dmg)
                    lines = ("* You hit for {} damage!".format(dmg), "")
                if route == "genocide" and not b["phase2"] and \
                        b["ehp"] <= ehp_max // 2:
                    b["phase2"] = True
                    _box_text(("* Villager: HRMMMM!",
                               "  FINAL DISCOUNT."), color=_YELLOW)
                    time.sleep_ms(900)
                if b["ehp"] == 0:
                    state["hp"] = b["hp"]
                    state["bread"] = b["bread"]
                    return "mine"
            elif act == "TALK":
                t = _BOSS_TALKS[route][min(b["friend"], 2)]
                b["friend"] += 1
                lines = t
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
                if route == "pacifist":
                    state["hp"] = b["hp"]
                    state["bread"] = b["bread"]
                    return "spare"
                if route == "genocide":
                    lines = ("* You raise your hand.",
                             "  He remembers. Nothing happens.")
                elif b["friend"] >= 3:
                    state["hp"] = b["hp"]
                    state["bread"] = b["bread"]
                    return "spare"
                else:
                    lines = ("* Villager: Hrmm. Not yet.", "")
            _box_text(lines)
            time.sleep_ms(600)

            idx = b["turn"]
            if idx >= len(script):
                idx = len(script) - 2 + (b["turn"] % 2)
            turn_lines, pattern, frames, dmg = script[idx]
            if b["phase2"]:
                pattern = "mix"
            b["turn"] += 1
            _box_text(turn_lines, color=_GRAY)
            time.sleep_ms(900)
            _dodge(kb, b, pattern, frames, dmg)
            if b["hp"] == 0:
                _game_over(kb)
                state["hp"] = 20        # fresh retry, counters intact
                break                    # rebuild b, restart the boss


def _victory(kb, state, route, result):
    _LCD.fillScreen(_BLACK)
    if route == "pacifist":
        _sprite(_SPR_VILLAGER, _PAL_VILLAGER, (_W - 40) // 2, 6, 5)
        _center("* Everyone lived. The Villager", 54, _CREAM)
        _center("* throws a trade party.", 67, _CREAM)
        _center("* Best price: friendship.", 80, _CREAM)
        _center("ENTER continue", 104, _GRAY)
    elif route == "genocide":
        _center("* The caves are silent now.", 56, _RED)
        _center("* Was it worth the emeralds?", 69, _RED)
        _center("ENTER continue", 100, _GRAY)
    else:
        _center("* The Villager nods. 'Hrmm.'", 56, _CREAM)
        _center("* 'Come back when you know", 69, _CREAM)
        _center("  who you are.'", 82, _CREAM)
        _center("ENTER continue", 104, _GRAY)
    _flush(kb)
    _wait_confirm(kb)

    _LCD.fillScreen(_BLACK)
    # size 2: at size 3 this is ~270 px wide and clips off the panel
    _center("MINE-TALE COMPLETE", 34, _ORANGE, size=2)
    _center("Mine-Tale: mini level 1 of ?", 64, _CREAM)
    _center("R play again    Q menu", 96, _GRAY)
    while True:
        if _poll(kb) == "restart":
            return
        time.sleep_ms(40)


# ---------------------------------------------------------------- main

def run():
    global _KS
    try:
        _LCD.setFont(_LCD.FONTS.DejaVu9)
    except Exception:
        pass
    kb = MatrixKeyboard()
    # Optional: if the frozen keyboard module exposes a held-key-state
    # accessor, remember it. Nothing currently requires it (the dodge
    # engine's latch-and-autorepeat already glides smoothly), but it's
    # there to lean on later without another hardware probing round.
    for _name in ("get_keys_state", "get_pressed", "is_pressed"):
        try:
            if hasattr(kb, _name):
                _KS = getattr(kb, _name)
                break
        except Exception:
            pass
    # Swallow the Enter keypress that launched us.
    time.sleep_ms(400)
    try:
        while True:                      # one loop = one full playthrough
            state = {"pos": _START, "room": 0, "hp": 20, "bread": 2,
                     "intro_done": False, "kills": 0, "spares": 0,
                     "left_c": 5, "left_z": 5, "boss_done": False,
                     "enemy": None}
            _title(kb)
            route = None
            result = None
            while not state["boss_done"]:
                ev = _overworld(kb, state)
                if ev == "boss":
                    route = _route(state)
                    result = _boss_battle(kb, state, route)
                    state["boss_done"] = True
                else:
                    kind = state["enemy"]
                    result = _battle(kb, state, kind)
                    if result == "mine":
                        state["kills"] += 1
                    else:
                        state["spares"] += 1
                    if kind == "creeper":
                        state["left_c"] -= 1
                    else:
                        state["left_z"] -= 1
                    cleared = state["kills"] + state["spares"]
                    _dialog(kb, ("* {} cleared. ({}/10)".format(
                        _ENEMIES[kind]["name"], cleared), ""))
                    if cleared == 10:
                        _dialog(kb, ("* You hear a distant door",
                                     "  unseal..."))
                    _draw_map(state)
            _victory(kb, state, route, result)
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
