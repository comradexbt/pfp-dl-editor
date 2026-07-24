import os
import math
import logging
import re
import asyncio
import json
import shutil
import tempfile
import urllib.request
from io import BytesIO
from pathlib import Path
from flask import Flask
from threading import Thread
from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageDraw, ImageFont

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
TARGET_ADMIN_ID = int(os.getenv("TARGET_ADMIN_ID", 0))

TARGET_SIZE = 1024
MAX_COLLAGE_PHOTOS = 1000
MIN_COLLAGE_PHOTOS = 2
MAX_COLLAGE_SIDE = 4000

STYLE_SELECT, BG_SELECT, TEXT_SELECT, COLLECTING = range(4)

STYLES = {
    "classic": {
        "title": "Classic Grid",
        "desc": "Clean white grid with light spacing",
        "kind": "plain",
        "gap": 6,
        "pad": 12,
        "bg": (255, 255, 255),
        "border": None,
        "border_w": 0,
    },
    "tight": {
        "title": "Tight Grid",
        "desc": "No gaps — edge-to-edge mosaic",
        "kind": "plain",
        "gap": 0,
        "pad": 0,
        "bg": (0, 0, 0),
        "border": None,
        "border_w": 0,
    },
    "bordered": {
        "title": "Bordered Grid",
        "desc": "Dark background + thin white frames",
        "kind": "plain",
        "gap": 8,
        "pad": 16,
        "bg": (18, 18, 18),
        "border": (240, 240, 240),
        "border_w": 2,
    },
    "labeled": {
        "title": "Labeled Grid",
        "desc": "Rounded icons + text under each (like app grid)",
        "kind": "labeled",
        "gap": 28,
        "pad": 48,
        "bg": (126, 200, 227),
        "border": None,
        "border_w": 0,
        "radius": 48,
        "label_gap": 14,
        "text_color": (255, 255, 255),
    },
    "glide": {
        "title": "Glide Gallery",
        "desc": "Centered icons + sleek labels (modern glide style)",
        "kind": "glide",
        "gap": 24,
        "pad": 40,
        "bg": (35, 35, 50),
        "border": None,
        "border_w": 0,
        "radius": 60,
        "label_gap": 16,
        "text_color": (255, 255, 255),
    },
    "app_style": {
        "title": "App Showcase",
        "desc": "Centered icons with dark app border & text below",
        "kind": "app_style",
        "gap": 35,
        "pad": 60,
        "bg": (126, 200, 227),
        "border": None,
        "border_w": 0,
        "radius": 45,
        "label_gap": 15,
        "text_color": (255, 255, 255),
    },
}

BG_PRESETS = {
    "sky": ((126, 200, 227), "Sky Blue"),
    "light": ((240, 244, 248), "Light"),
    "white": ((255, 255, 255), "White"),
    "dark": ((28, 28, 32), "Dark"),
    "black": ((0, 0, 0), "Black"),
    "deep": ((35, 35, 50), "Deep Slate"),
    "coral": ((255, 140, 105), "Coral"),
    "mint": ((180, 230, 200), "Mint"),
    "purple": ((120, 90, 180), "Purple"),
}

TEXT_PRESETS = {
    "white": ((255, 255, 255), "White"),
    "black": ((20, 20, 20), "Black"),
    "red": ((220, 50, 50), "Red"),
    "yellow": ((255, 220, 50), "Yellow"),
    "green": ((40, 200, 100), "Green"),
    "blue": ((40, 100, 255), "Blue"),
    "orange": ((255, 140, 0), "Orange"),
    "gray": ((160, 160, 160), "Gray"),
    "pink": ((255, 100, 150), "Pink"),
    "cyan": ((0, 200, 220), "Cyan"),
}

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "X PFP Scraper Bot is Alive and Running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

def keep_alive():
    Thread(target=run_flask, daemon=True).start()

def is_admin(user_id: int) -> bool:
    return user_id == TARGET_ADMIN_ID

def _parse_color(text: str):
    if not text:
        return None
    t = text.strip().lower()
    m = re.fullmatch(r"#?([0-9a-f]{6})", t)
    if m:
        h = m.group(1)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    m = re.fullmatch(r"(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})", t)
    if m:
        rgb = tuple(int(m.group(i)) for i in range(1, 4))
        if all(0 <= c <= 255 for c in rgb):
            return rgb
    return None

def _http_get(url: str, timeout: int = 45):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json,image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.geturl(), (resp.headers.get("Content-Type") or "")

def _avatar_variants(avatar_url: str):
    urls = []
    if not avatar_url:
        return urls
    original = re.sub(r"_(normal|bigger|mini|200x200|400x400|x96)(?=\.[A-Za-z0-9]+$)", "", avatar_url)
    if "." in original:
        four = re.sub(r"(\.[A-Za-z0-9]+)$", r"_400x400\1", original)
        bigger = re.sub(r"(\.[A-Za-z0-9]+)$", r"_bigger\1", original)
    else:
        four = bigger = original
    for u in (original, four, bigger, avatar_url):
        if u and u not in urls:
            urls.append(u)
    return urls

def _enhance_to_hq(data: bytes):
    im = Image.open(BytesIO(data))
    if im.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        rgba = im.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")

    w, h = im.size
    if max(w, h) < 80:
        raise RuntimeError(f"source too small: {w}x{h}")

    if max(w, h) < TARGET_SIZE:
        scale = TARGET_SIZE / float(max(w, h))
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        im = im.resize(new_size, Image.Resampling.LANCZOS)
        im = im.filter(ImageFilter.UnsharpMask(radius=1.4, percent=140, threshold=2))
        im = ImageEnhance.Sharpness(im).enhance(1.15)
        im = ImageEnhance.Contrast(im).enhance(1.05)
    else:
        im = ImageEnhance.Sharpness(im).enhance(1.1)

    out = BytesIO()
    im.save(out, format="JPEG", quality=97, optimize=True, progressive=True, subsampling=0)
    return out.getvalue(), "jpg"

def _download_hq_avatar(username: str):
    candidates = []
    try:
        raw, _, _ = _http_get(f"https://api.fxtwitter.com/{username}")
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        avatar_url = (payload.get("user") or {}).get("avatar_url") or ""
        for url in _avatar_variants(avatar_url):
            try:
                data, final, ctype = _http_get(url)
                if not data or len(data) < 800:
                    continue
                if "html" in ctype.lower() or data[:1] in (b"<", b"{"):
                    continue
                score = len(data) + (10_000_000 if len(data) >= 3000 else 0)
                candidates.append((score, data, final))
            except Exception:
                pass
    except Exception as e:
        logging.warning("@%s fxtwitter failed: %s", username, e)

    if not any(c[0] > 10_000_000 for c in candidates):
        for u in (
            f"https://unavatar.io/x/{username}?size=4096",
            f"https://unavatar.io/twitter/{username}?size=4096",
        ):
            try:
                data, final, ctype = _http_get(u)
                if data and len(data) >= 800 and "html" not in ctype.lower():
                    candidates.append((len(data), data, final))
            except Exception:
                pass

    if not candidates:
        raise RuntimeError(f"No avatar found for @{username}")

    candidates.sort(key=lambda x: (x[0], len(x[1])), reverse=True)
    last_err = None
    for score, data, final in candidates:
        try:
            hq_bytes, ext = _enhance_to_hq(data)
            return hq_bytes, f"{username}_hq.{ext}"
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Could not build HQ avatar for @{username}: {last_err}")

async def download_hq_avatar(username: str):
    return await asyncio.to_thread(_download_hq_avatar, username)

def _session_dir(context: ContextTypes.DEFAULT_TYPE) -> Path:
    d = context.user_data.get("collage_dir")
    if d and Path(d).is_dir():
        return Path(d)
    d = tempfile.mkdtemp(prefix="collage_")
    context.user_data["collage_dir"] = d
    context.user_data["collage_count"] = 0
    context.user_data["collage_labels"] = {}
    return Path(d)

def _cleanup_session(context: ContextTypes.DEFAULT_TYPE):
    d = context.user_data.pop("collage_dir", None)
    for k in (
        "collage_count", "collage_style", "collage_labels", "collage_bg", 
        "collage_text", "awaiting_custom_bg", "awaiting_custom_text",
    ):
        context.user_data.pop(k, None)
    if d and os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)

def _fit_square(im: Image.Image, size: int) -> Image.Image:
    im = im.convert("RGB")
    w, h = im.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    im = im.crop((left, top, left + side, top + side))
    return im.resize((size, size), Image.Resampling.LANCZOS)

def _rounded_square(im: Image.Image, size: int, radius: int) -> Image.Image:
    tile = _fit_square(im, size).convert("RGBA")
    radius = max(4, min(radius, size // 2))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(tile, (0, 0))
    out.putalpha(mask)
    return out

def _load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]
    for p in candidates:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size=size)
            except Exception:
                pass
    return ImageFont.load_default()

def _encode_jpeg(canvas: Image.Image) -> bytes:
    if canvas.mode != "RGB":
        canvas = canvas.convert("RGB")
    quality = 92
    out = BytesIO()
    canvas.save(out, format="JPEG", quality=quality, optimize=True, progressive=True, subsampling=0)
    data = out.getvalue()
    while len(data) > 9_500_000 and quality > 60:
        quality -= 8
        out = BytesIO()
        canvas.save(out, format="JPEG", quality=quality, optimize=True, progressive=True)
        data = out.getvalue()
    return data

def _build_plain_grid(image_paths: list[Path], style_key: str) -> bytes:
    style = STYLES[style_key]
    n = len(image_paths)
    if n < MIN_COLLAGE_PHOTOS:
        raise RuntimeError(f"Need at least {MIN_COLLAGE_PHOTOS} photos")
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    gap = style["gap"]
    pad = style["pad"]
    border_w = style.get("border_w") or 0
    border_color = style.get("border")
    overhead_w = pad * 2 + max(0, cols - 1) * gap + cols * 2 * border_w
    overhead_h = pad * 2 + max(0, rows - 1) * gap + rows * 2 * border_w
    cell = max(24, (MAX_COLLAGE_SIDE - overhead_w) // cols)
    cell = min(cell, 256)
    canvas_w = pad * 2 + cols * cell + max(0, cols - 1) * gap + cols * 2 * border_w
    canvas_h = pad * 2 + rows * cell + max(0, rows - 1) * gap + rows * 2 * border_w
    canvas = Image.new("RGB", (canvas_w, canvas_h), style["bg"])
    for i, path in enumerate(image_paths):
        row, col = divmod(i, cols)
        try:
            with Image.open(path) as im:
                tile = _fit_square(im, cell)
        except Exception as e:
            logging.warning("skip tile %s: %s", path, e)
            tile = Image.new("RGB", (cell, cell), (40, 40, 40))
        if border_w and border_color:
            tile = ImageOps.expand(tile, border=border_w, fill=border_color)
        x = pad + col * (cell + 2 * border_w + gap)
        y = pad + row * (cell + 2 * border_w + gap)
        canvas.paste(tile, (x, y))
    return _encode_jpeg(canvas)

def _build_labeled_grid(image_paths: list[Path], labels: list[str], bg: tuple, text_color: tuple) -> bytes:
    n = len(image_paths)
    if n < MIN_COLLAGE_PHOTOS:
        raise RuntimeError(f"Need at least {MIN_COLLAGE_PHOTOS} photos")
    style = STYLES["labeled"]
    cols = math.ceil(math.sqrt(n))
    if n >= 4:
        cols = min(cols + (0 if n <= 9 else 1), max(4, cols))
        cols = min(cols, 8)
        rows = math.ceil(n / cols)
        while rows > cols + 1 and cols < 10:
            cols += 1
            rows = math.ceil(n / cols)
    else:
        rows = math.ceil(n / cols)
    gap, pad, label_gap, radius_ratio = style["gap"], style["pad"], style["label_gap"], 0.22 
    rows_factor = rows * 1.28
    cols_factor = cols
    cell_by_h = int((MAX_COLLAGE_SIDE - pad * 2 - max(0, rows - 1) * gap) / max(rows_factor, 0.01))
    cell_by_w = int((MAX_COLLAGE_SIDE - pad * 2 - max(0, cols - 1) * gap) / max(cols_factor, 0.01))
    cell = max(64, min(cell_by_h, cell_by_w, 280))
    font_size = max(14, int(cell * 0.16))
    font = _load_font(font_size)
    try:
        bbox = ImageDraw.Draw(Image.new("RGB", (10, 10))).textbbox((0, 0), "Ay", font=font)
        text_h = bbox[3] - bbox[1]
    except Exception:
        text_h = font_size + 4
    label_h = text_h + 8
    slot_h = cell + label_gap + label_h
    canvas_w = pad * 2 + cols * cell + max(0, cols - 1) * gap
    canvas_h = pad * 2 + rows * slot_h + max(0, rows - 1) * gap
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (*bg, 255))
    draw_bg = ImageDraw.Draw(canvas)
    accent = tuple(min(255, c + 30) for c in bg[:3])
    accent2 = tuple(max(0, c - 40) for c in bg[:3])
    for cx, cy, r, col in (
        (int(canvas_w * 0.08), int(canvas_h * 0.12), int(cell * 0.35), accent),
        (int(canvas_w * 0.95), int(canvas_h * 0.08), int(cell * 0.2), accent2),
        (int(canvas_w * 0.9), int(canvas_h * 0.9), int(cell * 0.45), accent),
        (int(canvas_w * 0.05), int(canvas_h * 0.85), int(cell * 0.25), accent2),
    ):
        draw_bg.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*col, 90))
    radius = max(12, int(cell * radius_ratio))
    for i, path in enumerate(image_paths):
        row, col = divmod(i, cols)
        x0 = pad + col * (cell + gap)
        y0 = pad + row * (slot_h + gap)
        try:
            with Image.open(path) as im:
                tile = _rounded_square(im, cell, radius)
        except Exception as e:
            logging.warning("skip tile %s: %s", path, e)
            tile = Image.new("RGBA", (cell, cell), (40, 40, 40, 255))
        shadow = Image.new("RGBA", (cell + 12, cell + 12), (0, 0, 0, 0))
        sh_mask = Image.new("L", (cell, cell), 0)
        ImageDraw.Draw(sh_mask).rounded_rectangle([0, 0, cell - 1, cell - 1], radius=radius, fill=140)
        sh_layer = Image.new("RGBA", (cell, cell), (0, 0, 0, 80))
        sh_layer.putalpha(sh_mask)
        shadow.paste(sh_layer, (6, 8), sh_layer)
        canvas.alpha_composite(shadow, (x0 - 2, y0 - 2))
        canvas.alpha_composite(tile, (x0, y0))
        label = (labels[i] if i < len(labels) else "") or f"{i + 1}"
        if len(label) > 22:
            label = label[:20] + "…"
        draw = ImageDraw.Draw(canvas)
        try:
            bbox = draw.textbbox((0, 0), label, font=font)
            tw = bbox[2] - bbox[0]
        except Exception:
            tw = len(label) * font_size // 2
        tx = x0 + (cell - tw) // 2
        ty = y0 + cell + label_gap
        draw.text((tx, ty), label, font=font, fill=(*text_color, 255))
    return _encode_jpeg(canvas.convert("RGB"))

def _build_glide_grid(image_paths: list[Path], labels: list[str], bg: tuple, text_color: tuple) -> bytes:
    n = len(image_paths)
    if n < MIN_COLLAGE_PHOTOS:
        raise RuntimeError(f"Need at least {MIN_COLLAGE_PHOTOS} photos")
    style = STYLES["glide"]
    if n <= 2:
        cols, rows = n, 1
    elif n <= 4:
        cols, rows = 2, math.ceil(n / 2)
    elif n <= 6:
        cols, rows = 3, math.ceil(n / 3)
    else:
        cols, rows = 4, math.ceil(n / 4)
        while rows > cols + 1 and cols < 8:
            cols += 1
            rows = math.ceil(n / cols)
    gap, pad, label_gap = style["gap"], style["pad"], style["label_gap"]
    radius = min(style["radius"], 80)
    cell = max(72, min(int((MAX_COLLAGE_SIDE - pad * 2 - (cols - 1) * gap) / max(cols, 1)), 320))
    font_size = max(15, int(cell * 0.15))
    font = _load_font(font_size)
    try:
        bbox = ImageDraw.Draw(Image.new("RGB", (10, 10))).textbbox((0, 0), "Ag", font=font)
        text_h = bbox[3] - bbox[1]
    except Exception:
        text_h = font_size + 4
    label_h = text_h + 10
    slot_w = cell + gap
    total_content_w = cols * cell + (cols - 1) * gap
    slot_h = cell + label_gap + label_h
    total_content_h = rows * slot_h + (rows - 1) * gap
    canvas_w = pad * 2 + total_content_w
    canvas_h = pad * 2 + slot_h if n <= 2 else max(pad * 2 + total_content_h, int(MAX_COLLAGE_SIDE * 0.6))
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (*bg, 255))
    draw_bg = ImageDraw.Draw(canvas)
    accent_light = tuple(min(255, c + 35) for c in bg[:3])
    accent_dark = tuple(max(0, c - 35) for c in bg[:3])
    accent_glow = tuple(min(255, c + 60) for c in bg[:3])
    blobs = [
        (int(canvas_w * 0.12), int(canvas_h * 0.10), int(canvas_w * 0.35), accent_light),
        (int(canvas_w * 0.90), int(canvas_h * 0.15), int(canvas_w * 0.25), accent_dark),
        (int(canvas_w * 0.85), int(canvas_h * 0.85), int(canvas_w * 0.40), accent_light),
        (int(canvas_w * 0.08), int(canvas_h * 0.80), int(canvas_w * 0.22), accent_glow),
        (int(canvas_w * 0.50), int(canvas_h * 0.50), int(canvas_w * 0.30), accent_dark),
    ]
    for cx, cy, r, col in blobs:
        draw_bg.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*col, 55))
    x_offset = pad + (total_content_w - (n * cell + (n - 1) * gap)) // 2 if rows == 1 and n < cols else pad
    for i, path in enumerate(image_paths):
        row, col = divmod(i, cols)
        x0 = x_offset + col * (cell + gap)
        y0 = pad + row * (slot_h + gap)
        try:
            with Image.open(path) as im:
                tile = _rounded_square(im, cell, radius)
        except Exception as e:
            logging.warning("skip tile %s: %s", path, e)
            tile = Image.new("RGBA", (cell, cell), (40, 40, 40, 255))
        shadow = Image.new("RGBA", (cell + 16, cell + 16), (0, 0, 0, 0))
        sh_mask = Image.new("L", (cell, cell), 0)
        ImageDraw.Draw(sh_mask).rounded_rectangle([0, 0, cell - 1, cell - 1], radius=radius, fill=180)
        sh_layer1 = Image.new("RGBA", (cell, cell), (0, 0, 0, 70))
        sh_layer1.putalpha(sh_mask)
        sh_layer2 = Image.new("RGBA", (cell, cell), (0, 0, 0, 35))
        sh_layer2.putalpha(sh_mask)
        shadow.paste(sh_layer2, (4, 6), sh_layer2)
        shadow.paste(sh_layer1, (2, 4), sh_layer1)
        canvas.alpha_composite(shadow, (x0 - 4, y0 - 2))
        canvas.alpha_composite(tile, (x0, y0))
        glow = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
        ImageDraw.Draw(glow).rounded_rectangle([0, 0, cell - 1, cell - 1], radius=radius, outline=(*accent_glow, 60), width=1)
        canvas.alpha_composite(glow, (x0, y0))
        label = (labels[i] if i < len(labels) else "") or f"{i + 1}"
        if len(label) > 24: label = label[:22] + "…"
        draw = ImageDraw.Draw(canvas)
        try:
            bbox = draw.textbbox((0, 0), label, font=font)
            tw = bbox[2] - bbox[0]
        except Exception:
            tw = len(label) * font_size // 2
        tx = x0 + (cell - tw) // 2
        ty = y0 + cell + label_gap
        draw.text((tx + 1, ty + 1), label, font=font, fill=(0, 0, 0, 80))
        draw.text((tx, ty), label, font=font, fill=(*text_color, 255))
    return _encode_jpeg(canvas.convert("RGB"))

def _build_app_style_grid(image_paths: list[Path], labels: list[str], bg: tuple, text_color: tuple) -> bytes:
    n = len(image_paths)
    if n < MIN_COLLAGE_PHOTOS:
        raise RuntimeError(f"Need at least {MIN_COLLAGE_PHOTOS} photos")
    style = STYLES["app_style"]
    cols = min(4, n) if n <= 4 else (4 if n <= 8 else math.ceil(math.sqrt(n)))
    if n > 8 and cols < 4: cols = 4
    rows = math.ceil(n / cols)
    pad, gap, label_gap = style["pad"], style["gap"], style["label_gap"]
    cell = min(260, int((MAX_COLLAGE_SIDE - pad * 2 - (cols - 1) * gap) / cols))
    radius = int(cell * 0.25)
    font_size = max(18, int(cell * 0.16))
    font = _load_font(font_size)
    try:
        bbox = ImageDraw.Draw(Image.new("RGB", (10, 10))).textbbox((0, 0), "Ag", font=font)
        label_h = bbox[3] - bbox[1] + 10
    except:
        label_h = font_size + 10
    slot_w = cell
    slot_h = cell + label_gap + label_h
    total_w = cols * slot_w + (cols - 1) * gap
    total_h = rows * slot_h + (rows - 1) * gap
    canvas_w = pad * 2 + total_w
    canvas_h = pad * 2 + total_h
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (*bg, 255))
    draw = ImageDraw.Draw(canvas)
    c_orange, c_red = (235, 90, 50, 180), (210, 40, 40, 180)
    draw.ellipse([-canvas_w * 0.05, -canvas_h * 0.05, canvas_w * 0.15, canvas_h * 0.15], fill=c_orange)
    draw.ellipse([canvas_w * 0.85, -canvas_h * 0.1, canvas_w * 1.1, canvas_h * 0.15], fill=c_red)
    draw.ellipse([-canvas_w * 0.1, canvas_h * 0.8, canvas_w * 0.15, canvas_h * 1.1], fill=c_red)
    draw.ellipse([canvas_w * 0.8, canvas_h * 0.85, canvas_w * 1.05, canvas_h * 1.05], fill=c_orange)
    draw.ellipse([canvas_w * 0.15, canvas_h * 0.2, canvas_w * 0.15 + 25, canvas_h * 0.2 + 25], fill=c_orange)
    draw.ellipse([canvas_w * 0.82, canvas_h * 0.75, canvas_w * 0.82 + 30, canvas_h * 0.75 + 30], fill=c_orange)
    border_w = max(4, int(cell * 0.05))
    border_color = (15, 15, 25, 255)
    for i, path in enumerate(image_paths):
        row, col = divmod(i, cols)
        items_in_this_row = n - row * cols
        if items_in_this_row < cols and row == rows - 1:
            row_w = items_in_this_row * slot_w + (items_in_this_row - 1) * gap
            x0 = pad + (total_w - row_w) // 2 + col * (slot_w + gap)
        else:
            x0 = pad + col * (slot_w + gap)
        y0 = pad + row * (slot_h + gap)
        try:
            with Image.open(path) as im:
                tile = _rounded_square(im, cell, radius)
        except Exception as e:
            logging.warning("skip tile %s: %s", path, e)
            tile = Image.new("RGBA", (cell, cell), (40, 40, 40, 255))
        draw.rounded_rectangle([x0 - border_w, y0 - border_w, x0 + cell + border_w, y0 + cell + border_w], radius=radius + border_w, fill=border_color)
        canvas.alpha_composite(tile, (x0, y0))
        label = (labels[i] if i < len(labels) else "") or f"{i + 1}"
        if len(label) > 20: label = label[:18] + "…"
        try:
            bbox = draw.textbbox((0, 0), label, font=font)
            tw = bbox[2] - bbox[0]
        except:
            tw = len(label) * font_size // 2
        tx = x0 + (cell - tw) // 2
        ty = y0 + cell + label_gap
        draw.text((tx + 1, ty + 1), label, font=font, fill=(0, 0, 0, 90))
        draw.text((tx, ty), label, font=font, fill=(*text_color, 255))
    return _encode_jpeg(canvas.convert("RGB"))

def _build_grid_collage(image_paths: list[Path], style_key: str, labels: list[str] | None = None, bg: tuple | None = None, text_color: tuple | None = None) -> bytes:
    style = STYLES[style_key]
    if style.get("kind") == "labeled":
        return _build_labeled_grid(image_paths, labels or [], bg or style["bg"], text_color or style["text_color"])
    if style.get("kind") == "glide":
        return _build_glide_grid(image_paths, labels or [], bg or style["bg"], text_color or style["text_color"])
    if style.get("kind") == "app_style":
        return _build_app_style_grid(image_paths, labels or [], bg or style["bg"], text_color or style["text_color"])
    return _build_plain_grid(image_paths, style_key)

async def _save_incoming_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    count = context.user_data.get("collage_count", 0)
    if count >= MAX_COLLAGE_PHOTOS:
        await update.message.reply_text(f"⚠️ Max {MAX_COLLAGE_PHOTOS} photos reached. Send /done to build collage.")
        return False
    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document and (update.message.document.mime_type or "").startswith("image/"):
        file_id = update.message.document.file_id
    else:
        return False
    tg_file = await context.bot.get_file(file_id)
    raw = await tg_file.download_as_bytearray()
    session = _session_dir(context)
    idx = count + 1
    fname = f"{idx:04d}.jpg"
    path = session / fname
    caption = (update.message.caption or "").strip()
    labels = context.user_data.setdefault("collage_labels", {})
    labels[fname] = caption
    def _write():
        im = Image.open(BytesIO(bytes(raw)))
        tile = _fit_square(im, 512)
        tile.save(path, format="JPEG", quality=90, optimize=True)
    await asyncio.to_thread(_write)
    context.user_data["collage_count"] = idx
    style_key = context.user_data.get("collage_style")
    if style_key in ("labeled", "glide", "app_style"):
        shown = caption if caption else "(no caption — will use number)"
        if idx <= 5 or idx % 10 == 0 or idx == MAX_COLLAGE_PHOTOS:
            await update.message.reply_text(
                f"📸 {idx} saved — label: *{shown}*\n"
                f"Tip: send photo *with caption* for text under icon.\n"
                f"More photos or /done",
                parse_mode="Markdown",
            )
    else:
        if idx <= 3 or idx % 10 == 0 or idx == MAX_COLLAGE_PHOTOS:
            await update.message.reply_text(f"📸 Saved {idx}/{MAX_COLLAGE_PHOTOS}. Send more or /done")
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(
        "👋 *Commands*\n"
        "• Send X profile link / @username → HQ PFP photo\n"
        "• /collage → grid collage (6 styles, including App Showcase)",
        parse_mode="Markdown",
    )

async def process_pfp_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if context.user_data.get("collage_style") or context.user_data.get("awaiting_custom_bg") or context.user_data.get("awaiting_custom_text"):
        return
    raw_text = update.message.text or update.message.caption
    if not raw_text:
        return
    usernames_found = []
    for word in raw_text.split():
        link_match = re.search(r"https?://(?:www\.)?(?:x|twitter)\.com/([a-zA-Z0-9_]+)", word)
        if link_match:
            usernames_found.append(link_match.group(1))
        else:
            clean_name = re.sub(r"[^a-zA-Z0-9_]", "", word)
            if clean_name:
                usernames_found.append(clean_name)
    unique_usernames = list(dict.fromkeys(usernames_found))
    if not unique_usernames:
        return
    status_msg = await update.message.reply_text(f"⏳ Extracting {len(unique_usernames)} profile(s) in High Quality...")
    success_count = 0
    fail_count = 0
    for x_username in unique_usernames:
        try:
            data, filename = await download_hq_avatar(x_username)
            bio = BytesIO(data)
            bio.seek(0)
            await context.bot.send_photo(chat_id=TARGET_ADMIN_ID, photo=InputFile(bio, filename=filename))
            success_count += 1
        except Exception as e:
            logging.warning("Failed to fetch for %s: %s", x_username, e)
            try:
                await context.bot.send_message(chat_id=TARGET_ADMIN_ID, text=f"❌ Failed to fetch PFP for: @{x_username}")
            except Exception:
                pass
            fail_count += 1
        await asyncio.sleep(1)
    if len(unique_usernames) > 1:
        await status_msg.reply_text(f"✅ Done!\n\n📥 Success: {success_count}\n❌ Failed: {fail_count}")
    else:
        try:
            await status_msg.delete()
        except Exception:
            pass

def _bg_keyboard():
    rows = []
    keys = list(BG_PRESETS.keys())
    for i in range(0, len(keys), 2):
        row = []
        for k in keys[i : i + 2]:
            row.append(InlineKeyboardButton(f"🎨 {BG_PRESETS[k][1]}", callback_data=f"bg:{k}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Custom hex (#RRGGBB)", callback_data="bg:custom")])
    return InlineKeyboardMarkup(rows)

def _text_keyboard():
    rows = []
    keys = list(TEXT_PRESETS.keys())
    for i in range(0, len(keys), 2):
        row = []
        for k in keys[i : i + 2]:
            row.append(InlineKeyboardButton(f"✏️ {TEXT_PRESETS[k][1]}", callback_data=f"txt:{k}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Custom hex (#RRGGBB)", callback_data="txt:custom")])
    return InlineKeyboardMarkup(rows)

async def collage_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    _cleanup_session(context)
    keyboard = [
        [InlineKeyboardButton(f"1️⃣ {STYLES['classic']['title']}", callback_data="style:classic")],
        [InlineKeyboardButton(f"2️⃣ {STYLES['tight']['title']}", callback_data="style:tight")],
        [InlineKeyboardButton(f"3️⃣ {STYLES['bordered']['title']}", callback_data="style:bordered")],
        [InlineKeyboardButton(f"4️⃣ {STYLES['labeled']['title']}", callback_data="style:labeled")],
        [InlineKeyboardButton(f"5️⃣ {STYLES['glide']['title']}", callback_data="style:glide")],
        [InlineKeyboardButton(f"6️⃣ {STYLES['app_style']['title']}", callback_data="style:app_style")],
    ]
    await update.message.reply_text(
        "🧩 *Collage mode*\n\n"
        "Choose a *grid style*:\n\n"
        f"1️⃣ *{STYLES['classic']['title']}* — {STYLES['classic']['desc']}\n"
        f"2️⃣ *{STYLES['tight']['title']}* — {STYLES['tight']['desc']}\n"
        f"3️⃣ *{STYLES['bordered']['title']}* — {STYLES['bordered']['desc']}\n"
        f"4️⃣ *{STYLES['labeled']['title']}* — {STYLES['labeled']['desc']}\n"
        f"5️⃣ *{STYLES['glide']['title']}* — {STYLES['glide']['desc']}\n"
        f"6️⃣ *{STYLES['app_style']['title']}* — {STYLES['app_style']['desc']}\n\n"
        f"Photos: *{MIN_COLLAGE_PHOTOS}–{MAX_COLLAGE_PHOTOS}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return STYLE_SELECT

async def collage_style_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return ConversationHandler.END
    data = query.data or ""
    if not data.startswith("style:"):
        await query.edit_message_text("Invalid style. /collage again.")
        return ConversationHandler.END
    style_key = data.split(":", 1)[1]
    if style_key not in STYLES:
        await query.edit_message_text("Unknown style. /collage again.")
        return ConversationHandler.END
    context.user_data["collage_style"] = style_key
    s = STYLES[style_key]
    if s.get("kind") in ("labeled", "glide", "app_style"):
        await query.edit_message_text(
            f"✅ Style: *{s['title']}*\n\nNow choose *background colour*:",
            parse_mode="Markdown",
            reply_markup=_bg_keyboard(),
        )
        return BG_SELECT
    _session_dir(context)
    context.user_data["collage_count"] = 0
    await query.edit_message_text(
        f"✅ Style: *{s['title']}*\n\n"
        f"Send photos ({MIN_COLLAGE_PHOTOS}–{MAX_COLLAGE_PHOTOS}).\n"
        "When finished → /done",
        parse_mode="Markdown",
    )
    return COLLECTING

async def collage_bg_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return ConversationHandler.END
    data = query.data or ""
    if not data.startswith("bg:"):
        return BG_SELECT
    key = data.split(":", 1)[1]
    if key == "custom":
        context.user_data["awaiting_custom_bg"] = True
        await query.edit_message_text("✏️ Send background colour as hex, e.g.\n`#7EC8E3` or `126,200,227`", parse_mode="Markdown")
        return BG_SELECT
    if key not in BG_PRESETS:
        return BG_SELECT
    context.user_data["collage_bg"] = BG_PRESETS[key][0]
    await query.edit_message_text(
        f"✅ Background: *{BG_PRESETS[key][1]}*\n\nNow choose *text colour* (labels under icons):",
        parse_mode="Markdown",
        reply_markup=_text_keyboard(),
    )
    return TEXT_SELECT

async def collage_bg_custom_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    if not context.user_data.get("awaiting_custom_bg"):
        return BG_SELECT
    color = _parse_color(update.message.text or "")
    if not color:
        await update.message.reply_text("❌ Invalid colour. Try `#7EC8E3`")
        return BG_SELECT
    context.user_data["awaiting_custom_bg"] = False
    context.user_data["collage_bg"] = color
    await update.message.reply_text(
        f"✅ Background: `{color}`\n\nNow choose *text colour*:",
        parse_mode="Markdown",
        reply_markup=_text_keyboard(),
    )
    return TEXT_SELECT

async def collage_text_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return ConversationHandler.END
    data = query.data or ""
    if not data.startswith("txt:"):
        return TEXT_SELECT
    key = data.split(":", 1)[1]
    if key == "custom":
        context.user_data["awaiting_custom_text"] = True
        await query.edit_message_text("✏️ Send text colour as hex, e.g.\n`#FFFFFF` or `255,255,255`", parse_mode="Markdown")
        return TEXT_SELECT
    if key not in TEXT_PRESETS:
        return TEXT_SELECT
    context.user_data["collage_text"] = TEXT_PRESETS[key][0]
    _session_dir(context)
    context.user_data["collage_count"] = 0
    await query.edit_message_text(
        f"✅ Text colour: *{TEXT_PRESETS[key][1]}*\n\n"
        f"Send icons/photos ({MIN_COLLAGE_PHOTOS}–{MAX_COLLAGE_PHOTOS}).\n\n"
        "⚠️ *Important:* add *caption* on each photo = label under icon\n"
        "Example: photo caption `cove` → text “cove” under that icon.\n\n"
        "When finished → /done",
        parse_mode="Markdown",
    )
    return COLLECTING

async def collage_text_custom_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    if not context.user_data.get("awaiting_custom_text"):
        return TEXT_SELECT
    color = _parse_color(update.message.text or "")
    if not color:
        await update.message.reply_text("❌ Invalid colour. Try `#FFFFFF`")
        return TEXT_SELECT
    context.user_data["awaiting_custom_text"] = False
    context.user_data["collage_text"] = color
    _session_dir(context)
    context.user_data["collage_count"] = 0
    await update.message.reply_text(
        f"✅ Text colour: `{color}`\n\n"
        f"Send icons/photos ({MIN_COLLAGE_PHOTOS}–{MAX_COLLAGE_PHOTOS}).\n"
        "Add *caption* on each photo for the label under it.\n\n"
        "/done when finished",
        parse_mode="Markdown",
    )
    return COLLECTING

async def collage_collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    if not context.user_data.get("collage_style"):
        await update.message.reply_text("Start with /collage first.")
        return ConversationHandler.END
    ok = await _save_incoming_image(update, context)
    if not ok and not (update.message.photo or update.message.document):
        await update.message.reply_text("Send photos (or image files). For Labeled style use captions. /done when ready.")
    return COLLECTING

async def collage_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    style_key = context.user_data.get("collage_style")
    count = context.user_data.get("collage_count", 0)
    session = context.user_data.get("collage_dir")
    if not style_key or not session:
        await update.message.reply_text("No active collage. Start with /collage")
        return ConversationHandler.END
    if count < MIN_COLLAGE_PHOTOS:
        await update.message.reply_text(f"Need at least {MIN_COLLAGE_PHOTOS} photos (have {count}). Keep sending.")
        return COLLECTING
    status = await update.message.reply_text(f"🧩 Building *{STYLES[style_key]['title']}* collage from {count} photos...", parse_mode="Markdown")
    paths = sorted(Path(session).glob("*.jpg"))
    labels_map = context.user_data.get("collage_labels") or {}
    labels = [labels_map.get(p.name, "") for p in paths]
    bg = context.user_data.get("collage_bg")
    text_color = context.user_data.get("collage_text")
    try:
        data = await asyncio.to_thread(_build_grid_collage, paths, style_key, labels, bg, text_color)
        bio = BytesIO(data)
        bio.seek(0)
        await context.bot.send_photo(
            chat_id=TARGET_ADMIN_ID,
            photo=InputFile(bio, filename=f"collage_{style_key}_{count}.jpg"),
            caption=f"🧩 {STYLES[style_key]['title']} — {count} photos",
        )
        try:
            await status.delete()
        except Exception:
            pass
    except Exception as e:
        logging.exception("collage failed")
        await status.edit_text(f"❌ Collage failed: {e}")
    finally:
        _cleanup_session(context)
    return ConversationHandler.END

if __name__ == "__main__":
    keep_alive()

    if not BOT_TOKEN or TARGET_ADMIN_ID == 0:
        print("ERROR: BOT_TOKEN or TARGET_ADMIN_ID is not set in environment variables.")
        exit(1)

    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()

    collage_conv = ConversationHandler(
        entry_points=[CommandHandler("collage", collage_start)],
        states={
            STYLE_SELECT: [CallbackQueryHandler(collage_style_chosen, pattern=r"^style:")],
            BG_SELECT: [
                CallbackQueryHandler(collage_bg_chosen, pattern=r"^bg:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, collage_bg_custom_text),
            ],
            TEXT_SELECT: [
                CallbackQueryHandler(collage_text_chosen, pattern=r"^txt:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, collage_text_custom_text),
            ],
            COLLECTING: [
                MessageHandler(filters.PHOTO, collage_collect),
                MessageHandler(filters.Document.IMAGE, collage_collect),
                CommandHandler("done", collage_done),
            ],
        },
        fallbacks=[
            CommandHandler("done", collage_done),
        ],
        allow_reentry=True,
    )

    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(collage_conv)
    bot_app.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, process_pfp_requests))

    print("Scraper Bot is running smoothly...")
    bot_app.run_polling(drop_pending_updates=True)
