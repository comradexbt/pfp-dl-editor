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
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
TARGET_ADMIN_ID = int(os.getenv("TARGET_ADMIN_ID", 0))

TARGET_SIZE = 1024
MAX_COLLAGE_PHOTOS = 1000
MIN_COLLAGE_PHOTOS = 2
MAX_COLLAGE_SIDE = 4000  # px

# Conversation states
STYLE_SELECT, COLLECTING = range(2)

# All grid styles
STYLES = {
    "classic": {
        "title": "Classic Grid",
        "desc": "Clean white grid with light spacing",
        "gap": 6,
        "pad": 12,
        "bg": (255, 255, 255),
        "border": None,
        "border_w": 0,
    },
    "tight": {
        "title": "Tight Grid",
        "desc": "No gaps — edge-to-edge mosaic",
        "gap": 0,
        "pad": 0,
        "bg": (0, 0, 0),
        "border": None,
        "border_w": 0,
    },
    "bordered": {
        "title": "Bordered Grid",
        "desc": "Dark background + thin white frames",
        "gap": 8,
        "pad": 16,
        "bg": (18, 18, 18),
        "border": (240, 240, 240),
        "border_w": 2,
    },
}

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

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


# ───────────────── HQ avatar helpers ─────────────────

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
    original = re.sub(
        r"_(normal|bigger|mini|200x200|400x400|x96)(?=\.[A-Za-z0-9]+$)",
        "",
        avatar_url,
    )
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


# ───────────────── Collage helpers ─────────────────

def _session_dir(context: ContextTypes.DEFAULT_TYPE) -> Path:
    d = context.user_data.get("collage_dir")
    if d and Path(d).is_dir():
        return Path(d)
    d = tempfile.mkdtemp(prefix="collage_")
    context.user_data["collage_dir"] = d
    context.user_data["collage_count"] = 0
    return Path(d)


def _cleanup_session(context: ContextTypes.DEFAULT_TYPE):
    d = context.user_data.pop("collage_dir", None)
    context.user_data.pop("collage_count", None)
    context.user_data.pop("collage_style", None)
    if d and os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)


def _fit_square(im: Image.Image, size: int) -> Image.Image:
    """Center-crop to square then resize."""
    im = im.convert("RGB")
    w, h = im.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    im = im.crop((left, top, left + side, top + side))
    return im.resize((size, size), Image.Resampling.LANCZOS)


def _build_grid_collage(image_paths: list[Path], style_key: str) -> bytes:
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

    # cell size so total side <= MAX_COLLAGE_SIDE
    # total = pad*2 + cols*cell + (cols-1)*gap + cols*2*border_w (approx)
    overhead_w = pad * 2 + max(0, cols - 1) * gap + cols * 2 * border_w
    overhead_h = pad * 2 + max(0, rows - 1) * gap + rows * 2 * border_w
    cell = max(24, (MAX_COLLAGE_SIDE - overhead_w) // cols)
    cell = min(cell, 256)  # cap cell for huge batches (memory)

    canvas_w = pad * 2 + cols * cell + max(0, cols - 1) * gap + cols * 2 * border_w
    canvas_h = pad * 2 + rows * cell + max(0, rows - 1) * gap + rows * 2 * border_w
    canvas = Image.new("RGB", (canvas_w, canvas_h), style["bg"])

    for i, path in enumerate(image_paths):
        r, c = divmod(i, cols)
        # actually row-major: i // cols, i % cols
        row = i // cols
        col = i % cols
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

    out = BytesIO()
    # Prefer JPEG photo under ~9MB for Telegram send_photo
    quality = 92
    canvas.save(out, format="JPEG", quality=quality, optimize=True, progressive=True, subsampling=0)
    data = out.getvalue()
    while len(data) > 9_500_000 and quality > 60:
        quality -= 8
        out = BytesIO()
        canvas.save(out, format="JPEG", quality=quality, optimize=True, progressive=True)
        data = out.getvalue()
    return data


async def _save_incoming_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Download photo/document image into session dir. Returns True if saved."""
    count = context.user_data.get("collage_count", 0)
    if count >= MAX_COLLAGE_PHOTOS:
        await update.message.reply_text(
            f"⚠️ Max {MAX_COLLAGE_PHOTOS} photos reached. Send /done to build collage."
        )
        return False

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document and (update.message.document.mime_type or "").startswith(
        "image/"
    ):
        file_id = update.message.document.file_id
    else:
        return False

    tg_file = await context.bot.get_file(file_id)
    raw = await tg_file.download_as_bytearray()

    session = _session_dir(context)
    idx = count + 1
    path = session / f"{idx:04d}.jpg"

    def _write():
        im = Image.open(BytesIO(bytes(raw)))
        # store medium square thumb to save disk/RAM
        tile = _fit_square(im, 512)
        tile.save(path, format="JPEG", quality=90, optimize=True)

    await asyncio.to_thread(_write)
    context.user_data["collage_count"] = idx

    # Progress every 10 photos or first few
    if idx <= 3 or idx % 10 == 0 or idx == MAX_COLLAGE_PHOTOS:
        await update.message.reply_text(
            f"📸 Saved {idx}/{MAX_COLLAGE_PHOTOS}. Send more or /done"
        )
    return True


# ───────────────── Handlers: PFP ─────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(
        "👋 *Commands*\n"
        "• Send X profile link / @username → HQ PFP photo\n"
        "• /collage → make a grid collage from many photos\n"
        "• /cancel → cancel collage session",
        parse_mode="Markdown",
    )


async def process_pfp_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    # Ignore if in collage collecting mode (conversation handles that)
    if context.user_data.get("collage_style"):
        return

    raw_text = update.message.text or update.message.caption
    if not raw_text:
        return

    usernames_found = []
    for word in raw_text.split():
        link_match = re.search(
            r"https?://(?:www\.)?(?:x|twitter)\.com/([a-zA-Z0-9_]+)", word
        )
        if link_match:
            usernames_found.append(link_match.group(1))
        else:
            clean_name = re.sub(r"[^a-zA-Z0-9_]", "", word)
            if clean_name:
                usernames_found.append(clean_name)

    unique_usernames = list(dict.fromkeys(usernames_found))
    if not unique_usernames:
        return

    status_msg = await update.message.reply_text(
        f"⏳ Extracting {len(unique_usernames)} profile(s) in High Quality..."
    )

    success_count = 0
    fail_count = 0

    for x_username in unique_usernames:
        try:
            data, filename = await download_hq_avatar(x_username)
            bio = BytesIO(data)
            bio.seek(0)
            await context.bot.send_photo(
                chat_id=TARGET_ADMIN_ID,
                photo=InputFile(bio, filename=filename),
            )
            success_count += 1
        except Exception as e:
            logging.warning("Failed to fetch for %s: %s", x_username, e)
            try:
                await context.bot.send_message(
                    chat_id=TARGET_ADMIN_ID,
                    text=f"❌ Failed to fetch PFP for: @{x_username}",
                )
            except Exception:
                pass
            fail_count += 1
        await asyncio.sleep(1)

    if len(unique_usernames) > 1:
        await status_msg.reply_text(
            f"✅ Done!\n\n📥 Success: {success_count}\n❌ Failed: {fail_count}"
        )
    else:
        try:
            await status_msg.delete()
        except Exception:
            pass


# ───────────────── Handlers: Collage conversation ─────────────────

async def collage_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    _cleanup_session(context)

    keyboard = [
        [
            InlineKeyboardButton(
                f"1️⃣ {STYLES['classic']['title']}", callback_data="style:classic"
            )
        ],
        [
            InlineKeyboardButton(
                f"2️⃣ {STYLES['tight']['title']}", callback_data="style:tight"
            )
        ],
        [
            InlineKeyboardButton(
                f"3️⃣ {STYLES['bordered']['title']}", callback_data="style:bordered"
            )
        ],
    ]
    await update.message.reply_text(
        "🧩 *Collage mode*\n\n"
        "Choose a *grid style*:\n\n"
        f"1️⃣ *{STYLES['classic']['title']}* — {STYLES['classic']['desc']}\n"
        f"2️⃣ *{STYLES['tight']['title']}* — {STYLES['tight']['desc']}\n"
        f"3️⃣ *{STYLES['bordered']['title']}* — {STYLES['bordered']['desc']}\n\n"
        f"Then send *{MIN_COLLAGE_PHOTOS}–{MAX_COLLAGE_PHOTOS}* photos, and /done when finished.\n"
        "/cancel to abort.",
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

    _session_dir(context)
    context.user_data["collage_style"] = style_key
    context.user_data["collage_count"] = 0

    s = STYLES[style_key]
    await query.edit_message_text(
        f"✅ Style: *{s['title']}*\n\n"
        f"Now send me photos ({MIN_COLLAGE_PHOTOS}–{MAX_COLLAGE_PHOTOS}).\n"
        "You can send many at once.\n\n"
        "When finished → /done\n"
        "Cancel → /cancel",
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
        await update.message.reply_text(
            "Send photos (or image files). When ready: /done"
        )
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
        await update.message.reply_text(
            f"Need at least {MIN_COLLAGE_PHOTOS} photos (have {count}). Keep sending or /cancel."
        )
        return COLLECTING

    status = await update.message.reply_text(
        f"🧩 Building *{STYLES[style_key]['title']}* collage from {count} photos...",
        parse_mode="Markdown",
    )

    paths = sorted(Path(session).glob("*.jpg"))

    try:
        data = await asyncio.to_thread(_build_grid_collage, paths, style_key)
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


async def collage_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    _cleanup_session(context)
    await update.message.reply_text("❌ Collage cancelled.")
    return ConversationHandler.END


# ───────────────── Main ─────────────────

if __name__ == "__main__":
    keep_alive()

    if not BOT_TOKEN or TARGET_ADMIN_ID == 0:
        print("ERROR: BOT_TOKEN or TARGET_ADMIN_ID is not set in environment variables.")
        exit(1)

    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()

    collage_conv = ConversationHandler(
        entry_points=[CommandHandler("collage", collage_start)],
        states={
            STYLE_SELECT: [
                CallbackQueryHandler(collage_style_chosen, pattern=r"^style:")
            ],
            COLLECTING: [
                MessageHandler(filters.PHOTO, collage_collect),
                MessageHandler(filters.Document.IMAGE, collage_collect),
                CommandHandler("done", collage_done),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", collage_cancel),
            CommandHandler("done", collage_done),
        ],
        allow_reentry=True,
    )

    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(collage_conv)
    # PFP text handler — lower priority than conversation
    bot_app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION) & ~filters.COMMAND, process_pfp_requests
        )
    )

    print("Scraper Bot is running smoothly...")
    bot_app.run_polling(drop_pending_updates=True)
