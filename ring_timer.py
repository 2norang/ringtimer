"""
Ring Timer - 원형 그래프 타이머 (Windows 데스크톱 앱)

- 입력한 시간에서 시작해 100% → 0% 로 원이 줄어드는 방식
- 원 가운데에 남은 시간 표시
- 라이트 / 다크 모드, 원 색상 자유 선택
- 설정은 %APPDATA%\\RingTimer\\settings.json 에 자동 저장

필요 패키지: pillow  (pip install pillow)
실행:        python ring_timer.py
"""

import ctypes
import subprocess
import json
import math
import os
import re
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog
import colorsys

from PIL import Image, ImageDraw, ImageFont, ImageTk

APP_NAME = "RingTimer"
IS_MAC = sys.platform == "darwin"
SUPERSAMPLE = 3          # 원을 크게 그린 뒤 줄여서 테두리를 부드럽게
RING_THICKNESS = 0.105   # 원 두께 (지름 대비 비율)
FADE_MS = 280            # 라이트/다크 전환 페이드 시간 (밀리초)
PULSE_SEC = 2.0          # 무한 모드에서 원이 한 번 흐려졌다 돌아오는 시간 (초)
PULSE_LOW = 0.15         # 가장 흐려질 때 남는 원 색 비율
PLACEHOLDERS = ("HH", "MM", "SS")
ICON_GRAY = "#8E8E96"    # 실행 파일(exe) 아이콘 색

THEMES = {
    "light": {
        "bg": "#F2F2F4", "track": "#DCDCE0", "text": "#1A1A1A", "sub": "#6B6B73",
        "btn": "#E4E4E9", "btn_hover": "#D6D6DD", "entry": "#FFFFFF", "border": "#CFCFD6", "gray": "#9C9CA5",
    },
    "dark": {
        "bg": "#1C1C20", "track": "#34343B", "text": "#F4F4F6", "sub": "#9A9AA3",
        "btn": "#2E2E35", "btn_hover": "#3B3B44", "entry": "#26262C", "border": "#45454E", "gray": "#7E7E88",
    },
}

PRESET_COLORS = ["#E6AC17", "#F76B15", "#E5484D", "#D6409F", "#8E4EC6", "#0091FF", "#12A594", "#30A46C"]
MAX_ROWS = 12            # 인터벌 최대 줄 수
MAX_CUSTOM = 4           # 직접 추가할 수 있는 색 개수 (기본 8색은 항상 제공, 삭제 불가)

DEFAULT_SETTINGS = {
    "theme": "light",
    "accent": "#E6AC17",
    "custom_colors": [],
    "duration": 0,             # 처음엔 시간 공란
    "topmost": False,
    "sound": True,
    # 루틴 줄 목록 [{"secs": 초, "color": "accent" | "gray"}] — 처음엔 빈 1번(지정색)·2번(회색) 줄
    "intervals": [{"secs": 0, "color": "accent"}, {"secs": 0, "color": "gray"}],
    "interval_on": False,      # 루틴 사용 (처음엔 꺼짐)
    "interval_loop": False,    # 마지막 구간 뒤 처음으로 반복
    "sound_accent": "",        # 집중 알림음: 루틴 집중 구간 시작 / 일반 타이머가 끝날 때 ("" = 기본음)
    "sound_gray": "",          # 휴식 알림음: 루틴 휴식(회색) 구간 시작
    "sound_dir": "",           # 알림음 파일을 마지막으로 고른 폴더
    "geometry": "",
    "infinite": False,         # 무한 모드 (0부터 위로 세는 스톱워치)
    "mini": False,             # 미니 모드 (원 + 시간만, 정사각형 창)
    "mini_geometry": "",
}


# ─────────────────────────── 설정 저장 / 불러오기 ───────────────────────────

def settings_path():
    if IS_MAC:
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    folder = os.path.join(base, APP_NAME)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "settings.json")


def load_settings():
    data = json.loads(json.dumps(DEFAULT_SETTINGS))      # 기본값 깊은 복사
    try:
        with open(settings_path(), "r", encoding="utf-8") as f:
            data.update(json.load(f))
    except Exception:
        pass
    if data.get("theme") not in THEMES:
        data["theme"] = "light"
    src = data.get("custom_colors")
    if not isinstance(src, list):                      # 예전 버전 설정(palette)에서 옮겨오기
        src = data.get("palette") if isinstance(data.get("palette"), list) else []
    data.pop("palette", None)
    presets = [c.upper() for c in PRESET_COLORS]
    custom = [c.upper() for c in src if is_hex_color(c) and c.upper() not in presets]
    custom = list(dict.fromkeys(custom))[:MAX_CUSTOM]
    data["custom_colors"] = custom
    if not is_hex_color(data.get("accent", "")):
        data["accent"] = presets[0]
    data["accent"] = data["accent"].upper()
    if data["accent"] not in presets + custom:
        if len(custom) < MAX_CUSTOM:
            custom.append(data["accent"])
        else:
            data["accent"] = presets[0]
    # 인터벌 목록 정리 (예전 메모장 형식 intervals_text 에서 옮겨오기)
    rows = data.get("intervals")
    if not isinstance(rows, list) or (not rows and data.get("intervals_text")):
        rows = []
        text = data.get("intervals_text") or ""
        for line in text.split("\n"):
            r = parse_interval_line(line)
            if r:
                rows.append({"secs": r[1], "color": "gray" if len(rows) % 2 else "accent"})
    clean = []
    for r in rows[:30]:
        if isinstance(r, dict):
            try:
                secs = max(0, min(int(r.get("secs", 0)), 99 * 3600 + 59 * 60 + 59))
            except (TypeError, ValueError):
                secs = 0
            clean.append({"secs": secs, "color": "gray" if r.get("color") == "gray" else "accent"})
    data["intervals"] = clean
    data.pop("intervals_text", None)
    return data


def save_settings(data):
    try:
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ─────────────────────────── 색상 / 시간 도우미 ───────────────────────────

def is_hex_color(s):
    return isinstance(s, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", s) is not None


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(v))) for v in rgb)


def mix(c1, c2, t):
    a, b = hex_to_rgb(c1), hex_to_rgb(c2)
    return rgb_to_hex(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))


def readable_fg(bg):
    r, g, b = hex_to_rgb(bg)
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#1A1A1A" if lum > 150 else "#FFFFFF"


def format_time(seconds, floor=False):
    s = int(math.floor(max(0.0, seconds) + 1e-9)) if floor else int(math.ceil(max(0.0, seconds) - 1e-6))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ─────────────────────────── 그리기 (Pillow) ───────────────────────────

def render_ring(size, frac, track, color, bg, ss=SUPERSAMPLE):
    """frac(0~1) 만큼 12시 방향부터 시계 방향으로 채워진 원"""
    S = size * ss
    img = Image.new("RGB", (S, S), bg)
    d = ImageDraw.Draw(img)
    w = max(2, int(S * RING_THICKNESS))
    m = ss * 2
    box = [m, m, S - m, S - m]
    d.ellipse(box, outline=track, width=w)
    if frac >= 0.9995:
        d.ellipse(box, outline=color, width=w)
    elif frac > 0.0005:
        d.arc(box, -90, -90 + 360 * frac, fill=color, width=w)
    return img.resize((size, size), Image.LANCZOS)


_RING_MASKS = {}


def render_full_ring(size, color, bg, ss=SUPERSAMPLE):
    """꽉 찬 원 (무한 모드 깜빡임용). 모양은 크기별로 한 번만 그리고 색만 칠함"""
    if size not in _RING_MASKS:
        if len(_RING_MASKS) > 4:
            _RING_MASKS.clear()
        S = size * ss
        m = Image.new("L", (S, S), 0)
        w, mm = max(2, int(S * RING_THICKNESS)), ss * 2
        ImageDraw.Draw(m).ellipse([mm, mm, S - mm, S - mm], outline=255, width=w)
        _RING_MASKS[size] = m.resize((size, size), Image.LANCZOS)
    img = Image.new("RGB", (size, size), bg)
    img.paste(color, (0, 0), _RING_MASKS[size])
    return img


def render_disc(d, color, bg):
    """가운데 옅은 원 (원 영역이 버튼이라는 표시)"""
    k = 3
    img = Image.new("RGBA", (d * k, d * k), (0, 0, 0, 0))          # 바깥은 투명 (링을 가리지 않게)
    ImageDraw.Draw(img).ellipse([0, 0, d * k - 1, d * k - 1], fill=color)
    return img.resize((d, d), Image.LANCZOS)


def render_dot(size, color, selected, ring_color, bg):
    S = size * 4
    img = Image.new("RGB", (S, S), bg)
    d = ImageDraw.Draw(img)
    if selected:
        d.ellipse([0, 0, S - 1, S - 1], outline=ring_color, width=int(S * 0.09))
        pad = int(S * 0.2)
    else:
        pad = int(S * 0.12)
    d.ellipse([pad, pad, S - 1 - pad, S - 1 - pad], fill=color)
    return img.resize((size, size), Image.LANCZOS)


def render_icon(kind, size, fg, bg, circle=None):
    """버튼 아이콘 그리기. kind: play / pause / reset / sun / moon / pin
    circle: 동그란 버튼 배경색 (None 이면 배경 없음)"""
    S = size * 4
    img = Image.new("RGB", (S, S), bg)
    d = ImageDraw.Draw(img)
    base = circle or bg
    if circle:
        d.ellipse([0, 0, S - 1, S - 1], fill=circle)
    c = S / 2
    u = S / 100.0  # 크기의 1% 단위

    if kind == "play":
        d.polygon([(c - 12 * u, c - 19 * u), (c - 12 * u, c + 19 * u), (c + 20 * u, c)], fill=fg)
    elif kind == "pause":
        bw, bh, gap = 10 * u, 36 * u, 6 * u
        for x0 in (c - gap - bw, c + gap):
            d.rounded_rectangle([x0, c - bh / 2, x0 + bw, c + bh / 2], radius=3 * u, fill=fg)
    elif kind == "reset":
        r, w = 20 * u, int(7 * u)
        d.arc([c - r, c - r, c + r, c + r], -40, 270, fill=fg, width=w)
        tip_y = c - r + w / 2
        d.polygon([(c + 11 * u, tip_y), (c - 3 * u, tip_y - 11 * u), (c - 3 * u, tip_y + 11 * u)], fill=fg)
    elif kind == "sun":
        r = 13 * u
        d.ellipse([c - r, c - r, c + r, c + r], fill=fg)
        for i in range(8):
            a = math.radians(i * 45)
            x1, y1 = c + math.cos(a) * 21 * u, c + math.sin(a) * 21 * u
            x2, y2 = c + math.cos(a) * 30 * u, c + math.sin(a) * 30 * u
            d.line([x1, y1, x2, y2], fill=fg, width=int(6 * u))
            for (x, y) in ((x1, y1), (x2, y2)):
                d.ellipse([x - 3 * u, y - 3 * u, x + 3 * u, y + 3 * u], fill=fg)
    elif kind == "moon":
        r = 25 * u
        d.ellipse([c - r, c - r, c + r, c + r], fill=fg)
        r2, ox, oy = 21 * u, 13 * u, -10 * u
        d.ellipse([c + ox - r2, c + oy - r2, c + ox + r2, c + oy + r2], fill=base)
    elif kind == "close":
        L, w = 13 * u, int(6 * u)
        d.line([c - L, c - L, c + L, c + L], fill=fg, width=w)
        d.line([c - L, c + L, c + L, c - L], fill=fg, width=w)
    elif kind == "menu":
        w, L = 6 * u, 16 * u
        for dy in (-11 * u, 0, 11 * u):
            d.rounded_rectangle([c - L, c + dy - w / 2, c + L, c + dy + w / 2], radius=w / 2, fill=fg)
    elif kind == "plus":
        r = 46 * u
        d.ellipse([c - r, c - r, c + r, c + r], outline=fg, width=int(4 * u))
        L, w = 17 * u, int(6 * u)
        d.line([c - L, c, c + L, c], fill=fg, width=w)
        d.line([c, c - L, c, c + L], fill=fg, width=w)
    elif kind in ("mini", "expand"):
        # 네 귀퉁이 꺾쇠: mini = 안쪽을 향함(줄이기), expand = 바깥을 향함(늘리기)
        w = int(5 * u)
        a, b = (5 * u, 17 * u) if kind == "mini" else (19 * u, 8 * u)   # 꼭짓점 거리, 팔 끝 거리
        for sx in (-1, 1):
            for sy in (-1, 1):
                vx, vy = c + sx * a, c + sy * a
                d.line([vx, vy, c + sx * b, vy], fill=fg, width=w)
                d.line([vx, vy, vx, c + sy * b], fill=fg, width=w)
                d.ellipse([vx - w / 2, vy - w / 2, vx + w / 2, vy + w / 2], fill=fg)
    elif kind == "infinite":
        a, w = 27 * u, int(6 * u)
        pts = []
        for i in range(121):
            t = 2 * math.pi * i / 120
            k = 1 + math.sin(t) ** 2
            pts.append((c + a * math.cos(t) / k, c + a * math.sin(t) * math.cos(t) / k * 1.15))
        d.line(pts, fill=fg, width=w, joint="curve")
    elif kind == "pin":
        img.paste(fg, (0, 0), _pin_mask(S))
    return img.resize((size, size), Image.LANCZOS)


_PIN_CACHE = {}


def _pin_mask(S):
    """📌 모양 (기울인 상태) — 크기별로 한 번만 계산"""
    if S not in _PIN_CACHE:
        c, u = S / 2, S / 100.0
        m = Image.new("L", (S, S), 0)
        p = ImageDraw.Draw(m)
        p.rounded_rectangle([c - 13 * u, c - 32 * u, c + 13 * u, c - 23 * u], radius=3 * u, fill=255)
        p.polygon([(c - 8 * u, c - 24 * u), (c + 8 * u, c - 24 * u), (c + 11 * u, c + 2 * u), (c - 11 * u, c + 2 * u)], fill=255)
        p.rounded_rectangle([c - 19 * u, c, c + 19 * u, c + 8 * u], radius=3 * u, fill=255)
        p.line([c, c + 7 * u, c, c + 32 * u], fill=255, width=int(4 * u))
        _PIN_CACHE[S] = m.rotate(-40, resample=Image.BICUBIC, center=(c, c))
    return _PIN_CACHE[S]


def render_round_box(w, h, radius, fill, outline, outline_w, bg):
    """둥근 네모 (입력칸 배경)"""
    k = 4
    img = Image.new("RGB", (w * k, h * k), bg)
    d = ImageDraw.Draw(img)
    ow = max(1, int(outline_w * k))
    d.rounded_rectangle([0, 0, w * k - 1, h * k - 1], radius=radius * k, fill=outline)
    d.rounded_rectangle([ow, ow, w * k - 1 - ow, h * k - 1 - ow], radius=max(1, radius * k - ow), fill=fill)
    return img.resize((w, h), Image.LANCZOS)


_FONT_CACHE = {}


def get_pil_font(px_size):
    """칸 안에 숫자를 직접 그릴 때 쓰는 굵은 글꼴"""
    px_size = max(6, int(px_size))
    if px_size in _FONT_CACHE:
        return _FONT_CACHE[px_size]
    dirs = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
            "/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts",
            "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/TTF"]
    names = ["segoeuib.ttf", "seguisb.ttf", "malgunbd.ttf", "arialbd.ttf",
             "Arial Bold.ttf", "HelveticaNeue.ttc", "Helvetica.ttc", "SFNS.ttf", "DejaVuSans-Bold.ttf"]
    font = None
    for d in dirs:
        for n in names:
            path = os.path.join(d, n)
            if os.path.exists(path):
                try:
                    font = ImageFont.truetype(path, px_size)
                    break
                except Exception:
                    pass
        if font:
            break
    if font is None:
        try:
            font = ImageFont.load_default(px_size)
        except Exception:
            font = ImageFont.load_default()
    _FONT_CACHE[px_size] = font
    return font


def render_text_box(w, h, radius, fill, outline, outline_w, bg, text="", text_color="#000000", font_px=16):
    """둥근 칸 + 가운데 글자 (윈도우에서 입력칸 글자가 안 보이는 문제를 피하려고 직접 그림)"""
    k = 4
    img = Image.new("RGB", (w * k, h * k), bg)
    d = ImageDraw.Draw(img)
    ow = max(1, int(outline_w * k))
    d.rounded_rectangle([0, 0, w * k - 1, h * k - 1], radius=radius * k, fill=outline)
    d.rounded_rectangle([ow, ow, w * k - 1 - ow, h * k - 1 - ow], radius=max(1, radius * k - ow), fill=fill)
    if text:
        f = get_pil_font(font_px * k)
        try:
            d.text((w * k / 2, h * k / 2), text, font=f, fill=text_color, anchor="mm")
        except Exception:
            d.text((w * k / 4, h * k / 4), text, font=f, fill=text_color)
    return img.resize((w, h), Image.LANCZOS)


def _round_mask(w, h, radius):
    k = 4
    m = Image.new("L", (w * k, h * k), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w * k - 1, h * k - 1], radius=radius * k, fill=255)
    return m.resize((w, h), Image.LANCZOS)


def render_sv_square(w, h, hue, radius, bg):
    """색상판: 가로 = 채도(왼쪽 흰색 → 오른쪽 진한색), 세로 = 밝기(위 밝음 → 아래 검정)"""
    r, g, b = (int(x * 255) for x in colorsys.hsv_to_rgb(hue, 1, 1))
    grad = Image.linear_gradient("L")                       # 위 0 → 아래 255
    img = Image.composite(Image.new("RGB", (256, 256), "white"),
                          Image.new("RGB", (256, 256), (r, g, b)), grad.rotate(-90))
    img = Image.composite(Image.new("RGB", (256, 256), "black"), img, grad)
    img = img.resize((w, h), Image.BICUBIC)
    out = Image.new("RGB", (w, h), bg)
    out.paste(img, (0, 0), _round_mask(w, h, radius))
    return out


def render_hue_bar(w, h, bg):
    strip = Image.new("RGB", (360, 1))
    strip.putdata([tuple(int(x * 255) for x in colorsys.hsv_to_rgb(i / 360, 1, 1)) for i in range(360)])
    bar = strip.resize((w, h), Image.BILINEAR)
    out = Image.new("RGB", (w, h), bg)
    out.paste(bar, (0, 0), _round_mask(w, h, h // 2))
    return out


def render_handle(size, fill):
    """색상판 위의 동그란 손잡이 (흰 테두리 + 옅은 그림자)"""
    k = 4
    S = size * k
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, S - 1, S - 1], fill=(0, 0, 0, 70))
    d.ellipse([k, k, S - 1 - k, S - 1 - k], fill="white")
    p = int(S * 0.22)
    d.ellipse([p, p, S - 1 - p, S - 1 - p], fill=fill)
    return img.resize((size, size), Image.LANCZOS)


def render_switch(w, h, on, on_color, off_color, bg):
    """요즘 스타일 켜기/끄기 스위치"""
    k = 4
    W, H = w * k, h * k
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=H // 2, fill=on_color if on else off_color)
    m = int(H * 0.13)
    kd = H - 2 * m
    x0 = W - m - kd if on else m
    d.ellipse([x0, m, x0 + kd, m + kd], fill="white")
    return img.resize((w, h), Image.LANCZOS)


_UNITS = {"시간": 3600, "시": 3600, "h": 3600, "분": 60, "m": 60, "초": 1, "s": 1}


def parse_interval_line(line):
    """인터벌 한 줄 해석 → (이름, 초) / 빈 줄이면 None / 못 읽으면 False
    예) '1번 50분', '집중 50', '휴식 10:00', '1:30:00', '긴 휴식 1시간 5분'"""
    raw = line.strip()
    if not raw:
        return None
    txt = re.sub(r"^\s*\d+\s*(?:번|\)|\.(?!\d))\s*", "", raw)       # 앞의 번호(1번, 1., 1)) 떼기
    secs = None
    m = re.search(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", txt)
    if m:
        a, b, c = int(m.group(1)), int(m.group(2)), m.group(3)
        secs = a * 3600 + b * 60 + int(c) if c is not None else a * 60 + b
        label = txt[:m.start()] + txt[m.end():]
    else:
        pat = r"(\d+(?:\.\d+)?)\s*(시간|시|분|초|h|m|s)(?![a-z])"
        found = re.findall(pat, txt, flags=re.I)
        if found:
            secs = sum(float(n) * _UNITS[u.lower()] for n, u in found)
            label = re.sub(pat, "", txt, flags=re.I)
        else:
            m = re.search(r"\d+(?:\.\d+)?", txt)
            if m:
                secs = float(m.group(0)) * 60                               # 숫자만 쓰면 '분'
                label = txt[:m.start()] + txt[m.end():]
    if secs is None:
        return False
    secs = int(round(secs))
    if not 0 < secs <= 99 * 3600 + 59 * 60 + 59:
        return False
    return (label.strip(" -·,:/|~"), secs)


def make_icon_image(size=256, color="#E6AC17", frac=0.75, track="#DCDCE0", glass="#9A9AA2"):
    """앱 아이콘 = 작은 원 그래프 (frac 만큼 채움)"""
    S = size * 4
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w = int(S * 0.2)
    m = int(S * 0.03)
    box = [m, m, S - m, S - m]
    d.ellipse(box, outline=track, width=w)
    if frac >= 0.999:
        d.ellipse(box, outline=color, width=w)
    elif frac > 0.005:
        d.arc(box, -90, -90 + 360 * frac, fill=color, width=w)
    # 가운데 모래시계
    c = S / 2
    hw, hh, bar = S * 0.13, S * 0.17, S * 0.035          # 반폭, 반높이, 위아래 막대 두께
    d.rounded_rectangle([c - hw - bar * 0.6, c - hh - bar, c + hw + bar * 0.6, c - hh + bar * 0.2],
                        radius=bar * 0.5, fill=glass)
    d.rounded_rectangle([c - hw - bar * 0.6, c + hh - bar * 0.2, c + hw + bar * 0.6, c + hh + bar],
                        radius=bar * 0.5, fill=glass)
    neck = S * 0.018
    d.polygon([(c - hw, c - hh), (c + hw, c - hh), (c + neck, c), (c - neck, c)], fill=glass)
    d.polygon([(c - neck, c), (c + neck, c), (c + hw, c + hh), (c - hw, c + hh)], fill=glass)
    return img.resize((size, size), Image.LANCZOS)


# ─────────────────────────── Windows 전용 처리 ───────────────────────────

def enable_dpi_awareness():
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def style_titlebar(root, dark, caption_hex, flush=True):
    """Windows 10/11 제목 표시줄을 테마에 맞춤 (지원 안 되면 조용히 무시)"""
    if sys.platform != "win32":
        return
    try:
        if flush:
            root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        dwm = ctypes.windll.dwmapi
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (신/구 버전)
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
        r, g, b = hex_to_rgb(caption_hex)
        colorref = ctypes.c_int(r | (g << 8) | (b << 16))
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colorref), ctypes.sizeof(colorref))  # 캡션 색 (Win11)
        text = ctypes.c_int(0xF4F4F4 if dark else 0x1A1A1A)
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), ctypes.sizeof(text))
    except Exception:
        pass


def _win_rects(widget):
    """(창 전체 사각형, 눈에 보이는 테두리 사각형) — Windows 전용"""
    import ctypes.wintypes as wt
    hwnd = ctypes.windll.user32.GetParent(widget.winfo_id())
    full, vis = wt.RECT(), wt.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(full))
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(vis), ctypes.sizeof(vis)) != 0:
        vis = full   # DWMWA_EXTENDED_FRAME_BOUNDS 를 못 얻으면 전체 사각형 사용
    return full, vis


def snap_beside(win, main, match_height=False):
    """win 창을 main 창의 오른쪽 테두리에 빈틈 없이 붙임 (자리가 없으면 왼쪽)"""
    if sys.platform != "win32":
        return
    try:
        mfull, mvis = _win_rects(main)
        wfull, wvis = _win_rects(win)
        off_l = wvis.left - wfull.left            # 보이지 않는 테두리 두께
        off_t = wvis.top - wfull.top
        vis_w = wvis.right - wvis.left
        screen_w = ctypes.windll.user32.GetSystemMetrics(78) or main.winfo_screenwidth()  # 가상 화면 폭
        x = mvis.right - off_l
        if mvis.right + vis_w > screen_w:
            x = mvis.left - vis_w - off_l
        y = mvis.top - off_t
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        flags = 0x0004 | 0x0010                    # 순서 유지, 활성화 안 함
        if match_height:
            full_w = wfull.right - wfull.left
            full_h = (mvis.bottom - mvis.top) + ((wfull.bottom - wfull.top) - (wvis.bottom - wvis.top))
            ctypes.windll.user32.SetWindowPos(hwnd, 0, int(x), int(y), int(full_w), int(full_h), flags)
        else:
            ctypes.windll.user32.SetWindowPos(hwnd, 0, int(x), int(y), 0, 0, flags | 0x0001)
    except Exception:
        pass


def taskbar_is_light():
    """윈도우 작업 표시줄(맥은 Dock)이 밝은 테마인지"""
    if IS_MAC:
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                                 capture_output=True, text=True, timeout=2).stdout
            return "Dark" not in out
        except Exception:
            return True
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 1
    except OSError:
        return False


_MAC_SOUND = {"proc": None}


def play_sound_file(path):
    """원하는 소리 파일 재생. 성공하면 True (윈도우 = MCI, 맥 = afplay)"""
    if not path or not os.path.exists(path):
        return False
    if IS_MAC:
        try:
            stop_sound_file()
            _MAC_SOUND["proc"] = subprocess.Popen(["afplay", path])
            return True
        except Exception:
            return False
    if sys.platform != "win32":
        return False
    try:
        mci = ctypes.windll.winmm.mciSendStringW
        mci("close ringtimer_snd", None, 0, 0)
        if mci(f'open "{path}" type mpegvideo alias ringtimer_snd', None, 0, 0) != 0:
            if mci(f'open "{path}" alias ringtimer_snd', None, 0, 0) != 0:
                return False
        mci("play ringtimer_snd", None, 0, 0)
        return True
    except Exception:
        return False


def stop_sound_file():
    if IS_MAC:
        p = _MAC_SOUND.get("proc")
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
        _MAC_SOUND["proc"] = None
        return
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.winmm.mciSendStringW("close ringtimer_snd", None, 0, 0)
    except Exception:
        pass


def play_done_sound(root, times=3):
    if sys.platform == "win32":
        def run():
            try:
                import winsound
                for _ in range(times):
                    winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS)
                    time.sleep(0.35)
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()
    elif IS_MAC:
        def run_mac():
            for _ in range(times):
                try:
                    subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], timeout=5)
                except Exception:
                    break
        threading.Thread(target=run_mac, daemon=True).start()
    else:
        root.bell()


# ─────────────────────────── 위젯 ───────────────────────────

class FlatButton(tk.Label):
    """테마 색을 자유롭게 입힐 수 있는 평평한 버튼"""

    def __init__(self, master, text, command, padx=14, pady=7, **kw):
        super().__init__(master, text=text, cursor="hand2", padx=padx, pady=pady, **kw)
        self.command = command
        self._hover = False
        self._colors = ("#DDDDDD", "#CCCCCC", "#000000")
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._on_click)

    def set_colors(self, bg, hover, fg):
        self._colors = (bg, hover, fg)
        self._paint()

    def _paint(self):
        bg, hover, fg = self._colors
        self.configure(bg=hover if self._hover else bg, fg=fg)

    def _set_hover(self, v):
        self._hover = v
        self._paint()

    def _on_click(self, e):
        if 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self.command()


class IconButton(tk.Label):
    """그림(아이콘)만 있는 버튼. 마우스를 올리면 hover 그림으로 바뀜"""

    def __init__(self, master, command, **kw):
        super().__init__(master, bd=0, cursor="hand2", **kw)
        self.command = command
        self._hover = False
        self._photos = (None, None)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._on_click)

    def set_images(self, normal, hover, bg):
        new = (ImageTk.PhotoImage(normal), ImageTk.PhotoImage(hover))
        self.configure(bg=bg, image=new[1 if self._hover else 0])
        self._photos = new  # 새 그림을 먼저 붙인 뒤 예전 그림을 버림

    def _paint(self):
        ph = self._photos[1 if self._hover else 0]
        if ph is not None:
            self.configure(image=ph)

    def _set_hover(self, v):
        self._hover = v
        self._paint()

    def _on_click(self, e):
        if 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self.command()


class ColorPicker(tk.Toplevel):
    """색상판 + 색조 막대 + HEX 입력으로 된 색상 선택 창
    mode="add"  : [취소] [추가]
    mode="edit" : [삭제] ... [취소] [적용]"""

    def __init__(self, app, initial, mode="add", on_ok=None, on_delete=None):
        super().__init__(app.root)
        self.app, self.on_ok, self.on_delete = app, on_ok, on_delete
        t = self.t = app.colors
        px = app.px
        self.withdraw()
        self.title("색상 추가" if mode == "add" else "색상 편집")
        self.configure(bg=t["bg"])
        self.resizable(False, False)
        self.transient(app.root)

        self.h, self.s, self.v = colorsys.rgb_to_hsv(*[x / 255 for x in hex_to_rgb(initial)])
        self.W, self.SVH, self.HB, self.HS = px(264), px(176), px(14), px(20)
        pad = px(18)

        # 색상판
        self.sv = tk.Canvas(self, width=self.W, height=self.SVH, bg=t["bg"], highlightthickness=0, bd=0, cursor="crosshair")
        self.sv.pack(padx=pad, pady=(pad, px(14)))
        self.sv_img = self.sv.create_image(0, 0, anchor="nw")
        self.sv_handle = self.sv.create_image(0, 0, anchor="center")
        for ev in ("<Button-1>", "<B1-Motion>"):
            self.sv.bind(ev, self._on_sv)

        # 색조 막대
        self.hue = tk.Canvas(self, width=self.W, height=self.HS, bg=t["bg"], highlightthickness=0, bd=0, cursor="hand2")
        self.hue.pack(padx=pad)
        self._hue_photo = ImageTk.PhotoImage(render_hue_bar(self.W, self.HB, t["bg"]))
        self.hue.create_image(0, (self.HS - self.HB) // 2, anchor="nw", image=self._hue_photo)
        self.hue_handle = self.hue.create_image(0, self.HS // 2, anchor="center")
        for ev in ("<Button-1>", "<B1-Motion>"):
            self.hue.bind(ev, self._on_hue)

        # 미리보기 + HEX 입력
        row = tk.Frame(self, bg=t["bg"])
        row.pack(padx=pad, pady=(px(16), 0))                     # 가운데 정렬
        self.preview = tk.Label(row, bd=0, bg=t["bg"])
        self.preview.pack(side="left")
        hw, hh = px(120), px(36)
        self._hex_box_photo = ImageTk.PhotoImage(render_round_box(hw, hh, px(10), t["entry"], t["border"], px(1), t["bg"]))
        box = tk.Label(row, image=self._hex_box_photo, bd=0, bg=t["bg"])
        box.pack(side="left", padx=(px(12), 0))
        self.hex_var = tk.StringVar()
        self.hex_entry = tk.Entry(box, textvariable=self.hex_var, font=app.f_ui_bold, bd=0, relief="flat",
                                  highlightthickness=0, justify="center", bg=t["entry"], fg=t["text"],
                                  insertbackground=t["text"], selectbackground=t["btn_hover"], selectforeground=t["text"])
        self.hex_entry.place(relx=0.5, rely=0.5, anchor="center", width=hw - px(20))
        self.hex_entry.bind("<KeyRelease>", self._on_hex)

        # 버튼 줄
        btns = tk.Frame(self, bg=t["bg"])
        btns.pack(padx=pad, pady=(px(18), pad))                  # 가운데 정렬
        if mode == "edit" and on_delete:
            dele = FlatButton(btns, "삭제", self._delete, padx=14, pady=6, font=app.f_ui)
            dele.set_colors(t["btn"], t["btn_hover"], "#E5484D")
            dele.pack(side="left", padx=(0, px(8)))
        cancel = FlatButton(btns, "취소", self.destroy, padx=14, pady=6, font=app.f_ui)
        cancel.set_colors(t["btn"], t["btn_hover"], t["text"])
        cancel.pack(side="left", padx=(0, px(8)))
        self.btn_ok = FlatButton(btns, "추가" if mode == "add" else "적용", self._ok, padx=16, pady=6, font=app.f_ui_bold)
        self.btn_ok.pack(side="left")

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())

        self._render_sv()
        self._update(from_hex=False)

        # 타이머 창 바로 오른쪽에 딱 붙여서 띄우기 (화면이 모자라면 왼쪽)
        self.update_idletasks()
        r = app.root
        x = r.winfo_rootx() + r.winfo_width()
        if x + self.winfo_reqwidth() > r.winfo_screenwidth():
            x = r.winfo_rootx() - self.winfo_reqwidth()
        self.geometry(f"+{max(0, x)}+{max(0, r.winfo_rooty())}")
        self.deiconify()
        style_titlebar(self, app.cfg["theme"] == "dark", t["bg"])
        self.update_idletasks()
        snap_beside(self, app.side_anchor())
        self.focus_force()
        self.bind("<FocusOut>", self._on_focus_out)

    def _on_focus_out(self, e=None):
        self.after(60, self._close_if_outside)

    def _close_if_outside(self):
        try:
            if not self.winfo_exists():
                return
            f = self.focus_get()
            if f is None or not str(f).startswith(str(self)):
                try:
                    under = self.winfo_containing(*self.winfo_pointerxy())
                except (tk.TclError, KeyError):
                    under = None
                if under is self.app.btn_theme or getattr(self.app, "_overlay_fading", False):
                    return                              # 테마 버튼: 닫지 않음 (새 테마로 다시 그려짐)
                self.destroy()
        except (tk.TclError, KeyError):
            try:
                self.destroy()
            except tk.TclError:
                pass

    def color(self):
        return rgb_to_hex(tuple(x * 255 for x in colorsys.hsv_to_rgb(self.h, self.s, self.v)))

    def _render_sv(self):
        self._sv_photo = ImageTk.PhotoImage(render_sv_square(self.W, self.SVH, self.h, self.app.px(12), self.t["bg"]))
        self.sv.itemconfigure(self.sv_img, image=self._sv_photo)

    def _update(self, from_hex=False):
        col = self.color()
        px = self.app.px
        self._h1 = ImageTk.PhotoImage(render_handle(px(20), col))
        self.sv.itemconfigure(self.sv_handle, image=self._h1)
        self.sv.coords(self.sv_handle, self.s * (self.W - 1), (1 - self.v) * (self.SVH - 1))
        hue_col = rgb_to_hex(tuple(x * 255 for x in colorsys.hsv_to_rgb(self.h, 1, 1)))
        self._h2 = ImageTk.PhotoImage(render_handle(px(20), hue_col))
        self.hue.itemconfigure(self.hue_handle, image=self._h2)
        self.hue.coords(self.hue_handle, min(max(self.h * self.W, px(10)), self.W - px(10)), self.HS // 2)
        self._pv = ImageTk.PhotoImage(render_dot(px(36), col, False, col, self.t["bg"]))
        self.preview.configure(image=self._pv)
        if not from_hex:
            self.hex_var.set(col)
        hover = mix(col, "#000000", 0.12)
        self.btn_ok.set_colors(col, hover, readable_fg(col))

    def _on_sv(self, e):
        self.s = min(max(e.x / (self.W - 1), 0.0), 1.0)
        self.v = 1 - min(max(e.y / (self.SVH - 1), 0.0), 1.0)
        self._update()

    def _on_hue(self, e):
        self.h = min(max(e.x / self.W, 0.0), 0.9999)
        self._render_sv()
        self._update()

    def _on_hex(self, e):
        txt = self.hex_var.get().strip()
        if not txt.startswith("#"):
            txt = "#" + txt
        if is_hex_color(txt):
            self.h, self.s, self.v = colorsys.rgb_to_hsv(*[x / 255 for x in hex_to_rgb(txt)])
            self._render_sv()
            self._update(from_hex=True)

    def _ok(self):
        col = self.color()
        self.destroy()
        if self.on_ok:
            self.on_ok(col)

    def _delete(self):
        self.destroy()
        if self.on_delete:
            self.on_delete()


class TimeFields(tk.Frame):
    """hh : mm : ss 입력칸 세 개
    - 입력 중이 아닐 때: 숫자(또는 흐린 hh/mm/ss)를 칸 그림에 직접 그려서 항상 보이게 함
    - 칸을 클릭하면 그 칸에만 실제 입력칸이 나타남 (테두리는 지정색)"""
    MAX = (99, 59, 59)

    def __init__(self, master, app, placeholders, box_w, box_h, font, on_change=None, on_enter=None,
                 focus_color=None, on_tab=None):
        super().__init__(master, bd=0)
        self.app, self.phs = app, placeholders
        self.on_change, self.on_enter, self.on_tab = on_change, on_enter, on_tab
        self.focus_color = focus_color or (lambda: app.cfg["accent"])
        self.box_w, self.box_h = box_w, box_h
        self.entries, self.boxes, self.colons = [], [], []
        self.vals = [0, 0, 0]
        self.active = None
        self.locked = False
        self._typing = False                  # 실제로 글자를 고치는 중인지
        self._ph = False                      # 입력 중인 칸에 흐린 글자가 보이는지
        self._photos = [None, None, None]
        self.bgcolor = app.colors["bg"]
        self.font_px = int(round(abs(font.actual("size")) * app.root.winfo_fpixels("1i") / 72))
        px = app.px
        vcmd = (self.register(lambda P: P == "" or (P.isdigit() and len(P) <= 2)), "%P")
        for i in range(3):
            if i:
                lb = tk.Label(self, text=":", font=font)
                lb.pack(side="left", padx=px(2))
                self.colons.append(lb)
            box = tk.Label(self, bd=0, highlightthickness=0, cursor="xterm")
            box.pack(side="left")
            e = tk.Entry(box, width=2, font=font, bd=0, relief="flat", highlightthickness=0,
                         justify="center", validate="key", validatecommand=vcmd)
            box.bind("<Button-1>", lambda ev, i=i: self.activate(i))
            e.bind("<FocusOut>", lambda ev, i=i: self._deactivate(i))
            e.bind("<KeyPress>", self._keypress)
            e.bind("<KeyRelease>", lambda ev, i=i: self._keyrelease(ev, i))
            e.bind("<BackSpace>", lambda ev, i=i: self._backspace(ev, i))
            e.bind("<Left>", lambda ev, i=i: self._arrow(i, -1))
            e.bind("<Right>", lambda ev, i=i: self._arrow(i, +1))
            e.bind("<colon>", lambda ev, i=i: self._go(i + 1))
            e.bind("<Up>", lambda ev, i=i: self._step(i, +1))
            e.bind("<Down>", lambda ev, i=i: self._step(i, -1))
            e.bind("<Tab>", lambda ev, i=i: self._go(i + 1))
            e.bind("<Shift-Tab>", lambda ev, i=i: self._go(i - 1))
            e.bind("<ISO_Left_Tab>", lambda ev, i=i: self._go(i - 1))
            e.bind("<Return>", lambda ev: (self.on_enter and self.on_enter(), "break")[-1])
            self.entries.append(e)
            self.boxes.append(box)

    # ── 값 ──
    def _entry_value(self, i):
        txt = "" if self._ph else self.entries[i].get().strip()
        return min(int(txt), self.MAX[i]) if txt.isdigit() else 0

    def values(self):
        return [self._entry_value(i) if i == self.active else self.vals[i] for i in range(3)]

    def get_secs(self):
        h, m, s = self.values()
        return h * 3600 + m * 60 + s

    def set_secs(self, secs):
        h, rem = divmod(int(secs), 3600)
        m, s = divmod(rem, 60)
        self.vals = [min(h, 99), m, s]
        for i in range(3):
            if i != self.active:
                self._draw(i)

    # ── 그리기 ──
    def _draw(self, i, focused=None):
        t, px = self.app.colors, self.app.px
        if focused is None:
            focused = (i == self.active)
        r = max(px(9), self.box_h // 4)
        if focused:
            # 선택된(편집 중인) 칸 = 지정색 테두리
            typing = True                     # 칸을 선택하면 바로 지정색 테두리
            img = render_text_box(self.box_w, self.box_h, r, t["entry"],
                                  self.focus_color() if typing else t["border"],
                                  px(2) if typing else px(1), self.bgcolor)
        else:
            v = self.vals[i]
            faint = mix(t["sub"], t["entry"], 0.45)
            if v:
                text, color = f"{v:02d}", (t["sub"] if self.locked else t["text"])
            else:
                text, color = self.phs[i], faint
            img = render_text_box(self.box_w, self.box_h, r, t["entry"], t["border"], px(1), self.bgcolor,
                                  text, color, self.font_px)
        self._photos[i] = ImageTk.PhotoImage(img)
        self.boxes[i].configure(image=self._photos[i], bg=self.bgcolor)

    def apply_theme(self, bg):
        t = self.app.colors
        self.bgcolor = bg
        self.configure(bg=bg)
        for lb in self.colons:
            lb.configure(bg=bg, fg=t["sub"])
        for i, e in enumerate(self.entries):
            e.configure(bg=t["entry"], insertbackground=t["text"],
                        selectbackground=t["btn_hover"], selectforeground=t["text"])
            self._draw(i)
        self._paint_entry()

    def _paint_entry(self):
        if self.active is None:
            return
        t = self.app.colors
        self.entries[self.active].configure(fg=mix(t["sub"], t["entry"], 0.45) if self._ph else t["text"])

    # ── 입력칸 켜기 / 끄기 ──
    def _raw(self, i, text):
        e = self.entries[i]
        e.configure(validate="none")
        e.delete(0, "end")
        e.insert(0, text)
        e.configure(validate="key")
        e.xview_moveto(0)

    def _show_ph(self, i):
        self._raw(i, self.phs[i])
        self._ph = True
        self._paint_entry()
        self.entries[i].icursor(0)
        if self.active == i:
            self._draw(i, True)

    def set_locked(self, locked):
        """타이머가 도는 중이나 루틴 사용 중에는 입력 잠금"""
        locked = bool(locked)
        if locked == self.locked:
            return
        if locked and self.active is not None:
            self._deactivate(self.active)
        self.locked = locked
        for i, b in enumerate(self.boxes):
            b.configure(cursor="arrow" if locked else "xterm")
            self._draw(i)

    def activate(self, i):
        if not 0 <= i < 3 or self.locked:
            return "break"
        if self.active is not None and self.active != i:
            self._deactivate(self.active)
        self.active = i
        self._typing = False
        e = self.entries[i]
        e.place(relx=0.5, rely=0.5, anchor="center", width=self.box_w - self.app.px(10))
        v = self.vals[i]
        if v:
            self._raw(i, f"{v:02d}")
            self._ph = False
            self._paint_entry()
            e.after_idle(lambda: e.select_range(0, "end"))
        else:
            self._ph = True
            self._show_ph(i)                  # 비어 있으면 흐린 hh/mm/ss
        self._draw(i, True)
        e.focus_set()
        return "break"

    def _deactivate(self, i):
        if self.active != i:
            return
        self.vals[i] = self._entry_value(i)
        self.active = None
        self._ph = False
        self._typing = False
        self.entries[i].place_forget()
        self._draw(i, False)
        self._changed()

    def _go(self, i):
        if 0 <= i < 3:
            return self.activate(i)
        if self.on_tab:
            cur = self.active
            if cur is not None:
                self._deactivate(cur)
            self.on_tab(1 if i >= 3 else -1)
        return "break"

    # ── 키 입력 ──
    def _keypress(self, ev):
        if self.active is None:
            return
        editing = (ev.char and ev.char.isdigit()) or ev.keysym.isdigit() or \
            (ev.keysym in ("BackSpace", "Delete") and not self._ph)
        if editing and not self._typing:
            self._typing = True
            self._draw(self.active, True)             # 편집 시작 → 지정색 테두리
        if not self._ph:
            return
        if (ev.char and ev.char.isdigit()) or ev.keysym.isdigit():
            self._raw(self.active, "")
            self._ph = False
            self._paint_entry()
            self._draw(self.active, True)           # 입력 시작 → 지정색 테두리
        elif ev.keysym == "Delete":
            return "break"

    def _keyrelease(self, ev, i):
        if self.active != i:
            return
        e = self.entries[i]
        digit = (ev.char and ev.char.isdigit()) or ev.keysym.isdigit() or ev.keysym.startswith("KP_")
        if not self._ph and e.get() == "":
            self._show_ph(i)
        if digit and i < 2 and not self._ph and len(e.get()) >= 2 and e.index("insert") >= 2:
            self.activate(i + 1)
        elif ev.keysym not in ("Tab", "ISO_Left_Tab", "Return", "Up", "Down", "Left", "Right"):
            self._changed()

    def _backspace(self, ev, i):
        e = self.entries[i]
        if self._ph or (e.index("insert") == 0 and not e.selection_present()):
            if i > 0:
                self.activate(i - 1)
            return "break"

    def _arrow(self, i, d):
        e = self.entries[i]
        if self._ph:
            return self._go(i + d)
        if e.selection_present():
            return
        if d < 0 and e.index("insert") == 0:
            return self._go(i - 1)
        if d > 0 and e.index("insert") >= len(e.get()):
            return self._go(i + 1)

    def _step(self, i, d):
        self._typing = True
        v = (self._entry_value(i) + d) % (self.MAX[i] + 1)
        self._raw(i, f"{v:02d}")
        self._ph = False
        self._paint_entry()
        self._draw(i, True)
        self.entries[i].select_range(0, "end")
        self._changed()
        return "break"

    def _changed(self):
        if self.on_change:
            self.on_change()


class IntervalRow(tk.Frame):
    """인터벌 한 줄: [번호] [색] hh:mm:ss [×]"""

    def __init__(self, panel, secs, color):
        super().__init__(panel.inner, bd=0)
        app = self.app = panel.app
        px = app.px
        self.panel, self.color = panel, color
        self._hover = False
        self.configure(cursor="hand2")
        self.lbl_idx = tk.Label(self, text="", width=2, font=app.f_small, anchor="e", cursor="hand2")
        self.lbl_idx.pack(side="left", padx=(px(6), 0), pady=px(4))
        self.dot = tk.Label(self, bd=0, cursor="hand2")
        self.dot.pack(side="left", padx=(px(6), px(8)))
        self.dot.bind("<Button-1>", lambda e: panel.toggle_color(self))
        self.fields = TimeFields(self, app, ("hh", "mm", "ss"), px(46), px(36), app.f_row,
                                 on_change=panel.rows_changed, on_enter=lambda: panel.add_row(after=self),
                                 focus_color=lambda: app.cfg["accent"] if self.color == "accent" else app.colors["gray"],
                                 on_tab=lambda d: panel.tab_from(self, d))
        self.fields.pack(side="left")
        self.btn_del = IconButton(self, lambda: panel.delete_row(self))
        self.btn_del.pack(side="left", padx=(px(6), px(6)))
        self.fields.set_secs(secs)
        # 줄의 빈 곳(번호, 칸 사이 등)을 누르면 그 구간으로 이동
        for w in [self, self.lbl_idx, self.fields] + self.fields.colons:
            w.bind("<Button-1>", lambda e: panel.jump_to(self))
            w.configure(cursor="hand2")
        # 마우스가 줄 위에 있으면 옅은 네모 배경
        for w in self._all_widgets(self):
            w.bind("<Enter>", lambda e: self.after(10, self._check_hover), add="+")
            w.bind("<Leave>", lambda e: self.after(10, self._check_hover), add="+")

    def _all_widgets(self, w):
        out = [w]
        for ch in w.winfo_children():
            out += self._all_widgets(ch)
        return out

    def _check_hover(self):
        try:
            x, y = self.winfo_pointerxy()
            rx, ry = self.winfo_rootx(), self.winfo_rooty()
            inside = rx <= x < rx + self.winfo_width() and ry <= y < ry + self.winfo_height()
        except tk.TclError:
            return
        if inside != self._hover:
            self._hover = inside
            self.apply_theme(self.panel._is_current(self))

    def apply_theme(self, current=False):
        t, px = self.app.colors, self.app.px
        bg = mix(t["bg"], t["text"], 0.06) if self._hover else t["bg"]     # 마우스 올리면 옅은 네모
        self.configure(bg=bg)
        self.lbl_idx.configure(bg=bg, fg=t["text"] if current else t["sub"],
                               font=self.app.f_ui_bold if current else self.app.f_small)
        col = self.app.cfg["accent"] if self.color == "accent" else t["gray"]
        self._dot_photo = ImageTk.PhotoImage(render_dot(px(16), col, False, col, bg))
        self.dot.configure(image=self._dot_photo, bg=bg)
        s = px(24)
        self.btn_del.set_images(render_icon("close", s, mix(t["sub"], bg, 0.35), bg),
                                render_icon("close", s, t["text"], bg, circle=t["btn"]), bg)
        self.fields.apply_theme(bg)


class IntervalPanel(tk.Toplevel):
    """타이머 창 오른쪽에 붙는 인터벌 목록 (한 줄씩 추가)"""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        px = app.px
        self.withdraw()
        self.title("루틴")
        self.transient(app.root)
        self.protocol("WM_DELETE_WINDOW", app.toggle_interval_panel)
        self.minsize(px(270), px(300))
        self.rows = []
        self._plus_shown = False
        self.locked = False

        self.head = tk.Frame(self)
        self.head.pack(fill="x", padx=px(16), pady=(px(14), px(10)))
        self.lbl_title = tk.Label(self.head, text="루틴", font=app.f_ui_bold)
        self.lbl_title.pack(side="left")
        self.sw_on = tk.Label(self.head, bd=0, cursor="hand2")
        self.sw_on.pack(side="right")
        self.sw_on.bind("<Button-1>", lambda e: app.set_interval_on(not app.cfg.get("interval_on")))
        self.lbl_on = tk.Label(self.head, text="사용", font=app.f_small)
        self.lbl_on.pack(side="right", padx=(0, px(6)))

        self.foot = tk.Frame(self)
        self.foot.pack(side="bottom", fill="x", padx=px(16), pady=(px(8), px(14)))
        self.lbl_loop = tk.Label(self.foot, text="반복", font=app.f_small)
        self.lbl_loop.pack(side="left")
        self.sw_loop = tk.Label(self.foot, bd=0, cursor="hand2")
        self.sw_loop.pack(side="left", padx=(px(6), 0))
        self.sw_loop.bind("<Button-1>", lambda e: app.set_interval_loop(not app.cfg.get("interval_loop")))
        self.lbl_sum = tk.Label(self.foot, text="", font=app.f_small)
        self.lbl_sum.pack(side="right")

        # 알림음: 집중 끝 / 휴식 끝
        self.sound_box = tk.Frame(self)
        self.sound_box.pack(side="bottom", fill="x", padx=px(16), pady=(px(6), 0))
        self.lbl_sound = tk.Label(self.sound_box, text="알림음", font=app.f_ui_bold, anchor="w")
        self.lbl_sound.pack(fill="x", pady=(0, px(4)))
        self.sound_rows = {}
        for key, name in (("accent", "집중 시작"), ("gray", "휴식 시작")):
            row = tk.Frame(self.sound_box)
            row.pack(fill="x", pady=px(2))
            dot = tk.Label(row, bd=0)
            dot.pack(side="left")
            lb = tk.Label(row, text=name, font=app.f_small, width=6, anchor="w")
            lb.pack(side="left", padx=(px(6), px(4)))
            clr = IconButton(row, lambda k=key: self._clear_sound(k))
            clr.pack(side="right")
            play = IconButton(row, lambda k=key: self._preview_sound(k))
            play.pack(side="right", padx=(px(4), px(2)))
            pick = FlatButton(row, "", lambda k=key: self._pick_sound(k), padx=8, pady=3, font=app.f_small)
            pick.pack(side="left", fill="x", expand=True)
            self.sound_rows[key] = (row, dot, lb, pick, play, clr)
        self._dialog_open = False
        self._previewing = None

        # 줄 목록 (가운데 정렬)
        self.inner = tk.Frame(self)
        self.inner.pack(fill="both", expand=True, padx=px(8))

        # 마지막 줄 아래: 마우스를 올리면 + 가 나타남
        self.add_zone = tk.Frame(self.inner, height=px(42), cursor="hand2")
        self.add_zone.pack(fill="x", pady=(px(2), 0))
        self.add_zone.pack_propagate(False)
        self.plus = tk.Label(self.add_zone, bd=0, cursor="hand2")
        self.plus.place(relx=0.5, rely=0.5, anchor="center")
        for w in (self.add_zone, self.plus):
            w.bind("<Enter>", lambda e: self._show_plus(True))
            w.bind("<Leave>", lambda e: self.after(20, self._check_plus))
            w.bind("<Button-1>", lambda e: self.add_row())

        self.bind("<FocusOut>", lambda e: self.after(80, self._close_if_outside))

        data = app.cfg.get("intervals") or []
        for r in (data or [{"secs": 0, "color": "accent"}]):
            self._make_row(r["secs"], r["color"])
        self.apply_theme()

    # 줄 관리
    def _make_row(self, secs, color, after=None):
        row = IntervalRow(self, secs, color)
        row.fields.set_locked(self.locked)
        if after is not None and after in self.rows:
            idx = self.rows.index(after) + 1
            nxt = self.rows[idx] if idx < len(self.rows) else self.add_zone
            row.pack(pady=(0, self.app.px(6)), before=nxt)
            self.rows.insert(idx, row)
        else:
            row.pack(pady=(0, self.app.px(6)), before=self.add_zone)
            self.rows.append(row)
        return row

    def set_locked(self, locked):
        self.locked = bool(locked)
        for r in self.rows:
            r.fields.set_locked(self.locked)
            r.btn_del.configure(cursor="arrow" if self.locked else "hand2")
            r.dot.configure(cursor="arrow" if self.locked else "hand2")
        if self.locked:
            self._show_plus(False)

    def add_row(self, after=None):
        if len(self.rows) >= MAX_ROWS or self.locked:
            return
        n = len(self.rows)
        color = "gray" if n % 2 == 1 else "accent"   # 1번 = 기본색, 2번 = 회색, 번갈아
        row = self._make_row(0, color, after=after)
        self.apply_theme()
        self.rows_changed()
        self.after(10, lambda: row.fields.activate(0))

    def delete_row(self, row):
        if self.locked:
            return
        if len(self.rows) <= 1:
            row.fields.set_secs(0)
        else:
            self.rows.remove(row)
            row.destroy()
        self.apply_theme()
        self.rows_changed()

    def toggle_color(self, row):
        if self.locked:
            return
        row.color = "gray" if row.color == "accent" else "accent"
        row.apply_theme(self._is_current(row))
        self.rows_changed()

    def tab_from(self, row, d):
        i = self.rows.index(row) + d
        if 0 <= i < len(self.rows):
            self.rows[i].fields.activate(0 if d > 0 else 2)

    def jump_to(self, row):
        """이 줄 구간으로 이동 (처음 시간에서 일시정지 상태)"""
        self.app.jump_to_row(self.rows.index(row))

    def rows_changed(self):
        self.app.on_intervals_changed([{"secs": r.fields.get_secs(), "color": r.color} for r in self.rows])

    def focus_first(self):
        self.focus_force()

    # ── 알림음 ──
    def _pick_sound(self, key):
        self._dialog_open = True                    # 파일 창이 떠도 패널이 닫히지 않게
        try:
            cur = self.app.cfg.get(f"sound_{key}") or ""
            folder = os.path.dirname(cur) if cur else (self.app.cfg.get("sound_dir") or "")
            path = filedialog.askopenfilename(
                parent=self, title="알림음 파일 선택",
                initialdir=folder if folder and os.path.isdir(folder) else None,
                initialfile=os.path.basename(cur) if cur else None,
                filetypes=[("소리 파일", "*.mp3 *.wav *.wma *.m4a *.aac *.mid"), ("모든 파일", "*.*")])
        finally:
            self._dialog_open = False
        if path:
            path = os.path.normpath(path)
            self.app.cfg[f"sound_{key}"] = path
            self.app.cfg["sound_dir"] = os.path.dirname(path)      # 다음엔 이 폴더에서 열기
            save_settings(self.app.cfg)
            self.refresh_sounds()
        self.after(50, self.focus_force)

    def _clear_sound(self, key):
        self.app.cfg[f"sound_{key}"] = ""
        save_settings(self.app.cfg)
        self.refresh_sounds()

    def _preview_sound(self, key):
        if self._previewing == key:                  # 한 번 더 누르면 멈춤
            stop_sound_file()
            self._previewing = None
        else:
            self._previewing = key
            if not play_sound_file(self.app.cfg.get(f"sound_{key}")):
                play_done_sound(self.app.root, times=1)
                self._previewing = None
        self.refresh_sounds()

    def refresh_sounds(self):
        t, px = self.app.colors, self.app.px
        bg = t["bg"]
        for key, (row, dot, lb, pick, play, clr) in self.sound_rows.items():
            col = self.app.cfg["accent"] if key == "accent" else t["gray"]
            ph = ImageTk.PhotoImage(render_dot(px(12), col, False, col, bg))
            dot._ph = ph
            dot.configure(image=ph, bg=bg)
            row.configure(bg=bg)
            lb.configure(bg=bg, fg=t["sub"])
            path = self.app.cfg.get(f"sound_{key}") or ""
            name = os.path.basename(path) if path else "기본음"
            if len(name) > 16:
                name = name[:13] + "…"
            pick.configure(text=name, anchor="w")
            pick.set_colors(t["btn"], t["btn_hover"], t["text"] if path else t["sub"])
            s = px(24)
            kind = "pause" if self._previewing == key else "play"
            play.set_images(render_icon(kind, s, t["sub"], bg, circle=t["btn"]),
                            render_icon(kind, s, t["text"], bg, circle=t["btn_hover"]), bg)
            clr.set_images(render_icon("close", s, mix(t["sub"], bg, 0.35) if path else bg, bg),
                           render_icon("close", s, t["text"] if path else bg, bg, circle=t["btn"] if path else None), bg)
        self.sound_box.configure(bg=bg)
        self.lbl_sound.configure(bg=bg, fg=t["text"])

    def _close_if_outside(self):
        try:
            if not self.is_shown() or self._dialog_open or getattr(self.app, "_overlay_fading", False):
                return
            f = self.focus_get()
            if f is None or not str(f).startswith(str(self)):
                try:
                    under = self.winfo_containing(*self.winfo_pointerxy())
                except (tk.TclError, KeyError):
                    under = None
                if under is self.app.btn_reset:        # 리셋 버튼은 예외: 패널 유지
                    self.after(120, self.focus_force)
                    return
                if under is self.app.btn_theme:        # 테마 버튼도 예외 (페이드가 끝난 뒤 다시 포커스)
                    self.after(FADE_MS + 200, self.focus_force)
                    return
                self.withdraw()
                self.app._panel_closed_at = time.monotonic()
                self.app._update_controls()
        except (tk.TclError, KeyError):
            pass

    # + 아이콘
    def _show_plus(self, v):
        if v and self.locked:
            v = False
        if v == self._plus_shown:
            return
        self._plus_shown = v
        self._draw_plus()

    def _check_plus(self):
        try:
            w = self.winfo_containing(*self.winfo_pointerxy())
        except (tk.TclError, KeyError):
            w = None
        self._show_plus(w in (self.add_zone, self.plus))

    def _draw_plus(self):
        t, px = self.app.colors, self.app.px
        bg = t["bg"]
        if self._plus_shown:
            img = render_icon("plus", px(26), t["sub"], bg)
        else:
            img = Image.new("RGB", (px(26), px(26)), bg)
        self._plus_photo = ImageTk.PhotoImage(img)
        self.plus.configure(image=self._plus_photo, bg=bg)

    # 모양
    def _is_current(self, row):
        a = self.app
        if not a.interval_active():
            return False
        return a.intervals[a.seg_idx][2] == self.rows.index(row)

    def refresh_state(self):
        """번호/현재 구간 표시, 스위치, 합계만 새로 그림 (가벼움)"""
        t, px = self.app.colors, self.app.px
        bg = t["bg"]
        for i, r in enumerate(self.rows):
            cur = self._is_current(r)
            r.lbl_idx.configure(text="▶" if cur else str(i + 1), fg=t["text"] if cur else t["sub"])
        sw_w, sw_h = px(36), px(20)
        off = mix(t["border"], bg, 0.1)
        self._sw1 = ImageTk.PhotoImage(render_switch(sw_w, sw_h, bool(self.app.cfg.get("interval_on")),
                                                     self.app.cfg["accent"], off, bg))
        self._sw2 = ImageTk.PhotoImage(render_switch(sw_w, sw_h, bool(self.app.cfg.get("interval_loop")),
                                                     self.app.cfg["accent"], off, bg))
        self.sw_on.configure(image=self._sw1, bg=bg)
        self.sw_loop.configure(image=self._sw2, bg=bg)
        segs = self.app.intervals
        total = sum(x[1] for x in segs)
        self.lbl_sum.configure(text=f"{len(segs)}개 · 합계 {format_time(total)}" if segs else "")

    def apply_theme(self):
        t = self.app.colors
        bg = t["bg"]
        self.configure(bg=bg)
        for w in (self.head, self.foot, self.inner, self.add_zone):
            w.configure(bg=bg)
        self.lbl_title.configure(bg=bg, fg=t["text"])
        for lb in (self.lbl_on, self.lbl_loop, self.lbl_sum):
            lb.configure(bg=bg, fg=t["sub"])
        for r in self.rows:
            r.apply_theme(self._is_current(r))
        self.refresh_sounds()
        self._draw_plus()
        self.refresh_state()
        if not getattr(self.app, "_overlay_fading", False):
            style_titlebar(self, self.app.cfg["theme"] == "dark", bg, flush=False)

    def show(self):
        self.apply_theme()
        r, px = self.app.root, self.app.px
        self.geometry(f"{px(280)}x{r.winfo_height()}+{r.winfo_rootx() + r.winfo_width()}+{r.winfo_rooty()}")
        self.deiconify()
        self.update_idletasks()
        style_titlebar(self, self.app.cfg["theme"] == "dark", self.app.colors["bg"])
        snap_beside(self, r, match_height=True)

    def is_shown(self):
        try:
            return self.winfo_exists() and self.state() == "normal"
        except tk.TclError:
            return False


# ─────────────────────────── 앱 ───────────────────────────

class RingTimerApp:
    def __init__(self, root):
        self.root = root
        self.cfg = load_settings()
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)

        self.duration = max(0, int(self.cfg["duration"]))
        self.remaining = float(self.duration)
        self.running = False
        self.end_time = 0.0
        self.finished = False
        self.blink_on = False
        self._blink_left = 0
        self._ring_key = None
        self._ring_photo = None
        self._dot_photos = []
        self._fade_job = None
        self._fading = False
        self.colors = dict(THEMES[self.cfg["theme"]])
        self.infinite = bool(self.cfg.get("infinite"))
        self.elapsed = 0.0           # 무한 모드에서 센 시간
        self.start_mono = 0.0
        self.mini = False
        self._mini_side = 0
        self._square_job = None

        families = set(tkfont.families(root))
        self.ui_family = next((f for f in ("Malgun Gothic", "맑은 고딕", "Segoe UI", "Apple SD Gothic Neo",
                                           "AppleSDGothicNeo-Regular") if f in families), "TkDefaultFont")
        self.num_family = next((f for f in ("Segoe UI", "Bahnschrift", "SF Pro Display", "Helvetica Neue",
                                            "Arial") if f in families), self.ui_family)

        self.f_time = tkfont.Font(root, family=self.num_family, size=-40, weight="bold")
        self.f_pct = tkfont.Font(root, family=self.num_family, size=-16, weight="bold")
        self.f_small_c = tkfont.Font(root, family=self.num_family, size=-12)
        self.f_total = tkfont.Font(root, family=self.num_family, size=11, weight="bold")
        self.f_ui = tkfont.Font(root, family=self.ui_family, size=10)
        self.f_ui_bold = tkfont.Font(root, family=self.ui_family, size=11, weight="bold")
        self.f_small = tkfont.Font(root, family=self.ui_family, size=9)
        self.f_input = tkfont.Font(root, family=self.num_family, size=18, weight="bold")
        self.f_row = tkfont.Font(root, family=self.num_family, size=13, weight="bold")

        self.intervals = []          # [(이름, 초, 줄 번호, 색)]
        self.seg_idx = 0
        self.interval_panel = None
        self._parse_intervals()

        self._build_ui()
        self.apply_theme()
        self._set_entries(0 if self.infinite else self.duration)   # 무한 모드에선 입력칸을 비워 둠
        if self.interval_active():
            self._load_segment(0)
        self._update_controls()

        root.title("Ring Timer")
        root.minsize(self.px(330), self.px(470))
        geo = self.cfg.get("geometry") or ""
        root.geometry(geo if re.fullmatch(r"\d+x\d+[+-]-?\d+[+-]-?\d+", geo) else f"{self.px(380)}x{self.px(540)}")
        root.attributes("-topmost", bool(self.cfg.get("topmost")))
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        if self.cfg.get("mini"):
            self.set_mini(True, startup=True)

        try:
            self._icon_key = None
            self._update_app_icon(1.0)
        except Exception:
            pass

        root.bind("<Configure>", self._on_root_configure, add="+")
        root.bind("<space>", self._key_toggle)
        root.bind("<KeyPress-r>", self._key_reset)
        root.bind("<KeyPress-R>", self._key_reset)
        root.bind("<Escape>", lambda e: self.set_mini(False))
        self._tick()
        self._chip_poll()

    def px(self, n):
        return int(round(n * self.scale))

    # ── UI 구성 ──
    def _build_ui(self):
        r = self.root
        pad = self.px(16)

        # 맨 위: 테마 아이콘(왼쪽) / 항상 위 아이콘(오른쪽)
        self.row_top = tk.Frame(r)
        self.btn_theme = IconButton(self.row_top, self.toggle_theme)
        self.btn_theme.pack(side="left")
        self.btn_inf = IconButton(self.row_top, lambda: self.set_infinite(not self.infinite))
        self.btn_inf.pack(side="left")
        self.btn_pin = IconButton(self.row_top, self.toggle_topmost)
        self.btn_pin.pack(side="right")
        self.btn_mini = IconButton(self.row_top, lambda: self.set_mini(True))
        self.btn_mini.pack(side="right")
        self.lbl_total = tk.Label(self.row_top, text="", font=self.f_total)
        self.lbl_total.place(relx=0.5, rely=0.5, anchor="center")

        self.canvas = tk.Canvas(r, highlightthickness=0, bd=0, cursor="arrow")
        self.canvas.pack(fill="both", expand=True, padx=pad, pady=(self.px(2), self.px(4)))
        # 미니 모드에서 창에 마우스를 올리면 오른쪽 위에 나타나는 '원래 크기로' 버튼
        self.btn_restore = IconButton(r, lambda: self.set_mini(False))
        self.ring_item = self.canvas.create_image(0, 0, anchor="center")
        self.disc_item = self.canvas.create_image(0, 0, anchor="center", state="hidden")   # 마우스 올리면 옅은 원
        self._hover_alpha = 0.0
        self._hover_target = 0.0
        self._hover_job = None
        self._disc_key = None
        self._ring_geom = None
        self.time_item = self.canvas.create_text(0, 0, text="", font=self.f_time, anchor="center")
        self.pct_item = self.canvas.create_text(0, 0, text="", font=self.f_pct, anchor="center")
        self.total_item = self.canvas.create_text(0, 0, text="", font=self.f_small_c, anchor="center")
        self.canvas.bind("<Configure>", lambda e: (self.redraw(force=True), self._request_gap()))
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<Leave>", lambda e: self._set_ring_hover(False))
        self.canvas.bind("<MouseWheel>", self._on_wheel)                       # Windows
        self.canvas.bind("<Button-4>", lambda e: self._adjust_minutes(+1))     # Linux
        self.canvas.bind("<Button-5>", lambda e: self._adjust_minutes(-1))

        # 시간 입력  HH : MM : SS (루틴과 같은 방식: 숫자를 칸 그림에 직접 그림)
        self.row_input = tk.Frame(r)
        self.tf = TimeFields(self.row_input, self, PLACEHOLDERS, self.px(66), self.px(50), self.f_input,
                             on_change=self._apply_fields, on_enter=self._on_enter,
                             focus_color=self.ring_color)
        self.tf.pack()
        self.time_entries = self.tf.entries

        # 재생/일시정지 (가운데) + 리셋 (왼쪽)
        self.row_ctrl = tk.Frame(r)
        self.flex_gap = tk.Frame(r, height=1)                   # 창을 늘리면 벌어지는 여백 (버튼 ↔ 입력칸)
        self.flex_gap.pack_propagate(False)
        self._pack_rows()
        self.btn_reset = IconButton(self.row_ctrl, self.reset)
        self.btn_reset.grid(row=0, column=0, padx=self.px(14))
        self.btn_play = IconButton(self.row_ctrl, self.toggle)
        self.btn_play.grid(row=0, column=1)
        self.btn_menu = IconButton(self.row_ctrl, self.toggle_interval_panel)
        self.btn_menu.grid(row=0, column=2, padx=self.px(14))

        # 컬러칩 줄 (평소엔 숨김 → 창 아래쪽에 마우스를 대면 나타남)
        self.chip_panel = tk.Canvas(r, highlightthickness=0, bd=0, cursor="hand2")
        self._chip_bg_item = self.chip_panel.create_image(0, 0, anchor="nw")
        self._chip_items = []
        self._chip_plus = None
        self._plus_hover = False
        self._panel_size = (self.px(100), self.px(40))
        self._chip_alpha = 0.0
        self._rebuild_dots()

    def _pack_rows(self):
        """원 둘레의 줄들(위 아이콘 / 버튼 / 입력칸)을 원래 자리에 배치"""
        c = self.canvas
        self.row_top.pack(fill="x", padx=self.px(10), pady=(self.px(8), 0), before=c)
        self.row_input.pack(side="bottom", pady=(self.px(14), self.px(34)), before=c)
        self.flex_gap.pack(side="bottom", fill="x", before=c)
        self.row_ctrl.pack(side="bottom", pady=(self.px(6), 0), before=c)

    # ── 미니 모드 ──
    def set_mini(self, on, startup=False):
        """미니 모드: 원 + 시간만 보이는 정사각형 창"""
        if on == self.mini:
            return
        r = self.root
        r.update_idletasks()
        geo_re = r"(\d+)x(\d+)([+-]-?\d+[+-]-?\d+)"
        m = re.fullmatch(geo_re, r.geometry())
        pos = m.group(3) if m else ""
        self.mini = on
        if on:
            if not startup:
                self.cfg["geometry"] = r.geometry()
            p = self.interval_panel
            if p is not None and p.is_shown():
                p.withdraw()
            self._chip_alpha = 0.0
            self.chip_panel.place_forget()
            for w in (self.row_top, self.row_input, self.flex_gap, self.row_ctrl):
                w.pack_forget()
            self.canvas.pack_configure(padx=self.px(10), pady=self.px(10))
            r.minsize(self.px(150), self.px(150))
            mg = re.fullmatch(geo_re, self.cfg.get("mini_geometry") or "")
            side = int(mg.group(1)) if mg else self.px(240)
            if startup and mg:
                pos = mg.group(3)
            self._mini_side = side
            r.geometry(f"{side}x{side}{pos}")
        else:
            self.cfg["mini_geometry"] = r.geometry()
            self.btn_restore._set_hover(False)
            self.btn_restore.place_forget()
            self.canvas.pack_configure(padx=self.px(16), pady=(self.px(2), self.px(4)))
            self._pack_rows()
            r.minsize(self.px(330), self.px(470))
            mg = re.fullmatch(geo_re, self.cfg.get("geometry") or "")
            size = f"{mg.group(1)}x{mg.group(2)}" if mg else f"{self.px(380)}x{self.px(540)}"
            r.geometry(size + pos)
        self.cfg["mini"] = on
        save_settings(self.cfg)

    def _make_square(self):
        """미니 모드에서 창 크기를 바꾸면 정사각형으로 맞춤"""
        self._square_job = None
        r = self.root
        if not self.mini or r.state() == "zoomed":
            return
        w, h = r.winfo_width(), r.winfo_height()
        if w != h:
            side = w if w != self._mini_side else h          # 바뀐 쪽 길이에 맞춤
            m = re.search(r"[+-]-?\d+[+-]-?\d+$", r.geometry())
            r.geometry(f"{side}x{side}{m.group(0) if m else ''}")
            w = side
        self._mini_side = w

    # ── 테마 ──
    def apply_theme(self, colors=None, titlebar=True):
        t = dict(colors or THEMES[self.cfg["theme"]])
        self.colors = t
        accent = self.cfg["accent"]
        bg = t["bg"]
        self.root.configure(bg=bg)
        for w in (self.row_top, self.row_input, self.row_ctrl, self.flex_gap):
            w.configure(bg=bg)
        self.canvas.configure(bg=bg)
        self.tf.apply_theme(bg)
        self.canvas.itemconfigure(self.time_item, fill=t["text"])
        self.canvas.itemconfigure(self.pct_item, fill=t["sub"])
        self.canvas.itemconfigure(self.total_item, fill=t["sub"])
        self.lbl_total.configure(bg=bg, fg=t["sub"])

        # 아이콘들
        s = self.px(34)
        kind = "sun" if self.cfg["theme"] == "dark" else "moon"
        self.btn_theme.set_images(render_icon(kind, s, t["sub"], bg),
                                  render_icon(kind, s, t["text"], bg, circle=t["btn"]), bg)
        self._update_pin_icon()
        self.btn_mini.set_images(render_icon("mini", s, t["sub"], bg),
                                 render_icon("mini", s, t["text"], bg, circle=t["btn"]), bg)
        s = self.px(30)
        self.btn_restore.set_images(render_icon("expand", s, t["sub"], bg, circle=t["btn"]),
                                    render_icon("expand", s, t["text"], bg, circle=t["btn_hover"]), bg)
        s = self.px(44)
        self.btn_reset.set_images(render_icon("reset", s, t["text"], bg, circle=t["btn"]),
                                  render_icon("reset", s, t["text"], bg, circle=t["btn_hover"]), bg)
        self._update_controls()
        if not (self._fading and self._chip_alpha <= 0):
            self._draw_color_dots()
        p = self.interval_panel
        if p is not None and p.winfo_exists() and (p.is_shown() or not self._fading):
            p.apply_theme()
        # 모든 색을 바꾼 뒤 한 번에 그리고 → 제목 표시줄을 마지막에 (배경/원이 따로 놀지 않게)
        if titlebar:
            self.root.update_idletasks()
            style_titlebar(self.root, self.cfg["theme"] == "dark", bg, flush=False)
        self.redraw(force=True)

    def _update_pin_icon(self):
        t = self.colors
        bg = t["bg"]
        s = self.px(34)
        for btn, kind, on in ((self.btn_pin, "pin", bool(self.cfg.get("topmost"))),
                              (self.btn_inf, "infinite", self.infinite)):
            fg = self.cfg["accent"] if on else t["sub"]
            btn.set_images(render_icon(kind, s, fg, bg),
                           render_icon(kind, s, fg if on else t["text"], bg, circle=t["btn"]), bg)

    def palette(self):
        return [c.upper() for c in PRESET_COLORS] + self.cfg["custom_colors"]

    def _rebuild_dots(self):
        """팔레트가 바뀌었을 때 컬러칩을 다시 배치"""
        c = self.chip_panel
        for it, _ in self._chip_items:
            c.delete(it)
        if self._chip_plus:
            c.delete(self._chip_plus)
            self._chip_plus = None
        self._chip_items = []
        d, gap, pad, H = self.px(20), self.px(4), self.px(14), self.px(40)
        cols = self.palette()
        custom = self.cfg["custom_colors"]
        show_plus = len(custom) < MAX_CUSTOM
        n = len(cols) + (1 if show_plus else 0)
        W = pad * 2 + n * (d + gap) - gap
        self._panel_size = (W, H)
        c.configure(width=W, height=H)
        x = pad + d / 2
        for col in cols:
            it = c.create_image(x, H / 2, anchor="center")
            c.tag_bind(it, "<Button-1>", lambda e, col=col: self.set_accent(col))
            if col in custom:                                  # 직접 추가한 색만 편집/삭제
                c.tag_bind(it, "<Button-3>", lambda e, col=col: self.edit_color_dialog(col))
                c.tag_bind(it, "<Button-2>", lambda e, col=col: self.edit_color_dialog(col))           # 맥
                c.tag_bind(it, "<Control-Button-1>", lambda e, col=col: self.edit_color_dialog(col))   # 맥
                c.tag_bind(it, "<Double-Button-1>", lambda e, col=col: self.edit_color_dialog(col))
            self._chip_items.append((it, col))
            x += d + gap
        if show_plus:
            self._chip_plus = c.create_image(x, H / 2, anchor="center")
            c.tag_bind(self._chip_plus, "<ButtonRelease-1>", lambda e: self.add_color_dialog())
            c.tag_bind(self._chip_plus, "<Enter>", lambda e: self._set_plus_hover(True))
            c.tag_bind(self._chip_plus, "<Leave>", lambda e: self._set_plus_hover(False))
        self._draw_color_dots()

    def _set_plus_hover(self, v):
        self._plus_hover = v
        self._draw_color_dots()

    def _draw_color_dots(self):
        t = self.colors
        a = self._chip_alpha                      # 0 = 안 보임, 1 = 완전히 보임
        panel_bg = mix(t["bg"], t["entry"], a)
        c = self.chip_panel
        c.configure(bg=t["bg"])
        W, H = self._panel_size
        self._panel_photo = ImageTk.PhotoImage(render_round_box(
            W, H, H // 2, panel_bg, mix(t["bg"], t["border"], a), self.px(1), t["bg"]))
        c.itemconfigure(self._chip_bg_item, image=self._panel_photo)
        size = self.px(20)
        accent = self.cfg["accent"].upper()
        ring = mix(panel_bg, t["text"], a)
        photos = []
        for it, col in self._chip_items:
            ph = ImageTk.PhotoImage(render_dot(size, mix(panel_bg, col, a), col == accent, ring, panel_bg))
            c.itemconfigure(it, image=ph)
            photos.append(ph)
        if self._chip_plus:
            fg = t["text"] if self._plus_hover else t["sub"]
            ph = ImageTk.PhotoImage(render_icon("plus", size, mix(panel_bg, fg, a), panel_bg))
            c.itemconfigure(self._chip_plus, image=ph)
            photos.append(ph)
        self._dot_photos = photos

    def _chip_poll(self):
        """창 맨 아래로 마우스를 가져가면 컬러칩 패널이 입력칸 위에 겹쳐서 나타남"""
        try:
            r = self.root
            x, y = r.winfo_pointerxy()
            rx, ry, w, h = r.winfo_rootx(), r.winfo_rooty(), r.winfo_width(), r.winfo_height()
            in_window = rx <= x < rx + w and ry <= y < ry + h
            if self.mini:                     # 미니 모드: 컬러칩 대신 '원래 크기로' 버튼만
                shown = self.btn_restore.winfo_ismapped()
                if in_window and not shown:
                    self.btn_restore.place(relx=1.0, x=-self.px(6), y=self.px(6), anchor="ne")
                    self.btn_restore.lift()
                elif not in_window and shown:
                    self.btn_restore.place_forget()
                self.root.after(35, self._chip_poll)
                return
            boxes_bottom = ry + self.row_input.winfo_y() + self.row_input.winfo_height()
            boxes_mid = ry + self.row_input.winfo_y() + self.row_input.winfo_height() // 2
            # 입력칸 아래 절반 ~ 창 맨 아래, 또는 창 맨 아래 띠에 마우스가 오면 표시
            in_strip = in_window and (y >= boxes_mid or y >= ry + h - self.px(30))
            over_panel = False
            if self.chip_panel.winfo_ismapped():
                px_, py_ = self.chip_panel.winfo_rootx(), self.chip_panel.winfo_rooty()
                m = self.px(18)
                over_panel = (px_ - m <= x < px_ + self.chip_panel.winfo_width() + m and
                              py_ - m <= y < py_ + self.chip_panel.winfo_height() + m)
            target = 1.0 if (in_strip or over_panel) else 0.0
            if abs(self._chip_alpha - target) > 1e-3:
                step = 0.25 if target > self._chip_alpha else -0.2
                self._chip_alpha = round(min(1.0, max(0.0, self._chip_alpha + step)), 3)
                if self._chip_alpha > 0 and not self.chip_panel.winfo_ismapped():
                    self.chip_panel.place(relx=0.5, rely=1.0, y=-self.px(8), anchor="s")
                    self.chip_panel.lift()
                self._draw_color_dots()
                if self._chip_alpha <= 0:
                    self.chip_panel.place_forget()
        except tk.TclError:
            pass
        self.root.after(35, self._chip_poll)

    def toggle_theme(self):
        """라이트 ↔ 다크 전환 (색이 부드럽게 바뀌는 페이드 효과)"""
        start = dict(self.colors)
        self.cfg["theme"] = "dark" if self.cfg["theme"] == "light" else "light"
        save_settings(self.cfg)
        target = THEMES[self.cfg["theme"]]
        if self._fade_job:
            self.root.after_cancel(self._fade_job)
            self._fade_job = None
        self._clear_overlays()
        # ① 지금 화면(메인 창 + 열린 루틴 패널)을 찍어서 창 위에 덮기
        wins = [self.root]
        p = self.interval_panel
        if p is not None and p.winfo_exists() and p.is_shown():
            wins.append(p)
        overlays = []
        for w in wins:
            shot = self._grab_window(w)
            if shot is None:
                break
            overlays.append(self._make_overlay(*shot))
        if len(overlays) == len(wins):
            self._overlay_fading = True
            self.root.update_idletasks()
            # ② 덮인 아래에서 새 테마를 한 번에 적용
            self._fading = False
            self.apply_theme(target, titlebar=False)
            self._retheme_picker()
            self.root.update_idletasks()
            # ③ 덮은 그림을 서서히 투명하게 → 화면 전체가 동시에 바뀜
            self._fade_overlays(overlays, start, target, wins, time.perf_counter())
            return
        for ov in overlays:
            ov.destroy()
        # (스크린샷을 못 찍는 환경이면 예전 방식)
        self.root.after(FADE_MS + 30, self._retheme_picker)
        self._prepare_fade_ring(start, target)
        self._fade(start, target, time.perf_counter())

    def _grab_window(self, win):
        """창 안쪽 화면을 그대로 찍음 → (그림, (x, y, w, h))"""
        if IS_MAC:
            return None
        try:
            from PIL import ImageGrab
            win.update_idletasks()
            x, y, w, h = win.winfo_rootx(), win.winfo_rooty(), win.winfo_width(), win.winfo_height()
            if w < 10 or h < 10:
                return None
            if sys.platform == "win32":
                img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
            else:
                img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
            if img.size != (w, h):
                img = img.resize((w, h))
            return img.convert("RGB"), (x, y, w, h)
        except Exception:
            return None

    def _make_overlay(self, img, rect):
        x, y, w, h = rect
        ov = tk.Toplevel(self.root)
        ov.overrideredirect(True)
        try:
            ov.attributes("-topmost", True)
        except tk.TclError:
            pass
        ov.geometry(f"{w}x{h}+{x}+{y}")
        ph = ImageTk.PhotoImage(img)
        lb = tk.Label(ov, image=ph, bd=0, highlightthickness=0)
        lb.image = ph
        lb.place(x=0, y=0, width=w, height=h)
        self._overlays = getattr(self, "_overlays", []) + [ov]
        return ov

    def _clear_overlays(self):
        for ov in getattr(self, "_overlays", []):
            try:
                ov.destroy()
            except tk.TclError:
                pass
        self._overlays = []
        self._overlay_fading = False

    def _fade_overlays(self, overlays, start, target, wins, t0):
        k = min(1.0, (time.perf_counter() - t0) * 1000 / FADE_MS)
        k = k * k * (3 - 2 * k)
        dark = self.cfg["theme"] == "dark"
        cap = mix(start["bg"], target["bg"], k)
        for w in wins:                                   # 제목 표시줄 색도 같이
            style_titlebar(w, dark, cap, flush=False)
        if k < 1.0:
            for ov in overlays:
                try:
                    ov.attributes("-alpha", 1.0 - k)
                except tk.TclError:
                    pass
            self._fade_job = self.root.after(15, self._fade_overlays, overlays, start, target, wins, t0)
        else:
            self._fade_job = None
            self._clear_overlays()

    def _prepare_fade_ring(self, start, target):
        c = self.canvas
        size = max(60, min(c.winfo_width(), c.winfo_height()))
        frac = (self.remaining / self.duration) if self.duration > 0 else 0.0
        if self.finished:
            frac = 1.0 if self.blink_on else 0.0
        gray_seg = self.interval_active() and self.intervals[self.seg_idx][3] == "gray"
        ca = start["gray"] if gray_seg else self.cfg["accent"]
        cb = target["gray"] if gray_seg else self.cfg["accent"]
        self._fade_imgs = (size, render_ring(size, frac, start["track"], ca, start["bg"]),
                           render_ring(size, frac, target["track"], cb, target["bg"]))

    def _fade(self, start, target, t0):
        k = min(1.0, (time.perf_counter() - t0) * 1000 / FADE_MS)
        k = k * k * (3 - 2 * k)  # 처음과 끝을 부드럽게 (smoothstep)
        self._fading = k < 1.0
        self._fade_k = k
        self.apply_theme({key: mix(start[key], target[key], k) for key in target})
        if self._fading:
            self._fade_job = self.root.after(15, self._fade, start, target, t0)
        else:
            self._fade_job = None
            self._fade_imgs = None

    def set_accent(self, color):
        if self.cfg["accent"] == color.upper():
            return
        self.cfg["accent"] = color.upper()
        self.apply_theme()
        save_settings(self.cfg)

    def _open_picker(self, **kw):
        self._picker_args = kw
        self._picker = ColorPicker(self, **kw)

    def _retheme_picker(self):
        """색 선택 창이 열려 있으면 새 테마로 다시 열기 (고르던 색 유지)"""
        pk = getattr(self, "_picker", None)
        if pk is None:
            return
        try:
            if not pk.winfo_exists():
                return
            cur = pk.color()
            pk.destroy()
        except tk.TclError:
            return
        self._open_picker(**dict(self._picker_args, initial=cur))

    def add_color_dialog(self):
        self._open_picker(initial=self.cfg["accent"], mode="add", on_ok=self.add_color)

    def edit_color_dialog(self, col):
        self._open_picker(initial=col, mode="edit",
                    on_ok=lambda new: self.replace_color(col, new),
                    on_delete=lambda: self.delete_color(col))

    def add_color(self, col):
        col = col.upper()
        custom = self.cfg["custom_colors"]
        if col not in self.palette() and len(custom) < MAX_CUSTOM:
            custom.append(col)
        if col in self.palette():
            self.cfg["accent"] = col
        self._palette_changed()

    def replace_color(self, old, new):
        new = new.upper()
        custom = self.cfg["custom_colors"]
        if old in custom:
            if new in self.palette() and new != old:
                custom.remove(old)       # 같은 색이 이미 있으면 합치기
            else:
                custom[custom.index(old)] = new
        if self.cfg["accent"] == old:
            self.cfg["accent"] = new
        self._palette_changed()

    def delete_color(self, col):
        custom = self.cfg["custom_colors"]
        if col in custom:
            custom.remove(col)
            if self.cfg["accent"] == col:
                self.cfg["accent"] = self.palette()[0]
        self._palette_changed()

    def _palette_changed(self):
        save_settings(self.cfg)
        self._rebuild_dots()
        self.apply_theme()

    # ── 인터벌 ──
    def interval_active(self):
        return not self.infinite and bool(self.cfg.get("interval_on")) and len(self.intervals) > 0

    # ── 무한 모드 ──
    def set_infinite(self, on):
        """무한 모드: 0부터 위로 세는 스톱워치. 원은 꽉 찬 채로 깜빡임"""
        stop_sound_file()
        self.infinite = bool(on)
        self.cfg["infinite"] = self.infinite
        save_settings(self.cfg)
        self.running = False
        self.finished = False
        self._blink_left = 0
        self.elapsed = 0.0
        if self.infinite:
            self._set_entries(0)
        else:                                          # 원래 타이머(또는 루틴)로 돌아감
            if self.interval_active():
                self._load_segment(0)
            else:
                self.duration = max(0, int(self.cfg["duration"]))
                self._set_entries(self.duration)
                self.remaining = float(self.duration)
        self._update_pin_icon()
        self._update_controls()
        self._refresh_interval_ui()
        self.redraw(force=True)

    def side_anchor(self):
        """옆에 붙일 기준 창 (인터벌 패널이 열려 있으면 그 옆)"""
        p = self.interval_panel
        return p if (p is not None and p.is_shown()) else self.root

    def _parse_intervals(self):
        self.intervals = [("", r["secs"], i, r["color"])
                          for i, r in enumerate(self.cfg.get("intervals", [])) if r["secs"] > 0]

    def on_intervals_changed(self, rows):
        was = self.interval_active()
        self.cfg["intervals"] = rows
        save_settings(self.cfg)
        self._parse_intervals()
        self._interval_changed(was)

    def jump_to_row(self, row_idx):
        if not self.cfg.get("interval_on"):
            return
        for k, seg in enumerate(self.intervals):
            if seg[2] == row_idx:
                self._load_segment(k, autostart=False)   # 그 구간 처음 시간에서 멈춘 상태
                return

    def ring_color(self):
        """지금 원 색 (인터벌 구간이 '회색'이면 회색)"""
        if self.interval_active() and self.intervals[self.seg_idx][3] == "gray":
            return self.colors["gray"]
        return self.cfg["accent"]

    def set_interval_on(self, v):
        was = self.interval_active()
        self.cfg["interval_on"] = bool(v)
        save_settings(self.cfg)
        self._interval_changed(was)

    def set_interval_loop(self, v):
        self.cfg["interval_loop"] = bool(v)
        save_settings(self.cfg)
        self._refresh_interval_ui()

    def _interval_changed(self, was_active):
        active = self.interval_active()
        idle = not self.running and (self.finished or self.remaining >= self.duration)
        if active and not was_active:
            self._load_segment(0)                      # 인터벌을 켜면 1번 구간부터
        elif active:
            self.seg_idx = min(self.seg_idx, len(self.intervals) - 1)
            if idle:
                self._load_segment(self.seg_idx)       # 멈춰 있을 때만 바로 반영
        elif was_active:
            self.duration = int(self.cfg["duration"])  # 인터벌을 끄면 원래 타이머로
            self._set_entries(self.duration)
            self.reset()
        self._update_controls()
        self._refresh_interval_ui()
        self.redraw(force=True)

    def _load_segment(self, idx, autostart=False):
        self.seg_idx = idx
        self.duration = self.intervals[idx][1]
        self._set_entries(self.duration)
        self.running = False
        self.finished = False
        self._blink_left = 0
        self.remaining = float(self.duration)
        if autostart:
            self.start()
        else:
            self._update_controls()
            self.redraw(force=True)
        self._refresh_interval_ui()

    def _refresh_interval_ui(self):
        p = self.interval_panel
        if p is None or not p.winfo_exists():
            return
        p.refresh_state()

    def toggle_interval_panel(self):
        p = self.interval_panel
        # ☰ 버튼을 눌러서 패널이 방금 닫힌 경우엔 다시 열지 않음
        if time.monotonic() - getattr(self, "_panel_closed_at", 0) < 0.4:
            return
        if p is not None and p.is_shown():
            p.withdraw()
        else:
            if p is None or not p.winfo_exists():
                self.interval_panel = p = IntervalPanel(self)
            p.show()
            self._refresh_interval_ui()
            p.focus_first()
        self._update_controls()

    def _balance_gap(self):
        """남는 세로 공간을 '원 위'와 '버튼 ↔ 입력칸 사이'에 반씩 나눔"""
        self._gap_pending = False
        if self.mini:
            return
        try:
            total = self.canvas.winfo_height() + self.flex_gap.winfo_height()
            ring = min(self.canvas.winfo_width(), total)
            want = max(1, int((total - ring) / 2))              # 0 은 Tk 가 무시하므로 최소 1
            if abs(want - self.flex_gap.winfo_reqheight()) > 1:
                self.flex_gap.configure(height=want)
        except tk.TclError:
            pass

    def _on_root_configure(self, e):
        if e.widget is not self.root:
            return
        if self.mini:
            if self._square_job:
                self.root.after_cancel(self._square_job)
            self._square_job = self.root.after(250, self._make_square)
        self._request_gap()

    def _request_gap(self):
        if not getattr(self, "_gap_pending", False):
            self._gap_pending = True
            self.root.after(15, self._balance_gap)
        p = self.interval_panel
        if p is not None and p.is_shown() and not getattr(self, "_snap_pending", False):
            self._snap_pending = True

            def later():
                self._snap_pending = False
                if p.is_shown():
                    snap_beside(p, self.root, match_height=True)
            self.root.after(30, later)

    def toggle_topmost(self):
        self.cfg["topmost"] = not self.cfg.get("topmost")
        self.root.attributes("-topmost", self.cfg["topmost"])
        self._update_pin_icon()
        save_settings(self.cfg)

    # ── 시간 입력 칸 (HH : MM : SS) ──
    def _field_values(self):
        return self.tf.values()

    def _set_entries(self, secs):
        self.tf.set_secs(secs)

    def _apply_fields(self):
        """입력 칸 값을 타이머 시간으로 반영 (실행 중이 아닐 때만)"""
        if self.running or self.interval_active() or self.infinite:
            return
        h, m, s = self._field_values()
        total = h * 3600 + m * 60 + s
        if total > 0 and total != self.duration:
            self.set_duration(total, update_entries=False)

    def _on_enter(self):
        if self.tf.active is not None:
            self.tf._deactivate(self.tf.active)
        self._apply_fields()
        self.canvas.focus_set()
        self.start()
        return "break"

    # ── 타이머 동작 ──
    def set_duration(self, secs, update_entries=True):
        self.duration = max(1, int(secs))
        self.cfg["duration"] = self.duration
        save_settings(self.cfg)
        if update_entries:
            self._set_entries(self.duration)
        self.reset()

    def start(self):
        if self.running:
            return
        if self.infinite:                              # 무한 모드: 멈춘 곳부터 이어서 셈
            self.start_mono = time.monotonic() - self.elapsed
            self.running = True
            self._update_controls()
            self.redraw(force=True)
            return
        if self.duration <= 0 and not self.interval_active():   # 시간을 아직 안 넣었으면 시작 안 함
            if self.tf.active is None:
                self.tf.activate(1)                              # 분 칸으로 바로 안내
            return
        fresh = self.remaining <= 0 or self.finished or self.remaining >= self.duration - 1e-6   # 구간 처음부터?
        if self.remaining <= 0 or self.finished:
            if self.interval_active():                 # 다 끝난 뒤 다시 시작 → 1번 구간부터
                self.seg_idx = 0
                self.duration = self.intervals[0][1]
                self._set_entries(self.duration)
                self._refresh_interval_ui()
            self.remaining = float(self.duration)
        self.finished = False
        self._blink_left = 0
        self.end_time = time.monotonic() + self.remaining
        self.running = True
        self._update_controls()
        self.redraw(force=True)
        if fresh:
            if self.interval_active():                       # 루틴: 구간이 시작될 때 그 구간 알림음
                self._play_sound_for(self.intervals[self.seg_idx][3], times=1)
            else:                                            # 일반 타이머: 시작할 때 집중 알림음
                self._play_sound_for("accent", times=1)

    def pause(self):
        if not self.running:
            return
        if self.infinite:
            self.elapsed = time.monotonic() - self.start_mono
        else:
            self.remaining = max(0.0, self.end_time - time.monotonic())
        self.running = False
        self._update_controls()
        self.redraw(force=True)

    def toggle(self):
        stop_sound_file()
        self.pause() if self.running else self.start()

    def reset(self):
        stop_sound_file()
        if self.interval_active():
            self.seg_idx = 0
            self.duration = self.intervals[0][1]
            self._set_entries(self.duration)
            self._refresh_interval_ui()
        self.running = False
        self.finished = False
        self._blink_left = 0
        self.remaining = float(self.duration)
        self.elapsed = 0.0
        self._update_controls()
        self.redraw(force=True)

    def _play_sound_for(self, kind, times=1):
        """kind = 'accent'(집중) / 'gray'(휴식) 알림음. 파일이 없으면 기본음"""
        if not self.cfg.get("sound", True):
            return
        if not play_sound_file(self.cfg.get(f"sound_{kind}") or ""):
            play_done_sound(self.root, times=times)

    def finish(self):
        n = len(self.intervals)
        if self.interval_active() and (self.seg_idx < n - 1 or self.cfg.get("interval_loop")):
            # 다음 구간으로 (시작할 때 그 구간 알림음이 울림)
            self._load_segment((self.seg_idx + 1) % n, autostart=True)
            return
        self._play_sound_for("gray", times=3)            # 끝나면 (루틴이든 아니든) 휴식 알림음
        self.running = False
        self.remaining = 0.0
        self.finished = True
        self._update_controls()
        try:
            self.root.deiconify()
            self.root.lift()
            if not self.cfg.get("topmost"):
                self.root.attributes("-topmost", True)
                self.root.after(1500, lambda: self.root.attributes("-topmost", bool(self.cfg.get("topmost"))))
        except Exception:
            pass
        self._blink_left = 10
        self._blink()

    def _blink(self):
        if self._blink_left <= 0 or not self.finished:
            self.blink_on = False
            self.redraw(force=True)
            return
        self.blink_on = not self.blink_on
        self._blink_left -= 1
        self.redraw(force=True)
        self.root.after(350, self._blink)

    def _update_controls(self):
        """재생/일시정지 아이콘 바꾸기 + 실행 중에는 시간 입력 잠그기"""
        t = self.colors
        accent = self.ring_color()
        rest = self.interval_active() and self.intervals[self.seg_idx][3] == "gray"
        if rest:   # 휴식 구간: 배경과 같은 톤의 회색 버튼
            dark = self.cfg["theme"] == "dark"
            accent = mix(t["bg"], t["text"], 0.15 if dark else 0.10)
            hover = mix(t["bg"], t["text"], 0.23 if dark else 0.16)
            fg = t["text"]
        else:
            hover = mix(accent, "#000000" if self.cfg["theme"] == "light" else "#FFFFFF", 0.12)
            fg = readable_fg(accent)
        kind = "pause" if self.running else "play"
        s = self.px(60)
        self.btn_play.set_images(render_icon(kind, s, fg, t["bg"], circle=accent),
                                 render_icon(kind, s, fg, t["bg"], circle=hover), t["bg"])
        self.tf.set_locked(self.running or self.interval_active() or self.infinite)   # 도는 중 / 루틴·무한 모드엔 입력 잠금
        p = self.interval_panel
        if p is not None and p.winfo_exists():
            p.set_locked(self.running)                                  # 재생 중엔 루틴 편집 잠금
        s = self.px(44)
        on = self.interval_active()
        opened = self.interval_panel is not None and self.interval_panel.is_shown()
        if on:   # 루틴 사용 중: 재생 버튼처럼 배경 = 지금 원 색
            self.btn_menu.set_images(render_icon("menu", s, fg, t["bg"], circle=accent),
                                     render_icon("menu", s, fg, t["bg"], circle=hover), t["bg"])
        else:
            self.btn_menu.set_images(render_icon("menu", s, t["text"], t["bg"], circle=t["btn_hover"] if opened else t["btn"]),
                                     render_icon("menu", s, t["text"], t["bg"], circle=t["btn_hover"]), t["bg"])

    def _tick(self):
        if self.running and self.infinite:
            self.elapsed = time.monotonic() - self.start_mono
            self.redraw()
        elif self.running:
            self.remaining = max(0.0, self.end_time - time.monotonic())
            if self.remaining <= 0:
                self.finish()
            else:
                self.redraw()
        self.root.after(30, self._tick)

    # ── 작업 표시줄 아이콘 ──
    def _update_app_icon(self, frac):
        """창/작업 표시줄 아이콘을 지금 원(색 + 남은 비율)으로 바꿈"""
        # 작업 표시줄 밝기에 맞춘 색 (앱 테마와 상관없이) — 20초마다 다시 확인
        now = time.monotonic()
        if now - getattr(self, "_tb_checked", -99) > 20:
            self._tb_checked = now
            self._tb_light = taskbar_is_light()
        light = self._tb_light
        rest = self.interval_active() and self.intervals[self.seg_idx][3] == "gray"
        if rest:
            color = "#6A6A74" if light else "#C8C8D0"       # 휴식 원: 밝은 바에선 진하게, 어두운 바에선 밝게
        else:
            color = self.cfg["accent"]
        track = "#D0D0D6" if light else "#4C4C55"           # 빈 원
        glass = "#7C7C86" if light else "#A6A6AE"           # 모래시계 (회색 고정)
        key = (round(frac * 60), color, track, glass)       # 6도 단위로만 새로 그림 (가볍게)
        if key == getattr(self, "_icon_key", None):
            return
        self._icon_key = key
        try:
            imgs = [ImageTk.PhotoImage(make_icon_image(sz, color, frac, track, glass)) for sz in (16, 32, 48, 64)]
            self.root.iconphoto(True, *imgs)
            self._icon_imgs = imgs
        except tk.TclError:
            pass

    # ── 원 영역 = 재생/일시정지 버튼 ──
    def _in_ring(self, x, y):
        g = self._ring_geom
        if not g:
            return False
        cx, cy, size = g
        return (x - cx) ** 2 + (y - cy) ** 2 <= (size / 2) ** 2

    def _on_canvas_click(self, e):
        self.canvas.focus_set()
        if self._in_ring(e.x, e.y):
            self.toggle()

    def _on_canvas_motion(self, e):
        inside = self._in_ring(e.x, e.y)
        self.canvas.configure(cursor="hand2" if inside else "arrow")
        self._set_ring_hover(inside)

    def _set_ring_hover(self, v):
        self._hover_target = 1.0 if v else 0.0
        if not self._hover_job:
            self._hover_step()

    def _hover_step(self):
        a, tgt = self._hover_alpha, self._hover_target
        if abs(a - tgt) < 1e-3:
            self._hover_job = None
            return
        self._hover_alpha = round(min(1.0, a + 0.25) if tgt > a else max(0.0, a - 0.25), 3)
        self._draw_disc()
        self._hover_job = self.root.after(16, self._hover_step)

    def _draw_disc(self):
        g = self._ring_geom
        c = self.canvas
        if not g or self._hover_alpha <= 0:
            c.itemconfigure(self.disc_item, state="hidden")
            return
        cx, cy, size = g
        t = self.colors
        d = max(10, int(size * (1 - 2 * RING_THICKNESS)) - self.px(10))
        col = mix(t["bg"], t["text"], 0.06 * self._hover_alpha)
        key = (d, col, t["bg"])
        if key != self._disc_key:
            self._disc_key = key
            self._disc_photo = ImageTk.PhotoImage(render_disc(d, col, t["bg"]))
            c.itemconfigure(self.disc_item, image=self._disc_photo)
        c.coords(self.disc_item, cx, cy)
        c.itemconfigure(self.disc_item, state="normal")

    def _on_wheel(self, e):
        self._adjust_minutes(1 if e.delta > 0 else -1)

    def _adjust_minutes(self, step):
        if self.infinite or self.interval_active() or self.running or (0 < self.remaining < self.duration and not self.finished):
            return
        self.set_duration(max(60, min(99 * 3600, (self.duration // 60 + step) * 60)))

    def _key_toggle(self, e):
        if isinstance(self.root.focus_get(), tk.Entry):
            return
        self.toggle()
        return "break"

    def _key_reset(self, e):
        if isinstance(self.root.focus_get(), tk.Entry):
            return
        self.reset()

    # ── 그리기 ──
    def redraw(self, force=False):
        c = self.canvas
        w, h = c.winfo_width(), c.winfo_height()
        if w < 20 or h < 20:
            return
        t = self.colors
        accent = self.ring_color()
        size = max(60, min(w, h))
        cx, cy = w / 2, (h / 2 if self.mini else h - size / 2)   # 평소엔 원을 캔버스 아래(버튼 쪽)에 붙임

        frac = (self.remaining / self.duration) if self.duration > 0 else 1.0   # 공란이면 꽉 찬 원
        if self.finished:
            frac = 1.0 if self.blink_on else 0.0
        shown = self.remaining
        pulse = 1.0
        if self.infinite:                     # 무한 모드: 꽉 찬 원이 도는 동안 천천히 흐려졌다 돌아옴
            frac, shown = 1.0, self.elapsed
            if self.running:
                k = math.cos(math.pi * self.elapsed / PULSE_SEC) ** 2      # 1 → 0 → 1 (ease-in-out)
                pulse = round(PULSE_LOW + (1 - PULSE_LOW) * k, 2)

        key = (size, round(frac * 1440), pulse, t["bg"], t["track"], accent)
        if force or key != self._ring_key:
            self._ring_key = key
            fi = getattr(self, "_fade_imgs", None)
            if self._fading and fi and fi[0] == size:
                img = Image.blend(fi[1], fi[2], self._fade_k)      # 미리 그린 두 장을 섞음
            elif self.infinite:
                img = render_full_ring(size, mix(t["track"], accent, pulse), t["bg"])
            else:
                img = render_ring(size, frac, t["track"], accent, t["bg"])
            self._ring_photo = ImageTk.PhotoImage(img)
            c.itemconfigure(self.ring_item, image=self._ring_photo)
            c.coords(self.ring_item, cx, cy)

        time_txt = format_time(shown, floor=True)
        ratio = 0.19 if len(time_txt) <= 5 else 0.145
        new_size = -max(12, int(size * ratio))
        if self.f_time.cget("size") != new_size:
            self.f_time.configure(size=new_size)
            self.f_pct.configure(size=-max(9, int(size * 0.075)))
            self.f_small_c.configure(size=-max(8, int(size * 0.05)))

        c.itemconfigure(self.time_item, text=time_txt)
        c.coords(self.time_item, cx, cy - size * 0.03)

        pct = int(math.floor(((self.remaining / self.duration) if self.duration else 0) * 100 + 1e-9))
        c.itemconfigure(self.pct_item, text="")

        cs = int((max(0.0, shown) % 1) * 100 + 1e-6) % 100        # 1/100 초
        c.itemconfigure(self.total_item, text=f".{cs:02d}")
        if self.infinite:
            top = "∞"
        elif self.interval_active():
            name, secs, _, _ = self.intervals[self.seg_idx]
            top = f"{self.seg_idx + 1}/{len(self.intervals)} · {name or format_time(secs)}"
        else:
            top = format_time(self.duration)
        self.lbl_total.configure(text=top)
        c.coords(self.total_item, cx, cy + size * 0.11)
        self._ring_geom = (cx, cy, size)
        self._update_app_icon(frac)
        if self._hover_alpha > 0:
            self._draw_disc()

    def on_close(self):
        try:
            self.cfg["mini_geometry" if self.mini else "geometry"] = self.root.geometry()
        except Exception:
            pass
        save_settings(self.cfg)
        self.root.destroy()


def run_selftest():
    """클라우드 맥에서 앱이 오류 없이 도는지 확인 (오류 0개면 0 반환)"""
    import tempfile
    import traceback
    errors = []
    os.environ["APPDATA"] = tempfile.mkdtemp()           # 실제 설정은 건드리지 않음
    if IS_MAC:
        os.environ["HOME"] = tempfile.mkdtemp()
    root = tk.Tk()
    if IS_MAC:
        root.tk.call("tk", "scaling", 96 / 72)
    root.report_callback_exception = lambda *a: errors.append("".join(traceback.format_exception(*a)))
    try:
        app = RingTimerApp(root)
    except Exception:
        print(traceback.format_exc())
        return 1
    steps = [
        lambda: app.set_duration(2),
        lambda: app.toggle(),
        lambda: app.toggle(),
        lambda: app.reset(),
        lambda: app.toggle_theme(),
        lambda: app.toggle_interval_panel(),
        lambda: app.interval_panel.add_row(),
        lambda: app.on_intervals_changed([{"secs": 1, "color": "accent"}, {"secs": 1, "color": "gray"}]),
        lambda: app.set_interval_on(True),
        lambda: app.start(),
        lambda: app.toggle_interval_panel(),
        lambda: app.add_color_dialog(),
        lambda: app._picker.destroy(),
        lambda: app.toggle_theme(),
        lambda: app.set_accent("#0091FF"),
        lambda: app.set_infinite(True),
        lambda: app.start(),
        lambda: app.set_mini(True),
        lambda: app.set_mini(False),
        lambda: app.set_infinite(False),
    ]

    def run(i=0):
        if i < len(steps):
            try:
                steps[i]()
            except Exception:
                errors.append(traceback.format_exc())
            root.after(400, run, i + 1)
        else:
            root.after(2500, root.destroy)

    root.after(800, run)
    root.mainloop()
    if errors:
        print("SELFTEST FAILED")
        for e in errors:
            print(e)
        return 1
    print("SELFTEST OK")
    return 0


def main():
    if "--make-icon-png" in sys.argv:          # 맥 앱 아이콘용 (PyInstaller 가 icns 로 바꿔 줌)
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ring_timer_icon.png")
        make_icon_image(1024, ICON_GRAY, 1.0, ICON_GRAY, ICON_GRAY).save(out)
        print("icon saved:", out)
        return
    if "--make-icon" in sys.argv:
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ring_timer.ico")
        # 실행 파일 아이콘: 회색 원 + 가운데 회색 모래시계 (밝은/어두운 배경 모두에서 보이는 중간 회색)
        sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
        imgs = [make_icon_image(sz, ICON_GRAY, 1.0, ICON_GRAY, ICON_GRAY) for sz in sizes]
        imgs[-1].save(out, format="ICO", sizes=[(sz, sz) for sz in sizes], append_images=imgs[:-1])
        print("icon saved:", out)
        return
    if "--selftest" in sys.argv:               # 자동 점검: 창을 띄워 주요 기능을 눌러 보고 종료
        sys.exit(run_selftest())
    enable_dpi_awareness()
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RingTimer.App")
        except Exception:
            pass
    try:
        root = tk.Tk()
        if IS_MAC:
            root.tk.call("tk", "scaling", 96 / 72)
        RingTimerApp(root)
        root.mainloop()
    except Exception:
        # pythonw 로 실행하면 오류가 안 보이므로 error.log 파일에 남깁니다
        import traceback
        log = os.path.join(os.path.dirname(settings_path()), "error.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        try:
            from tkinter import messagebox
            messagebox.showerror("Ring Timer 오류", f"오류가 발생했습니다.\n자세한 내용: {log}")
        except Exception:
            pass


if __name__ == "__main__":
    main()
