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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
TARGET_ADMIN_ID = int(os.getenv("TARGET_ADMIN_ID", 0))

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
    """Twitter CDN variants. Original (no size suffix) is best quality available."""
    urls = []
    if not avatar_url:
        return urls

    # _normal is only 48x48 (blurry). Strip size suffixes for full original.
    original = re.sub(
        r"_(normal|bigger|mini|200x200|400x400|x96)(?=\.[A-Za-z0-9]+$)",
        "",
        avatar_url,
    )
    if "." in original:
        four = re.sub(r"(\.[A-Za-z0-9]+)$", r"_400x400\1", original)
    else:
        four = original

    for u in (original, four, avatar_url):
        if u and u not in urls:
            urls.append(u)
    return urls


def _download_hq_avatar(username: str):
    """
    Real HQ:
    1) FixTweet API -> real pbs.twimg.com avatar URL
    2) Strip _normal (48x48) so we get original CDN image
    3) Pick largest download
    4) unavatar only as last fallback (often low quality)
    """
    candidates = []

    try:
        raw, _, _ = _http_get(f"https://api.fxtwitter.com/{username}")
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        avatar_url = (payload.get("user") or {}).get("avatar_url") or ""
        logging.info("@%s fxtwitter avatar_url=%s", username, avatar_url)

        for url in _avatar_variants(avatar_url):
            try:
                data, final, ctype = _http_get(url)
                if not data or len(data) < 500:
                    continue
                if "html" in ctype.lower() or data[:1] in (b"<", b"{"):
                    continue
                candidates.append((len(data), data, final))
                logging.info("@%s candidate %s bytes <- %s", username, len(data), final)
            except Exception as e:
                logging.info("@%s variant fail %s: %s", username, url, e)
    except Exception as e:
        logging.warning("@%s fxtwitter failed: %s", username, e)

    if not candidates:
        for u in (
            f"https://unavatar.io/x/{username}?size=4096",
            f"https://unavatar.io/twitter/{username}?size=4096",
        ):
            try:
                data, final, ctype = _http_get(u)
                if data and len(data) >= 500 and "html" not in ctype.lower():
                    candidates.append((len(data), data, final))
            except Exception as e:
                logging.info("@%s unavatar fail %s: %s", username, u, e)

    if not candidates:
        raise RuntimeError(f"No avatar found for @{username}")

    candidates.sort(key=lambda x: x[0], reverse=True)
    size, data, final = candidates[0]
    logging.info("@%s CHOSE %s bytes from %s", username, size, final)

    ext = "jpg"
    low = (final or "").lower()
    if low.endswith(".png") or data[:8].startswith(b"\x89PNG"):
        ext = "png"
    elif low.endswith(".webp") or data[:4] == b"RIFF":
        ext = "webp"

    return data, f"{username}_hq.{ext}"


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
            # send_document keeps original file quality (no Telegram photo compression)
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
