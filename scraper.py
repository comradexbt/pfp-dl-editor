import os
import logging
import re
import math
import requests
from io import BytesIO
from flask import Flask
from threading import Thread
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BOT_TOKEN = "8585014628:AAHrW6o4dlKHfkoIFHVfKzWsM0a24fCk4s0"
TARGET_ADMIN_ID = 7323039280  

# Data temporary store karne ke liye
user_images = {}

# ===== DUMMY SERVER =====
flask_app = Flask(__name__)
@flask_app.route('/')
def home(): return "Collage Bot Alive!"
def run_flask(): flask_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
Thread(target=run_flask, daemon=True).start()

# --- COLLAGE ENGINE ---
def create_collage(image_list, text_watermark="Creators Club"):
    images = []
    for img_data in image_list:
        try:
            if isinstance(img_data, str): # Agar URL hai
                response = requests.get(img_data, timeout=5)
                img = Image.open(BytesIO(response.content)).convert("RGBA")
            else: # Agar direct image file hai
                img = Image.open(BytesIO(img_data)).convert("RGBA")
            
            img = img.resize((150, 150))
            images.append(img)
        except: continue

    if not images: return None
    cols = math.ceil(math.sqrt(len(images)))
    rows = math.ceil(len(images) / cols)
    collage = Image.new('RGBA', (cols * 150, rows * 150), (0, 0, 0, 255))

    for idx, img in enumerate(images):
        collage.paste(img, ((idx % cols) * 150, (idx // cols) * 150))

    # Watermark
    draw = ImageDraw.Draw(collage)
    font = ImageFont.load_default(size=40)
    w, h = collage.size
    draw.text((w/2 - 100, h/2 - 20), text_watermark, font=font, fill="white")
    
    output = BytesIO()
    collage.convert("RGB").save(output, format='JPEG')
    output.seek(0)
    return output

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != TARGET_ADMIN_ID: return
    user_images[TARGET_ADMIN_ID] = []
    kb = [[KeyboardButton("🖼️ New Collage")], [KeyboardButton("✅ Make Collage Now"), KeyboardButton("🗑️ Clear List")]]
    await update.message.reply_text("👋 Bot Ready! Links bhejein ya Photos upload karein.", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, persistent=True))

async def handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != TARGET_ADMIN_ID: return

    # Agar button dabaya hai
    text = update.message.text
    if text == "🖼️ New Collage": user_images[user_id] = []; await update.message.reply_text("✨ List Khali! Ab bhejain."); return
    if text == "🗑️ Clear List": user_images[user_id] = []; await update.message.reply_text("🗑️ Done."); return
    
    if text == "✅ Make Collage Now":
        if not user_images.get(user_id): await update.message.reply_text("❌ List Khali hai!"); return
        await update.message.reply_text("🎨 Collage ban raha hai...")
        img = create_collage(user_images[user_id])
        if img: await context.bot.send_photo(chat_id=user_id, photo=img, caption="🎉 Aap ka Collage!"); user_images[user_id] = [] # Auto-Delete Data
        else: await update.message.reply_text("❌ Error!"); user_images[user_id] = []
        return

    # Agar Photo bheji hai
    if update.message.photo:
        photo_file = await update.message.photo[-1].get_file()
        img_bytes = BytesIO()
        await photo_file.download_to_memory(img_bytes)
        user_images.setdefault(user_id, []).append(img_bytes.getvalue())
        await update.message.reply_text(f"📸 Image add hui! Total: {len(user_images[user_id])}")

    # Agar Link bheja hai
    elif text:
        links = re.findall(r'(https?://\S+)', text)
        for link in links:
            if "x.com" in link or "twitter.com" in link:
                username = link.rstrip('/').split('/')[-1]
                user_images.setdefault(user_id, []).append(f"https://unavatar.io/x/{username}")
        await update.message.reply_text(f"🔗 Links processed! Total: {len(user_images[user_id])}")

if __name__ == '__main__':
    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_input))
    bot_app.run_polling()