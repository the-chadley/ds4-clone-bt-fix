#!/usr/bin/env python3
"""ds4-tester — Steam-style tester for DS4 clone controllers on Linux (Tkinter)

  * vector controller view with live button/stick/trigger/touchpad highlights
  * live readouts for triggers, sticks, touchpad and pressed buttons
  * lightbar colour swatches + custom picker
  * rumble test of both motors (kernel force-feedback; hidraw fallback)
  * hardware info: BT MAC, USB ids, battery, firmware/hardware versions

Run:             python3 ds4-tester.py
List-only mode:  python3 ds4-tester.py --list

Buttons, sticks and rumble work as a normal desktop user. The touchpad
device and sysfs LED writes are root-only by default: install
70-ds4-tester.rules (instructions in the file) to open them up. Without it
the touchpad view is disabled and the lightbar is set over hidraw instead.
Custom pad names and the last lightbar colour are stored per MAC in
~/.config/ds4-tester/pads.json (ds4-lightbar-restore reapplies the colour
on every reconnect).
Needs python3-evdev and python3-cairo (pycairo).
"""
import os
import sys
import glob
import errno
import base64
import json

import tkinter as tk
from tkinter import colorchooser

try:
    import evdev
    from evdev import ecodes as E, ff
except ImportError:
    evdev = None

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ds4art as art

# ---------------------------------------------------------------- pad discovery

def find_pads():
    pads = []
    for led in sorted(glob.glob('/sys/class/leds/input*:red')):
        base = os.path.basename(led).split(':')[0]
        if not (os.path.exists(f'/sys/class/leds/{base}:green') and
                os.path.exists(f'/sys/class/leds/{base}:blue')):
            continue
        try:
            dev = os.path.realpath(f'/sys/class/leds/{base}:red/device')
            uniq = open(f'{dev}/input/{base}/uniq').read().strip().upper()
        except OSError:
            continue
        pad = {'uniq': uniq, 'led_dir': f'/sys/class/leds/{base}',
               'hid_dev': os.path.realpath(f'/sys/class/leds/{base}:red/device')}
        # HID device dirs are named BUS:VID:PID.N, e.g. 0005:054C:09CC.000A
        ids = os.path.basename(pad['hid_dev']).split('.')[0].split(':')
        pad['vid'], pad['pid'] = (f'0x{ids[1].lower()}', f'0x{ids[2].lower()}') \
            if len(ids) == 3 else ('?', '?')
        try:
            pad['battery'] = int(open(
                f'/sys/class/power_supply/ps-controller-battery-{uniq.lower()}/capacity'
            ).read().strip())
        except (OSError, ValueError):
            pad['battery'] = None
        for key in ('firmware_version', 'hardware_version'):
            try:
                pad[key] = open(os.path.join(pad['hid_dev'], key)).read().strip()
            except OSError:
                pad[key] = '?'
        hr = glob.glob(os.path.join(pad['hid_dev'], 'hidraw', 'hidraw*'))
        pad['hidraw'] = '/dev/' + os.path.basename(hr[0]) if hr else None
        pad['event_gamepad'] = pad['event_touch'] = None
        for inp in sorted(glob.glob(os.path.join(pad['hid_dev'], 'input', 'input*'))):
            name = open(os.path.join(inp, 'name')).read().strip()
            evfile = glob.glob(os.path.join(inp, 'event*'))
            if not evfile:
                continue
            evpath = '/dev/input/' + os.path.basename(evfile[0])
            if 'Touchpad' in name:
                pad['event_touch'] = evpath
            elif 'Motion' not in name:
                pad['event_gamepad'] = evpath
        rgb = []
        for ch in ('red', 'green', 'blue'):
            try:
                rgb.append(int(open(pad['led_dir'] + ':' + ch + '/brightness').read()))
            except OSError:
                rgb.append(0)
        pad['rgb'] = rgb
        pads.append(pad)
    return pads

# ---------------------------------------------------------------- valid-layout rumble

def rumble_frame(pad, small=0, big=0, rgb=None, flags=0x03):
    import binascii, struct
    f = bytearray(78)
    f[0] = 0x11
    f[1] = 0xC0
    f[2] = 0x00
    f[3] = flags
    f[6] = small
    f[7] = big
    r, g, b = rgb if rgb is not None else pad['rgb']
    f[8], f[9], f[10] = r, g, b
    crc = binascii.crc32(b'\xa2' + bytes(f[:74]))
    f[74:78] = struct.pack('<I', crc)
    return bytes(f)

def send_hidraw(pad, small=0, big=0, rgb=None, flags=0x03):
    if not pad.get('hidraw'):
        return False
    try:
        fd = os.open(pad['hidraw'], os.O_WRONLY)
        try:
            os.write(fd, rumble_frame(pad, small, big, rgb=rgb, flags=flags))
        finally:
            os.close(fd)
        return True
    except OSError as e:
        print('hidraw write failed:', e)
        return False

def enable_full_reports(pad):
    """Clones boot into the 10-byte BT report 0x01 (sticks/buttons only, no
    touchpad or motion). Reading feature report 0x02 (calibration) flips them
    to the full 0x11 report — the kernel's own probe reads 0x05, which they
    ignore. Returns True if the read went through."""
    if not pad.get('hidraw'):
        return False
    import fcntl
    buf = bytearray(37)
    buf[0] = 0x02
    try:
        fd = os.open(pad['hidraw'], os.O_RDWR)
        try:
            fcntl.ioctl(fd, (3 << 30) | (len(buf) << 16) | (ord('H') << 8) | 0x07, buf, True)
        finally:
            os.close(fd)
        return True
    except OSError as e:
        print('full-report switch failed:', e)
        return False

_ff_ids = {}   # event path -> uploaded effect id (kernel slots are limited)

def send_rumble(pad, small, big, dev=None, ms=1000):
    """Rumble via kernel force-feedback (no root needed); hidraw fallback."""
    if dev is not None:
        try:
            effect = ff.Effect(E.FF_RUMBLE, -1, 0, ff.Trigger(0, 0),
                               ff.Replay(ms, 0),
                               ff.EffectType(ff_rumble_effect=ff.Rumble(
                                   strong_magnitude=big * 257,
                                   weak_magnitude=small * 257)))
            old = _ff_ids.pop(dev.path, None)
            if old is not None:
                try:
                    dev.erase_effect(old)
                except OSError:
                    pass
            eid = dev.upload_effect(effect)
            _ff_ids[dev.path] = eid
            dev.write(E.EV_FF, eid, 1)
            return True
        except OSError as e:
            print('force-feedback failed, trying hidraw:', e)
    return send_hidraw(pad, small, big, rgb=pad['rgb'])

# ---------------------------------------------------------------- GUI

COL = {'bg': art.BG, 'bar': '#0e1218', 'card': '#232a35', 'button': '#3d4450',
       'hover': '#4f5866', 'text': '#dcdedf', 'muted': '#8b929a',
       'accent': art.ACCENT}
FONT = 'Noto Sans'
POLL_MS = 16
STICK_TRAVEL = 20       # px the knob moves at full deflection (well is 66, knob 44)
TITLE_H = 50            # canvas space above the controller art
SWATCHES = ['FF0000', 'FF6600', 'FFFF00', '00FF00', '00FFFF', '0000FF',
            '8000FF', 'FF00FF', 'FF4FA0', 'FFFFFF', '000000']

BTN_NAMES = {}
if evdev is not None:
    BTN_NAMES = {E.BTN_SOUTH: 'Cross', E.BTN_EAST: 'Circle', E.BTN_NORTH: 'Triangle',
                 E.BTN_WEST: 'Square', E.BTN_TL: 'L1', E.BTN_TR: 'R1',
                 E.BTN_TL2: 'L2', E.BTN_TR2: 'R2', E.BTN_SELECT: 'Share',
                 E.BTN_START: 'Options', E.BTN_MODE: 'Home',
                 E.BTN_THUMBL: 'L3', E.BTN_THUMBR: 'R3'}
    # evdev button -> sprite that lights up
    BTN_SPRITES = {E.BTN_NORTH: 'face_triangle', E.BTN_EAST: 'face_circle',
                   E.BTN_SOUTH: 'face_cross', E.BTN_WEST: 'face_square',
                   E.BTN_SELECT: 'share', E.BTN_START: 'options', E.BTN_MODE: 'home',
                   E.BTN_TL: 'l1', E.BTN_TR: 'r1', E.BTN_TL2: 'l2', E.BTN_TR2: 'r2'}


def config_path(name='pads.json'):
    """~/.config/ds4-tester/<name> — the invoking user's, even under sudo."""
    if os.geteuid() == 0 and os.environ.get('SUDO_USER'):
        import pwd
        base = os.path.join(pwd.getpwnam(os.environ['SUDO_USER']).pw_dir, '.config')
    else:
        base = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return os.path.join(base, 'ds4-tester', name)


def load_pads():
    """MAC -> {'name': str, 'rgb': 'rrggbb'} (either key optional)."""
    try:
        with open(config_path()) as fh:
            return {k.upper(): v for k, v in json.load(fh).items()}
    except (OSError, ValueError):
        pass
    try:    # earlier versions kept only names, in names.json
        with open(config_path('names.json')) as fh:
            return {k.upper(): {'name': v} for k, v in json.load(fh).items()}
    except (OSError, ValueError):
        return {}


def save_pads(pads):
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as fh:
        json.dump({k: v for k, v in pads.items() if v}, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)
    if os.geteuid() == 0 and os.environ.get('SUDO_UID'):
        # don't leave root-owned files in the user's config dir
        uid, gid = int(os.environ['SUDO_UID']), int(os.environ.get('SUDO_GID', -1))
        for p in (os.path.dirname(path), path):
            os.chown(p, uid, gid)


def font(size=10, weight='normal'):
    return (FONT, size, weight)


class FlatButton(tk.Label):
    """Steam-style flat button."""
    def __init__(self, parent, text, command, width=16):
        super().__init__(parent, text=text, bg=COL['button'], fg=COL['text'],
                         font=font(10), width=width, pady=10, cursor='hand2')
        self.command = command
        self.bind('<Enter>', lambda e: self.config(bg=COL['hover']))
        self.bind('<Leave>', lambda e: self.config(bg=COL['button']))
        self.bind('<Button-1>', lambda e: self.command())


class Card(tk.Frame):
    """Readout row: title on the left, value on the right, optional bar."""
    def __init__(self, parent, title, bar=False):
        super().__init__(parent, bg=COL['card'], padx=12, pady=8)
        tk.Label(self, text=title, bg=COL['card'], fg=COL['text'],
                 font=font(10)).grid(row=0, column=0, sticky='w')
        self.value = tk.Label(self, text='—', bg=COL['card'], fg=COL['muted'],
                              font=font(10))
        self.value.grid(row=0, column=1, sticky='e')
        self.columnconfigure(1, weight=1)
        self.bar = None
        if bar:
            self.bar = tk.Canvas(self, height=4, bg='#1a1f27', highlightthickness=0)
            self.bar.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(6, 0))
            self.fill = self.bar.create_rectangle(0, 0, 0, 4, fill=COL['accent'], outline='')

    def set(self, text, frac=None):
        if self.value.cget('text') != text:
            self.value.config(text=text)
        if self.bar is not None and frac is not None:
            self.bar.coords(self.fill, 0, 0, self.bar.winfo_width() * frac, 4)


class Tester(tk.Tk):
    def __init__(self):
        super().__init__(className='ds4-tester')
        self.title('Controller Layout — DS4 Clone')
        self.configure(bg=COL['bg'])
        self.resizable(False, False)
        self.icons = [tk.PhotoImage(data=base64.b64encode(art.icon_png(n)))
                      for n in (128, 64, 32)]
        self.iconphoto(True, *self.icons)
        self.cfg = load_pads()          # MAC -> {'name', 'rgb'}
        self.rename_entry = None
        self.pads = find_pads()
        self.inp = self.blank_input()
        self.devs = {}          # role ('gamepad' / 'touch') -> evdev.InputDevice
        self.ranges = {}        # (role, abs code) -> (min, max), from the device
        self.current = None
        self.shown = {}         # canvas item -> currently visible?
        self.build_ui()
        self.bind('<Escape>', self.on_escape)
        self.timers = {'tick': self.after(POLL_MS, self.tick),
                       'slow': self.after(500, self.slow_tick)}
        self.restore_colours()
        self.select_pad(0 if self.pads else None)
        if not self.pads:
            self.status('No DS4 controller found — connect one and press Refresh.')

    # ---------------- static layout
    def build_ui(self):
        bar = tk.Frame(self, bg=COL['bar'], padx=14, pady=8)
        bar.pack(fill='x')
        self.device = tk.Label(bar, bg=COL['bar'], fg=COL['text'], font=font(11, 'bold'))
        self.device.pack(side='left')
        self.hwinfo = tk.Label(bar, bg=COL['bar'], fg=COL['muted'], font=font(9))
        self.hwinfo.pack(side='left', padx=12)
        self.chips = tk.Frame(bar, bg=COL['bar'])
        self.chips.pack(side='right')

        w, h = art.SIZE
        self.canvas = tk.Canvas(self, width=w, height=h + TITLE_H, bg=COL['bg'],
                                highlightthickness=0)
        self.canvas.pack(padx=20)
        self.build_canvas()

        row = tk.Frame(self, bg=COL['bg'])
        row.pack(pady=(4, 8))
        for i, (text, cmd) in enumerate((('Rumble', self.rumble),
                                         ('Identify', self.identify),
                                         ('Custom Colour…', self.pick_led),
                                         ('Refresh', self.refresh))):
            FlatButton(row, text, cmd).grid(row=0, column=i, padx=6)

        sw = tk.Frame(self, bg=COL['bg'])
        sw.pack(pady=(0, 10))
        tk.Label(sw, text='Lightbar', bg=COL['bg'], fg=COL['muted'],
                 font=font(9)).pack(side='left', padx=(0, 8))
        for hx in SWATCHES:
            s = tk.Frame(sw, bg='#' + hx, width=26, height=18, cursor='hand2',
                         highlightthickness=1, highlightbackground='#3d4450')
            s.pack(side='left', padx=3)
            s.bind('<Button-1>', lambda e, x=hx: self.set_led(x))

        cards = tk.Frame(self, bg=COL['bg'], padx=60)
        cards.pack(fill='x', pady=(0, 14))
        cards.columnconfigure(0, weight=1, uniform='c')
        cards.columnconfigure(1, weight=1, uniform='c')
        self.cards = {}
        for i, (key, title, has_bar) in enumerate((
                ('l2', 'Left Trigger', True), ('r2', 'Right Trigger', True),
                ('ls', 'Left Joystick', False), ('rs', 'Right Joystick', False),
                ('touch', 'Touchpad', False), ('buttons', 'Buttons', False))):
            c = Card(cards, title, has_bar)
            c.grid(row=i // 2, column=i % 2, sticky='ew', padx=10, pady=3)
            self.cards[key] = c

        foot = tk.Frame(self, bg=COL['bar'], padx=14, pady=8)
        foot.pack(fill='x', side='bottom')
        self.statusbar = tk.Label(foot, bg=COL['bar'], fg=COL['muted'], font=font(9),
                                  anchor='w')
        self.statusbar.pack(side='left', fill='x', expand=True)
        tk.Label(foot, text='ESC  QUIT', bg=COL['bar'], fg=COL['text'],
                 font=font(9, 'bold')).pack(side='right')

    def build_canvas(self):
        """Controller art + one hidden canvas item per highlight sprite."""
        c = self.canvas
        self.photos = {}

        def photo(png):
            return tk.PhotoImage(data=base64.b64encode(png))

        self.photos['base'] = photo(art.base_png())
        c.create_image(0, TITLE_H, anchor='nw', image=self.photos['base'])
        self.title_id = c.create_text(art.SIZE[0] / 2, TITLE_H / 2 + 4, text='',
                                      fill=COL['text'], font=font(16), tags='title')
        self.pencil_id = c.create_text(0, TITLE_H / 2 + 5, text='✎', anchor='w',
                                       fill=COL['muted'], font=font(12), tags='title')
        c.tag_bind('title', '<Button-1>', lambda e: self.start_rename())
        c.tag_bind('title', '<Enter>', lambda e: c.config(cursor='hand2'))
        c.tag_bind('title', '<Leave>', lambda e: c.config(cursor=''))
        x0, y0, x1, y1 = art.LIGHTBAR
        self.lightbar = c.create_rectangle(x0, y0 + TITLE_H, x1, y1 + TITLE_H,
                                           outline='', fill='#000000')
        self.sprites = {}
        for name in art.SPRITES:
            if name.startswith('knob_') or name == 'touch_dot':
                continue
            png, (sx, sy) = art.sprite_png(name)
            self.photos[name] = photo(png)
            self.sprites[name] = c.create_image(sx, sy + TITLE_H, anchor='nw',
                                                image=self.photos[name], state='hidden')
        # stick knobs: three looks per stick, moved together
        for style in ('idle', 'moved', 'pressed'):
            png, _ = art.sprite_png(f'knob_{style}')
            self.photos[f'knob_{style}'] = photo(png)
        self.knobs = {}
        for key, (sx, sy) in art.STICKS.items():
            self.knobs[key] = {style: c.create_image(sx, sy + TITLE_H, anchor='center',
                                                     image=self.photos[f'knob_{style}'],
                                                     state='normal' if style == 'idle' else 'hidden')
                               for style in ('idle', 'moved', 'pressed')}
        png, _ = art.sprite_png('touch_dot')
        self.photos['touch_dot'] = photo(png)
        self.touch_dot = c.create_image(0, 0, image=self.photos['touch_dot'],
                                        state='hidden')

    def show(self, item, on):
        """Toggle a canvas item, skipping the Tk call when nothing changed."""
        if self.shown.get(item) != on:
            self.shown[item] = on
            self.canvas.itemconfigure(item, state='normal' if on else 'hidden')

    @property
    def chip_font(self):
        import tkinter.font as tkfont
        if not hasattr(self, '_chip_font'):
            self._chip_font = tkfont.Font(family=FONT, size=9)
        return self._chip_font

    def pad_name(self, idx):
        p = self.pads[idx]
        return self.cfg.get(p['uniq'], {}).get('name') or (
            'DS4 Clone' + (f' {idx + 1}' if len(self.pads) > 1 else ''))

    def set_title(self, text):
        c = self.canvas
        c.itemconfigure(self.title_id, text=text)
        c.itemconfigure(self.pencil_id, state='normal' if text else 'hidden')
        if text:
            x1 = c.bbox(self.title_id)[2]
            c.coords(self.pencil_id, x1 + 8, TITLE_H / 2 + 5)

    # ---------------- renaming (stored per MAC)
    def start_rename(self):
        if not self.current or self.rename_entry is not None:
            return
        c = self.canvas
        e = tk.Entry(c, font=font(15), justify='center', width=24, bg=COL['card'],
                     fg=COL['text'], insertbackground=COL['text'], relief='flat',
                     highlightthickness=1, highlightcolor=COL['accent'])
        e.insert(0, c.itemcget(self.title_id, 'text'))
        e.select_range(0, 'end')
        self.rename_entry = e
        self.rename_win = c.create_window(art.SIZE[0] / 2, TITLE_H / 2 + 4, window=e)
        e.focus_set()
        e.bind('<Return>', lambda ev: self.finish_rename(True))
        e.bind('<KP_Enter>', lambda ev: self.finish_rename(True))
        e.bind('<FocusOut>', lambda ev: self.finish_rename(True))
        self.status('Type a name, Enter to save (empty = default), Esc to cancel')

    def destroy(self):
        for t in getattr(self, 'timers', {}).values():
            self.after_cancel(t)
        self.close_devs()
        super().destroy()

    def on_escape(self, event=None):
        if self.rename_entry is not None:
            self.finish_rename(False)
        else:
            self.destroy()

    def finish_rename(self, save):
        e, self.rename_entry = self.rename_entry, None
        if e is None:
            return
        text = e.get().strip()
        self.canvas.delete(self.rename_win)
        e.destroy()
        if not save or not self.current:
            self.status('Rename cancelled')
            return
        mac = self.current['uniq']
        entry = self.cfg.setdefault(mac, {})
        if text and text != self.pad_name(self.pads.index(self.current)):
            entry['name'] = text
        elif not text:
            entry.pop('name', None)
        else:
            self.status('Name unchanged')
            return
        try:
            save_pads(self.cfg)
            self.status(f'Saved name for {mac} → {config_path()}')
        except OSError as err:
            self.status(f'Could not save name: {err}')
        self.set_title(self.pad_name(self.pads.index(self.current)))
        self.build_chips()

    def build_chips(self):
        """One battery chip per connected pad, click to switch."""
        for w in self.chips.winfo_children():
            w.destroy()
        for i, p in enumerate(self.pads):
            label = self.pad_name(i)
            tw = self.chip_font.measure(label)
            w = 66 + tw
            c = tk.Canvas(self.chips, width=w, height=22, bg=COL['bar'],
                          highlightthickness=0, cursor='hand2')
            c.pack(side='left', padx=3)
            selected = p is self.current
            c.create_rectangle(1, 1, w - 1, 21, outline=COL['accent'] if selected else '#2c333d',
                               width=2 if selected else 1)
            c.create_oval(7, 7, 15, 15, outline='', fill='#%02x%02x%02x' % tuple(p['rgb']))
            c.create_text(21, 11, text=label, anchor='w', font=font(9),
                          fill=COL['text'] if selected else COL['muted'])
            bx = 27 + tw
            c.create_rectangle(bx, 6, bx + 24, 16, outline=COL['muted'])
            c.create_rectangle(bx + 24, 9, bx + 26, 13, outline='', fill=COL['muted'])
            b = p['battery']
            if b is not None:
                colr = '#5ba32b' if b > 40 else '#d6a21e' if b > 15 else '#d64545'
                c.create_rectangle(bx + 2, 8, bx + 2 + 20 * b / 100, 14, outline='', fill=colr)
            c.bind('<Button-1>', lambda e, idx=i: self.select_pad(idx))

    # ---------------- pad switching
    @staticmethod
    def blank_input():
        return {'btn': set(), 'axes': {},
                'touch': {'x': 0, 'y': 0, 'down': False, 'click': False}}

    def select_pad(self, idx):
        self.close_devs()
        self.current = self.pads[idx] if idx is not None and 0 <= idx < len(self.pads) else None
        self.inp = self.blank_input()
        self.build_chips()
        if not self.current:
            self.device.config(text='No controller')
            self.hwinfo.config(text='')
            self.set_title('')
            self.redraw()
            return
        p = self.current
        self.set_title(self.pad_name(idx))
        self.device.config(text=f'Device: {p["uniq"]} ({p["vid"]},{p["pid"]})')
        self.update_info()
        self.open_devs()
        self.redraw()

    def update_info(self):
        p = self.current
        batt = f'{p["battery"]}%' if p['battery'] is not None else '?'
        self.hwinfo.config(text=(f'battery {batt}   fw {p["firmware_version"]}   '
                                 f'hw {p["hardware_version"]}'))
        self.canvas.itemconfigure(self.lightbar, fill='#%02x%02x%02x' % tuple(p['rgb']))

    def open_devs(self):
        self.devs, self.ranges, problems = {}, {}, []
        enable_full_reports(self.current)      # touchpad data needs full mode
        for role in ('gamepad', 'touch'):
            path = self.current.get(f'event_{role}')
            if not path:
                continue
            try:
                # python-evdev opens O_RDWR|O_NONBLOCK (falls back to read-only)
                d = evdev.InputDevice(path)
            except PermissionError:
                problems.append(f'{role} ({path}): permission denied')
                continue
            except OSError as e:
                problems.append(f'{role}: {e.strerror}')
                continue
            self.devs[role] = d
            for code, info in d.capabilities().get(E.EV_ABS, []):
                self.ranges[(role, code)] = (info.min, info.max)
        if 'gamepad' not in self.devs:
            self.status('Cannot read buttons — ' + '; '.join(problems))
        elif problems:
            self.status('Buttons OK · touchpad blocked — install 70-ds4-tester.rules (see its header)')
        else:
            self.status('Reading input — press some buttons')

    def close_devs(self):
        for d in self.devs.values():
            try:
                d.close()
            except OSError:
                pass
        self.devs = {}

    def norm(self, role, code, value, lo=-1.0, hi=1.0):
        """Map a raw axis value onto lo..hi using the device-reported range."""
        mn, mx = self.ranges.get((role, code), (0, 255))
        if mx == mn:
            return lo
        return lo + (value - mn) * (hi - lo) / (mx - mn)

    # ---------------- event polling
    def tick(self):
        try:
            if self.poll():
                self.redraw()
        except Exception as e:      # never let one bad frame kill the loop
            self.status(f'tick error: {e}')
        finally:
            self.timers['tick'] = self.after(POLL_MS, self.tick)

    def poll(self):
        """Read pending events; True if anything arrived."""
        got = False
        for role, d in list(self.devs.items()):
            try:
                events = list(d.read())
            except BlockingIOError:
                continue
            except OSError as e:
                if e.errno == errno.ENODEV:
                    self.devs.pop(role, None)
                    self.status(f'{self.current["uniq"]} disconnected — '
                                'reconnect and press Refresh')
                    self.inp = self.blank_input()
                    got = True
                continue
            got = got or bool(events)
            if role == 'gamepad':
                self.feed_gamepad(events)
            else:
                self.feed_touch(events)
        return got

    def feed_gamepad(self, events):
        s = self.inp
        for e in events:
            if e.type == E.EV_KEY:
                if e.value:
                    s['btn'].add(e.code)
                else:
                    s['btn'].discard(e.code)
            elif e.type == E.EV_ABS:
                s['axes'][e.code] = e.value

    def feed_touch(self, events):
        t = self.inp['touch']
        for e in events:
            if e.type == E.EV_KEY:
                if e.code == E.BTN_LEFT:        # physical touchpad click
                    t['click'] = bool(e.value)
                elif e.code == E.BTN_TOUCH:
                    t['down'] = bool(e.value)
            elif e.type == E.EV_ABS:
                if e.code == E.ABS_X:
                    t['x'] = e.value
                elif e.code == E.ABS_Y:
                    t['y'] = e.value

    def slow_tick(self):
        if self.current:
            uniq = self.current['uniq'].lower()
            try:
                self.current['battery'] = int(open(
                    f'/sys/class/power_supply/ps-controller-battery-{uniq}/capacity'
                ).read().strip())
                self.update_info()
                self.build_chips()
            except (OSError, ValueError):
                pass
        self.timers['slow'] = self.after(5000 if self.current else 500, self.slow_tick)

    # ---------------- render state
    def redraw(self):
        s = self.inp
        btn, ax, touch = s['btn'], s['axes'], s['touch']
        hat = (ax.get(E.ABS_HAT0X, 0), ax.get(E.ABS_HAT0Y, 0))
        l2 = self.norm('gamepad', E.ABS_Z, ax.get(E.ABS_Z, 0), 0.0, 1.0)
        r2 = self.norm('gamepad', E.ABS_RZ, ax.get(E.ABS_RZ, 0), 0.0, 1.0)

        lit = {BTN_SPRITES[b] for b in btn if b in BTN_SPRITES}
        lit |= {f'dpad_{d}' for d, on in (('up', hat[1] < 0), ('down', hat[1] > 0),
                                          ('left', hat[0] < 0), ('right', hat[0] > 0)) if on}
        if l2 > 0.03:
            lit.add('l2')
        if r2 > 0.03:
            lit.add('r2')
        if touch['click']:
            lit.add('touch_click')
        for name, item in self.sprites.items():
            self.show(item, name in lit)

        # sticks
        sticks = {}
        for key, cx_code, cy_code, press in (('left', E.ABS_X, E.ABS_Y, E.BTN_THUMBL),
                                             ('right', E.ABS_RX, E.ABS_RY, E.BTN_THUMBR)):
            nx = self.norm('gamepad', cx_code, ax.get(cx_code, 128))
            ny = self.norm('gamepad', cy_code, ax.get(cy_code, 128))
            sticks[key] = (ax.get(cx_code, 128), ax.get(cy_code, 128))
            style = ('pressed' if press in btn else
                     'moved' if abs(nx) > 0.12 or abs(ny) > 0.12 else 'idle')
            sx, sy = art.STICKS[key]
            for st, item in self.knobs[key].items():
                self.show(item, st == style)
            self.canvas.coords(self.knobs[key][style], sx + nx * STICK_TRAVEL,
                               sy + ny * STICK_TRAVEL + TITLE_H)

        # touchpad finger
        x0, y0, x1, y1 = art.TOUCH
        if touch['down'] and 'touch' in self.devs:
            fx = self.norm('touch', E.ABS_X, touch['x'], 0.0, 1.0)
            fy = self.norm('touch', E.ABS_Y, touch['y'], 0.0, 1.0)
            self.canvas.coords(self.touch_dot, x0 + 6 + fx * (x1 - x0 - 12),
                               y0 + 6 + fy * (y1 - y0 - 12) + TITLE_H)
            self.show(self.touch_dot, True)
            self.canvas.tag_raise(self.touch_dot)
        else:
            self.show(self.touch_dot, False)

        # readout cards
        cd = self.cards
        cd['l2'].set(str(ax.get(E.ABS_Z, 0)), l2)
        cd['r2'].set(str(ax.get(E.ABS_RZ, 0)), r2)
        cd['ls'].set('%d, %d' % (sticks['left'][0] - 128, sticks['left'][1] - 128))
        cd['rs'].set('%d, %d' % (sticks['right'][0] - 128, sticks['right'][1] - 128))
        if 'touch' not in self.devs:
            cd['touch'].set('no access (udev rule)' if self.current else '—')
        elif touch['down']:
            cd['touch'].set(f'{touch["x"]}, {touch["y"]}' + ('  click' if touch['click'] else ''))
        else:
            cd['touch'].set('—')
        names = [BTN_NAMES.get(b, str(b)) for b in sorted(btn)]
        names += [d.title() for d, on in (('up', hat[1] < 0), ('down', hat[1] > 0),
                                          ('left', hat[0] < 0), ('right', hat[0] > 0)) if on]
        cd['buttons'].set('  '.join(names) or '—')

    # ---------------- LED + rumble actions
    def set_led(self, hx, pad=None, remember=True):
        pad = pad or self.current
        if not pad:
            return
        hx = hx.lower()
        r, g, b = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
        try:
            for ch, v in (('red', r), ('green', g), ('blue', b)):
                with open(f'{pad["led_dir"]}:{ch}/brightness', 'w') as fh:
                    fh.write(str(v))
            how = 'sysfs'
        except OSError:
            # sysfs LEDs are root-only; hidraw is usually open to the desktop user
            if not send_hidraw(pad, rgb=(r, g, b), flags=0x02):
                self.status('Lightbar write failed (needs root, or hidraw access)')
                return
            how = 'hidraw'
        pad['rgb'] = [r, g, b]
        if remember:
            self.cfg.setdefault(pad['uniq'], {})['rgb'] = hx
            try:
                save_pads(self.cfg)
            except OSError as err:
                self.status(f'Lightbar set, but could not save it: {err}')
                remember = False
        if pad is self.current:
            self.update_info()
        self.build_chips()
        self.status(f'Lightbar → #{hx.upper()} ({how})' + (' · remembered' if remember else ''))

    def restore_colours(self):
        """Reapply each pad's saved colour (covers pads that connected before
        ds4-lightbar-restore was installed)."""
        for p in self.pads:
            saved = self.cfg.get(p['uniq'], {}).get('rgb')
            if saved and saved != '%02x%02x%02x' % tuple(p['rgb']):
                self.set_led(saved, pad=p, remember=False)

    def pick_led(self):
        if not self.current:
            return
        rgb = colorchooser.askcolor('#%02x%02x%02x' % tuple(self.current['rgb']),
                                    title='Custom lightbar colour', parent=self)
        if not rgb or rgb[0] is None:
            return
        self.set_led('%02x%02x%02x' % tuple(min(255, max(0, int(v))) for v in rgb[0]))

    def rumble(self):
        """Strong (left) motor, then weak (right) motor, so both get tested."""
        if not self.current:
            return
        pad, dev = self.current, self.devs.get('gamepad')

        def step(small, big, label):
            ok = send_rumble(pad, small, big, dev=dev, ms=600)
            self.status(label if ok else 'Rumble failed (no force-feedback or hidraw access)')
            return ok

        if step(0, 255, 'Rumble: strong motor (left)…'):
            self.after(700, lambda: step(255, 0, 'Rumble: weak motor (right)…'))
            if dev is None:     # hidraw rumble has no duration; stop it ourselves
                self.after(1400, lambda: send_hidraw(pad, 0, 0, rgb=pad['rgb']))
            self.after(1400, lambda: self.status('Rumble done'))

    def identify(self):
        if not self.current:
            return
        base = '%02x%02x%02x' % tuple(self.current['rgb'])
        seq = ['FF0000', '0000FF', 'FF0000', '0000FF', '00FF00', base]
        for i, hx in enumerate(seq):
            self.after(300 * i, lambda x=hx: self.set_led(x, remember=False))

    def refresh(self):
        keep = self.current['uniq'] if self.current else None
        self.close_devs()
        self.pads = find_pads()
        self.restore_colours()
        uniqs = [p['uniq'] for p in self.pads]
        if self.pads:
            self.select_pad(uniqs.index(keep) if keep in uniqs else 0)
        else:
            self.select_pad(None)
            self.status('No DS4 controller found — connect one and press Refresh.')

    def status(self, msg):
        self.statusbar.config(text=msg)

# ---------------------------------------------------------------- CLI mode

def list_mode():
    print('connected DS4 pads:')
    for p in find_pads():
        print(f"  {p['uniq']}")
        print(f"     battery={p['battery']}%  fw={p['firmware_version']}  "
              f"hw={p['hardware_version']}")
        print(f"     gamepad={p['event_gamepad']}  touchpad={p['event_touch']}")
        print(f"     hidraw={p['hidraw']}  rgb=%02x%02x%02x" % tuple(p['rgb']))

def main():
    if '--list' in sys.argv:
        list_mode()
        return 0
    if evdev is None:
        print('python-evdev is required:  sudo dnf install python3-evdev')
        return 1
    app = Tester()
    app.mainloop()
    return 0

if __name__ == '__main__':
    sys.exit(main())
