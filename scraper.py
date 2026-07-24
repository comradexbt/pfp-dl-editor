import os
import logging
import re
import asyncio
import urllib.request
from io import BytesIO
from flask import Flask
from threading import Thread
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Logging setup
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# Env vars
BOT_TOKEN = os.getenv("BOT_TOKEN")
TARGET_ADMIN_ID = int(os.getenv("TARGET_ADMIN_ID", 0))

# ===== DUMMY WEB SERVER =====
flask_app = Flask(__name__)


@flask_app.route("/")
def home():
    return "X PFP Scraper Bot is Alive and Running!"


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)


def keep_alive():
    t = Thread(target=run_flask)
    t.start()
# ============================


def _upgrade_twitter_avatar_url(url: str) -> str:
    """Twitter/X CDN size suffixes ko hata kar original full-size URL banata hai."""
    for suffix in ("_normal", "_bigger", "_mini", "_200x200", "_400x400", "_x96"):
        if suffix in url:
            url = url.replace(suffix, "")
    return url


def _download_hq_avatar(username: str) -> tuple[bytes, str]:
    """
    High quality avatar download:
    1) unavatar se large size
    2) agar twimg URL mile to original (no size suffix) try
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }

    # size=4096 => max quality from unavatar
    source_url = f"https://unavatar.io/x/{username}?size=4096"
    req = urllib.request.Request(source_url, headers=headers)
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = resp.read()
        final_url = resp.geturl()
        content_type = resp.headers.get("Content-Type", "image/jpeg") or "image/jpeg"

    # Upgrade to original Twitter CDN asset when possible
    upgraded = _upgrade_twitter_avatar_url(final_url)
    if upgraded != final_url:
        try:
            req2 = urllib.request.Request(upgraded, headers=headers)
            with urllib.request.urlopen(req2, timeout=45) as resp2:
                upgraded_data = resp2.read()
                if len(upgraded_data) >= len(data):
                    data = upgraded_data
                    final_url = upgraded
                    content_type = resp2.headers.get("Content-Type", content_type) or content_type
        except Exception as e:
            logging.info("Original-size upgrade skipped for @%s: %s", username, e)

    ext = "jpg"
    ct = content_type.lower()
    low = final_url.lower()
    if "png" in ct or low.endswith(".png"):
        ext = "png"
    elif "webp" in ct or low.endswith(".webp"):
        ext = "webp"
    elif "jpeg" in ct or "jpg" in ct or low.endswith(".jpg") or low.endswith(".jpeg"):
        ext = "jpg"

    return data, f"{username}.{ext}"


async def download_hq_avatar(username: str) -> tuple[bytes, str]:
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
            # send_document = no Telegram compression (full quality file)
            await context.bot.send_document(
                chat_id=TARGET_ADMIN_ID,
                document=InputFile(BytesIO(data), filename=filename),
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
    bot_app.run_polling()
