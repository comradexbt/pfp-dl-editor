import os
import logging
import re
import asyncio
import json
import urllib.request
from io import BytesIO
from flask import Flask
from threading import Thread
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from PIL import Image, ImageEnhance, ImageFilter

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
TARGET_ADMIN_ID = int(os.getenv("TARGET_ADMIN_ID", 0))

# Output size for sharp full-screen viewing on phones
TARGET_SIZE = 1024

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
    """Build Twitter CDN variants. Original (no size suffix) is best quality."""
    urls = []
    if not avatar_url:
        return urls

    # _normal = 48x48 blurry. Strip size suffixes for original CDN file.
    original = re.sub(
        r"_(normal|bigger|mini|200x200|400x400|x96)(?=\.[A-Za-z0-9]+$)",
        "",
        avatar_url,
    )
    if "." in original:
        four = re.sub(r"(\.[A-Za-z0-9]+)$", r"_400x400\1", original)
        bigger = re.sub(r"(\.[A-Za-z0-9]+)$", r"_bigger\1", original)
    else:
        four = original
        bigger = original

    for u in (original, four, bigger, avatar_url):
        if u and u not in urls:
            urls.append(u)
    return urls


def _enhance_to_hq(data: bytes) -> tuple[bytes, str]:
    """
    X stores PFPs at max ~400x400 — on phone screens that looks soft/blurry.
    We take the best source pixels, upscale with LANCZOS + mild sharpen,
    and export a high-quality JPEG for sharp full-screen viewing.
    """
    im = Image.open(BytesIO(data))
    if im.mode not in ("RGB", "L"):
        # keep alpha as white background for RGBA
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            rgba = im.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
    else:
        im = im.convert("RGB")

    w, h = im.size
    logging.info("source image %sx%s", w, h)

    # Reject tiny/broken images (likely _normal 48px)
    if max(w, h) < 80:
        raise RuntimeError(f"source too small: {w}x{h}")

    # Upscale so phone full-screen is not blocky
    if max(w, h) < TARGET_SIZE:
        scale = TARGET_SIZE / float(max(w, h))
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        im = im.resize(new_size, Image.Resampling.LANCZOS)
        # Mild sharpen — makes edges crisp after upscale
        im = im.filter(ImageFilter.UnsharpMask(radius=1.4, percent=140, threshold=2))
        im = ImageEnhance.Sharpness(im).enhance(1.15)
        im = ImageEnhance.Contrast(im).enhance(1.05)
    else:
        # Already large enough — light polish only
        im = ImageEnhance.Sharpness(im).enhance(1.1)

    out = BytesIO()
    # quality=97 + no chroma subsampling = clean JPEG
    im.save(
        out,
        format="JPEG",
        quality=97,
        optimize=True,
        progressive=True,
        subsampling=0,
    )
    return out.getvalue(), "jpg"


def _download_hq_avatar(username: str):
    """
    1) FixTweet -> real pbs.twimg.com URL
    2) Strip _normal, pick largest CDN file
    3) Enhance/upscale to sharp 1024px JPEG
    """
    candidates = []  # (bytes_len, data, final_url)

    try:
        raw, _, _ = _http_get(f"https://api.fxtwitter.com/{username}")
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        avatar_url = (payload.get("user") or {}).get("avatar_url") or ""
        logging.info("@%s fxtwitter avatar_url=%s", username, avatar_url)

        for url in _avatar_variants(avatar_url):
            try:
                data, final, ctype = _http_get(url)
                if not data or len(data) < 800:
                    continue
                if "html" in ctype.lower() or data[:1] in (b"<", b"{"):
                    continue
                # Prefer larger payloads; skip obvious tiny thumbs
                if len(data) < 3000:
                    # might still be bigger.jpg ~2.5kb — keep as last resort
                    candidates.append((len(data), data, final))
                else:
                    candidates.append((len(data) + 10_000_000, data, final))  # boost non-tiny
                logging.info("@%s candidate %s bytes <- %s", username, len(data), final)
            except Exception as e:
                logging.info("@%s variant fail %s: %s", username, url, e)
    except Exception as e:
        logging.warning("@%s fxtwitter failed: %s", username, e)

    # Fallback sources
    if not any(c[0] > 10_000_000 for c in candidates):
        for u in (
            f"https://unavatar.io/x/{username}?size=4096",
            f"https://unavatar.io/twitter/{username}?size=4096",
        ):
            try:
                data, final, ctype = _http_get(u)
                if data and len(data) >= 800 and "html" not in ctype.lower():
                    candidates.append((len(data), data, final))
            except Exception as e:
                logging.info("@%s unavatar fail %s: %s", username, u, e)

    if not candidates:
        raise RuntimeError(f"No avatar found for @{username}")

    # Sort by score (boosted size), then real size
    candidates.sort(key=lambda x: (x[0], len(x[1])), reverse=True)

    last_err = None
    for score, data, final in candidates:
        try:
            hq_bytes, ext = _enhance_to_hq(data)
            logging.info(
                "@%s CHOSE source=%s (%s bytes) -> hq=%s bytes",
                username,
                final,
                len(data),
                len(hq_bytes),
            )
            return hq_bytes, f"{username}_hq.{ext}"
        except Exception as e:
            last_err = e
            logging.info("@%s enhance fail for %s: %s", username, final, e)

    raise RuntimeError(f"Could not build HQ avatar for @{username}: {last_err}")


async def download_hq_avatar(username: str):
    return await asyncio.to_thread(_download_hq_avatar, username)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != TARGET_ADMIN_ID:
        return
    await update.message.reply_text(
        "👋 Send X profile link, @username, or just the name to download PFP."
    )


async def process_pfp_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != TARGET_ADMIN_ID:
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
            await context.bot.send_document(
                chat_id=TARGET_ADMIN_ID,
                document=InputFile(bio, filename=filename),
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


if __name__ == "__main__":
    keep_alive()

    if not BOT_TOKEN or TARGET_ADMIN_ID == 0:
        print("ERROR: BOT_TOKEN or TARGET_ADMIN_ID is not set in environment variables.")
        exit(1)

    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION) & ~filters.COMMAND, process_pfp_requests
        )
    )

    print("Scraper Bot is running smoothly...")
    bot_app.run_polling(drop_pending_updates=True)
