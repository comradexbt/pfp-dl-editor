import os
import logging
import re
import math
import asyncio
import requests
from io import BytesIO
from flask import Flask
from threading import Thread
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Logging setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BOT_TOKEN = "8585014628:AAHrW6o4dlKHfkoIFHVfKzWsM0a24fCk4s0"
TARGET_ADMIN_ID = 7323039280  

# Data and State Management
user_images = {}
user_states = {} # "INSTANT_PFP" ya "COLLAGE_MAKER"

# ===== DUMMY SERVER =====
flask_app = Flask(__name__)
@flask_app.route('/')
def home(): 
    return "X PFP & Collage Bot is Alive!"
def run_flask(): 
    flask_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# --- COLLAGE ENGINE ---
def create_collage(image_list, text_watermark="Creators Club"):
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
        except: 
            continue

    if not images: return None
    
    cols = math.ceil(math.sqrt(len(images)))
    rows = math.ceil(len(images) / cols)
    collage = Image.new('RGBA', (cols * 150, rows * 150), (0, 0, 0, 255))

    for idx, img in enumerate(images):
        collage.paste(img, ((idx % cols) * 150, (idx // cols) * 150))

    # Watermark
    draw = ImageDraw.Draw(collage)
    try: font = ImageFont.load_default(size=40)
    except: font = ImageFont.load_default()
        
    w, h = collage.size
    bbox = draw.textbbox((0, 0), text_watermark, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (w - tw) / 2
    ty = (h - th) / 2
    
    draw.rectangle([tx - 10, ty - 10, tx + tw + 10, ty + th + 10], fill=(0, 0, 0, 150))
    draw.text((tx, ty), text_watermark, font=font, fill="white")
    
    output = BytesIO()
    collage.convert("RGB").save(output, format='JPEG')
    output.seek(0)
    return output

# --- DYNAMIC KEYBOARDS ---
def get_instant_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("🖼️ Start Collage Maker")]], resize_keyboard=True, persistent=True)

def get_collage_keyboard():
    keyboard = [
        [KeyboardButton("✅ Make Collage"), KeyboardButton("🗑️ Cancel Collage")],
        [KeyboardButton("🔙 Back to PFP Mode")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, persistent=True)


# --- MAIN LOGIC ---
async def handle_everything(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != TARGET_ADMIN_ID: return
    
    text = update.message.text or ""
    current_state = user_states.get(user_id, "INSTANT_PFP")

    # 1. Start Command (Reset Everything)
    if text == "/start":
        user_states[user_id] = "INSTANT_PFP"
        user_images[user_id] = []
        await update.message.reply_text(
            "👋 **Bot is Ready!**\n\n"
            "⚡ **Instant PFP Mode Active:** Mujhe koi bhi Twitter link bhejein, main foran PFP bhejunga.\n\n"
            "Agar Collage banana hai toh neeche diye gaye button par click karein.",
            reply_markup=get_instant_keyboard()
        )
        return

    # 2. Button: Start Collage Maker
    if text == "🖼️ Start Collage Maker":
        user_states[user_id] = "COLLAGE_MAKER"
        user_images[user_id] = []
        await update.message.reply_text(
            "🎨 **Collage Mode ON!**\n\n"
            "Ab aap jo bhi Links ya Photos bhejenge, wo Collage ke liye save honge.\n"
            "Jab tasweerein poori ho jayen toh **'✅ Make Collage'** dabayen.",
            reply_markup=get_collage_keyboard()
        )
        return

    # 3. Button: Back to PFP Mode
    if text == "🔙 Back to PFP Mode":
        user_states[user_id] = "INSTANT_PFP"
        user_images[user_id] = []
        await update.message.reply_text(
            "⚡ **Wapis Instant PFP Mode Mein Aagaye!**\n\n"
            "Ab bheje gaye links ki PFP direct download hogi.",
            reply_markup=get_instant_keyboard()
        )
        return

    # 4. Button: Cancel Collage
    if text == "🗑️ Cancel Collage":
        user_images[user_id] = []
        await update.message.reply_text("🗑️ **Collage Cancelled.**\nSaari list clear kar di gayi hai. Aap naye links bhej sakte hain ya wapis PFP mode mein ja sakte hain.")
        return

    # 5. Button: Make Collage
    if text == "✅ Make Collage":
        if not user_images.get(user_id): 
            await update.message.reply_text("❌ List khali hai! Pehle tasweerein ya links bhejein.")
            return
            
        await update.message.reply_text("🎨 Collage ban raha hai, please wait...")
        img = create_collage(user_images[user_id])
        
        if img: 
            await context.bot.send_photo(chat_id=user_id, photo=img, caption="🎉 Yeh raha aap ka Creators Club Collage!")
        else: 
            await update.message.reply_text("❌ Collage banane mein masla aagaya.")
            
        user_images[user_id] = [] # Memory saaf
        return

    # ==========================================
    # LOGIC: Jab User Collage Mode Mein Ho
    # ==========================================
    if current_state == "COLLAGE_MAKER":
        if update.message.photo:
            photo_file = await update.message.photo[-1].get_file()
            img_bytes = BytesIO()
            await photo_file.download_to_memory(img_bytes)
            user_images[user_id].append(img_bytes.getvalue())
            await update.message.reply_text(f"📸 Image collage ke liye save ho gayi! Total: {len(user_images[user_id])}")
        
        elif text:
            links = re.findall(r'(https?://[^\s]+)', text)
            added = 0
            for link in links:
                if "x.com" in link or "twitter.com" in link:
                    username = link.rstrip('/').split('/')[-1].split('?')[0]
                    user_images[user_id].append(f"https://unavatar.io/x/{username}")
                    added += 1
            if added > 0:
                await update.message.reply_text(f"🔗 {added} Links collage ke liye save ho gaye! Total: {len(user_images[user_id])}")

    # ==========================================
    # LOGIC: Jab User Instant PFP Mode Mein Ho
    # ==========================================
    elif current_state == "INSTANT_PFP":
        if not text: return
        
        links = re.findall(r'(https?://[^\s]+)', text)
        valid_links = [link for link in links if "x.com" in link or "twitter.com" in link]
        
        if not valid_links: return

        for link in valid_links:
            username = link.rstrip('/').split('/')[-1].split('?')[0]
            avatar_url = f"https://unavatar.io/x/{username}"
            caption_text = f"👤 @{username}\n🔗 {link}"
            try:
                await context.bot.send_photo(chat_id=user_id, photo=avatar_url, caption=caption_text)
            except Exception as e:
                await context.bot.send_message(chat_id=user_id, text=f"❌ Failed to fetch PFP for @{username}")
            
            await asyncio.sleep(1)

if __name__ == "__main__":
    print("Bot is starting...")
    # Polling ko thoda stable banane ke liye parameters
    bot.polling(none_stop=True, interval=0, timeout=20)