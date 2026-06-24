import os
import logging
import re
import asyncio
from flask import Flask
from threading import Thread
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Logging setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Aap ke naye bot ki details
BOT_TOKEN = "8585014628:AAHrW6o4dlKHfkoIFHVfKzWsM0a24fCk4s0"
TARGET_ADMIN_ID = 7323039280  

# ===== DUMMY WEB SERVER =====
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "X PFP Scraper Bot is Alive and Running!"

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    flask_app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()
# ============================

# 1. Start Command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != TARGET_ADMIN_ID: return
    await update.message.reply_text("👋 Send X profile link to download PFP.")

# 2. Bulk Processing & Forwarded Message Logic
async def process_pfp_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != TARGET_ADMIN_ID: return

    # text ya caption dono check karega
    raw_text = update.message.text or update.message.caption
    if not raw_text: return

    # SMART REGEX
    usernames_found = re.findall(r'https?://(?:www\.)?(?:x|twitter)\.com/([a-zA-Z0-9_]+)', raw_text)
    
    unique_usernames = list(set(usernames_found))

    if not unique_usernames: 
        return

    status_msg = await update.message.reply_text(f"⏳ Extracting {len(unique_usernames)} profile(s) in High Quality...")

    success_count = 0
    fail_count = 0

    for x_username in unique_usernames:
        avatar_url = f"https://unavatar.io/x/{x_username}?size=1000"

        try:
            # Yahan se caption hata diya gaya hai, ab sirf picture aayegi
            await context.bot.send_photo(chat_id=TARGET_ADMIN_ID, photo=avatar_url)
            success_count += 1
        except Exception as e:
            logging.warning(f"Failed to fetch for {x_username}: {e}")
            try:
                await context.bot.send_message(chat_id=TARGET_ADMIN_ID, text=f"❌ **Failed to fetch PFP for:** @{x_username}")
            except: pass
            fail_count += 1

        # Telegram rate limit se bachne ke liye 1 second ka pause
        await asyncio.sleep(1)

    if len(unique_usernames) > 1:
        await status_msg.reply_text(f"✅ **Done!**\n\n📥 Success: {success_count}\n❌ Failed: {fail_count}")
    else:
        await status_msg.delete()

if __name__ == '__main__':
    keep_alive()
    
    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    
    bot_app.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, process_pfp_requests))
    
    print("Scraper Bot is running smoothly...")
    bot_app.run_polling()
