import os, logging, re, math, asyncio, requests
from io import BytesIO
from flask import Flask
from threading import Thread
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = "8585014628:AAHrW6o4dlKHfkoIFHVfKzWsM0a24fCk4s0"
TARGET_ADMIN_ID = 7323039280

user_images = {}
user_states = {}

# --- DYNAMIC KEYBOARDS ---
def get_main_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("🖼️ Start Collage Maker")]], resize_keyboard=True, persistent=True)

def get_collage_keyboard():
    return ReplyKeyboardMarkup([
        ["✅ Make Collage", "🗑️ Cancel Collage"],
        ["🔙 Back to PFP Mode"]
    ], resize_keyboard=True, persistent=True)

# --- COLLAGE LOGIC ---
def create_collage(image_list, text_watermark="Creators Club"):
    # (Pehle wala collage code yahan waisa hi rahay ga)
    images = []
    for img_data in image_list:
        try:
            if isinstance(img_data, str): 
                response = requests.get(img_data, timeout=5)
                img = Image.open(BytesIO(response.content)).convert("RGBA")
            else: 
                img = Image.open(BytesIO(img_data)).convert("RGBA")
            img = img.resize((150, 150))
            images.append(img)
        except: continue
    if not images: return None
    cols = math.ceil(math.sqrt(len(images)))
    rows = math.ceil(len(images) / cols)
    collage = Image.new('RGBA', (cols * 150, rows * 150), (0, 0, 0, 255))
    for idx, img in enumerate(images): collage.paste(img, ((idx % cols) * 150, (idx // cols) * 150))
    output = BytesIO()
    collage.convert("RGB").save(output, format='JPEG')
    output.seek(0)
    return output

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != TARGET_ADMIN_ID: return
    user_states[user_id] = "INSTANT_PFP"
    user_images[user_id] = []
    await update.message.reply_text("👋 Bot Ready!", reply_markup=get_main_keyboard())

async def handle_buttons_and_logic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != TARGET_ADMIN_ID: return
    text = update.message.text
    state = user_states.get(user_id, "INSTANT_PFP")

    # 1. Buttons Handle karna
    if text == "🖼️ Start Collage Maker":
        user_states[user_id] = "COLLAGE_MAKER"
        await update.message.reply_text("🎨 Mode: Collage. Link/Photo bhejein.", reply_markup=get_collage_keyboard())
        return
    elif text == "🔙 Back to PFP Mode":
        user_states[user_id] = "INSTANT_PFP"
        await update.message.reply_text("⚡ Mode: Instant PFP.", reply_markup=get_main_keyboard())
        return
    elif text == "🗑️ Cancel Collage":
        user_images[user_id] = []
        await update.message.reply_text("🗑️ List Saaf.")
        return
    elif text == "✅ Make Collage":
        img = create_collage(user_images[user_id])
        if img: await context.bot.send_photo(chat_id=user_id, photo=img)
        user_images[user_id] = []
        return

    # 2. Collage Mode mein Data Save karna
    if state == "COLLAGE_MAKER":
        if update.message.photo:
            # (Photo download logic)
            pass
        elif "x.com" in text or "twitter.com" in text:
            # (Link add logic)
            pass
        return

    # 3. Default PFP Mode (Sirf tab chalay ga jab upar kuch nahi hoga)
    if "x.com" in text or "twitter.com" in text:
        # (Direct PFP logic)
        pass

if __name__ == '__main__':
    # ... Flask ...
    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    # Yahan filter sahi set karna hai: Button pehle, phir normal text
    bot_app.add_handler(MessageHandler(filters.TEXT, handle_buttons_and_logic)) 
    bot_app.run_polling()
