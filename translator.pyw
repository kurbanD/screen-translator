"""
Экранный переводчик RU <-> EN (Windows 10/11).

  1. python translator.py   (или pythonw translator.py — без консоли)
  2. Нажми Ctrl+Alt+T — экран затемнится, как при Win+Shift+S.
  3. Обведи мышкой область с текстом — отпусти кнопку.
  4. Рядом появится перевод. Русский -> английский, остальное -> русский.

Esc — отмена / закрыть окно. Клик мимо окна — тоже закрывает.

Текст читается встроенным OCR Windows, поэтому работает везде:
в играх, картинках, видео, PDF, программах без выделения текста.
Нужен установленный OCR-язык (русский и/или английский): обычно он есть,
если язык добавлен в Параметры -> Время и язык -> Язык и регион.
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import queue
import re
import struct
import base64
import html
import shutil
import urllib.error
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import traceback
import urllib.parse
import urllib.request
import winreg
import zlib

# ---- Настройки -------------------------------------------------------------
MAX_CHARS = 4000
VERSION = "1.9"                         # виден в подсказке значка в трее
# Всё остальное (сочетание клавиш, язык, затемнение…) меняется в окне «Настройки» из меню значка в трее.
# ----------------------------------------------------------------------------

BG_KEY = "#010101"      # этот цвет в окнах становится прозрачным
CARD = "#1b1d2a"
BORDER = "#34374d"
ACCENT = "#7aa2f7"
TEXT = "#e6e8f2"
MUTED = "#7f849c"
FONT = "Segoe UI"

# DPI-awareness — до создания Tk, чтобы координаты были в реальных пикселях
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
H = wt.HANDLE
CI = ctypes.c_int

user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = H
user32.ReleaseDC.argtypes = [wt.HWND, H]
user32.GetParent.argtypes = [wt.HWND]
user32.GetParent.restype = wt.HWND
user32.SetWindowRgn.argtypes = [wt.HWND, H, wt.BOOL]
gdi32.CreateRectRgn.argtypes = [CI, CI, CI, CI]
gdi32.CreateRectRgn.restype = H
gdi32.CombineRgn.argtypes = [H, H, H, CI]
gdi32.DeleteObject.argtypes = [H]
gdi32.CreateCompatibleDC.argtypes = [H]
gdi32.CreateCompatibleDC.restype = H
gdi32.CreateCompatibleBitmap.argtypes = [H, CI, CI]
gdi32.CreateCompatibleBitmap.restype = H
gdi32.SelectObject.argtypes = [H, H]
gdi32.SelectObject.restype = H
gdi32.BitBlt.argtypes = [H, CI, CI, CI, CI, H, CI, CI, wt.DWORD]
gdi32.BitBlt.restype = wt.BOOL
gdi32.GetDIBits.argtypes = [H, H, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
gdi32.DeleteDC.argtypes = [H]

VK_ESCAPE, VK_LBUTTON = 0x1B, 0x01
WM_HOTKEY = 0x0312
SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000

events = queue.Queue()

LOG_PATH = os.path.join(tempfile.gettempdir(), "ScreenTranslator.log")


def log(msg):
    """Пишет ошибки в %TEMP%\\ScreenTranslator.log — окна у программы нет, иначе их не видно."""
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except OSError:
        pass


# ---- настройки пользователя -------------------------------------------------
MOD_ALT, MOD_CTRL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
DEFAULTS = {
    "mods": MOD_CTRL | MOD_ALT,   # сочетание клавиш: Ctrl+Alt+T
    "vk": 0x54,
    "target": "auto",             # auto = русский->английский, остальное->русский; ru / en = всегда в этот язык
    "dim": 55,                    # насколько затемнять экран при выделении, %
    "font": 12,                   # размер текста в окне перевода
    "autocopy": False,            # сразу копировать перевод в буфер обмена
}
SETTINGS_PATH = os.path.join(os.environ.get("APPDATA") or tempfile.gettempdir(),
                             "ScreenTranslator", "settings.json")
cfg = dict(DEFAULTS)

_VK_NAMES = {0x20: "Space", 0x0D: "Enter", 0x09: "Tab", 0x08: "Backspace", 0x2D: "Insert",
             0x2E: "Delete", 0x24: "Home", 0x23: "End", 0x21: "PageUp", 0x22: "PageDown",
             0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓", 0xBA: ";", 0xBB: "=", 0xBC: ",",
             0xBD: "-", 0xBE: ".", 0xBF: "/", 0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'"}
MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}


def vk_name(vk):
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    if 0x60 <= vk <= 0x69:
        return f"Num{vk - 0x60}"
    return _VK_NAMES.get(vk, f"Key{vk:02X}")


def hotkey_label(mods, vk):
    parts = [n for bit, n in ((MOD_WIN, "Win"), (MOD_CTRL, "Ctrl"), (MOD_ALT, "Alt"),
                              (MOD_SHIFT, "Shift")) if mods & bit]
    return "+".join(parts + [vk_name(vk)])


def current_label():
    return hotkey_label(cfg["mods"], cfg["vk"])


def _valid(key, v):
    d = DEFAULTS[key]
    if type(v) is not type(d):
        return False
    if key == "mods":
        return 0 < v <= 0xF
    if key == "vk":
        return 1 <= v <= 254 and v not in MODIFIER_VKS
    if key == "target":
        return v in ("auto", "ru", "en")
    if key == "dim":
        return 20 <= v <= 85
    if key == "font":
        return 9 <= v <= 20
    return True


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    if isinstance(data, dict):
        for k in DEFAULTS:
            if k in data and _valid(k, data[k]):
                cfg[k] = data[k]


def save_settings():
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        log("Не удалось сохранить настройки:\n" + traceback.format_exc())


# ---- система ---------------------------------------------------------------
def is_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def virtual_screen():
    return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
            user32.GetSystemMetrics(78), user32.GetSystemMetrics(79))


# ---- значок в трее ----------------------------------------------------------
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

WM_TRAY = 0x0400 + 1
NIM_ADD, NIM_DELETE = 0, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
WM_LBUTTONUP, WM_RBUTTONUP, WM_NULL = 0x0202, 0x0205, 0x0000
ID_TRANSLATE, ID_QUIT, ID_AUTOSTART, ID_SETTINGS = 1000, 1001, 1002, 1003
NIM_MODIFY = 1
WM_REREG = 0x8000 + 2   # WM_APP+2: «перенастрой сочетание клавиш» (шлём потоку с горячей клавишей)
_hotkey_tid = [0]
_pending_hotkey = []
_tray_nid = [None]


def is_full_build():
    base = getattr(sys, "_MEIPASS", None)
    return bool(base) and os.path.isdir(os.path.join(base, "tesseract"))


def tray_tip():
    return f"Экранный переводчик v{VERSION} — {current_label()}" + (" · Full" if is_full_build() else "")


def update_tray_tip():
    nid = _tray_nid[0]
    if nid is None:
        return
    for i in range(128):
        nid.szTip[i] = 0
    for i, ch in enumerate(tray_tip()[:127]):
        nid.szTip[i] = ord(ch)
    nid.uFlags = NIF_TIP
    shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class GUID(ctypes.Structure):
    _fields_ = [("a", wt.DWORD), ("b", wt.WORD), ("c", wt.WORD), ("d", ctypes.c_ubyte * 8)]


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
                ("uCallbackMessage", wt.UINT), ("hIcon", H), ("szTip", ctypes.c_uint16 * 128),
                ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", ctypes.c_uint16 * 256),
                ("uVersion", wt.UINT), ("szInfoTitle", ctypes.c_uint16 * 64),
                ("dwInfoFlags", wt.DWORD), ("guidItem", GUID), ("hBalloonIcon", H)]


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", CI), ("cbWndExtra", CI),
                ("hInstance", H), ("hIcon", H), ("hCursor", H), ("hbrBackground", H),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                ("hbmMask", H), ("hbmColor", H)]


kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = H
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
user32.RegisterClassW.restype = wt.ATOM
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, CI, CI, CI, CI,
                                   wt.HWND, H, H, ctypes.c_void_p]
user32.CreateWindowExW.restype = wt.HWND
user32.CreatePopupMenu.restype = H
user32.AppendMenuW.argtypes = [H, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.TrackPopupMenu.argtypes = [H, wt.UINT, CI, CI, CI, wt.HWND, ctypes.c_void_p]
user32.DestroyMenu.argtypes = [H]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.CreateIconIndirect.restype = H
gdi32.CreateBitmap.argtypes = [CI, CI, wt.UINT, wt.UINT, ctypes.c_void_p]
gdi32.CreateBitmap.restype = H
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.c_void_p]

_tray_refs = []  # чтобы сборщик мусора не убил обработчик окна


def icon_pixels():
    """32x32 BGRA: синий скруглённый квадрат с белой буквой T."""
    n, r = 32, 7
    px = bytearray(n * n * 4)
    for y in range(n):
        for x in range(n):
            cx, cy = min(max(x, r), n - 1 - r), min(max(y, r), n - 1 - r)
            if (x - cx) ** 2 + (y - cy) ** 2 > r * r:
                continue
            white = (7 <= y <= 11 and 7 <= x <= 24) or (11 <= y <= 25 and 14 <= x <= 17)
            b, g, rr = (255, 255, 255) if white else (0xF7, 0xA2, 0x7A)
            i = (y * n + x) * 4
            px[i:i + 4] = bytes([b, g, rr, 255])
    return px


def make_icon():
    px = icon_pixels()
    color = gdi32.CreateBitmap(32, 32, 1, 32, (ctypes.c_ubyte * len(px)).from_buffer(px))
    mask_bits = (ctypes.c_ubyte * 128)()
    mask = gdi32.CreateBitmap(32, 32, 1, 1, mask_bits)
    ii = ICONINFO(True, 0, 0, mask, color)
    return user32.CreateIconIndirect(ctypes.byref(ii))


def setup_tray():
    hinst = kernel32.GetModuleHandleW(None)
    taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
    state = {}

    def remove_icon():
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(state["nid"]))

    def show_menu(hwnd):
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, 0, ID_TRANSLATE, "Перевести область")
        user32.AppendMenuW(menu, 0, ID_SETTINGS, "Настройки…")
        user32.AppendMenuW(menu, 0x0008 if is_autostart() else 0, ID_AUTOSTART,  # 0x8 = галочка
                           "Запускать вместе с Windows")
        user32.AppendMenuW(menu, 0, ID_QUIT, "Выход")
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(hwnd)
        cmd = user32.TrackPopupMenu(menu, 0x0100 | 0x0002 | 0x0020, pt.x, pt.y, 0, hwnd, None)
        user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        if cmd == ID_TRANSLATE:
            events.put(("hotkey", None))
        elif cmd == ID_SETTINGS:
            events.put(("settings", None))
        elif cmd == ID_AUTOSTART:
            try:
                set_autostart(not is_autostart())
            except OSError:
                pass
        elif cmd == ID_QUIT:
            remove_icon()
            os._exit(0)

    def wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            ev = lparam & 0xFFFF
            if ev == WM_LBUTTONUP:
                events.put(("hotkey", None))
            elif ev == WM_RBUTTONUP:
                show_menu(hwnd)
            return 0
        if msg == taskbar_created and "nid" in state:  # проводник перезапустился
            shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(state["nid"]))
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc = WNDPROC(wndproc)
    wc = WNDCLASS()
    wc.lpfnWndProc = proc
    wc.hInstance = hinst
    wc.lpszClassName = "ScreenTranslatorTray"
    user32.RegisterClassW(ctypes.byref(wc))
    hwnd = user32.CreateWindowExW(0, "ScreenTranslatorTray", "ScreenTranslator", 0,
                                  0, 0, 0, 0, None, None, hinst, None)
    nid = NOTIFYICONDATA()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_TRAY
    nid.hIcon = make_icon()
    for i, ch in enumerate(tray_tip()[:127]):
        nid.szTip[i] = ord(ch)
    state["nid"] = nid
    _tray_nid[0] = nid
    shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
    _tray_refs.extend([proc, wc, nid, state])


# ---- автозагрузка и единственный экземпляр -----------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "ScreenTranslator"
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
kernel32.CreateMutexW.restype = H
_mutex = None


def autostart_command():
    if getattr(sys, "frozen", False):          # собранный .exe
        return f'"{sys.executable}"'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pythonw if os.path.exists(pythonw) else sys.executable
    return f'"{exe}" "{os.path.abspath(__file__)}"'


def is_autostart():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass


def first_run_setup():
    """При самом первом запуске включаем автозагрузку (потом её можно выключить в меню значка)."""
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\ScreenTranslator") as k:
            try:
                winreg.QueryValueEx(k, "Initialized")
                return
            except FileNotFoundError:
                pass
            set_autostart(True)
            winreg.SetValueEx(k, "Initialized", 0, winreg.REG_DWORD, 1)
    except OSError:
        pass


def already_running():
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, "Local\\ScreenTranslatorMutex")
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]


def request_hotkey(new):
    """Просит поток с горячей клавишей перейти на новое сочетание (mods, vk)."""
    _pending_hotkey[:] = [new]
    if _hotkey_tid[0]:
        user32.PostThreadMessageW(_hotkey_tid[0], WM_REREG, 0, 0)


def hotkey_thread():
    _hotkey_tid[0] = kernel32.GetCurrentThreadId()
    try:
        setup_tray()
    except Exception as e:  # без значка приложение всё равно работает
        print("Не удалось создать значок в трее:", e)
    if not user32.RegisterHotKey(None, 1, cfg["mods"] | MOD_NOREPEAT, cfg["vk"]):
        events.put(("error", f"Не удалось занять {current_label()} — сочетание уже используется "
                             "другой программой. Выбери другое: значок в трее → Настройки."))
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == WM_HOTKEY:
            events.put(("hotkey", None))
        elif msg.message == WM_REREG:
            if _pending_hotkey:
                new = _pending_hotkey.pop()
                user32.UnregisterHotKey(None, 1)
                if user32.RegisterHotKey(None, 1, new[0] | MOD_NOREPEAT, new[1]):
                    events.put(("hotkey_ok", new))
                else:   # занято — возвращаем прежнее, чтобы приложение не осталось без клавиши
                    user32.RegisterHotKey(None, 1, cfg["mods"] | MOD_NOREPEAT, cfg["vk"])
                    events.put(("hotkey_fail", new))
        else:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


# ---- снимок экрана ---------------------------------------------------------
def capture(x, y, w, h, topdown=False):
    """BGRA-пиксели области экрана (по умолчанию строки снизу вверх, как в BMP)."""
    hdc = user32.GetDC(None)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    gdi32.BitBlt(mdc, 0, 0, w, h, hdc, x, y, SRCCOPY | CAPTUREBLT)
    size = w * h * 4
    ba = bytearray(size)
    buf = (ctypes.c_char * size).from_buffer(ba)
    bih = struct.pack("<IiiHHIIiiII", 40, w, -h if topdown else h, 1, 32, 0, size, 0, 0, 0, 0)
    bih_buf = ctypes.create_string_buffer(bih, 40)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, bih_buf, 0)
    gdi32.SelectObject(mdc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(None, hdc)
    del buf
    return ba


def upscale(ba, w, h, k):
    """Целочисленное увеличение BGRA-буфера (для лучшего распознавания мелкого текста)."""
    if k <= 1:
        return ba, w, h
    row_len = w * 4
    out = bytearray()
    for y in range(h):
        row = ba[y * row_len:(y + 1) * row_len]
        big = bytearray(row_len * k)
        for j in range(4):
            src = row[j::4]
            for m in range(k):
                big[m * 4 + j::4 * k] = src
        out += bytes(big) * k
    return out, w * k, h * k


def shot_to_ppm(buf, w, h, brightness=1.0):
    """BGRA (сверху вниз) -> PPM для tk.PhotoImage, при желании затемнённый."""
    n = w * h
    rgb = bytearray(3 * n)
    rgb[0::3] = buf[2::4]
    rgb[1::3] = buf[1::4]
    rgb[2::3] = buf[0::4]
    if brightness < 1.0:
        rgb = rgb.translate(bytes(int(i * brightness) for i in range(256)))
    return b"P6 %d %d 255\n" % (w, h) + bytes(rgb)


def shot_to_png(buf, w, h, brightness=1.0):
    """Запасной вариант картинки для Tk (PNG на стандартной библиотеке), если PPM не принялся."""
    ppm = shot_to_ppm(buf, w, h, brightness)
    rgb = ppm[len(b"P6 %d %d 255\n" % (w, h)):]
    stride = w * 3
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgb[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 1)) + chunk(b"IEND", b""))


def make_photo(root, buf, w, h, brightness=1.0):
    try:
        return tk.PhotoImage(master=root, data=shot_to_ppm(buf, w, h, brightness))
    except tk.TclError as e:
        log(f"PPM не принялся ({e}), пробую PNG")
    return tk.PhotoImage(master=root, data=base64.b64encode(shot_to_png(buf, w, h, brightness)))


def crop_bottom_up(buf, full_w, x, y, w, h):
    """Вырезает область из BGRA-снимка (сверху вниз) и отдаёт строки снизу вверх — для BMP."""
    rows = []
    for r in range(h - 1, -1, -1):
        o = ((y + r) * full_w + x) * 4
        rows.append(buf[o:o + w * 4])
    return bytearray(b"".join(rows))


def write_bmp(path, ba, w, h):
    ba[3::4] = b"\xff" * (w * h)  # непрозрачный альфа-канал
    size = len(ba)
    header = struct.pack("<2sIHHI", b"BM", 54 + size, 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 0, size, 2835, 2835, 0, 0)
    with open(path, "wb") as f:
        f.write(header + info + bytes(ba))


# ---- OCR (встроенный в Windows, через PowerShell) ---------------------------
OCR_PS1 = r"""
param([string]$img, [string]$out)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null = [Windows.Storage.FileAccessMode,Windows.Storage,ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]

$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
    $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    $t.Result
}

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($img)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bmp = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

$sb = New-Object System.Text.StringBuilder
$any = $false
foreach ($lang in [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages) {
    $eng = $null
    try { $eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang) } catch { }
    if ($eng -ne $null) {
        $any = $true
        $r = Await ($eng.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
        [void]$sb.AppendLine('###' + $lang.LanguageTag)
        [void]$sb.AppendLine($r.Text)
    }
}
if (-not $any) {
    $eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($eng -ne $null) {
        $any = $true
        $r = Await ($eng.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
        [void]$sb.AppendLine('###auto')
        [void]$sb.AppendLine($r.Text)
    }
}
if (-not $any) { [void]$sb.AppendLine('###none') }
[System.IO.File]::WriteAllText($out, $sb.ToString(), (New-Object System.Text.UTF8Encoding($false)))
"""


def parse_ocr(raw):
    results, cur = {}, None
    for line in raw.splitlines():
        if line.startswith("###"):
            cur = line[3:].strip()
            results[cur] = []
        elif cur is not None:
            results[cur].append(line)
    return {k: " ".join(" ".join(v).split()) for k, v in results.items()}


def pick_best(results):
    best, best_score = "", -1
    for tag, text in results.items():
        if tag == "none":
            continue
        if tag.lower().startswith("ru"):
            score = len(re.findall(r"[А-Яа-яЁё]", text))
        elif tag.lower().startswith("en"):
            score = len(re.findall(r"[A-Za-z]", text))
        else:
            score = len(re.findall(r"[A-Za-zА-Яа-яЁё]", text))
        if score > best_score:
            best, best_score = text, score
    return best


class OcrMissing(RuntimeError):
    """Нет ни одного работающего способа распознать текст."""


def powershell_exe():
    """64-битный PowerShell: 32-битный процесс на 64-битной Windows иначе не видит языки OCR."""
    win = os.environ.get("WINDIR", r"C:\Windows")
    sysnative = os.path.join(win, "Sysnative", "WindowsPowerShell", "v1.0", "powershell.exe")
    if os.path.exists(sysnative):
        return sysnative
    p = os.path.join(win, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return p if os.path.exists(p) else "powershell"


LIST_PS1 = r"""
param([string]$out)
$null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
$t = @()
foreach ($l in [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages) { $t += $l.LanguageTag }
[System.IO.File]::WriteAllText($out, ($t -join ','), (New-Object System.Text.UTF8Encoding($false)))
"""


def windows_ocr_languages():
    """Список языков OCR, которые Windows реально видит сейчас (None — не удалось проверить)."""
    tmp = tempfile.mkdtemp(prefix="scrtr_")
    ps1, out = os.path.join(tmp, "list.ps1"), os.path.join(tmp, "out.txt")
    try:
        with open(ps1, "w", encoding="ascii") as f:
            f.write(LIST_PS1)
        subprocess.run([powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1,
                        "-out", out], capture_output=True, timeout=60, creationflags=0x08000000)
        if not os.path.exists(out):
            return None
        with open(out, encoding="utf-8") as f:
            return [t for t in f.read().strip().split(",") if t]
    except Exception:
        log("Проверка языков OCR:\n" + traceback.format_exc())
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def install_outcome(result, list_languages):
    """Разбирает файл результата установки OCR по СТРОКАМ и перепроверяет, что Windows реально видит язык.
    Возвращает событие для окна: ("retry"|"error", текст)."""
    global _win_ocr_dead
    lines = [l.strip() for l in result.splitlines()]
    ok = [l for l in lines if l.startswith("OK|")]
    fails = [l.split("|", 2)[-1] for l in lines if l.startswith("FAIL|")]
    if ok:
        langs = list_languages()
        if langs:
            _win_ocr_dead = False
            return ("retry", "Готово! Windows видит языки: " + ", ".join(langs) + ". Повторяю распознавание…")
        return ("error", "Windows говорит, что поставила компонент, но пока его не видит: нужна перезагрузка "
                         "компьютера (не программы). Или возьми версию Full — ей это не нужно.")
    if fails:
        code = re.search(r"0x[0-9A-Fa-f]{8}", " ".join(fails))
        why = ("Windows не смогла скачать компонент" + (f" (ошибка {code.group(0)})" if code else "") +
               ": Windows Update отключён или недоступен.")
        return ("error", why + " Попробуй «Настройки языка» вручную или возьми версию Full — ей это не нужно.")
    return ("error", "Установка не началась: запрос Windows отклонён или нет прав. Нажми кнопку ещё раз "
                     "и подтверди запрос, либо возьми версию Full — ей это не нужно.")


# ---- запасной OCR: Tesseract (его кладёт в exe сборка «Full») -----------------
def tesseract_paths():
    """(tesseract.exe, папка tessdata) или None."""
    dirs = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        dirs.append(os.path.join(base, "tesseract"))
    me = sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)
    dirs.append(os.path.join(os.path.dirname(me), "tesseract"))
    for env in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        pf = os.environ.get(env)
        if pf:
            dirs.append(os.path.join(pf, "Tesseract-OCR"))
    for d in dirs:
        exe = os.path.join(d, "tesseract.exe")
        if os.path.isfile(exe):
            return exe, os.path.join(d, "tessdata")
    found = shutil.which("tesseract")
    if found:
        return found, os.path.join(os.path.dirname(found), "tessdata")
    return None


def _png_from_raw(w, h, raw):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 1)) + chunk(b"IEND", b""))


def bottom_up_bgra_to_png(ba, w, h):
    stride = w * 4
    raw = bytearray()
    for y in range(h - 1, -1, -1):
        row = ba[y * stride:(y + 1) * stride]
        rgb = bytearray(3 * w)
        rgb[0::3], rgb[1::3], rgb[2::3] = row[2::4], row[1::4], row[0::4]
        raw.append(0)
        raw += rgb
    return _png_from_raw(w, h, raw)


def parse_tsv(tsv):
    """Вывод tesseract (tsv) -> (текст, суммарная уверенность по словам)."""
    lines, score = {}, 0.0
    for row in tsv.splitlines()[1:]:
        c = row.split("\t")
        if len(c) < 12 or c[0] != "5":      # уровень 5 = слово
            continue
        text = c[11].strip()
        try:
            conf = float(c[10])
        except ValueError:
            continue
        if not text or conf < 0:
            continue
        lines.setdefault((c[2], c[3], c[4]), []).append(text)
        score += conf * min(len(text), 8)    # длинные уверенные слова весят больше мусора
    return " ".join(" ".join(v) for v in lines.values()), score


INVERT = bytes(255 - i for i in range(256))

# Русские и латинские буквы-двойники: при распознавании «rus+eng» Tesseract их путает
CYR2LAT = dict(zip("аеорсухАВЕКМНОРСТУХ", "aeopcyxABEKMHOPCTYX"))
LAT2CYR = {v: k for k, v in CYR2LAT.items()}


def _is_cyr(ch):
    return "А" <= ch <= "я" or ch in "Ёё"


def _is_lat(ch):
    return "A" <= ch <= "Z" or "a" <= ch <= "z"


def fix_homoglyphs(text):
    """«Hello this is а test» (с русской а) -> латиница; «Пpивет» (с латинской p) -> кириллица."""
    cyr, lat = sum(map(_is_cyr, text)), sum(map(_is_lat, text))
    if not cyr or not lat:
        return text
    glob = "cyr" if cyr >= lat else "lat"
    out = []
    for word in text.split(" "):
        c, l = sum(map(_is_cyr, word)), sum(map(_is_lat, word))
        if c and l:                              # внутри слова смесь — берём преобладающий алфавит
            target = "cyr" if c > l else ("lat" if l > c else glob)
        elif c + l == 1:                         # одиночная буква («а», «a», «о») — как во всём тексте
            target = glob
        else:                                    # целое слово одного алфавита («PC», «Steam») не трогаем
            out.append(word)
            continue
        table, is_other = (LAT2CYR, _is_lat) if target == "cyr" else (CYR2LAT, _is_cyr)
        out.append("".join(table.get(ch, ch) if is_other(ch) else ch for ch in word))
    return " ".join(out)


def _tess_one(exe, tessdata, langs, png_bytes, tmp, name):
    img = os.path.join(tmp, name + ".png")
    with open(img, "wb") as f:
        f.write(png_bytes)
    p = subprocess.run([exe, img, "stdout", "-l", "+".join(langs), "--psm", "6",
                        "--tessdata-dir", tessdata, "tsv"],
                       capture_output=True, timeout=90, creationflags=0x08000000)
    if p.returncode != 0 and not p.stdout:
        raise RuntimeError("Tesseract: " + p.stderr.decode("utf-8", "replace")[:300])
    return parse_tsv(p.stdout.decode("utf-8", "replace"))


def ocr_tesseract(ba, w, h):
    """Tesseract читает картинку и её негатив (светлый текст на тёмном фоне), берём лучший результат."""
    found = tesseract_paths()
    if not found:
        raise OcrMissing("Нет встроенного распознавания текста.")
    exe, tessdata = found
    langs = [l for l in ("rus", "eng") if os.path.exists(os.path.join(tessdata, l + ".traineddata"))]
    if not langs:
        raise OcrMissing("У Tesseract нет языковых данных (rus/eng).")
    k = 3 if (h <= 150 and w <= 1200) else (2 if (h <= 400 and w <= 1500) else 1)
    ba, w, h = upscale(ba, w, h, k)
    pngs = {"normal": bottom_up_bgra_to_png(ba, w, h),
            "invert": bottom_up_bgra_to_png(bytearray(ba.translate(INVERT)), w, h)}
    tmp = tempfile.mkdtemp(prefix="scrtr_")
    results, errors = {}, []

    def run(name):
        try:
            results[name] = _tess_one(exe, tessdata, langs, pngs[name], tmp, name)
        except Exception as e:
            errors.append(e)

    try:
        threads = [threading.Thread(target=run, args=(n,)) for n in pngs]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if not results:
            raise errors[0]
        text, _ = max(results.values(), key=lambda r: r[1])
        return fix_homoglyphs(text)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_win_ocr_dead = False   # Windows уже сказала «нет OCR» — в Full не тратим время на повтор


def ocr_image(ba, w, h):
    """Сначала OCR Windows, если он не сработал — Tesseract (в сборке Full)."""
    global _win_ocr_dead
    win_error = None
    if not (_win_ocr_dead and tesseract_paths()):
        try:
            text = ocr_windows(ba, w, h)
            _win_ocr_dead = False
            if text.strip():
                return text
        except OcrMissing as e:
            _win_ocr_dead, win_error = True, e
            log("OCR Windows: компонент не найден")
        except Exception as e:
            win_error = e
            log("OCR Windows не сработал: " + repr(e))
    if tesseract_paths():
        return ocr_tesseract(ba, w, h)
    if win_error is not None:
        raise win_error
    return ""


def ocr_windows(ba, w, h):
    k = 2 if (h <= 400 and w <= 1500) else 1
    ba, w, h = upscale(ba, w, h, k)
    tmp = tempfile.mkdtemp(prefix="scrtr_")
    img, out, ps1 = (os.path.join(tmp, n) for n in ("in.bmp", "out.txt", "ocr.ps1"))
    try:
        write_bmp(img, ba, w, h)
        with open(ps1, "w", encoding="ascii") as f:
            f.write(OCR_PS1)
        p = subprocess.run(
            [powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1,
             "-img", img, "-out", out],
            capture_output=True, timeout=60, creationflags=0x08000000)
        if not os.path.exists(out):
            err = (p.stderr or p.stdout or b"").decode("utf-8", "replace").strip()
            raise RuntimeError("Не удалось запустить OCR Windows. " + err[:300])
        with open(out, encoding="utf-8") as f:
            results = parse_ocr(f.read())
        if list(results) == ["none"]:
            raise OcrMissing("В Windows нет компонента распознавания текста (OCR). Это не то же самое, "
                             "что установленный язык — нужен отдельный компонент. "
                             "Нажми «Установить OCR» (нужен интернет и подтверждение Windows) "
                             "или открой «Настройки языка»: ⋯ у языка → Языковые параметры → "
                             "Оптическое распознавание символов → Скачать. "
                             "Не получится — возьми версию Full: ей это не нужно.")
        return pick_best(results)
    finally:
        for p_ in (img, out, ps1):
            try:
                os.remove(p_)
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


# ---- перевод ---------------------------------------------------------------
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_tr_cache = {}          # уже переведённое: повторный запрос не уходит в сеть
_RETRY_CODES = (429, 500, 502, 503)


def _http_get(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def split_chunks(text, limit):
    """Режет длинный текст по предложениям/словам, чтобы запрос не был слишком большим."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for piece in re.split(r"(?<=[.!?…])\s+", text):
        while len(piece) > limit:                       # одно «предложение» длиннее лимита
            cut = piece.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if cur:
                parts.append(cur); cur = ""
            parts.append(piece[:cut]); piece = piece[cut:].lstrip()
        if cur and len(cur) + 1 + len(piece) > limit:
            parts.append(cur); cur = piece
        else:
            cur = (cur + " " + piece).strip()
    if cur:
        parts.append(cur)
    return parts


def _tr_google_api(text, target, src):
    out = []
    for ch in split_chunks(text, 1000):
        data = json.loads(_http_get("https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto"
                                    f"&tl={target}&dt=t&q={urllib.parse.quote(ch)}"))
        out.append("".join(part[0] for part in data[0] if part[0]))
    return " ".join(out)


def _tr_google_mobile(text, target, src):
    """Мобильная страница Google Translate: другой адрес, часто работает, когда первый ограничил запросы."""
    out = []
    for ch in split_chunks(text, 800):
        page = _http_get(f"https://translate.google.com/m?sl=auto&tl={target}&q={urllib.parse.quote(ch)}")
        m = re.search(r'class="result-container">(.*?)</div>', page, re.S)
        if not m:
            raise RuntimeError("не удалось разобрать ответ")
        out.append(html.unescape(m.group(1)).strip())
    return " ".join(out)


def _tr_mymemory(text, target, src):
    out = []
    for ch in split_chunks(text, 400):                  # у сервиса лимит 500 байт на запрос
        data = json.loads(_http_get("https://api.mymemory.translated.net/get?q="
                                    f"{urllib.parse.quote(ch)}&langpair={src}|{target}"))
        res = (data.get("responseData") or {}).get("translatedText")
        if not res or data.get("responseStatus") not in (200, "200"):
            raise RuntimeError(str(data.get("responseDetails") or "пустой ответ"))
        out.append(html.unescape(res))
    return " ".join(out)


PROVIDERS = (_tr_google_api, _tr_google_mobile, _tr_mymemory)


def translate(text, mode="auto"):
    """mode: auto — русский->английский, остальное->русский; ru / en — всегда на этот язык.
    Если один сервис отвечает «слишком много запросов» (429) — пауза, повтор и переход на запасной."""
    cyr = len(re.findall(r"[А-Яа-яЁё]", text))
    lat = len(re.findall(r"[A-Za-z]", text))
    target = mode if mode in ("ru", "en") else ("en" if cyr >= lat else "ru")
    src = "ru" if cyr >= lat and cyr else "en"
    if src == "ru" and target == "ru":
        return text, target                             # русский текст -> русский: запрос не нужен
    key = (target, text)
    if key in _tr_cache:
        return _tr_cache[key], target
    errors, limited = [], False
    for fn in PROVIDERS:
        for attempt in range(2):
            try:
                res = fn(text, target, src).strip()
                if res:
                    if len(_tr_cache) > 200:
                        _tr_cache.clear()
                    _tr_cache[key] = res
                    return res, target
                errors.append(f"{fn.__name__}: пустой ответ")
                break
            except urllib.error.HTTPError as e:
                limited = limited or e.code == 429
                errors.append(f"{fn.__name__}: HTTP {e.code}")
                if e.code in _RETRY_CODES and attempt == 0:
                    time.sleep(1.5)                     # короткая пауза и ещё одна попытка
                    continue
                break
            except Exception as e:
                errors.append(f"{fn.__name__}: {e}")
                break
    log("Перевод не удался: " + "; ".join(errors))
    if limited:
        raise RuntimeError("Сервисы перевода временно ограничили запросы (слишком много переводов подряд). "
                           "Подожди пару минут и повтори.")
    raise RuntimeError("Не удалось перевести: нет интернета или сервисы перевода недоступны.")


# ---- интерфейс -------------------------------------------------------------
def round_rect(c, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


def hwnd_of(win):
    wid = win.winfo_id()
    return user32.GetParent(wid) or wid


class Selector:
    """Затемнение экрана и выделение области — как Win+Shift+S."""

    def __init__(self, app, done, shot):
        self.app, self.done = app, done
        self.bright = self.dim = self.ui = None
        self.finished = False
        try:
            self._build(app, done, shot)
        except Exception:
            self.finished = True
            for w in (self.ui, self.dim, self.bright):  # не оставляем полузаконченные окна на экране
                try:
                    w.destroy()
                except Exception:
                    pass
            raise

    def _build(self, app, done, shot):
        buf, self.vx, self.vy, self.vw, self.vh = shot
        self.start = self.cur = None
        self.finished = False
        root = app.root
        geo = f"{self.vw}x{self.vh}{self.vx:+d}{self.vy:+d}"

        # замороженный снимок: яркий (внизу) и затемнённый (сверху, с «дыркой» под выделение)
        self.img_bright = make_photo(root, buf, self.vw, self.vh)
        self.img_dim = make_photo(root, buf, self.vw, self.vh, (100 - cfg["dim"]) / 100)

        self.bright = tk.Toplevel(root)
        self.bright.overrideredirect(True)
        self.bright.attributes("-topmost", True)
        self.bright.geometry(geo)
        bc = tk.Canvas(self.bright, width=self.vw, height=self.vh, bd=0, highlightthickness=0,
                       cursor="crosshair")
        bc.pack()
        bc.create_image(0, 0, image=self.img_bright, anchor="nw")

        self.dim = tk.Toplevel(root)
        self.dim.overrideredirect(True)
        self.dim.attributes("-topmost", True)
        self.dim.geometry(geo)
        self.dimc = tk.Canvas(self.dim, width=self.vw, height=self.vh, bd=0, highlightthickness=0,
                              cursor="crosshair")
        self.dimc.pack()
        self.dimc.create_image(0, 0, image=self.img_dim, anchor="nw")

        self.ui = tk.Toplevel(root)
        self.ui.overrideredirect(True)
        self.ui.attributes("-topmost", True)
        self.ui.configure(bg=BG_KEY)
        self.ui.attributes("-transparentcolor", BG_KEY)
        self.ui.geometry(geo)
        self.cv = tk.Canvas(self.ui, bg=BG_KEY, highlightthickness=0, bd=0, cursor="crosshair")
        self.cv.pack(fill="both", expand=True)

        self.dim.update_idletasks()
        self.ui.update_idletasks()
        self.hwnd = hwnd_of(self.dim)

        # подсказка сверху по центру основного монитора
        S = app.S
        cx = root.winfo_screenwidth() // 2 - self.vx
        cy = S(46) - self.vy
        t = self.cv.create_text(cx, cy, text="Выдели область с текстом   ·   Esc — отмена",
                                fill=TEXT, font=(FONT, 11), tags="hint")
        b = self.cv.bbox(t)
        pad = S(14)
        bg = round_rect(self.cv, b[0] - pad, b[1] - S(8), b[2] + pad, b[3] + S(8), S(14),
                        fill=CARD, outline=BORDER, tags="hint")
        self.cv.tag_lower(bg, t)

        for w in (self.dimc, self.cv):
            w.bind("<ButtonPress-1>", self.on_press)
            w.bind("<B1-Motion>", self.on_move)
            w.bind("<ButtonRelease-1>", self.on_release)
            w.bind("<Escape>", lambda e: self.cancel())
        self.dim.focus_force()

    def on_press(self, e):
        self.start = self.cur = (e.x_root, e.y_root)
        self.cv.delete("hint")
        self.rect = self.cv.create_rectangle(0, 0, 0, 0, outline=ACCENT, width=2)
        self.lab_bg = self.cv.create_rectangle(0, 0, 0, 0, fill=ACCENT, outline="")
        self.lab = self.cv.create_text(0, 0, text="", fill="#10131f", font=(FONT, 9, "bold"), anchor="nw")
        self.redraw()

    def on_move(self, e):
        if self.start:
            self.cur = (e.x_root, e.y_root)
            self.redraw()

    def box(self):
        (sx, sy), (cx, cy) = self.start, self.cur
        return min(sx, cx), min(sy, cy), max(sx, cx), max(sy, cy)

    def redraw(self):
        x1, y1, x2, y2 = self.box()
        wx1, wy1, wx2, wy2 = x1 - self.vx, y1 - self.vy, x2 - self.vx, y2 - self.vy
        # дырка в затемнении = выделенная область остаётся яркой
        outer = gdi32.CreateRectRgn(0, 0, self.vw, self.vh)
        hole = gdi32.CreateRectRgn(wx1, wy1, wx2, wy2)
        gdi32.CombineRgn(outer, outer, hole, 4)  # RGN_DIFF
        user32.SetWindowRgn(self.hwnd, outer, True)
        gdi32.DeleteObject(hole)

        self.cv.coords(self.rect, wx1 - 1, wy1 - 1, wx2 + 1, wy2 + 1)
        S = self.app.S
        self.cv.itemconfig(self.lab, text=f"{x2 - x1} × {y2 - y1}")
        ty = wy2 + S(10)
        if ty + S(30) > self.vh:
            ty = wy1 - S(32)
        self.cv.coords(self.lab, wx1 + S(8), ty + S(4))
        b = self.cv.bbox(self.lab)
        self.cv.coords(self.lab_bg, b[0] - S(8), b[1] - S(4), b[2] + S(8), b[3] + S(4))

    def on_release(self, e):
        if not self.start or self.finished:
            return
        self.cur = (e.x_root, e.y_root)
        x1, y1, x2, y2 = self.box()
        w, h = x2 - x1, y2 - y1
        self.finish((x1, y1, w, h) if (w >= 12 and h >= 12) else None)

    def cancel(self):
        self.finish(None)

    def finish(self, rect):
        if self.finished:
            return
        self.finished = True
        for w in (self.ui, self.dim, self.bright):
            try:
                w.destroy()
            except tk.TclError:
                pass
        self.app.root.after(10, lambda: self.done(rect))


class SettingsWindow:
    """Окно «Настройки»: сочетание клавиш, язык перевода, затемнение, размер текста…"""

    def __init__(self, app, on_close):
        self.app, self.on_close = app, on_close
        self.new_hotkey = (cfg["mods"], cfg["vk"])
        self.capturing = False
        S = app.S
        w = self.win = tk.Toplevel(app.root)
        w.title("Настройки — Экранный переводчик")
        w.configure(bg=CARD)
        w.resizable(False, False)
        w.attributes("-topmost", True)
        w.protocol("WM_DELETE_WINDOW", self.close)
        w.bind("<Escape>", lambda e: None if self.capturing else self.close())

        body = tk.Frame(w, bg=CARD)
        body.pack(padx=S(24), pady=S(18))

        def section(text):
            tk.Label(body, text=text, bg=CARD, fg=ACCENT, font=(FONT, 9, "bold"),
                     anchor="w").pack(fill="x", pady=(S(14), S(5)))

        def check(text, var):
            c = tk.Checkbutton(body, text=text, variable=var, bg=CARD, fg=TEXT, selectcolor="#2a2d42",
                               activebackground=CARD, activeforeground=TEXT, font=(FONT, 10),
                               anchor="w", highlightthickness=0, bd=0)
            c.pack(fill="x")

        def slider(var, lo, hi, fmt):
            row = tk.Frame(body, bg=CARD)
            row.pack(fill="x")
            val = tk.Label(row, text=fmt(var.get()), bg=CARD, fg=TEXT, font=(FONT, 10), width=5, anchor="e")
            val.pack(side="right")
            tk.Scale(row, variable=var, from_=lo, to=hi, orient="horizontal", showvalue=False,
                     length=S(300), bg=CARD, fg=TEXT, troughcolor="#2a2d42", highlightthickness=0,
                     bd=0, activebackground=ACCENT,
                     command=lambda v: val.config(text=fmt(int(float(v))))).pack(side="left")

        section("СОЧЕТАНИЕ КЛАВИШ")
        row = tk.Frame(body, bg=CARD)
        row.pack(fill="x")
        self.key_lbl = tk.Label(row, text=current_label(), bg="#2a2d42", fg=TEXT,
                                font=(FONT, 12, "bold"), padx=S(14), pady=S(6))
        self.key_lbl.pack(side="left")
        chg = tk.Label(row, text="Изменить", bg=ACCENT, fg="#10131f", font=(FONT, 9, "bold"),
                       padx=S(12), pady=S(6), cursor="hand2")
        chg.pack(side="left", padx=(S(10), 0))
        chg.bind("<Button-1>", lambda e: self.start_capture())

        section("ЯЗЫК ПЕРЕВОДА")
        self.var_target = tk.StringVar(master=w, value=cfg["target"])
        for val, text in (("auto", "Авто: русский → английский, всё остальное → русский"),
                          ("ru", "Всегда на русский"), ("en", "Всегда на английский")):
            tk.Radiobutton(body, text=text, variable=self.var_target, value=val, bg=CARD, fg=TEXT,
                           selectcolor="#2a2d42", activebackground=CARD, activeforeground=TEXT,
                           font=(FONT, 10), anchor="w", highlightthickness=0, bd=0).pack(fill="x")

        section("ЗАТЕМНЕНИЕ ЭКРАНА ПРИ ВЫДЕЛЕНИИ")
        self.var_dim = tk.IntVar(master=w, value=cfg["dim"])
        slider(self.var_dim, 20, 85, lambda v: f"{v}%")

        section("РАЗМЕР ТЕКСТА ПЕРЕВОДА")
        self.var_font = tk.IntVar(master=w, value=cfg["font"])
        slider(self.var_font, 9, 20, lambda v: f"{v} пт")

        section("ПРОЧЕЕ")
        self.var_copy = tk.BooleanVar(master=w, value=cfg["autocopy"])
        check("Сразу копировать перевод в буфер обмена", self.var_copy)
        self.var_auto = tk.BooleanVar(master=w, value=is_autostart())
        check("Запускать вместе с Windows", self.var_auto)

        self.status = tk.Label(body, text="", bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w",
                               wraplength=S(380), justify="left")
        self.status.pack(fill="x", pady=(S(12), 0))

        foot = tk.Frame(body, bg=CARD)
        foot.pack(fill="x", pady=(S(10), 0))
        close = tk.Label(foot, text="Закрыть", bg="#2a2d42", fg=TEXT, font=(FONT, 9, "bold"),
                         padx=S(14), pady=S(6), cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.close())
        save = tk.Label(foot, text="Сохранить", bg=ACCENT, fg="#10131f", font=(FONT, 9, "bold"),
                        padx=S(14), pady=S(6), cursor="hand2")
        save.pack(side="right", padx=(0, S(8)))
        save.bind("<Button-1>", lambda e: self.save())

        w.update_idletasks()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        w.geometry(f"+{(sw - w.winfo_reqwidth()) // 2}+{(sh - w.winfo_reqheight()) // 3}")
        try:   # тёмная заголовочная полоса на Windows 10/11
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd_of(w), 20, ctypes.byref(ctypes.c_int(1)), 4)
        except Exception:
            pass
        w.focus_force()

    # ---- сочетание клавиш ----------------------------------------------------
    def start_capture(self):
        self.capturing = True
        self.app.capturing = True
        self.key_lbl.config(text="Нажми новое сочетание…", fg=ACCENT)
        self.say("Зажми Ctrl, Alt или Shift и нажми клавишу. Esc — отмена.")
        self.win.bind("<KeyPress>", self.on_key)
        self.win.focus_force()

    def stop_capture(self):
        self.capturing = False
        self.app.capturing = False
        self.win.unbind("<KeyPress>")
        self.key_lbl.config(text=hotkey_label(*self.new_hotkey), fg=TEXT)

    def on_key(self, e):
        vk = e.keycode                      # в Tk на Windows keycode = виртуальный код клавиши
        if vk in MODIFIER_VKS:
            return "break"                  # одни модификаторы — ждём основную клавишу
        mods = ((MOD_CTRL if is_down(0x11) else 0) | (MOD_ALT if is_down(0x12) else 0) |
                (MOD_SHIFT if is_down(0x10) else 0) | (MOD_WIN if is_down(0x5B) or is_down(0x5C) else 0))
        if vk == VK_ESCAPE and not mods:
            self.stop_capture()
            self.say("")
            return "break"
        if not mods:
            self.say("Нужен хотя бы один из Ctrl / Alt / Shift вместе с клавишей.")
            return "break"
        self.new_hotkey = (mods, vk)
        self.stop_capture()
        self.say("Нажми «Сохранить», чтобы применить.")
        return "break"

    # ---- сохранение ----------------------------------------------------------
    def say(self, text, error=False):
        try:
            self.status.config(text=text, fg="#f7768e" if error else MUTED)
        except tk.TclError:
            pass

    def save(self):
        cfg["target"] = self.var_target.get()
        cfg["dim"] = int(self.var_dim.get())
        cfg["font"] = int(self.var_font.get())
        cfg["autocopy"] = bool(self.var_copy.get())
        problems = []
        try:
            if bool(self.var_auto.get()) != is_autostart():
                set_autostart(bool(self.var_auto.get()))
        except OSError:
            problems.append("автозапуск не удалось изменить")
        save_settings()
        if self.new_hotkey != (cfg["mods"], cfg["vk"]):
            request_hotkey(self.new_hotkey)   # итог придёт событием hotkey_ok / hotkey_fail
            self.say("Применяю сочетание…")
        else:
            self.say("Сохранено ✓" if not problems else "Сохранено, но " + ", ".join(problems), bool(problems))

    def hotkey_result(self, ok, new):
        if ok:
            self.new_hotkey = new
            self.key_lbl.config(text=hotkey_label(*new))
            self.say("Сохранено ✓ Новое сочетание работает.")
        else:
            self.new_hotkey = (cfg["mods"], cfg["vk"])
            self.key_lbl.config(text=current_label(), fg=TEXT)
            self.say(f"{hotkey_label(*new)} занято другой программой — оставил {current_label()}. "
                     "Выбери другое.", error=True)

    def close(self):
        self.app.capturing = False
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.on_close()

    def lift(self):
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
        except tk.TclError:
            pass


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.report_callback_exception = lambda *a: log("".join(traceback.format_exception(*a)))
        self.S = lambda v: int(round(v * self.root.winfo_fpixels("1i") / 96.0))
        self.popup = None
        self.settings_win = None
        self.capturing = False   # пока записываем новое сочетание — горячая клавиша не запускает перевод
        self.last_crop = None
        self.selector = None
        self.anchor = (100, 100, 0, 0)
        self.opened_at = 0.0
        threading.Thread(target=hotkey_thread, daemon=True).start()
        self.root.after(50, self.poll)
        print(f"Готово. Нажми {current_label()} и обведи текст на экране.")

    def close(self):
        if self.popup is not None:
            try:
                self.popup.destroy()
            except tk.TclError:
                pass
            self.popup = None

    # ---- окно результата ----------------------------------------------------
    def show(self, label, body, original=None, loading=False, error=False, action=None):
        animate = self.popup is None
        self.close()
        S = self.S
        vx, vy, vw, vh = virtual_screen()
        p = tk.Toplevel(self.root)
        self.popup = p
        p.overrideredirect(True)
        p.attributes("-topmost", True)
        p.configure(bg=BG_KEY)
        p.attributes("-transparentcolor", BG_KEY)

        c = tk.Canvas(p, bg=BG_KEY, highlightthickness=0, bd=0)
        c.pack()
        f = tk.Frame(c, bg=CARD)  # ребёнок канваса — значит поверх подложки

        head = tk.Frame(f, bg=CARD)
        head.pack(fill="x")
        chip_bg = "#f7768e" if error else ACCENT
        tk.Label(head, text=label, bg=chip_bg, fg="#10131f", font=(FONT, 8, "bold"),
                 padx=S(8), pady=1).pack(side="left")
        x_btn = tk.Label(head, text="✕", bg=CARD, fg=MUTED, font=(FONT, 10), cursor="hand2")
        x_btn.pack(side="right")
        x_btn.bind("<Button-1>", lambda e: self.close())
        x_btn.bind("<Enter>", lambda e: x_btn.config(fg="#f7768e"))
        x_btn.bind("<Leave>", lambda e: x_btn.config(fg=MUTED))

        wrap = S(440)
        if original:
            short = original if len(original) <= 200 else original[:197] + "…"
            tk.Label(f, text=short, bg=CARD, fg=MUTED, font=(FONT, 9), wraplength=wrap,
                     justify="left", anchor="w").pack(fill="x", pady=(S(10), 0))
            tk.Frame(f, bg=BORDER, height=1).pack(fill="x", pady=S(8))
        else:
            tk.Frame(f, height=S(8), bg=CARD).pack()

        shown = body if len(body) <= 1500 else body[:1497] + "…"
        tk.Label(f, text=shown, bg=CARD, fg=(MUTED if (loading or error) else TEXT),
                 font=(FONT, cfg["font"], "italic" if loading else "normal"),
                 wraplength=wrap, justify="left", anchor="w").pack(fill="x")

        if not loading and not error:
            btn = tk.Label(f, text="Копировать", bg="#2a2d42", fg=TEXT, font=(FONT, 9),
                           padx=S(12), pady=S(4), cursor="hand2")
            btn.pack(anchor="e", pady=(S(12), 0))

            def do_copy(_e=None):
                self.root.clipboard_clear()
                self.root.clipboard_append(body)
                self.root.update()
                btn.config(text="Скопировано ✓", fg="#9ece6a")

            btn.bind("<Button-1>", do_copy)
            btn.bind("<Enter>", lambda e: btn.config(bg="#363a55"))
            btn.bind("<Leave>", lambda e: btn.config(bg="#2a2d42"))

        if action:
            acts = action if isinstance(action, list) else [action]
            row = tk.Frame(f, bg=CARD)
            row.pack(anchor="e", pady=(S(12), 0))
            for i, (action_text, action_fn) in enumerate(acts):
                first = i == 0
                ab = tk.Label(row, text=action_text, bg=ACCENT if first else "#2a2d42",
                              fg="#10131f" if first else TEXT, font=(FONT, 9, "bold"),
                              padx=S(14), pady=S(5), cursor="hand2")
                ab.pack(side="left", padx=(0 if first else S(8), 0))
                ab.bind("<Button-1>", lambda e, fn=action_fn: fn())

        f.update_idletasks()
        px, py = S(18), S(14)
        w, h = f.winfo_reqwidth() + 2 * px, f.winfo_reqheight() + 2 * py
        c.config(width=w, height=h)
        round_rect(c, 1, 1, w - 1, h - 1, S(16), fill=CARD, outline=BORDER)
        c.create_window(px, py, window=f, anchor="nw")

        ax, ay, aw, ah = self.anchor
        x, y = ax, ay + ah + S(10)
        if y + h > vy + vh - S(50):
            y = ay - h - S(10)
        x = max(vx + 5, min(x, vx + vw - w - 5))
        y = max(vy + 5, min(y, vy + vh - h - 5))
        p.geometry(f"{w}x{h}{x:+d}{y:+d}")
        self.opened_at = time.time()

        if animate:
            def fade(a=0.0):
                if self.popup is not p:
                    return
                try:
                    p.attributes("-alpha", min(a, 1.0))
                except tk.TclError:
                    return
                if a < 1.0:
                    p.after(15, lambda: fade(a + 0.2))
            fade()

    # ---- логика -------------------------------------------------------------
    def open_settings(self):
        if self.settings_win is not None:
            self.settings_win.lift()
            return
        self.settings_win = SettingsWindow(self, lambda: setattr(self, "settings_win", None))

    def on_hotkey(self):
        if self.selector is not None or self.capturing:
            return
        self.close()
        self.root.update()
        time.sleep(0.15)  # даём закрытому окну/меню исчезнуть с экрана
        vx, vy, vw, vh = virtual_screen()
        try:
            buf = capture(vx, vy, vw, vh, topdown=True)
        except Exception as e:
            self.show("Ошибка", f"Не удалось снять экран: {e}", error=True)
            return
        self.shot = (buf, vx, vy, vw, vh)
        try:
            self.selector = Selector(self, self.on_selected, self.shot)
        except Exception as e:
            self.selector = None
            self.shot = None
            log("Не удалось показать затемнение:\n" + traceback.format_exc())
            self.show("Ошибка", f"Не удалось показать выделение экрана: {e}", error=True)

    def open_language_settings(self):
        try:
            os.startfile("ms-settings:regionlanguage")
        except Exception:
            log("Не открылись настройки языка:\n" + traceback.format_exc())

    def install_ocr(self):
        """Ставит компонент OCR (английский и русский) через Windows; нужен UAC и интернет."""
        self.show("Установка", "Устанавливаю компонент распознавания… Подтверди запрос Windows "
                  "и подожди 1–3 минуты.", loading=True)

        def work():
            out = os.path.join(tempfile.gettempdir(), "ScreenTranslator_ocr_install.txt")
            try:
                os.remove(out)
            except OSError:
                pass
            inner = (
                "$r=@(); foreach($n in 'Language.OCR~~~en-US~~~0.0.1.0','Language.OCR~~~ru-RU~~~0.0.1.0'){ "
                "try { $x = Add-WindowsCapability -Online -Name $n -ErrorAction Stop; "
                "$r += ('OK|' + $n + '|restart=' + $x.RestartNeeded) } "
                "catch { $r += ('FAIL|' + $n + '|' + ($_.Exception.Message -replace '\\s+',' ')) } }; "
                "$r | Out-File -Encoding utf8 '" + out.replace("'", "''") + "'")
            b64 = base64.b64encode(inner.encode("utf-16-le")).decode()
            cmd = ("Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
                   f"-ArgumentList '-NoProfile','-EncodedCommand','{b64}'")
            try:
                subprocess.run([powershell_exe(), "-NoProfile", "-Command", cmd],
                               capture_output=True, timeout=1200, creationflags=0x08000000)
            except Exception:
                log("Установка OCR:\n" + traceback.format_exc())
            result = ""
            if os.path.exists(out):
                with open(out, encoding="utf-8-sig", errors="replace") as fh:
                    result = fh.read()
                log("Результат установки OCR:\n" + result)
            events.put(install_outcome(result, windows_ocr_languages))

        threading.Thread(target=work, daemon=True).start()

    def run_ocr(self, ba, w, h):
        """Распознаёт и переводит вырезанную область (её же повторяем после установки OCR)."""
        self.last_crop = (ba, w, h)
        self.show("Перевод", "Распознаю текст…", loading=True)

        def work():
            try:
                text = ocr_image(ba, w, h).strip()
                if not text:
                    events.put(("error", "Текст не найден. Выдели область поближе и покрупнее."))
                    return
                text = text[:MAX_CHARS]
                events.put(("loading", "Перевожу…"))
                mode = cfg["target"]
                res, target = translate(text, mode)
                label = ("RU → EN" if target == "en" else "EN → RU") if mode == "auto" else f"→ {target.upper()}"
                events.put(("result", (res, label, text)))
            except OcrMissing as e:
                events.put(("ocr_missing", str(e)))
            except Exception as e:
                log("Ошибка распознавания/перевода:\n" + traceback.format_exc())
                events.put(("error", f"{e}"))

        threading.Thread(target=work, daemon=True).start()

    def on_selected(self, rect):
        self.selector = None
        if rect is None:
            return
        x, y, w, h = rect
        self.anchor = rect
        buf, vx, vy, vw, vh = self.shot
        self.shot = None
        x0 = max(0, min(x - vx, vw - 1))
        y0 = max(0, min(y - vy, vh - 1))
        w, h = max(1, min(w, vw - x0)), max(1, min(h, vh - y0))
        ba = crop_bottom_up(buf, vw, x0, y0, w, h)  # режем замороженный снимок
        self.run_ocr(ba, w, h)

    def poll(self):
        try:
            while True:
                kind, payload = events.get_nowait()
                if kind == "hotkey":
                    self.on_hotkey()
                elif kind == "loading":
                    self.show("Перевод", payload, loading=True)
                elif kind == "result":
                    self.show(payload[1], payload[0], original=payload[2])
                    if cfg["autocopy"]:
                        self.root.clipboard_clear()
                        self.root.clipboard_append(payload[0])
                elif kind == "settings":
                    self.open_settings()
                elif kind == "hotkey_ok":
                    cfg["mods"], cfg["vk"] = payload
                    save_settings()
                    update_tray_tip()
                    if self.settings_win is not None:
                        self.settings_win.hotkey_result(True, payload)
                elif kind == "hotkey_fail":
                    if self.settings_win is not None:
                        self.settings_win.hotkey_result(False, payload)
                    else:
                        self.show("Ошибка", f"{hotkey_label(*payload)} занято другой программой. "
                                            f"Осталось {current_label()}.", error=True)
                elif kind == "error":
                    self.show("Ошибка", payload, error=True)
                elif kind == "ocr_missing":
                    self.show("Ошибка", payload, error=True,
                              action=[("Установить OCR", self.install_ocr),
                                      ("Настройки языка", self.open_language_settings)])
                elif kind == "notice":
                    self.show("Установка", payload, loading=True)
                elif kind == "retry":   # OCR поставлен и виден Windows — повторяем ту же область
                    if self.last_crop:
                        self.run_ocr(*self.last_crop)
                    else:
                        self.show("Установка", payload, loading=True)
        except queue.Empty:
            pass

        if self.selector is not None and is_down(VK_ESCAPE):
            self.selector.cancel()
        elif self.popup is not None and time.time() - self.opened_at > 0.3:
            if is_down(VK_ESCAPE):
                self.close()
            elif is_down(VK_LBUTTON):
                try:
                    px, py = self.root.winfo_pointerx(), self.root.winfo_pointery()
                    x0, y0 = self.popup.winfo_rootx(), self.popup.winfo_rooty()
                    if not (x0 <= px <= x0 + self.popup.winfo_width() and
                            y0 <= py <= y0 + self.popup.winfo_height()):
                        self.close()
                except tk.TclError:
                    pass
        self.root.after(60, self.poll)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if already_running():   # уже запущено (например, из автозагрузки) — второй копии не нужно
        sys.exit(0)
    load_settings()
    first_run_setup()
    App().run()
