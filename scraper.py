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

# ===== DUMMY WEB SERVER (24/7 On rakhne ke liye) =====
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
# ======================================================

# 1. Start Command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != TARGET_ADMIN_ID: return
    await update.message.reply_text(
        "🚀 **X PFP Scraper Ready!**\n\n"
        "Aap ek sath jitne marzi Twitter usernames ya links paste kar ke bhej dein "
        "(chahe space de kar likhein ya nayi line me). Bot sab ki profile pictures nikaal kar aap ko bhej dega."
    )

# 2. Bulk Processing Logic
async def process_pfp_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Sirf aap use kar sakein is liye security check
    if update.message.from_user.id != TARGET_ADMIN_ID: return

    raw_text = update.message.text
    if not raw_text: return

    # Text ko split karna (space, comma, ya nayi line ke zariye)
    raw_items = re.split(r'[\s,\n]+', raw_text)
    
    # Khali items mita kar clean list banana
    items = [item.strip() for item in raw_items if item.strip()]

    if not items:
        await update.message.reply_text("❌ Mujhe koi valid username ya link nahi mila.")
        return

    status_msg = await update.message.reply_text(f"⏳ Total {len(items)} profiles mili hain. Processing shuru ho rahi hai...")

    success_count = 0
    fail_count = 0

    for item in items:
        x_username = None
        
        # Username extract karne ka smart logic
        if "x.com" in item.lower() or "twitter.com" in item.lower():
            x_username = item.rstrip('/').split('/')[-1].split('?')[0]
        else:
            x_username = item.replace("@", "").strip()

        if not x_username:
            fail_count += 1
            continue

        avatar_url = f"https://unavatar.io/x/{x_username}"
        caption_text = f"👤 **Username:** @{x_username}\n🔗 **Link:** https://x.com/{x_username}"

        try:
            # Picture seedhi aap ki chat mein send hogi
            await context.bot.send_photo(chat_id=TARGET_ADMIN_ID, photo=avatar_url, caption=caption_text)
            success_count += 1
        except Exception as e:
            logging.warning(f"Failed to fetch for {x_username}: {e}")
            # Agar picture na mile toh text message bhej dega taake record miss na ho
            try:
                await context.bot.send_message(chat_id=TARGET_ADMIN_ID, text=f"❌ **Failed to fetch PFP for:** @{x_username}\n🔗 Link: https://x.com/{x_username}")
            except:
                pass
            fail_count += 1

        # 🚨 Rate limit se bachne ke liye har photo ke baad 1 second ka pause
        await asyncio.sleep(1)

    await status_msg.reply_text(f"✅ **Kaam Mukammal!**\n\n📥 Success: {success_count}\n❌ Failed: {fail_count}")

if __name__ == '__main__':
    keep_alive()
    
    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_pfp_requests))
    
    print("Scraper Bot is running...")
    bot_app.run_polling()