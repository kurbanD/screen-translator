"""
Экранный переводчик RU <-> EN (Windows 10/11).

  1. python translator.py   (или pythonw translator.py — без консоли)
  2. Нажми Ctrl+Alt+T — экран затемнится, как при Win+Shift+S.
  3. Обведи мышкой область с текстом — отпусти кнопку.
  4. Рядом появится перевод. Русский -> английский, остальное -> русский.

Esc — отмена / закрыть окно. Клик мимо окна — тоже закрывает.

Текст читается встроенным OCR Windows: в обычных окнах, картинках, видео, PDF.
Эксклюзивный полноэкранный режим и защищённое содержимое могут не захватываться.
OCR локальный; распознанный текст отправляется онлайн-сервисам перевода.
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
from tkinter import ttk
import traceback
import urllib.parse
import urllib.request
import winreg
import zlib
import textwrap
from collections import OrderedDict, deque

# ---- Настройки -------------------------------------------------------------
MAX_CHARS = 4000
VERSION = "2.0"                         # виден в подсказке значка в трее
# Всё остальное (сочетание клавиш, язык, затемнение…) меняется в окне «Настройки» из меню значка в трее.
# ----------------------------------------------------------------------------

BG_KEY = "#010101"      # этот цвет в окнах становится прозрачным
CARD = "#11151D"
BORDER = "#2C3547"
ACCENT = "#75B8FF"
TEXT = "#F1F5F9"
MUTED = "#A5B4C7"
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
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, CI, CI, CI, CI, wt.UINT]
user32.SetWindowPos.restype = wt.BOOL
user32.MonitorFromPoint.argtypes = [wt.POINT, wt.DWORD]
user32.MonitorFromPoint.restype = H

class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

user32.GetMonitorInfoW.argtypes = [H, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wt.BOOL

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
    "src": "auto",                # язык оригинала: auto или код языка
    "target": "auto",             # язык перевода: auto (ru<->en) или код языка
    "popw": 330,                  # ширина окна перевода, px
    "autoclose": 0,               # закрывать окно перевода через N сек (0 — не закрывать)
    "showorig": False,             # показывать распознанный оригинал в окне перевода
    "dim": 55,                    # насколько затемнять экран при выделении, %
    "font": 11,                   # размер текста в окне перевода
    "autocopy": False,
    "animations": True,
    "pin": False,
    "compact_layout": True,  # маркер миграции настроек компактного интерфейса
}
# языки, на которые можно переводить (код Google/MyMemory -> название)
LANGS = {
    "ru": "Русский", "en": "Английский", "ar": "Арабский", "av": "Аварский",
    "az": "Азербайджанский", "sq": "Албанский", "am": "Амхарский", "hy": "Армянский",
    "af": "Африкаанс", "eu": "Баскский", "be": "Белорусский", "bn": "Бенгальский",
    "bg": "Болгарский", "bs": "Боснийский", "cy": "Валлийский", "hu": "Венгерский",
    "vi": "Вьетнамский", "gl": "Галисийский", "el": "Греческий", "ka": "Грузинский",
    "gu": "Гуджарати", "da": "Датский", "zu": "Зулусский", "he": "Иврит", "yi": "Идиш",
    "id": "Индонезийский", "ga": "Ирландский", "is": "Исландский", "es": "Испанский",
    "it": "Итальянский", "kk": "Казахский", "kn": "Каннада", "ca": "Каталанский",
    "ky": "Киргизский", "zh-CN": "Китайский (упрощённый)", "zh-TW": "Китайский (традиционный)",
    "ko": "Корейский", "co": "Корсиканский", "ku": "Курдский (курманджи)",
    "ckb": "Курдский (сорани)", "km": "Кхмерский", "lo": "Лаосский", "la": "Латинский",
    "lv": "Латышский", "lt": "Литовский", "lb": "Люксембургский", "mk": "Македонский",
    "ms": "Малайский", "ml": "Малаялам", "mt": "Мальтийский", "mr": "Маратхи",
    "mn": "Монгольский", "my": "Мьянманский (бирманский)", "de": "Немецкий", "ne": "Непальский",
    "nl": "Нидерландский", "no": "Норвежский", "or": "Одия", "pa": "Панджаби",
    "fa": "Персидский (фарси)", "pl": "Польский", "pt": "Португальский", "ps": "Пушту",
    "ro": "Румынский", "sr": "Сербский", "si": "Сингальский", "sk": "Словацкий",
    "sl": "Словенский", "so": "Сомалийский", "sw": "Суахили", "tg": "Таджикский",
    "th": "Тайский", "ta": "Тамильский", "tt": "Татарский", "te": "Телугу", "bo": "Тибетский",
    "tr": "Турецкий", "tk": "Туркменский", "uz": "Узбекский", "uk": "Украинский",
    "ur": "Урду", "tl": "Филиппинский", "fi": "Финский", "fr": "Французский", "hi": "Хинди",
    "hr": "Хорватский", "ce": "Чеченский", "cs": "Чешский", "sv": "Шведский",
    "gd": "Шотландский гэльский", "et": "Эстонский", "eo": "Эсперанто", "jv": "Яванский",
    "ja": "Японский", "ba": "Башкирский", "os": "Осетинский", "cv": "Чувашский",
    "ha": "Хауса", "yo": "Йоруба", "ig": "Игбо", "mg": "Малагасийский",
    "mi": "Маори", "sm": "Самоанский", "haw": "Гавайский", "ht": "Гаитянский креольский",
    "as": "Ассамский", "sa": "Санскрит", "sd": "Синдхи", "ug": "Уйгурский",
}
AUTO_SRC = "Автоопределение"
AUTO_DST = "Авто (русский ⇄ английский)"
FAVORITES = ("ru", "en", "ar", "de", "fr", "es", "tr", "zh-CN", "ja", "ko")

# Коды Tesseract отличаются от кодов сервиса перевода.
TESS_LANGS = dict(zip(
    "ru en ar az sq am hy af eu be bn bg bs cy hu vi gl el ka gu da he yi id ga is es it kk kn ca ky zh-CN zh-TW ko ku km lo la lv lt mk ms ml mt mr mn my de ne nl no or pa fa pl pt ps ro sr si sk sl sw tg th ta tt te bo tr uz uk ur tl fi fr hi hr cs sv et eo jv ja as sa sd ug".split(),
    "rus eng ara aze sqi amh hye afr eus bel ben bul bos cym hun vie glg ell kat guj dan heb yid ind gle isl spa ita kaz kan cat kir chi_sim chi_tra kor kur khm lao lat lav lit mkd msa mal mlt mar mon mya deu nep nld nor ori pan fas pol por pus ron srp sin slk slv swa tgk tha tam tat tel bod tur uzb ukr urd fil fin fra hin hrv ces swe est epo jav jpn asm san snd uig".split()))


def ocr_tag_matches(tag, code):
    tag, code = tag.lower().replace("_", "-"), code.lower()
    if code == "zh-cn":
        return tag in ("zh", "zh-cn", "zh-sg", "zh-hans") or tag.startswith("zh-hans-")
    if code == "zh-tw":
        return tag in ("zh-tw", "zh-hk", "zh-mo", "zh-hant") or tag.startswith("zh-hant-")
    aliases = {"he": {"he", "iw"}, "id": {"id", "in"}, "no": {"no", "nb", "nn"}, "tl": {"fil", "tl"}, "jv": {"jv", "jw"}}
    return tag.split("-")[0] in aliases.get(code, {code.split("-")[0]})


def matching_languages(query=""):
    q = query.strip().casefold().replace("ё", "е")
    codes = list(FAVORITES) + sorted((c for c in LANGS if c not in FAVORITES), key=lambda c: LANGS[c])
    return [c for c in codes if not q or q in LANGS[c].casefold().replace("ё", "е") or q in c.casefold()]


def lang_titles(auto_title):
    return [auto_title] + [LANGS[c] for c in matching_languages()]


def lang_title(code, auto_title):
    return LANGS.get(code, auto_title)


def lang_code(title):
    for code, name in LANGS.items():
        if title == name:
            return code
    return "auto"


def route_text(src, dst):
    n = lambda c: LANGS.get(c, c)
    if src == "auto" and dst == "auto":
        return "Русский → английский, всё остальное → русский"
    if dst == "auto":
        return f"{n(src)} → " + ("Английский" if src == "ru" else "Русский")
    if src == "auto":
        return f"Любой язык → {n(dst)}"
    return f"{n(src)} → {n(dst)}"


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
    if key in ("target", "src"):
        return v == "auto" or v in LANGS
    if key == "popw":
        return 240 <= v <= 900
    if key == "autoclose":
        return 0 <= v <= 120
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
        if "compact_layout" not in data:
            # Старые стандартные размеры заменяем компактными; нестандартные сохраняем.
            if cfg["popw"] == 440:
                cfg["popw"] = DEFAULTS["popw"]
            if cfg["font"] == 12:
                cfg["font"] = DEFAULTS["font"]
            cfg["showorig"] = False


def save_settings():
    tmp = None
    try:
        folder = os.path.dirname(SETTINGS_PATH)
        os.makedirs(folder, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder,
                                         prefix="settings-", delete=False) as f:
            tmp = f.name
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_PATH)
        return True
    except OSError:
        log("Не удалось сохранить настройки:\n" + traceback.format_exc())
        return False
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

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
ID_TRANSLATE, ID_QUIT, ID_AUTOSTART, ID_SETTINGS, ID_LAST, ID_HISTORY = 1000, 1001, 1002, 1003, 1004, 1005
ID_CLIPBOARD = 1006
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
user32.TrackPopupMenu.restype = wt.UINT
user32.DestroyIcon.argtypes = [H]
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
    try:
        return user32.CreateIconIndirect(ctypes.byref(ii))
    finally:
        if color:
            gdi32.DeleteObject(color)
        if mask:
            gdi32.DeleteObject(mask)


def setup_tray():
    hinst = kernel32.GetModuleHandleW(None)
    taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
    state = {}

    def remove_icon():
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(state["nid"]))

    def show_menu(hwnd):
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, 0, ID_TRANSLATE, "Перевести область")
        user32.AppendMenuW(menu, 0, ID_CLIPBOARD, "Перевести текст из буфера")
        user32.AppendMenuW(menu, 0, ID_LAST, "Показать последний перевод")
        user32.AppendMenuW(menu, 0, ID_HISTORY, "История переводов")
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
        elif cmd == ID_CLIPBOARD:
            events.put(("clipboard", None))
        elif cmd == ID_SETTINGS:
            events.put(("settings", None))
        elif cmd == ID_LAST:
            events.put(("last", None))
        elif cmd == ID_HISTORY:
            events.put(("history", None))
        elif cmd == ID_AUTOSTART:
            try:
                set_autostart(not is_autostart())
            except OSError:
                pass
        elif cmd == ID_QUIT:
            remove_icon()
            events.put(("quit", None))

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
    """Автозагрузка только по выбору пользователя; существующая настройка сохраняется."""
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\ScreenTranslator") as k:
            try:
                winreg.QueryValueEx(k, "Initialized")
                return
            except FileNotFoundError:
                pass
            winreg.SetValueEx(k, "Initialized", 0, winreg.REG_DWORD, 1)
    except OSError:
        pass


def already_running():
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, "Local\\ScreenTranslatorMutex")
    if not _mutex:
        raise ctypes.WinError()
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]


def request_hotkey(new):
    """Просит поток с горячей клавишей перейти на новое сочетание (mods, vk)."""
    _pending_hotkey[:] = [new]
    if not _hotkey_tid[0] or not user32.PostThreadMessageW(_hotkey_tid[0], WM_REREG, 0, 0):
        events.put(("hotkey_fail", new))


def hotkey_thread():
    _hotkey_tid[0] = kernel32.GetCurrentThreadId()
    try:
        setup_tray()
    except Exception as e:  # без значка приложение всё равно работает
        print("Не удалось создать значок в трее:", e)
    active = (cfg["mods"], cfg["vk"])
    registered = bool(user32.RegisterHotKey(None, 1, active[0] | MOD_NOREPEAT, active[1]))
    if not registered:
        events.put(("error", f"Не удалось занять {current_label()} — сочетание уже используется "
                             "другой программой. Выбери другое: значок в трее → Настройки."))
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            events.put(("hotkey", None))
        elif msg.message == WM_REREG:
            if _pending_hotkey:
                new = _pending_hotkey.pop()
                if registered:
                    user32.UnregisterHotKey(None, 1)
                registered = bool(user32.RegisterHotKey(None, 1, new[0] | MOD_NOREPEAT, new[1]))
                if registered:
                    active = new
                    events.put(("hotkey_ok", new))
                else:
                    registered = bool(user32.RegisterHotKey(None, 1, active[0] | MOD_NOREPEAT, active[1]))
                    events.put(("hotkey_fail", new))
                    if not registered:
                        events.put(("error", "Горячая клавиша не зарегистрирована. Выбери другую в настройках."))
        else:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


# ---- снимок экрана ---------------------------------------------------------
def capture(x, y, w, h, topdown=False):
    """BGRA-пиксели области экрана. Не возвращает пустую картинку при сбое GDI."""
    if w <= 0 or h <= 0:
        raise ValueError("Некорректный размер области захвата")
    hdc = user32.GetDC(None)
    if not hdc:
        raise ctypes.WinError()
    mdc = bmp = old = None
    try:
        mdc = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        if not mdc or not bmp:
            raise ctypes.WinError()
        old = gdi32.SelectObject(mdc, bmp)
        if not old or old == ctypes.c_void_p(-1).value:
            old = None
            raise ctypes.WinError()
        if not gdi32.BitBlt(mdc, 0, 0, w, h, hdc, x, y, SRCCOPY | CAPTUREBLT):
            raise ctypes.WinError()
        if not gdi32.SelectObject(mdc, old):
            raise ctypes.WinError()
        old = None
        size = w * h * 4
        ba = bytearray(size)
        buf = (ctypes.c_char * size).from_buffer(ba)
        bih = struct.pack("<IiiHHIIiiII", 40, w, -h if topdown else h, 1, 32, 0, size, 0, 0, 0, 0)
        bih_buf = ctypes.create_string_buffer(bih, 40)
        if gdi32.GetDIBits(mdc, bmp, 0, h, buf, bih_buf, 0) != h:
            raise RuntimeError("Не удалось получить все пиксели экрана")
        return ba
    finally:
        if old and mdc:
            gdi32.SelectObject(mdc, old)
        if bmp:
            gdi32.DeleteObject(bmp)
        if mdc:
            gdi32.DeleteDC(mdc)
        user32.ReleaseDC(None, hdc)

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


def pick_best(results, preferred="auto"):
    candidates = [(tag, text) for tag, text in results.items() if tag not in ("none", "error")]
    if preferred != "auto":
        candidates = [(tag, text) for tag, text in candidates if ocr_tag_matches(tag, preferred)]
        if not candidates:
            raise OcrMissing(f"Нет OCR для языка «{LANGS.get(preferred, preferred)}». Установи его в параметрах языка Windows или выбери автоопределение.")
    candidates = [(tag, text) for tag, text in candidates if text.strip()]
    if not candidates:
        return ""
    def score(item):
        tag, text = item
        letters = sum(ch.isalpha() for ch in text)
        if tag.lower().startswith("ru"):
            letters = len(re.findall(r"[А-Яа-яЁё]", text))
        elif tag.lower().startswith("en"):
            letters = len(re.findall(r"[A-Za-z]", text))
        return letters, sum(ch.isdigit() for ch in text)
    return max(candidates, key=score)[1]

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
            reset_ocr_worker()      # постоянный процесс OCR запомнил старый список языков
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


def ocr_tesseract(ba, w, h, preferred="auto"):
    """Tesseract читает картинку и её негатив (светлый текст на тёмном фоне), берём лучший результат."""
    found = tesseract_paths()
    if not found:
        raise OcrMissing("Нет встроенного распознавания текста.")
    exe, tessdata = found
    available = {name[:-12] for name in os.listdir(tessdata) if name.endswith(".traineddata")} if os.path.isdir(tessdata) else set()
    if preferred == "auto":
        langs = [l for l in ("rus", "eng") if l in available]
        if not langs:
            langs = sorted(available & set(TESS_LANGS.values()))[:4]
    else:
        code = TESS_LANGS.get(preferred)
        langs = [code] if code in available else []
    if not langs:
        raise OcrMissing(f"У Tesseract нет языковых данных для «{LANGS.get(preferred, 'автоопределение')}».")
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
        return fix_homoglyphs(text) if set(langs) == {"rus", "eng"} else text
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_win_ocr_dead = False   # Windows уже сказала «нет OCR» — в Full не тратим время на повтор


def ocr_image(ba, w, h, preferred="auto"):
    """Сначала OCR Windows, если он не сработал — Tesseract (в сборке Full)."""
    global _win_ocr_dead
    win_error = None
    if not (preferred == "auto" and _win_ocr_dead and tesseract_paths()):
        try:
            text = ocr_windows(ba, w, h, preferred)
            _win_ocr_dead = False
            if text.strip():
                return text
        except OcrMissing as e:
            _win_ocr_dead, win_error = preferred == "auto", e
            log("OCR Windows: компонент не найден")
        except Exception as e:
            win_error = e
            log("OCR Windows не сработал: " + repr(e))
    if tesseract_paths():
        return ocr_tesseract(ba, w, h, preferred)
    if win_error is not None:
        raise win_error
    return ""


# Постоянный процесс PowerShell: движки OCR создаются один раз при старте, а не при каждом переводе
# (запуск PowerShell + загрузка WinRT давали 1–3 секунды задержки на каждое выделение).
OCR_WORKER_PS1 = OCR_PS1.split("$file = Await")[0].replace("param([string]$img, [string]$out)\n", "") + r"""
$engines = @()
foreach ($lang in [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages) {
    $e = $null
    try { $e = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang) } catch { }
    if ($e -ne $null) { $engines += ,@($lang.LanguageTag, $e) }
}
if ($engines.Count -eq 0) {
    $e = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($e -ne $null) { $engines += ,@('auto', $e) }
}
function B64($s) { [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s)) }
[Console]::Out.WriteLine('READY'); [Console]::Out.Flush()
while ($true) {
    $line = [Console]::In.ReadLine()
    if ($line -eq $null) { break }
    $p = $line.Split('|')
    $img = B64 $p[0]
    $out = B64 $p[1]
    $sb = New-Object System.Text.StringBuilder
    $stream = $null
    try {
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($img)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bmp = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        foreach ($pair in $engines) {
            $r = Await ($pair[1].RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
            [void]$sb.AppendLine('###' + $pair[0])
            [void]$sb.AppendLine($r.Text)
        }
        if ($engines.Count -eq 0) { [void]$sb.AppendLine('###none') }
    } catch {
        $sb = New-Object System.Text.StringBuilder
        [void]$sb.AppendLine('###error')
        [void]$sb.AppendLine($_.Exception.Message)
    } finally {
        if ($stream -ne $null) { try { $stream.Dispose() } catch { } }
    }
    [System.IO.File]::WriteAllText($out, $sb.ToString(), (New-Object System.Text.UTF8Encoding($false)))
    [Console]::Out.WriteLine('DONE'); [Console]::Out.Flush()
}
"""


class OcrWorker:
    def __init__(self):
        self.lock = threading.RLock()
        self.proc = None
        self.tmp = None
        self.lines = queue.Queue()

    def _wait(self, token, timeout):
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise RuntimeError("OCR-процесс не ответил вовремя")
            try:
                line = self.lines.get(timeout=left)
            except queue.Empty:
                raise RuntimeError("OCR-процесс не ответил вовремя")
            if line is None:
                raise RuntimeError("OCR-процесс завершился")
            if line == token:
                return

    def _start(self):
        self.tmp = tempfile.mkdtemp(prefix="scrtr_w_")
        ps1 = os.path.join(self.tmp, "worker.ps1")
        with open(ps1, "w", encoding="ascii") as f:
            f.write(OCR_WORKER_PS1)
        q = self.lines = queue.Queue()
        proc = subprocess.Popen(
            [powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", ps1],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=0x08000000)
        self.proc = proc

        def reader():
            try:
                for raw in proc.stdout:
                    q.put(raw.decode("utf-8", "replace").strip())
            except Exception:
                pass
            q.put(None)

        threading.Thread(target=reader, daemon=True).start()
        self._wait("READY", 60)

    def stop(self):
        with self.lock:
            p, self.proc = self.proc, None
            if p is not None:
                try:
                    p.kill()
                    p.wait(timeout=3)
                except Exception:
                    pass
            if self.tmp:
                shutil.rmtree(self.tmp, ignore_errors=True)
                self.tmp = None

    def ensure(self):
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self.stop()
                self._start()

    def recognize(self, img, out, timeout=60):
        enc = lambda s: base64.b64encode(s.encode("utf-8")).decode("ascii")
        with self.lock:
            try:
                self.ensure()
                self.proc.stdin.write((enc(img) + "|" + enc(out) + "\n").encode("ascii"))
                self.proc.stdin.flush()
                self._wait("DONE", timeout)
            except Exception:
                self.stop()
                raise


_ocr_worker = [None]
_ocr_worker_lock = threading.Lock()


def get_ocr_worker():
    with _ocr_worker_lock:
        if _ocr_worker[0] is None:
            _ocr_worker[0] = OcrWorker()
        return _ocr_worker[0]


def reset_ocr_worker():
    w = _ocr_worker[0]
    if w is not None:
        w.stop()


def warm_up_ocr():
    """Запускается в фоне при старте: к первому выделению OCR уже готов."""
    try:
        get_ocr_worker().ensure()
    except Exception:
        log("Прогрев OCR не удался (будет обычный запуск):\n" + traceback.format_exc())


def ocr_windows(ba, w, h, preferred="auto"):
    orig = (ba, w, h)
    k = 2 if (h <= 400 and w <= 1500) else 1
    ba, w, h = upscale(ba, w, h, k)
    tmp = tempfile.mkdtemp(prefix="scrtr_")
    img, out = os.path.join(tmp, "in.bmp"), os.path.join(tmp, "out.txt")
    results = None
    try:
        write_bmp(img, ba, w, h)
        get_ocr_worker().recognize(img, out)
        with open(out, encoding="utf-8") as f:
            results = parse_ocr(f.read())
    except Exception:
        log("Быстрый OCR не сработал, пробую обычный запуск:\n" + traceback.format_exc())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not results or "error" in results or list(results) == ["none"]:
        return _ocr_windows_oneshot(*orig, preferred)   # он же выдаст понятную ошибку, если OCR в Windows нет
    try:
        return pick_best(results, preferred)
    except OcrMissing:
        # Пользователь мог установить язык Windows уже после запуска фонового OCR.
        reset_ocr_worker()
        return _ocr_windows_oneshot(*orig, preferred)


def _ocr_windows_oneshot(ba, w, h, preferred="auto"):
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
        return pick_best(results, preferred)
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
_tr_cache = OrderedDict()
_tr_cache_lock = threading.Lock()          # уже переведённое: повторный запрос не уходит в сеть
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


def _tr_google_api(text, target, src, sl):
    out, detected = [], None
    for ch in split_chunks(text, 1000):
        data = json.loads(_http_get("https://translate.googleapis.com/translate_a/single?client=gtx"
                                    f"&sl={sl}&tl={target}&dt=t&q={urllib.parse.quote(ch)}"))
        out.append("".join(part[0] for part in data[0] if part and part[0]))
        if detected is None and len(data) > 2 and isinstance(data[2], str):
            detected = data[2]
    return " ".join(out), detected


def _tr_google_mobile(text, target, src, sl):
    """Мобильная страница Google Translate: другой адрес, часто работает, когда первый ограничил запросы."""
    out = []
    for ch in split_chunks(text, 800):
        page = _http_get(f"https://translate.google.com/m?sl={sl}&tl={target}&q={urllib.parse.quote(ch)}")
        m = re.search(r'class="result-container">(.*?)</div>', page, re.S)
        if not m:
            raise RuntimeError("не удалось разобрать ответ")
        out.append(html.unescape(m.group(1)).strip())
    return " ".join(out)


def _tr_mymemory(text, target, src, sl):
    out = []
    if src == "auto":
        raise RuntimeError("MyMemory требует известный язык оригинала")
    for ch in split_utf8_chunks(text, 500):             # лимит в байтах, а не символах
        data = json.loads(_http_get("https://api.mymemory.translated.net/get?q="
                                    f"{urllib.parse.quote(ch)}&langpair={src}|{target}"))
        res = (data.get("responseData") or {}).get("translatedText")
        if not res or data.get("responseStatus") not in (200, "200"):
            raise RuntimeError(str(data.get("responseDetails") or "пустой ответ"))
        out.append(html.unescape(res))
    return " ".join(out)


def split_utf8_chunks(text, limit):
    parts, current, size = [], [], 0
    for char in text:
        n = len(char.encode("utf-8"))
        if current and size + n > limit:
            chunk = "".join(current)
            cut = chunk.rfind(" ")
            if cut > len(chunk) // 2:
                parts.append(chunk[:cut])
                current = list(chunk[cut + 1:])
                size = len("".join(current).encode("utf-8"))
            else:
                parts.append(chunk)
                current, size = [], 0
            if size + n > limit:
                parts.append("".join(current))
                current, size = [], 0
        current.append(char)
        size += n
    if current:
        parts.append("".join(current))
    return parts


PROVIDERS = (_tr_google_api, _tr_google_mobile, _tr_mymemory)


def translate(text, src_mode="auto", dst_mode="auto", cancelled=lambda: False):
    """Перевод с автоматическим определением источника на стороне сервиса."""
    text = text.strip()
    if not text:
        raise ValueError("Нет текста для перевода")
    cyr = len(re.findall(r"[А-Яа-яЁё]", text))
    lat = len(re.findall(r"[A-Za-z]", text))
    letters = sum(ch.isalpha() for ch in text)
    explicit = src_mode in LANGS
    # Предположение только для направления по умолчанию и запасного сервиса.
    russian = bool(cyr and cyr >= lat and cyr * 2 >= letters
                   and not re.search(r"[іїєґўІЇЄҐЎ]", text))
    guess = "ru" if russian else ("en" if lat and lat == letters and not cyr else "auto")
    src = src_mode if explicit else guess
    target = dst_mode if dst_mode in LANGS else ("en" if (src == "ru") else "ru")
    sl = src_mode if explicit else "auto"
    if explicit and src == target:
        return text, target, src
    key = (dst_mode, target, sl, text)
    with _tr_cache_lock:
        if key in _tr_cache:
            _tr_cache.move_to_end(key)
            return _tr_cache[key]
    errors, limited = [], False
    for fn in PROVIDERS:
        if cancelled():
            raise RuntimeError("Перевод отменён")
        # Не подставляем английский для неизвестного алфавита, а также не выдаём оригинал за перевод.
        if fn is _tr_mymemory and (src == "auto" or src == target):
            continue
        for attempt in range(2):
            if cancelled():
                raise RuntimeError("Перевод отменён")
            try:
                result = fn(text, target, src, sl)
                res, detected = result if isinstance(result, tuple) else (result, None)
                res = res.strip()
                if res:
                    actual = src_mode if explicit else (detected or (src if fn is _tr_mymemory else "auto"))
                    if actual not in LANGS:
                        actual = next((code for code in LANGS if ocr_tag_matches(actual, code)), "auto")
                    output_target = target
                    if not explicit and dst_mode == "auto" and detected:
                        expected = "en" if detected.lower() == "ru" else "ru"
                        if expected != target:
                            res, output_target, _ = translate(text, detected if detected in LANGS else "auto", expected, cancelled)
                    value = (res, output_target, actual)
                    if not cancelled():
                        with _tr_cache_lock:
                            _tr_cache[key] = value
                            _tr_cache.move_to_end(key)
                            while len(_tr_cache) > 200:
                                _tr_cache.popitem(last=False)
                    return value
                errors.append(f"{fn.__name__}: пустой ответ")
                break
            except urllib.error.HTTPError as e:
                limited = limited or e.code == 429
                errors.append(f"{fn.__name__}: HTTP {e.code}")
                if e.code in _RETRY_CODES and attempt == 0 and not cancelled():
                    time.sleep(0.5)
                    continue
                break
            except Exception as e:
                # Не записываем URL запроса с распознанным текстом в лог.
                errors.append(f"{fn.__name__}: {type(e).__name__}")
                break
    log("Перевод не удался: " + "; ".join(errors))
    if limited:
        raise RuntimeError("Сервисы перевода временно ограничили запросы. Повтори позже.")
    raise RuntimeError("Не удалось перевести: проверь интернет или повтори попытку.")

# ---- интерфейс -------------------------------------------------------------
def round_rect(c, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


def hwnd_of(win):
    wid = win.winfo_id()
    return user32.GetParent(wid) or wid


def monitor_workarea(x, y):
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    monitor = user32.MonitorFromPoint(wt.POINT(x, y), 2)
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        r = info.rcWork
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    return virtual_screen()


def place_window(win, w, h, x, y):
    win.geometry(f"{w}x{h}+0+0")
    win.update_idletasks()
    user32.SetWindowPos(hwnd_of(win), None, x, y, w, h, 0x0014)  # NOZORDER | NOACTIVATE


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
        for win in (self.bright, self.dim, self.ui):
            place_window(win, self.vw, self.vh, self.vx, self.vy)
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

        for w in (bc, self.dimc, self.cv):
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
        self.lab = self.cv.create_text(0, 0, text="", fill="#0B1525", font=(FONT, 9, "bold"), anchor="nw")
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
        if not user32.SetWindowRgn(self.hwnd, outer, True):
            gdi32.DeleteObject(outer)
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


class LanguagePicker:
    def __init__(self, app, parent, current, auto_title, on_choose, on_close=lambda: None):
        self.on_choose, self.on_close = on_choose, on_close
        self.closed = False
        S = app.S
        w = self.win = tk.Toplevel(parent)
        w.title("Выбрать язык")
        w.configure(bg=CARD)
        w.attributes("-topmost", True)
        if not parent.overrideredirect():
            w.transient(parent)
        w.protocol("WM_DELETE_WINDOW", self.close)
        w.bind("<Escape>", lambda e: self.close())
        tk.Label(w, text="Выбрать язык", font=(FONT, 15, "bold"), bg=CARD, fg=TEXT).pack(anchor="w", padx=S(14), pady=(S(12), S(4)))
        tk.Label(w, text=f"{len(LANGS)} языков · поиск по названию или коду", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=S(14))
        self.query = tk.StringVar(master=w)
        entry = tk.Entry(w, textvariable=self.query, bg="#202A39", fg=TEXT, insertbackground=TEXT,
                         relief="flat", font=(FONT, 11), highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
        entry.pack(fill="x", padx=S(14), pady=S(10), ipady=S(5))
        panel = tk.Frame(w, bg=CARD)
        panel.pack(fill="both", expand=True, padx=S(14))
        self.lb = tk.Listbox(panel, bg="#192334", fg=TEXT, selectbackground="#34465F", selectforeground=TEXT,
                             font=(FONT, 11), bd=0, highlightthickness=0, activestyle="none", exportselection=False)
        bar = ttk.Scrollbar(panel, command=self.lb.yview)
        bar.pack(side="right", fill="y")
        self.lb.config(yscrollcommand=bar.set)
        self.lb.pack(side="left", fill="both", expand=True)
        self.auto_title, self.current = auto_title, current
        self.codes = []
        self.query.trace_add("write", lambda *a: self.refresh())
        self.refresh()
        def choose(e=None):
            selected = self.lb.curselection()
            if selected and self.codes:
                code = self.codes[selected[0]]
                self.close()
                on_choose(code)
        def down(e):
            if self.codes:
                self.lb.focus_set()
                self.lb.activate(self.lb.curselection()[0] if self.lb.curselection() else 0)
            return "break"
        self.lb.bind("<Double-Button-1>", choose)
        w.bind("<Return>", choose)
        entry.bind("<Down>", down)
        tk.Button(w, text="Выбрать", command=choose, bg=ACCENT, fg="#101827", activebackground="#A5D4FF", relief="flat", font=(FONT, 10, "bold")).pack(fill="x", padx=S(14), pady=S(12), ipady=S(4))
        w.update_idletasks()
        vx, vy, sw, sh = monitor_workarea(parent.winfo_rootx(), parent.winfo_rooty())
        width, height = min(S(360), sw - S(30)), min(S(470), sh - S(60))
        x = max(vx, min(parent.winfo_rootx(), vx + sw - width))
        y = max(vy, min(parent.winfo_rooty(), vy + sh - height - S(30)))
        place_window(w, width, height, x, y)
        w.minsize(min(width, S(280)), min(height, S(260)))
        w.grab_set()
        entry.focus_force()

    def refresh(self):
        q = self.query.get().strip()
        self.codes = (["auto"] if not q or q.casefold() in self.auto_title.casefold() or q.casefold() in "auto" else []) + matching_languages(q)
        self.lb.delete(0, "end")
        for c in self.codes:
            self.lb.insert("end", self.auto_title if c == "auto" else f"{LANGS[c]}  ·  {c.upper()}")
        if self.codes:
            ix = self.codes.index(self.current) if self.current in self.codes and not q else 0
            self.lb.selection_set(ix)
            self.lb.activate(ix)
            self.lb.see(ix)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.win.destroy()
        self.on_close()


class SettingsWindow:
    """Окно «Настройки»: сочетание клавиш, языки перевода, внешний вид, поведение."""

    CARD2 = "#192334"      # фон карточек-секций
    FIELD = "#243247"      # фон полей и кнопок
    HOVER = "#34465F"

    def __init__(self, app, on_close):
        self.app, self.on_close = app, on_close
        self.new_hotkey = (cfg["mods"], cfg["vk"])
        self.capturing = False
        S = self.S = app.S
        w = self.win = tk.Toplevel(app.root)
        w.title("Настройки — Экранный переводчик")
        w.configure(bg=CARD)
        w.resizable(True, True)             # окно можно растягивать
        w.attributes("-topmost", True)
        w.protocol("WM_DELETE_WINDOW", self.close)
        w.bind("<Escape>", lambda e: None if self.capturing else self.close())

        # ---- шапка
        top = tk.Frame(w, bg=CARD)
        top.pack(fill="x", padx=S(24), pady=(S(18), 0))
        tk.Label(top, text="SCREEN / TRANSLATE", bg=CARD, fg=ACCENT, font=(FONT, 9, "bold")).pack(anchor="w")
        tk.Label(top, text="Настройки", bg=CARD, fg=TEXT, font=(FONT, 17, "bold")).pack(side="left")
        tk.Label(top, text=f"v{VERSION}", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=(S(10), 0), pady=(S(8), 0))

        # ---- подвал (упакован раньше тела, чтобы не пропадал при сжатии окна)
        foot = tk.Frame(w, bg=CARD)
        foot.pack(side="bottom", fill="x", padx=S(24), pady=(S(6), S(18)))
        self.status = tk.Label(w, text="", bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w",
                               wraplength=S(500), justify="left")
        self.status.pack(side="bottom", fill="x", padx=S(24))
        self.button(foot, "Закрыть", self.close).pack(side="right")
        self.button(foot, "Сохранить", self.save, primary=True).pack(side="right", padx=(0, S(8)))
        self.button(foot, "Сбросить", self.reset_defaults).pack(side="left")

        outer = tk.Frame(w, bg=CARD)
        outer.pack(fill="both", expand=True, padx=S(24), pady=(S(6), S(4)))
        scroll = tk.Canvas(outer, bg=CARD, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(outer, orient="vertical", command=scroll.yview)
        bar.pack(side="right", fill="y")
        scroll.pack(side="left", fill="both", expand=True)
        scroll.configure(yscrollcommand=bar.set)
        body = tk.Frame(scroll, bg=CARD)
        item = scroll.create_window(0, 0, anchor="nw", window=body)
        body.bind("<Configure>", lambda e: scroll.configure(scrollregion=scroll.bbox("all")))
        scroll.bind("<Configure>", lambda e: scroll.itemconfigure(item, width=e.width))
        def wheel(e):
            if isinstance(e.widget, (ttk.Combobox, tk.Scale)):
                return
            scroll.yview_scroll(-1 if e.delta > 0 else 1, "units")
            return "break"
        w.bind("<MouseWheel>", wheel)

        # ---- сочетание клавиш
        box = self.card(body, "СОЧЕТАНИЕ КЛАВИШ")
        row = tk.Frame(box, bg=self.CARD2)
        row.pack(fill="x")
        self.key_lbl = tk.Label(row, text=current_label(), bg=self.FIELD, fg=TEXT,
                                font=(FONT, 12, "bold"), padx=S(14), pady=S(6))
        self.key_lbl.pack(side="left")
        self.button(row, "Изменить", self.start_capture, primary=True).pack(side="left", padx=(S(10), 0))
        tk.Label(row, text="вызывает выделение области", bg=self.CARD2, fg=MUTED,
                 font=(FONT, 9)).pack(side="left", padx=(S(12), 0))

        # ---- языки: «откуда» ⇄ «куда»
        box = self.card(body, "ЯЗЫКИ ПЕРЕВОДА")
        self.style_combo(w)
        grid = tk.Frame(box, bg=self.CARD2)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1, uniform="lang")
        grid.columnconfigure(2, weight=1, uniform="lang")
        tk.Label(grid, text="Откуда", bg=self.CARD2, fg=MUTED, font=(FONT, 9),
                 anchor="w").grid(row=0, column=0, sticky="w")
        tk.Label(grid, text="Куда", bg=self.CARD2, fg=MUTED, font=(FONT, 9),
                 anchor="w").grid(row=0, column=2, sticky="w")
        self.var_src = tk.StringVar(master=w, value=lang_title(cfg["src"], AUTO_SRC))
        self.var_dst = tk.StringVar(master=w, value=lang_title(cfg["target"], AUTO_DST))
        self.cb_src = tk.Button(grid, textvariable=self.var_src, command=lambda: self.choose_language(True),
                                bg=self.FIELD, fg=TEXT, activebackground=self.HOVER, activeforeground=TEXT,
                                font=(FONT, 10), relief="flat", anchor="w", padx=S(8), pady=S(5), cursor="hand2")
        self.cb_dst = tk.Button(grid, textvariable=self.var_dst, command=lambda: self.choose_language(False),
                                bg=self.FIELD, fg=TEXT, activebackground=self.HOVER, activeforeground=TEXT,
                                font=(FONT, 10), relief="flat", anchor="w", padx=S(8), pady=S(5), cursor="hand2")
        self.cb_src.grid(row=1, column=0, sticky="ew", pady=(S(3), 0))
        self.cb_dst.grid(row=1, column=2, sticky="ew", pady=(S(3), 0))
        swap = self.button(grid, "⇄", self.swap_langs, primary=True, pad=(S(12), S(3)))
        swap.config(font=(FONT, 12, "bold"))
        swap.grid(row=1, column=1, padx=S(10), pady=(S(3), 0))
        self.route = tk.Label(box, text="", bg=self.CARD2, fg=ACCENT, font=(FONT, 10, "bold"), anchor="w", wraplength=S(420), justify="left")
        self.route.pack(fill="x", pady=(S(10), 0))
        tk.Label(box, text="Нажми на язык, чтобы открыть список с поиском.",
                 bg=self.CARD2, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x")
        self.update_route()

        # ---- внешний вид
        box = self.card(body, "ВНЕШНИЙ ВИД")
        self.var_dim = tk.IntVar(master=w, value=cfg["dim"])
        self.slider(box, "Затемнение экрана при выделении", self.var_dim, 20, 85, lambda v: f"{v}%")
        self.var_font = tk.IntVar(master=w, value=cfg["font"])
        self.slider(box, "Размер текста перевода", self.var_font, 9, 20, lambda v: f"{v} пт")
        self.var_popw = tk.IntVar(master=w, value=cfg["popw"])
        self.slider(box, "Максимальная ширина перевода", self.var_popw, 240, 900, lambda v: f"{v} px")

        # ---- поведение
        box = self.card(body, "ПОВЕДЕНИЕ")
        self.var_copy = tk.BooleanVar(master=w, value=cfg["autocopy"])
        self.check(box, "Сразу копировать перевод в буфер обмена", self.var_copy)
        self.var_orig = tk.BooleanVar(master=w, value=cfg["showorig"])
        self.check(box, "Сразу раскрывать оригинал (иначе по кнопке)", self.var_orig)
        self.var_anim = tk.BooleanVar(master=w, value=cfg["animations"])
        self.check(box, "Плавное появление окон", self.var_anim)
        self.var_pin = tk.BooleanVar(master=w, value=cfg["pin"])
        self.check(box, "Закреплять окно перевода по умолчанию", self.var_pin)
        self.var_auto = tk.BooleanVar(master=w, value=is_autostart())
        self.check(box, "Запускать вместе с Windows", self.var_auto)
        self.var_close = tk.IntVar(master=w, value=cfg["autoclose"])
        self.slider(box, "Закрывать окно перевода автоматически", self.var_close, 0, 60,
                    lambda v: "выкл" if v == 0 else f"{v} с")

        tk.Label(body, text="OCR работает локально. Распознанный текст отправляется сервису перевода. История хранится только до выхода.",
                 bg=CARD, fg=MUTED, font=(FONT, 9), wraplength=S(470), justify="left",
                 anchor="w").pack(fill="x", pady=S(12))
        w.update_idletasks()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        W, H = min(S(550), sw - S(32)), min(S(640), sh - S(80))
        w.minsize(min(W, S(450)), min(H, S(350)))
        w.geometry(f"{W}x{H}+{(sw - W) // 2}+{max(10, (sh - H) // 3)}")
        try:   # тёмная заголовочная полоса на Windows 10/11
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd_of(w), 20, ctypes.byref(ctypes.c_int(1)), 4)
        except Exception:
            pass
        w.focus_force()

    # ---- строительные блоки ---------------------------------------------------
    def card(self, parent, title):
        S = self.S
        tk.Label(parent, text=title, bg=CARD, fg=ACCENT, font=(FONT, 9, "bold"),
                 anchor="w").pack(fill="x", pady=(S(12), S(4)))
        box = tk.Frame(parent, bg=self.CARD2, highlightbackground=BORDER, highlightthickness=1)
        box.pack(fill="x")
        inner = tk.Frame(box, bg=self.CARD2)
        inner.pack(fill="x", padx=S(14), pady=S(10))
        return inner

    def button(self, parent, text, cmd, primary=False, pad=None):
        S = self.S
        bg, hover = (ACCENT, "#93C5FD") if primary else (self.FIELD, self.HOVER)
        b = tk.Label(parent, text=text, bg=bg, fg="#0B1525" if primary else TEXT,
                     font=(FONT, 9, "bold"), padx=(pad or (S(14), S(6)))[0],
                     pady=(pad or (S(14), S(6)))[1], cursor="hand2")
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.config(bg=hover))
        b.bind("<Leave>", lambda e: b.config(bg=bg))
        return b

    def check(self, parent, text, var):
        tk.Checkbutton(parent, text=text, variable=var, bg=self.CARD2, fg=TEXT, selectcolor=self.FIELD,
                       activebackground=self.CARD2, activeforeground=TEXT, font=(FONT, 10),
                       anchor="w", highlightthickness=0, bd=0).pack(fill="x", pady=1)

    def slider(self, parent, label, var, lo, hi, fmt):
        S = self.S
        head = tk.Frame(parent, bg=self.CARD2)
        head.pack(fill="x", pady=(S(4), 0))
        tk.Label(head, text=label, bg=self.CARD2, fg=TEXT, font=(FONT, 10), anchor="w").pack(side="left")
        val = tk.Label(head, text=fmt(var.get()), bg=self.CARD2, fg=ACCENT, font=(FONT, 10, "bold"), anchor="e")
        val.pack(side="right")
        tk.Scale(parent, variable=var, from_=lo, to=hi, orient="horizontal", showvalue=False,
                 bg=self.CARD2, fg=TEXT, troughcolor=self.FIELD, highlightthickness=0, bd=0,
                 activebackground=ACCENT, sliderrelief="flat", width=S(12)).pack(fill="x")   # тянется вместе с окном
        var.trace_add("write", lambda *a: val.config(text=fmt(int(var.get()))))

    def style_combo(self, w):
        try:
            st = ttk.Style(w)
            st.theme_use("clam")
            st.configure("Dark.TCombobox", fieldbackground=self.FIELD, background=self.FIELD,
                         foreground=TEXT, arrowcolor=TEXT, bordercolor=BORDER,
                         lightcolor=self.FIELD, darkcolor=self.FIELD, padding=self.S(5))
            st.map("Dark.TCombobox", fieldbackground=[("readonly", self.FIELD)],
                   foreground=[("readonly", TEXT)], selectbackground=[("readonly", self.FIELD)],
                   selectforeground=[("readonly", TEXT)], background=[("active", self.HOVER)])
            for opt, val in (("background", self.FIELD), ("foreground", TEXT),
                             ("selectBackground", ACCENT), ("selectForeground", "#0B1525")):
                w.option_add(f"*TCombobox*Listbox.{opt}", val)
            w.option_add("*TCombobox*Listbox.font", (FONT, 10))
        except tk.TclError:
            pass

    # ---- языки -----------------------------------------------------------------
    def choose_language(self, source):
        var, auto = (self.var_src, AUTO_SRC) if source else (self.var_dst, AUTO_DST)
        def picked(code):
            var.set(lang_title(code, auto))
            self.update_route()
        LanguagePicker(self.app, self.win, lang_code(var.get()), auto, picked)

    def codes(self):
        return lang_code(self.var_src.get()), lang_code(self.var_dst.get())

    def update_route(self):
        self.route.config(text="Сейчас: " + route_text(*self.codes()))

    def swap_langs(self):
        src, dst = self.codes()
        if src == "auto" and dst == "auto":
            self.say("Выбери хотя бы один язык — тогда можно поменять местами.")
            return
        self.var_src.set(lang_title(dst, AUTO_SRC))
        self.var_dst.set(lang_title(src, AUTO_DST))
        self.update_route()
        self.say("")

    def reset_defaults(self):
        self.var_src.set(AUTO_SRC)
        self.var_dst.set(AUTO_DST)
        self.var_dim.set(DEFAULTS["dim"])
        self.var_font.set(DEFAULTS["font"])
        self.var_popw.set(DEFAULTS["popw"])
        self.var_close.set(DEFAULTS["autoclose"])
        self.var_copy.set(DEFAULTS["autocopy"])
        self.var_orig.set(DEFAULTS["showorig"])
        self.var_anim.set(DEFAULTS["animations"])
        self.var_pin.set(DEFAULTS["pin"])
        self.new_hotkey = (DEFAULTS["mods"], DEFAULTS["vk"])
        self.key_lbl.config(text=hotkey_label(*self.new_hotkey), fg=TEXT)
        self.update_route()
        self.say("Значения по умолчанию подставлены — нажми «Сохранить».")

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
            self.status.config(text=text, fg="#FDA4AF" if error else MUTED)
        except tk.TclError:
            pass

    def save(self):
        cfg["src"], cfg["target"] = self.codes()
        cfg["dim"] = int(self.var_dim.get())
        cfg["font"] = int(self.var_font.get())
        cfg["popw"] = int(self.var_popw.get())
        cfg["autoclose"] = int(self.var_close.get())
        cfg["autocopy"] = bool(self.var_copy.get())
        cfg["showorig"] = bool(self.var_orig.get())
        cfg["animations"] = bool(self.var_anim.get())
        cfg["pin"] = bool(self.var_pin.get())
        problems = []
        try:
            if self.var_auto.get() or is_autostart():
                set_autostart(bool(self.var_auto.get()))
        except OSError:
            problems.append("автозапуск не удалось изменить")
        if not save_settings():
            problems.append("файл настроек не удалось записать")
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
        self.last_result = None
        self.history = deque(maxlen=50)
        self.history_win = None
        self.language_picker = None
        self.installing_ocr = False
        self.job = 0
        self.select_pending = False
        self.shot = None
        self.pinned = False
        self.last_left = False
        self.selector = None
        self.anchor = (100, 100, 0, 0)
        self.opened_at = 0.0
        threading.Thread(target=hotkey_thread, daemon=True).start()
        threading.Thread(target=warm_up_ocr, daemon=True).start()
        self.root.after(50, self.poll)
        print(f"Готово. Нажми {current_label()} и обведи текст на экране.")

    def close(self, invalidate=True):
        if self.language_picker is not None:
            self.language_picker.close()
        if invalidate:
            self.job += 1
        if self.popup is not None:
            try:
                self.popup.destroy()
            except tk.TclError:
                pass
            self.popup = None

    # ---- окно результата ----------------------------------------------------
    def show(self, label, body, original=None, loading=False, error=False, action=None, expanded=None, pinned=None):
        animate = self.popup is None and cfg["animations"]
        self.close(invalidate=False)
        self.pinned = (cfg["pin"] if pinned is None else pinned) and not loading
        S = self.S
        ax, ay, aw, ah = self.anchor
        vx, vy, vw, vh = monitor_workarea(ax + aw // 2, ay + ah // 2)
        p = tk.Toplevel(self.root)
        self.popup = p
        p.overrideredirect(True)
        p.attributes("-topmost", True)
        p.configure(bg=BG_KEY)
        p.attributes("-transparentcolor", BG_KEY)
        c = tk.Canvas(p, bg=BG_KEY, highlightthickness=0, bd=0)
        c.pack()
        f = tk.Frame(c, bg=CARD)
        from tkinter import font as tkfont
        font_obj = tkfont.Font(root=p, font=(FONT, cfg["font"]))
        small_font = tkfont.Font(root=p, font=(FONT, 9))
        limit = min(S(cfg["popw"]), max(S(130), vw - S(30)))
        if action:
            limit = min(max(limit, S(300)), vw - S(30))
        if loading:
            width = min(limit, max(S(148), small_font.measure(body) + S(72)))
        else:
            measured = max((font_obj.measure(t) for t in body.splitlines()), default=0)
            width = min(limit, max(S(238), measured + S(28)))
        inner = max(S(100), width - S(20))
        color = "#FDA4AF" if error else TEXT
        drag = {}
        def d_start(e):
            drag["o"] = (e.x_root - p.winfo_rootx(), e.y_root - p.winfo_rooty())
        def d_move(e):
            o = drag.get("o")
            if o:
                user32.SetWindowPos(hwnd_of(p), None, e.x_root - o[0], e.y_root - o[1], 0, 0, 0x0015)
        def button(parent, title, callback, primary=False):
            bg = "#243247" if primary else CARD
            b = tk.Label(parent, text=title, bg=bg, fg=ACCENT if primary else MUTED,
                         font=(FONT, 9), padx=S(5), pady=S(3), cursor="hand2")
            b.bind("<Button-1>", lambda e: callback())
            b.bind("<Enter>", lambda e: b.config(bg="#303E52", fg=TEXT))
            b.bind("<Leave>", lambda e: b.config(bg=bg, fg=ACCENT if primary else MUTED))
            return b
        if loading:
            row = tk.Frame(f, bg=CARD, width=inner, height=S(38))
            row.pack(fill="x")
            row.pack_propagate(False)
            ring = tk.Canvas(row, width=S(25), height=S(25), bg=CARD, highlightthickness=0)
            ring.pack(side="left", padx=(0, S(7)))
            r = S(25)
            ring.create_oval(S(3), S(3), r-S(3), r-S(3), outline=BORDER, width=S(2))
            arc = ring.create_arc(S(3), S(3), r-S(3), r-S(3), start=90, extent=105,
                                  style="arc", outline=ACCENT, width=S(3))
            button(row, "×", self.close).pack(side="right")
            # Long status details wrap in a small separate popup rather than stretching the spinner.
            status_text = body
            if len(body) > 48:
                status_text = label + "…"
            status = tk.Label(row, text=status_text, bg=CARD, fg=TEXT, font=(FONT, 9),
                              anchor="w", justify="left", wraplength=max(S(62), inner-S(63)))
            status.pack(side="left", fill="both", expand=True)
            if len(body) > 48 and label == "Установка":
                tk.Label(f, text=body, bg=CARD, fg=MUTED, font=(FONT, 9), wraplength=inner, justify="left").pack(fill="x")
            def spin(angle=90):
                if self.popup is p and ring.winfo_exists():
                    ring.itemconfigure(arc, start=angle)
                    self.root.after(25, lambda: spin((angle-16) % 360))
            spin()
        else:
            head = tk.Frame(f, bg=CARD, width=inner)
            head.pack(fill="x")
            button(head, "×", self.close).pack(side="right")
            pin = button(head, "●" if self.pinned else "○", lambda: toggle_pin())
            pin.pack(side="right")
            def toggle_pin():
                self.pinned = not self.pinned
                pin.config(text="●" if self.pinned else "○", fg=ACCENT if self.pinned else MUTED)
            chip = tk.Label(head, text=label, bg=CARD, fg="#FDA4AF" if error else ACCENT,
                            font=(FONT, 9, "bold"), anchor="w", justify="left", wraplength=max(S(95), inner-S(54)))
            chip.pack(side="left", fill="x", expand=True)
            for wdg in (head, chip):
                wdg.bind("<ButtonPress-1>", d_start)
                wdg.bind("<B1-Motion>", d_move)
                wdg.config(cursor="fleur")
            def text_panel(text, fg, font, max_lines):
                # Label gives exact natural height for short text. Long blocks stay scrollable.
                panel = tk.Frame(f, bg=CARD, width=inner)
                panel.pack(fill="x", pady=(S(4), 0))
                preview = tk.Label(panel, text=text, bg=CARD, fg=fg, font=font,
                                   wraplength=inner-S(3), justify="left", anchor="w", padx=0, pady=0)
                preview.update_idletasks()
                metrics = tkfont.Font(root=p, font=font)
                if preview.winfo_reqheight() <= metrics.metrics("linespace") * max_lines + S(5):
                    preview.pack(fill="x")
                else:
                    preview.destroy()
                    sb = tk.Scrollbar(panel, orient="vertical", width=S(9))
                    sb.pack(side="right", fill="y")
                    tx = tk.Text(panel, bg=CARD, fg=fg, font=font, wrap="word", width=1,
                                 height=max_lines, bd=0, highlightthickness=0, yscrollcommand=sb.set,
                                 selectbackground=ACCENT, selectforeground="#101827", padx=0, pady=0)
                    tx.insert("1.0", text)
                    tx.config(state="disabled")
                    sb.config(command=tx.yview)
                    tx.pack(side="left", fill="x", expand=True)
            f.configure(width=inner)
            max_lines = max(2, min(7, int((vh-S(150))/max(1, font_obj.metrics("linespace")))))
            text_panel(body, color, (FONT, cfg["font"]), max_lines)
            expanded = cfg["showorig"] if expanded is None else expanded
            if original and expanded:
                tk.Frame(f, bg=BORDER, height=1).pack(fill="x", pady=(S(7), S(2)))
                tk.Label(f, text="ОРИГИНАЛ", bg=CARD, fg=MUTED, font=(FONT, 8, "bold"), anchor="w").pack(fill="x")
                text_panel(original, MUTED, (FONT, 9), max(2, min(4, max_lines)))
            if not error:
                tools = tk.Frame(f, bg=CARD)
                tools.pack(fill="x", pady=(S(5), 0))
                def copy_result():
                    self.copy_text(body)
                    copy_btn.config(text="Готово ✓")
                copy_btn = button(tools, "Копировать", copy_result, True)
                copy_btn.pack(side="right")
                if original:
                    button(tools, "Язык", lambda: self.pick_result_language(original, label)).pack(side="left")
                    button(tools, "Скрыть" if expanded else "Оригинал",
                           lambda: self.show(label, body, original, expanded=not expanded, pinned=self.pinned)).pack(side="left")
                if original and expanded:
                    button(f, "Копировать оригинал", lambda: self.copy_text(original)).pack(anchor="w")
            if action:
                acts = action if isinstance(action, list) else [action]
                for action_text, action_fn in acts:
                    button(f, action_text, action_fn, True).pack(anchor="w", pady=(S(4), 0))
        f.update_idletasks()
        w, h = max(width, f.winfo_reqwidth()+S(20)), f.winfo_reqheight()+S(16)
        c.config(width=w, height=h)
        round_rect(c, 1, 1, w-1, h-1, S(10), fill=CARD, outline=BORDER)
        c.create_window(S(10), S(8), window=f, anchor="nw", width=w-S(20))
        x, y = ax, ay+ah+S(6)
        if y+h > vy+vh-S(6):
            y = ay-h-S(6)
        x = max(vx+3, min(x, vx+vw-w-3))
        y = max(vy+3, min(y, vy+vh-h-3))
        place_window(p, w, h, x, y)
        self.opened_at = time.monotonic()
        if cfg["autoclose"] and not loading and not error and not action:
            self.root.after(cfg["autoclose"]*1000, lambda: self.close() if self.popup is p and not self.pinned and self.language_picker is None else None)
        if animate:
            def fade(a=0.25):
                if self.popup is p:
                    p.attributes("-alpha", min(a, 1.0))
                    if a < 1.0:
                        self.root.after(15, lambda: fade(a+0.25))
            fade()

    # ---- логика -------------------------------------------------------------
    def open_settings(self):
        if self.settings_win is not None:
            self.settings_win.lift()
            return
        self.settings_win = SettingsWindow(self, lambda: setattr(self, "settings_win", None))

    def on_hotkey(self):
        if self.selector is not None or self.capturing or self.select_pending or self.language_picker is not None:
            return
        had_popup = self.popup is not None
        self.close()
        self.select_pending = True
        self.root.after(120 if had_popup else 30, self.begin_selection)

    def begin_selection(self):
        self.select_pending = False
        if self.selector is not None or self.capturing:
            return
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
        if self.installing_ocr:
            self.show("Установка", "Установка уже выполняется. Проверь запрос Windows.", error=True)
            return
        self.installing_ocr = True
        job, _ = self.new_job()
        self.show("Установка", "Устанавливаю компонент распознавания… Подтверди запрос Windows "
                  "и подожди 1–3 минуты.", loading=True)

        def work():
            tmp = tempfile.mkdtemp(prefix="scrtr_install_")
            out = os.path.join(tmp, "result.txt")
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
            try:
                result = ""
                if os.path.exists(out):
                    with open(out, encoding="utf-8-sig", errors="replace") as fh:
                        result = fh.read()
                    log("Результат установки OCR:\n" + result)
                kind, payload = install_outcome(result, windows_ocr_languages)
                self.send_job(job, kind, payload)
            except Exception:
                self.send_job(job, "error", "Не удалось проверить установку OCR. Открой настройки языка Windows.")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
                events.put(("install_finished", None))

        threading.Thread(target=work, daemon=True).start()

    def new_job(self):
        self.job += 1
        job = self.job
        return job, lambda: self.job != job

    def send_job(self, job, kind, payload):
        events.put(("job", (job, kind, payload)))

    def run_ocr(self, ba, w, h):
        self.last_crop = (ba, w, h)
        job, cancelled = self.new_job()
        src_mode, dst_mode = cfg["src"], cfg["target"]
        self.show("Распознавание", "Читаю текст…", loading=True)

        def work():
            try:
                text = ocr_image(ba, w, h, src_mode).strip()
                if cancelled():
                    return
                if not text:
                    self.send_job(job, "error", "Текст не найден. Выдели более чёткую область.")
                    return
                if len(text) > MAX_CHARS:
                    self.send_job(job, "error", f"Найдено больше {MAX_CHARS} символов. Выдели текст по частям, чтобы ничего не потерять.")
                    return
                self.send_job(job, "loading", "Перевожу текст…")
                self.translate_job(job, cancelled, text, src_mode, dst_mode)
            except OcrMissing as e:
                self.send_job(job, "ocr_missing", str(e))
            except Exception as e:
                log("Ошибка OCR: " + type(e).__name__)
                self.send_job(job, "error", str(e))

        threading.Thread(target=work, daemon=True).start()

    def translate_job(self, job, cancelled, text, src_mode, dst_mode):
        try:
            res, target, src = translate(text, src_mode, dst_mode, cancelled)
            if not cancelled():
                label = f"{LANGS.get(src, 'Авто')} → {LANGS.get(target, target)}"
                self.send_job(job, "result", (res, label, text))
        except Exception as e:
            if not cancelled():
                self.send_job(job, "translate_error", (str(e), text, dst_mode, src_mode))

    def copy_text(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def retry_text(self, text, target=None, source=None):
        job, cancelled = self.new_job()
        self.show("Перевод", "Перевожу…", loading=True)
        threading.Thread(target=self.translate_job,
                         args=(job, cancelled, text, cfg["src"] if source is None else source, cfg["target"] if target is None else target), daemon=True).start()

    def pick_result_language(self, original, label=""):
        if self.language_picker is not None or self.popup is None:
            return
        def clear():
            self.language_picker = None
            self.opened_at = time.monotonic()
        names = label.split(" → ", 1)
        source = lang_code(names[0])
        target = lang_code(names[1]) if len(names) == 2 else cfg["target"]
        self.language_picker = LanguagePicker(self, self.popup, target, AUTO_DST,
                                               lambda code: self.retry_text(original, code, source), clear)

    def translate_clipboard(self):
        if self.selector is not None or self.select_pending or self.language_picker is not None:
            return
        self.close()
        self.anchor = (self.root.winfo_pointerx(), self.root.winfo_pointery(), 0, 0)
        try:
            text = self.root.clipboard_get().strip()
        except tk.TclError:
            text = ""
        if not text:
            self.show("Буфер обмена", "В буфере нет текста.", error=True)
        elif len(text) > MAX_CHARS:
            self.show("Буфер обмена", f"Скопируй не больше {MAX_CHARS} символов за раз.", error=True)
        else:
            self.retry_text(text)

    def open_history(self):
        if self.history_win is not None and self.history_win.winfo_exists():
            self.history_win.destroy()
        win = self.history_win = tk.Toplevel(self.root)
        win.title("История переводов · текущий сеанс")
        win.configure(bg=CARD)
        S = self.S
        win.geometry(f"{S(650)}x{S(430)}")
        tk.Label(win, text="История переводов", bg=CARD, fg=TEXT, font=(FONT, 18, "bold")).pack(anchor="w", padx=S(20), pady=(S(16), S(2)))
        tk.Label(win, text="Последние 50 переводов · сохраняются только в памяти", bg=CARD, fg=MUTED, font=(FONT, 10)).pack(anchor="w", padx=S(20))
        panel = tk.Frame(win, bg=CARD)
        panel.pack(fill="both", expand=True, padx=S(20), pady=S(12))
        lb = tk.Listbox(panel, bg="#192334", fg=TEXT, selectbackground="#34465F", selectforeground=TEXT,
                        font=(FONT, 11), bd=0, highlightthickness=0, activestyle="none")
        sb = ttk.Scrollbar(panel, command=lb.yview)
        lb.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)
        records = list(self.history)
        for res, label, original in records:
            lb.insert("end", f"{label}    {' '.join(res.split())[:85]}")
        if not records:
            lb.insert("end", "Пока нет переводов")
        def chosen():
            sel = lb.curselection()
            if sel and records:
                return records[sel[0]]
        def show_chosen(e=None):
            item = chosen()
            if item:
                self.close()
                self.show(item[1], item[0], original=item[2])
        def copy_chosen():
            item = chosen()
            if item:
                self.root.clipboard_clear()
                self.root.clipboard_append(item[0])
        def clear_history():
            self.history.clear()
            with _tr_cache_lock:
                _tr_cache.clear()
            self.last_result = None
            records.clear()
            lb.delete(0, "end")
            lb.insert("end", "История очищена")
        actions = tk.Frame(win, bg=CARD)
        actions.pack(fill="x", padx=S(20), pady=(0, S(16)))
        for label, fn in (("Открыть", show_chosen), ("Копировать", copy_chosen), ("Очистить", clear_history)):
            tk.Button(actions, text=label, command=fn, bg="#243247", fg=TEXT, activebackground=ACCENT,
                      relief="flat", padx=S(12), pady=S(5)).pack(side="left", padx=(0, S(8)))
        lb.bind("<Double-Button-1>", show_chosen)
        win.bind("<Escape>", lambda e: win.destroy())
        win.bind("<Destroy>", lambda e: setattr(self, "history_win", None) if e.widget is win else None)

    def on_selected(self, rect):
        self.selector = None
        if rect is None:
            self.shot = None
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
                if kind == "job":
                    job, kind, payload = payload
                    if job != self.job:
                        continue
                if kind == "quit":
                    self.root.quit()
                    return
                elif kind == "install_finished":
                    self.installing_ocr = False
                elif kind == "history":
                    if self.selector is None and not self.select_pending:
                        self.open_history()
                elif kind == "clipboard":
                    self.translate_clipboard()
                elif kind == "hotkey":
                    self.on_hotkey()
                elif kind == "loading":
                    self.show("Перевод", payload, loading=True)
                elif kind == "last":
                    if self.selector is not None or self.select_pending:
                        continue
                    self.close()
                    if self.last_result:
                        self.show(self.last_result[1], self.last_result[0], original=self.last_result[2])
                    else:
                        self.show("История", "Пока ничего не переводилось.", error=True)
                elif kind == "result":
                    self.last_result = payload
                    if not self.history or self.history[0] != payload:
                        self.history.appendleft(payload)
                    self.show(payload[1], payload[0], original=payload[2])
                    if cfg["autocopy"]:
                        self.root.clipboard_clear()
                        self.root.clipboard_append(payload[0])
                elif kind == "settings":
                    if self.selector is None and not self.select_pending:
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
                elif kind == "translate_error":
                    msg, text, target, source = payload
                    self.show("Ошибка перевода", msg, original=text, error=True,
                              action=[("Повторить", lambda text=text, target=target, source=source: self.retry_text(text, target, source)),
                                      ("Копировать оригинал", lambda text=text: self.copy_text(text))])
                elif kind == "error":
                    self.show("Ошибка", payload, error=True)
                elif kind == "ocr_missing":
                    self.show("Ошибка", payload, error=True,
                              action=([("Установить OCR", self.install_ocr), ("Языки Windows", self.open_language_settings)]
                                      if cfg["src"] in ("auto", "ru", "en") else
                                      [("Языки Windows", self.open_language_settings), ("Настройки перевода", self.open_settings)]))
                elif kind == "notice":
                    self.show("Установка", payload, loading=True)
                elif kind == "retry":   # OCR поставлен и виден Windows — повторяем ту же область
                    if self.last_crop:
                        self.run_ocr(*self.last_crop)
                    else:
                        self.show("Установка", payload, loading=True)
        except queue.Empty:
            pass
        except Exception:
            log("Ошибка обработки событий:\n" + traceback.format_exc())

        left = is_down(VK_LBUTTON)
        clicked = left and not self.last_left
        self.last_left = left
        if self.selector is not None and is_down(VK_ESCAPE):
            self.selector.cancel()
        elif self.popup is not None and self.language_picker is None and time.monotonic() - self.opened_at > 0.3:
            if is_down(VK_ESCAPE):
                self.close()
            elif clicked and not self.pinned and not self.capturing:
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
        try:
            self.root.mainloop()
        finally:
            self.job += 1
            # Закрытие pipe при выходе процесса остановит дочерний OCR.
            self.root.destroy()


def self_test_bundle(expect_full):
    """Verify the frozen OCR payload, without opening the UI or altering settings."""
    if is_full_build() != expect_full:
        return 2
    if not expect_full:
        return 0
    exe, data = tesseract_paths()
    required = set("rus eng ara deu fra spa ita por tur ukr bel kaz aze hin chi_sim chi_tra jpn kor fas heb pol nld ces vie".split())
    try:
        result = subprocess.run([exe, "--list-langs", "--tessdata-dir", data],
                                capture_output=True, timeout=30, creationflags=0x08000000, check=True)
        if not required <= set(result.stdout.decode("utf-8", "replace").splitlines()):
            return 3
        return 0
    except Exception:
        return 4


if __name__ == "__main__":
    if "--self-test-full" in sys.argv:
        sys.exit(self_test_bundle(True))
    if "--self-test-lite" in sys.argv:
        sys.exit(self_test_bundle(False))
    if already_running():   # уже запущено (например, из автозагрузки) — второй копии не нужно
        sys.exit(0)
    load_settings()
    first_run_setup()
    App().run()
