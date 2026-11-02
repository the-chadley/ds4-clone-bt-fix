"""ds4art — vector line-art DS4-style controller, drawn with pycairo.

Everything is in "design" coordinates (roughly 700x430, mirror axis at CX).
The base image holds the static outline; each control also has a
highlighted sprite so the GUI can show/hide it cheaply.
"""
import io
import math

import cairo

CX = 500                    # mirror axis
LINE = '#e8eaed'
DIM = '#5d6673'
ACCENT = '#1a9fff'
BG = '#151b24'
FACE_COLORS = {'triangle': '#3fc8c0', 'circle': '#f06d74',
               'cross': '#8fb3f0', 'square': '#d796d8'}

# ------------------------------------------------------------ geometry
DPAD = (287, 163)
FACE = (714, 163)
FACE_OFF = {'triangle': (0, -51), 'circle': (51, 0), 'cross': (0, 51), 'square': (-51, 0)}
FACE_R = 21
RING_R = 81
STICKS = {'left': (392, 261), 'right': (608, 261)}
STICK_RING = 66
KNOB_R = 44
SHARE = (360, 93)
OPTIONS = (640, 93)
HOME = (500, 261)
TOUCH = (386, 58, 614, 187)         # x0 y0 x1 y1
LIGHTBAR = (392, 61, 608, 65)
SIZE = (1000, 480)                  # canvas size the design fits in


def hexrgb(h, a=1.0):
    h = h.lstrip('#')
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, a)


def mirrored(cr, fn):
    """Run fn on the right half, then again mirrored onto the left half."""
    fn(cr)
    cr.save()
    cr.translate(2 * CX, 0)
    cr.scale(-1, 1)
    fn(cr)
    cr.restore()


def rounded_poly(cr, pts, r):
    n = len(pts)
    cr.new_sub_path()
    for i in range(n):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
        v1 = (p0[0] - p1[0], p0[1] - p1[1])
        v2 = (p2[0] - p1[0], p2[1] - p1[1])
        l1, l2 = math.hypot(*v1), math.hypot(*v2)
        rr = min(r, l1 / 2, l2 / 2)
        a = (p1[0] + v1[0] / l1 * rr, p1[1] + v1[1] / l1 * rr)
        b = (p1[0] + v2[0] / l2 * rr, p1[1] + v2[1] / l2 * rr)
        if i == 0:
            cr.move_to(*a)
        else:
            cr.line_to(*a)
        cr.curve_to(p1[0], p1[1], p1[0], p1[1], *b)
    cr.close_path()


def fillet_poly(cr, pts, r):
    """Convex polygon (clockwise on screen) with true circular-arc corners."""
    n = len(pts)
    cr.new_sub_path()
    for i in range(n):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
        u1 = (p0[0] - p1[0], p0[1] - p1[1])
        u2 = (p2[0] - p1[0], p2[1] - p1[1])
        l1, l2 = math.hypot(*u1), math.hypot(*u2)
        u1, u2 = (u1[0] / l1, u1[1] / l1), (u2[0] / l2, u2[1] / l2)
        half = math.acos(max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))) / 2
        bis = (u1[0] + u2[0], u1[1] + u2[1])
        bl = math.hypot(*bis)
        d = r / math.sin(half)                       # corner → arc centre
        c = (p1[0] + bis[0] / bl * d, p1[1] + bis[1] / bl * d)
        t = r / math.tan(half)                       # corner → tangent points
        a = (p1[0] + u1[0] * t, p1[1] + u1[1] * t)
        b = (p1[0] + u2[0] * t, p1[1] + u2[1] * t)
        cr.arc(c[0], c[1], r, math.atan2(a[1] - c[1], a[0] - c[0]),
               math.atan2(b[1] - c[1], b[0] - c[0]))
    cr.close_path()


def rounded_rect(cr, x0, y0, x1, y1, r):
    rounded_poly(cr, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], r)


def stroke(cr, color=LINE, width=3.0, fill=None):
    if fill:
        cr.set_source_rgba(*hexrgb(fill) if isinstance(fill, str) else fill)
        cr.fill_preserve()
    cr.set_source_rgba(*hexrgb(color))
    cr.set_line_width(width)
    cr.stroke()


# ------------------------------------------------------------ shapes
def body_half(cr):
    """Right half of the controller shell, top-centre → bottom-centre."""
    sx, sy = STICKS['right']
    r = STICK_RING + 4
    cr.move_to(CX, 55)
    cr.line_to(650, 55)
    cr.curve_to(662, 45, 742, 42, 770, 54)            # shoulder under bumper
    cr.curve_to(800, 68, 820, 118, 830, 178)          # outer edge
    cr.curve_to(845, 268, 848, 360, 843, 400)
    cr.curve_to(838, 448, 795, 462, 763, 452)         # grip bottom
    cr.curve_to(735, 440, 705, 390, 683, 336)         # inner grip
    cr.curve_to(678, 324, 672, 316, 666, 310)
    # wrap around the bottom of the stick ring, then across the centre
    a0 = math.atan2(310 - sy, 666 - sx)
    a1 = math.atan2(306 - sy, sx - math.sqrt(r * r - (306 - sy) ** 2) - sx)
    cr.arc(sx, sy, r, a0, a1)
    cr.line_to(CX, 306)


def bumper_path(cr, trigger=False):
    if trigger:     # L2/R2: the taller tab behind the bumper
        cr.move_to(662, 46)
        cr.curve_to(668, 30, 700, 26, 728, 27)
        cr.curve_to(748, 28, 762, 34, 766, 50)
        cr.curve_to(735, 40, 690, 40, 662, 46)
    else:           # L1/R1
        cr.move_to(656, 54)
        cr.curve_to(660, 44, 700, 38, 730, 40)
        cr.curve_to(752, 41, 768, 46, 772, 56)
    cr.close_path()


def dpad_arm(cr, direction):
    angle = {'up': 0, 'right': 90, 'down': 180, 'left': 270}[direction]
    pts = [(-19, -60), (19, -60), (19, -24), (0, -7), (-19, -24)]
    a = math.radians(angle)
    cx, cy = DPAD
    pts = [(cx + x * math.cos(a) - y * math.sin(a),
            cy + x * math.sin(a) + y * math.cos(a)) for x, y in pts]
    rounded_poly(cr, pts, 7)


def face_symbol(cr, name, color, width=2.2):
    ox, oy = FACE_OFF[name]
    cx, cy = FACE[0] + ox, FACE[1] + oy
    if name == 'triangle':
        cr.move_to(cx, cy - 10)
        cr.line_to(cx + 11, cy + 8)
        cr.line_to(cx - 11, cy + 8)
        cr.close_path()
    elif name == 'circle':
        cr.new_sub_path()
        cr.arc(cx, cy, 10, 0, 2 * math.pi)
    elif name == 'cross':
        cr.move_to(cx - 9, cy - 9); cr.line_to(cx + 9, cy + 9)
        cr.move_to(cx - 9, cy + 9); cr.line_to(cx + 9, cy - 9)
    elif name == 'square':
        cr.rectangle(cx - 9, cy - 9, 18, 18)
    stroke(cr, color, width)


def pill(cr, cx, cy, w=20, h=40):
    rounded_rect(cr, cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, w / 2)


def home_shape(cr):
    """The clone's home button: an upside-down triangle with rounded corners."""
    cx, cy = HOME
    fillet_poly(cr, [(cx - 28, cy - 17), (cx + 28, cy - 17), (cx, cy + 28)], 11)


def home_icon(cr, color):
    """Small house glyph printed on the home button."""
    cx, cy = HOME[0], HOME[1] - 3
    cr.save()
    cr.translate(cx, cy); cr.scale(0.72, 0.72); cr.translate(-cx, -cy)
    cr.move_to(cx - 10, cy - 1)
    cr.line_to(cx, cy - 10)
    cr.line_to(cx + 10, cy - 1)
    cr.move_to(cx - 7, cy - 3)
    cr.line_to(cx - 7, cy + 9)
    cr.line_to(cx + 7, cy + 9)
    cr.line_to(cx + 7, cy - 3)
    cr.move_to(cx - 2.5, cy + 9)
    cr.line_to(cx - 2.5, cy + 3)
    cr.line_to(cx + 2.5, cy + 3)
    cr.line_to(cx + 2.5, cy + 9)
    cr.restore()
    stroke(cr, color, 1.8)


def tiny_arrow(cr, cx, cy, direction):
    a = math.radians({'up': 0, 'right': 90, 'down': 180, 'left': 270}[direction])
    pts = [(0, -5), (6, 3), (-6, 3)]
    pts = [(cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a))
           for x, y in pts]
    cr.move_to(*pts[0]); cr.line_to(*pts[1]); cr.line_to(*pts[2]); cr.close_path()
    stroke(cr, DIM, 1.5)


# ------------------------------------------------------------ base image
def draw_base(cr):
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)

    def shell(c):
        body_half(c); stroke(c, LINE, 4.0)
        bumper_path(c, trigger=True); stroke(c, LINE, 3.0)
        bumper_path(c); stroke(c, LINE, 3.0, fill=BG)
    mirrored(cr, shell)

    # touchpad
    rounded_rect(cr, *TOUCH, 8)
    stroke(cr, LINE, 3.0)

    # d-pad ring + arms + direction ticks
    cr.new_sub_path(); cr.arc(*DPAD, RING_R, 0, 2 * math.pi); stroke(cr, DIM, 2.0)
    for d in ('up', 'down', 'left', 'right'):
        dpad_arm(cr, d); stroke(cr, LINE, 2.6)
    for d, (dx, dy) in (('up', (0, -70)), ('down', (0, 70)),
                        ('left', (-70, 0)), ('right', (70, 0))):
        tiny_arrow(cr, DPAD[0] + dx, DPAD[1] + dy, d)

    # face ring + buttons
    cr.new_sub_path(); cr.arc(*FACE, RING_R, 0, 2 * math.pi); stroke(cr, DIM, 2.0)
    for name, (ox, oy) in FACE_OFF.items():
        cr.new_sub_path(); cr.arc(FACE[0] + ox, FACE[1] + oy, FACE_R, 0, 2 * math.pi)
        stroke(cr, LINE, 2.6)
        face_symbol(cr, name, FACE_COLORS[name])

    # share / options
    pill(cr, *SHARE); stroke(cr, LINE, 2.6)
    pill(cr, *OPTIONS); stroke(cr, LINE, 2.6)

    # speaker grille
    for row, (n, y) in enumerate(((5, 201), (4, 211), (5, 221))):
        for i in range(n):
            x = CX + (i - (n - 1) / 2) * 11
            cr.new_sub_path(); cr.arc(x, y, 2.6, 0, 2 * math.pi)
            stroke(cr, DIM, 1.2)

    # home button
    home_shape(cr); stroke(cr, LINE, 2.6)
    home_icon(cr, LINE)

    # stick wells (the knobs are sprites so they can move)
    for sx, sy in STICKS.values():
        cr.new_sub_path(); cr.arc(sx, sy, STICK_RING, 0, 2 * math.pi)
        stroke(cr, DIM, 2.0, fill=BG)


# ------------------------------------------------------------ sprites
# name -> (bbox, draw_fn). Bboxes are in design coords.
def _face_pressed(name):
    def fn(cr):
        ox, oy = FACE_OFF[name]
        cr.new_sub_path(); cr.arc(FACE[0] + ox, FACE[1] + oy, FACE_R, 0, 2 * math.pi)
        stroke(cr, '#ffffff', 2.6, fill=FACE_COLORS[name])
        face_symbol(cr, name, BG, 2.6)
    ox, oy = FACE_OFF[name]
    x, y = FACE[0] + ox, FACE[1] + oy
    return (x - 26, y - 26, x + 26, y + 26), fn


def _dpad_pressed(d):
    def fn(cr):
        dpad_arm(cr, d); stroke(cr, '#ffffff', 2.6, fill=ACCENT)
    return (DPAD[0] - 66, DPAD[1] - 66, DPAD[0] + 66, DPAD[1] + 66), fn


def _pill_pressed(cx, cy):
    def fn(cr):
        pill(cr, cx, cy); stroke(cr, '#ffffff', 2.6, fill=ACCENT)
    return (cx - 16, cy - 26, cx + 16, cy + 26), fn


def _home_pressed(cr):
    home_shape(cr); stroke(cr, '#ffffff', 2.6, fill=ACCENT)
    home_icon(cr, '#ffffff')


def _bumper_pressed(side, trigger):
    def fn(cr):
        if side == 'left':
            cr.translate(2 * CX, 0); cr.scale(-1, 1)
        bumper_path(cr, trigger); stroke(cr, '#ffffff', 3.0, fill=ACCENT)
    x0, x1 = (650, 778) if side == 'right' else (2 * CX - 778, 2 * CX - 650)
    return (x0, 20, x1, 62), fn


def _knob(style):
    def fn(cr):
        cx, cy = 0, 0
        fill = {'idle': BG, 'moved': BG, 'pressed': ACCENT}[style]
        ring = {'idle': LINE, 'moved': ACCENT, 'pressed': '#ffffff'}[style]
        cr.new_sub_path(); cr.arc(cx, cy, KNOB_R, 0, 2 * math.pi)
        stroke(cr, ring, 3.4, fill=fill)
        cr.new_sub_path(); cr.arc(cx, cy, KNOB_R - 14, 0, 2 * math.pi)
        stroke(cr, ring, 2.6)
    r = KNOB_R + 4
    return (-r, -r, r, r), fn


def _touch_pressed(cr):
    rounded_rect(cr, *TOUCH, 8)
    stroke(cr, '#ffffff', 3.0, fill=hexrgb(ACCENT, 0.30))


def _touch_dot(cr):
    cr.new_sub_path(); cr.arc(0, 0, 9, 0, 2 * math.pi)
    stroke(cr, '#ffffff', 2.0, fill=ACCENT)


SPRITES = {f'face_{n}': _face_pressed(n) for n in FACE_OFF}
SPRITES.update({f'dpad_{d}': _dpad_pressed(d) for d in ('up', 'down', 'left', 'right')})
SPRITES.update({
    'share': _pill_pressed(*SHARE),
    'options': _pill_pressed(*OPTIONS),
    'home': ((HOME[0] - 30, HOME[1] - 22, HOME[0] + 30, HOME[1] + 30), _home_pressed),
    'l1': _bumper_pressed('left', False), 'r1': _bumper_pressed('right', False),
    'l2': _bumper_pressed('left', True), 'r2': _bumper_pressed('right', True),
    'knob_idle': _knob('idle'), 'knob_moved': _knob('moved'),
    'knob_pressed': _knob('pressed'),
    'touch_click': ((TOUCH[0] - 4, TOUCH[1] - 4, TOUCH[2] + 4, TOUCH[3] + 4), _touch_pressed),
    'touch_dot': ((-12, -12, 12, 12), _touch_dot),
})


# ------------------------------------------------------------ rendering
def _png(w, h, ox, oy, fn, scale=1.0):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, int(w * scale)), max(1, int(h * scale)))
    cr = cairo.Context(surf)
    cr.scale(scale, scale)
    cr.translate(-ox, -oy)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    fn(cr)
    buf = io.BytesIO()
    surf.write_to_png(buf)
    return buf.getvalue()


def base_png(scale=1.0):
    return _png(SIZE[0], SIZE[1], 0, 0, draw_base, scale)


def sprite_png(name, scale=1.0):
    """Returns (png_bytes, (x0, y0)) — y0/x0 in design coords of the top-left."""
    (x0, y0, x1, y1), fn = SPRITES[name]
    return _png(x1 - x0, y1 - y0, x0, y0, fn, scale), (x0, y0)


def draw_icon(cr, size):
    """Window/app icon: a chunky gamepad glyph on a rounded dark tile."""
    cr.scale(size / 64, size / 64)
    rounded_rect(cr, 2, 2, 62, 62, 14)
    cr.set_source_rgba(*hexrgb('#1b2430')); cr.fill()
    # body
    cr.move_to(20, 20)
    cr.line_to(44, 20)
    cr.curve_to(53, 20, 57, 26, 59, 36)
    cr.curve_to(61, 46, 58, 50, 54, 50)
    cr.curve_to(50, 50, 47, 44, 44, 40)
    cr.line_to(20, 40)
    cr.curve_to(17, 44, 14, 50, 10, 50)
    cr.curve_to(6, 50, 3, 46, 5, 36)
    cr.curve_to(7, 26, 11, 20, 20, 20)
    cr.close_path()
    cr.set_source_rgba(*hexrgb(ACCENT)); cr.fill()
    # d-pad
    cr.set_source_rgba(*hexrgb('#ffffff'))
    cr.rectangle(14, 28.5, 12, 4); cr.fill()
    cr.rectangle(18, 24.5, 4, 12); cr.fill()
    # face buttons
    for (x, y), name in (((46, 25.5), 'triangle'), ((50.5, 30.5), 'circle'),
                         ((46, 35.5), 'cross'), ((41.5, 30.5), 'square')):
        cr.new_sub_path(); cr.arc(x, y, 2.6, 0, 2 * math.pi)
        cr.set_source_rgba(*hexrgb(FACE_COLORS[name])); cr.fill()


def icon_png(size=64):
    return _png(size, size, 0, 0, lambda cr: draw_icon(cr, size))


if __name__ == '__main__':
    # preview: base + every sprite, written to the path given on the command line
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else 'ds4art-preview.png'
    pressed = set(sys.argv[2].split(',')) if len(sys.argv) > 2 else set()
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, *SIZE)
    cr = cairo.Context(surf)
    cr.set_source_rgba(*hexrgb(BG)); cr.paint()
    draw_base(cr)
    for name in pressed:
        (x0, y0, x1, y1), fn = SPRITES[name]
        cr.save(); fn(cr); cr.restore()
    for key, (sx, sy) in STICKS.items():
        cr.save(); cr.translate(sx, sy); SPRITES['knob_idle'][1](cr); cr.restore()
    surf.write_to_png(out)
    print('wrote', out)
