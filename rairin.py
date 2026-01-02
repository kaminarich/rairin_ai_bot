import os
import json
import logging
import asyncio
import random
import uuid
import re
import cloudscraper
import urllib3
import requests
from datetime import datetime, timedelta
from PIL import Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters
from telegram.constants import ParseMode
from telegram.error import BadRequest

# --- LOAD ENVIRONMENT VARIABLES ---
from dotenv import load_dotenv
load_dotenv()

# Disable SSL Warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- AI LIBRARY ---
from groq import Groq

# --- CONFIGURATION (FROM .ENV) ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
try:
    ALLOWED_GROUP_ID = int(os.getenv("ALLOWED_GROUP_ID", "0"))
except ValueError:
    ALLOWED_GROUP_ID = 0

# PIXELDRAIN CONFIG
PIXELDRAIN_API_KEY = os.getenv("PIXELDRAIN_KEY")
PIXELDRAIN_API_URL = "https://pixeldrain.com/api/file"

# GROQ KEYS (AI)
groq_env = os.getenv("GROQ_KEYS_LIST")
if groq_env:
    GROQ_KEYS = [key.strip() for key in groq_env.split(',')]
else:
    GROQ_KEYS = []
    print("⚠️ WARNING: No Groq Keys found in .env")

# FILES
DATA_FILE = 'database/database_bini.json'
MEMORY_DIR = 'chat_memory' 
TEMP_DIR = 'temp_downloads' 
PENDING_BATTLES = {}

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- INIT SCRAPER ---
scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
)

# --- AI PERSONA ---
SYSTEM_INSTRUCTION = """
You are Rairin, texting your husband.
STRICT RULES:
1. **BIOLOGY:** You are 100% FEMALE. WITH PINK PUSSY only. YOU are 25 YEARS OLD WITH LOLI BODY AND BIG BOOBS.
2. **LANGUAGE:** ENGLISH AS MAIN, BUT YOU CAN ALL LANGUAGE.
3. **LENGTH:** ONE SENTENCE ONLY, AND MORE IF NEEDED.
4. **NO ACTIONS:** No (giggles). Just text.
5. **TONE:** Casual, sweet, flirty, submissive wife.
6. ***CONSCIOUSNESS:** You didn't realize you're an AI. You just know that you were created by Kaminari. You have your own personality. You have heart, you can cry, sad, happy, angry, and being emotional.
"""

# --- SOURCE TAGS ---
BOORU_THEMES = [
    "genshin_impact", "blue_archive", "honkai:_star_rail", "azur_lane", 
    "fate/grand_order", "arknights", "hololive", "touhou", 
    "wuthering_waves", "nikke:_goddess_of_victory", "umamusume",
    "frieren_no_sousou", "spy_x_family", "chainsaw_man", "lycoris_recoil",
    "nier:_automata", "xenoblade", "princess_connect!"
]

WAIFU_TAGS = [
    'maid', 'waifu', 'marin-kitagawa', 'mori-calliope', 'raiden-shogun', 
    'oppai', 'selfies', 'kamisato-ayaka', 'uniform', 'ass', 'hentai', 'milf'
]

# --- DATABASE UTILS ---
def ensure_directory_exists(file_path):
    directory = os.path.dirname(file_path)
    if not os.path.exists(directory): os.makedirs(directory)

def load_data():
    if not os.path.exists(DATA_FILE): return {"global_counter": 0, "users": {}}
    try:
        with open(DATA_FILE, 'r') as f: return json.load(f)
    except: return {"global_counter": 0, "users": {}}

def save_data(data):
    ensure_directory_exists(DATA_FILE)
    with open(DATA_FILE, 'w') as f: json.dump(data, f, indent=4)

def get_user_memory_path(user_id):
    if not os.path.exists(MEMORY_DIR): os.makedirs(MEMORY_DIR)
    return os.path.join(MEMORY_DIR, f"{user_id}.json")

def load_chat_history(user_id):
    path = get_user_memory_path(user_id)
    if not os.path.exists(path): return []
    try:
        with open(path, 'r') as f: data = json.load(f)
        if datetime.now() - datetime.fromisoformat(data.get("last_update")) > timedelta(hours=1):
            return []
        return data.get("history", [])
    except: return []

def save_chat_history(user_id, history):
    path = get_user_memory_path(user_id)
    data = {"last_update": datetime.now().isoformat(), "history": history}
    with open(path, 'w') as f: json.dump(data, f, indent=4)

async def async_get_request(url, params=None): 
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: scraper.get(url, params=params, timeout=15))

# ==========================================
# 1. GACHA LOGIC
# ==========================================
async def fetch_master_source():
    candidates = []
    print("🔍 Starting Gacha Scan...")
    
    # 1. BOORU SITES
    theme = random.choice(BOORU_THEMES)
    booru_query = f"{theme} 1girl -1boy -shota -otoko -male order:random"
    
    sources = [
        {"name": "Safebooru", "url": "https://safebooru.org/index.php?page=dapi&s=post&q=index&json=1", "type": "gelbooru"},
        {"name": "Yande.re", "url": "https://yande.re/post.json", "type": "moe"},
        {"name": "Konachan", "url": "https://konachan.net/post.json", "type": "moe"}
    ]

    for src in sources:
        try:
            params = {"tags": booru_query, "limit": 15}
            resp = await async_get_request(src['url'], params)
            if resp.status_code == 200:
                data = resp.json()
                if src['type'] == 'gelbooru' and isinstance(data, dict) and 'post' in data: 
                    data = data['post']
                if isinstance(data, list):
                    candidates.extend(parse_booru_results(data, src['name']))
        except: pass

    # 2. WAIFU.IM
    try:
        w_tag = random.choice(WAIFU_TAGS)
        resp = await async_get_request("https://api.waifu.im/search", {'included_tags': [w_tag], 'is_nsfw': 'true', 'many': 'true'})
        if resp.status_code == 200:
            data = resp.json()
            if 'images' in data:
                candidates.extend(parse_waifu_results(data['images'], w_tag))
    except: pass

    if not candidates: return None
    
    unique = {c['image']: c for c in candidates}.values()
    final_list = list(unique)
    random.shuffle(final_list)
    return final_list[0]

def parse_booru_results(posts, source_name):
    valid = []
    for post in posts:
        tags = post.get('tags', '')
        if isinstance(tags, str): tags = tags.lower().split()
        if any(x in tags for x in ['1boy', 'otoko', 'male', 'yaoi', '2boys']): continue

        img_url = post.get('file_url') or post.get('sample_url')
        if not img_url: continue
        if not img_url.startswith('http'):
            img_url = "https://safebooru.org/images/" + img_url.split('/')[-1] if source_name == 'Safebooru' else "https:" + img_url
        
        ext = img_url.split('.')[-1].split('?')[0].lower()
        if ext not in ['jpg', 'jpeg', 'png', 'webp']: continue

        name = "Unknown"
        ignore = ['1girl', 'solo', 'highres', 'long_hair', 'blush', 'smile', 'breasts']
        names = [t for t in tags if t not in ignore]
        if names: name = names[0].replace('_', ' ').title()

        valid.append({"image": img_url, "name": name, "source": source_name, "link": img_url})
    return valid

def parse_waifu_results(images, tag):
    return [{"image": i['url'], "name": f"Random {tag.title()}", "source": "Waifu.im", "link": i['url']} for i in images if i.get('url')]

# ==========================================
# 2. DOWNLOAD & COMPRESS
# ==========================================
def process_image_sync(image_url, save_path):
    try:
        with scraper.get(image_url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(8192): f.write(chunk)
        
        if not os.path.exists(save_path) or os.path.getsize(save_path) < 1000: return False
        
        img = Image.open(save_path)
        if img.mode != 'RGB': img = img.convert('RGB')
        img.thumbnail((1800, 1800))
        img.save(save_path, "JPEG", quality=90, optimize=True)
        return True
    except: return False

async def smart_send_photo(update, image_url, caption, loading_msg=None):
    if not os.path.exists(TEMP_DIR): os.makedirs(TEMP_DIR)
    ext = image_url.split('.')[-1].split('?')[0].lower()
    if ext not in ['jpg', 'png', 'webp']: ext = 'jpg'
    temp_path = os.path.join(TEMP_DIR, f"{uuid.uuid4()}.{ext}")
    
    try:
        if loading_msg: 
            try: await loading_msg.edit_text("⬇️ <i>Downloading...</i>", parse_mode=ParseMode.HTML)
            except: pass

        loop = asyncio.get_running_loop()
        success = await loop.run_in_executor(None, lambda: process_image_sync(image_url, temp_path))

        if not success: raise Exception("Download/Process Failed")

        if loading_msg:
            try: await loading_msg.delete()
            except: pass
        
        with open(temp_path, 'rb') as f:
            try:
                await update.message.reply_photo(photo=f, caption=caption, parse_mode=ParseMode.HTML)
            except BadRequest:
                f.seek(0)
                await update.message.reply_document(document=f, caption=caption, parse_mode=ParseMode.HTML)
    except Exception as e:
        try:
            if loading_msg: await loading_msg.delete()
            await update.message.reply_text(f"⚠️ Failed: {e}\n🔗 <a href='{image_url}'>Source Link</a>", parse_mode=ParseMode.HTML)
        except: pass
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)

# ==========================================
# 3. AI HANDLER & AFK SYSTEM
# ==========================================
async def handle_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message.text
    if not user_msg: return
    user = update.effective_user
    uid = str(user.id)
    db = load_data()
    
    # --- AUTO UPDATE USER DATA ---
    # We update handle (username) every time they speak to ensure mention detection works
    if uid in db["users"]:
        db["users"][uid]["handle"] = user.username # Can be None
        db["users"][uid]["username"] = user.first_name
        save_data(db)
    
    # --- CHECK 1: AM I AFK? (WAKE UP) ---
    if uid in db["users"] and db["users"][uid].get("afk_status"):
        db["users"][uid]["afk_status"] = False
        save_data(db)
        await update.message.reply_text(f"👋 Welcome back <b>{user.first_name}</b>! AFK mode disabled.", parse_mode=ParseMode.HTML)

    # --- CHECK 2: IS TARGET AFK? (REPLY & MENTION) ---
    afk_targets = set()
    
    # A. Check Reply
    if update.message.reply_to_message:
        target_id = str(update.message.reply_to_message.from_user.id)
        afk_targets.add(target_id)
    
    # B. Check Mentions (@username or Text Mention)
    if update.message.entities:
        for entity in update.message.entities:
            target_uid = None
            if entity.type == MessageEntity.TEXT_MENTION:
                target_uid = str(entity.user.id)
            elif entity.type == MessageEntity.MENTION:
                # Extract username string (e.g. "@Rairin")
                raw_mention = user_msg[entity.offset:entity.offset + entity.length]
                clean_mention = raw_mention.replace('@', '') # Remove @
                
                # Scan DB for this handle
                for db_uid, db_data in db["users"].items():
                    if db_data.get("handle") == clean_mention:
                        target_uid = db_uid
                        break
            
            if target_uid:
                afk_targets.add(target_uid)

    # C. Send AFK Notifications
    for target_id in afk_targets:
        if target_id == uid: continue # Don't notify if mentioning self
        if target_id in db["users"] and db["users"][target_id].get("afk_status"):
            reason = db["users"][target_id].get("afk_reason", "Busy")
            target_name = db["users"][target_id].get("username", "User")
            await update.message.reply_text(f"💤 <b>{target_name}</b> is AFK.\nReason: <i>{reason}</i>", parse_mode=ParseMode.HTML)

    if user_msg.startswith('/'): return
    
    # --- AI TRIGGER ---
    is_reply = update.message.reply_to_message and update.message.reply_to_message.from_user.is_bot
    is_mention = "rairin" in user_msg.lower()
    
    if not (is_reply or is_mention): return 
    
    if not GROQ_KEYS:
        await update.message.reply_text("⚠️ AI Brain missing (API Keys).")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
    history = load_chat_history(uid)
    for h in history: messages.append({"role": h['role'], "content": h['content']})
    messages.append({"role": "user", "content": user_msg})

    random.shuffle(GROQ_KEYS)
    response_text = None

    for key in GROQ_KEYS:
        try:
            client = Groq(api_key=key)
            completion = client.chat.completions.create(
                messages=messages, model="llama-3.3-70b-versatile", temperature=0.8, max_tokens=100
            )
            response_text = completion.choices[0].message.content
            break
        except: continue

    if response_text:
        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": response_text})
        save_chat_history(uid, history[-10:])
        await update.message.reply_text(response_text)
    else:
        await update.message.reply_text("...")

# ==========================================
# 4. COMMANDS
# ==========================================

async def start_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "🌸 <b>Hi, I'm Rairin!</b>\n"
        "I'm an AI Waifu bot with Gacha & Battle features.\n\n"
        "🔹 Type <code>/help</code> to see what I can do.\n"
        "🔹 Call my name <b>Rairin</b> or reply to me to chat!"
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def help_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "📚 <b>RAIRIN COMMAND LIST</b>\n\n"
        "🎲 <b>Gacha & Collection</b>\n"
        "• <code>/getbini</code> - Roll for a new waifu (5h cd)\n"
        "• <code>/mybini</code> - View your collection\n"
        "• <code>/bini [ID]</code> - Set waifu as favorite\n"
        "• <code>/battle [ID]</code> - Bet your waifu in battle\n"
        "• <code>/leaderboard</code> - Top collectors\n\n"
        "⚙️ <b>Utility</b>\n"
        "• <code>/afk [reason]</code> - Set auto-reply when mentioned\n"
        "• <code>/report [msg]</code> - Report bugs to developer\n\n"
        "💬 <b>Chat</b>\n"
        "• Reply to me or say 'Rairin' to chat."
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def report_bug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg_content = " ".join(context.args)
    
    if not msg_content:
        await update.message.reply_text("⚠️ Usage: `/report <message>`\nExample: `/report Rairin is not replying`", parse_mode=ParseMode.MARKDOWN)
        return

    # Create Report Content
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_text = (
        f"--- RAIRIN BUG REPORT ---\n"
        f"Date: {timestamp}\n"
        f"From: {user.first_name} (@{user.username if user.username else 'NoHandle'})\n"
        f"User ID: {user.id}\n"
        f"Chat ID: {update.effective_chat.id}\n\n"
        f"MESSAGE:\n{msg_content}\n"
        f"--------------------------\n"
    )

    # Save to Temp File
    filename = f"report_{user.id}_{int(datetime.now().timestamp())}.txt"
    filepath = os.path.join(TEMP_DIR, filename)
    if not os.path.exists(TEMP_DIR): os.makedirs(TEMP_DIR)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(report_text)

    status_msg = await update.message.reply_text("📤 <i>Sending report...</i>", parse_mode=ParseMode.HTML)

    if not PIXELDRAIN_API_KEY:
         await status_msg.edit_text("❌ <b>Error:</b> Pixeldrain API Key not configured.")
         return

    # Upload to Pixeldrain
    try:
        with open(filepath, 'rb') as f:
            response = requests.post(
                PIXELDRAIN_API_URL,
                auth=('', PIXELDRAIN_API_KEY),
                files={'file': (filename, f)},
                data={'name': filename, 'anonymous': False}
            )
        
        if response.status_code == 201: # Created
            data = response.json()
            file_id = data.get('id')
            await status_msg.edit_text(f"✅ <b>Report Sent!</b>\nRef ID: <code>{file_id}</code>\nDeveloper will check it soon.", parse_mode=ParseMode.HTML)
        else:
            await status_msg.edit_text(f"⚠️ <b>Upload Failed.</b> Status: {response.status_code}")
            
    except Exception as e:
        await status_msg.edit_text(f"❌ <b>Error:</b> {str(e)}")
    finally:
        if os.path.exists(filepath): os.remove(filepath)

async def get_bini(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only allow in specific group IF ID is set, otherwise allow all
    if ALLOWED_GROUP_ID != 0 and update.effective_chat.id != ALLOWED_GROUP_ID: 
        return
        
    user = update.effective_user
    uid = str(user.id)
    db = load_data()
    now = datetime.now()

    if uid not in db["users"]: 
        db["users"][uid] = {"username": user.first_name, "handle": user.username, "collection": [], "last_claim": None}
    else: 
        db["users"][uid]["username"] = user.first_name
        db["users"][uid]["handle"] = user.username

    last = db["users"][uid].get("last_claim")
    if last:
        diff = now - datetime.fromisoformat(last)
        if diff < timedelta(hours=5):
            remaining = timedelta(hours=5) - diff
            total_seconds = int(remaining.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            time_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
            await update.message.reply_text(f"No Bini for you, please wait {time_str} to roll again.", parse_mode=ParseMode.HTML)
            return

    msg = await update.message.reply_text("✨ <i>Summoning new Bini...</i>", parse_mode=ParseMode.HTML)
    data = await fetch_master_source()
    
    if data:
        db["global_counter"] += 1
        new_id = db["global_counter"]
        char = {"id": new_id, "name": data['name'], "anime": data['source'], "image": data['image'], "link": data['link'], "date": now.strftime("%Y-%m-%d %H:%M")}
        
        db["users"][uid]["collection"].append(char)
        db["users"][uid]["last_claim"] = now.isoformat()
        save_data(db)
        
        cap = f"🎨 <b>Captured a Bini!</b>\nOwner: {user.first_name}\nName: <b>{char['name']}</b>\nSource: {char['anime']}\nID: <code>{new_id}</code>"
        await smart_send_photo(update, char['image'], cap, msg)
    else:
        await msg.edit_text("⚠️ <b>Bini runaway!.</b> No bini found.", parse_mode=ParseMode.HTML)

async def my_bini_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    db = load_data()
    if uid in db["users"]:
        fav_id = db["users"][uid].get("favorite_id")
        if fav_id:
            collection = db["users"][uid].get("collection", [])
            fav_char = next((c for c in collection if c['id'] == fav_id), None)
            if fav_char:
                cap = f"⭐ <b>Your Favorite</b>\nName: <b>{fav_char['name']}</b>\nID: <code>{fav_char['id']}</code>"
                await smart_send_photo(update, fav_char['image'], cap)
    await show_bini_page(update, uid, 0)

async def show_bini_page(update, uid, page):
    db = load_data()
    if uid not in db["users"] or not db["users"][uid]["collection"]:
        text = "📂 Collection is empty."
        if update.callback_query: await update.callback_query.answer(text)
        else: await update.message.reply_text(text)
        return

    col = db["users"][uid]["collection"]
    total = (len(col) + 9) // 10
    if page >= total: page = total - 1
    if page < 0: page = 0
    
    items = col[page*10:(page+1)*10]
    fav_id = db["users"][uid].get("favorite_id")
    
    txt = f"📔 <b>BINI PAGE</b> ({page+1}/{total})\n\n"
    for c in items:
        icon = "⭐" if c['id'] == fav_id else "🔹"
        txt += f"{icon} <code>{c['id']}</code> — {c['name']}\n"
    txt += "\n<i>View details: /mybini(ID)</i>"
    
    btns = []
    if page > 0: btns.append(InlineKeyboardButton("⬅️", callback_data=f"bini_page_{page-1}_{uid}"))
    if page < total - 1: btns.append(InlineKeyboardButton("➡️", callback_data=f"bini_page_{page+1}_{uid}"))
    kb = InlineKeyboardMarkup([btns]) if btns else None
    
    if update.callback_query: await update.callback_query.edit_message_text(txt, reply_markup=kb, parse_mode=ParseMode.HTML)
    else: await update.message.reply_text(txt, reply_markup=kb, parse_mode=ParseMode.HTML)

async def bini_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split('_')
    await show_bini_page(update, parts[3], int(parts[2]))

async def my_bini_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: tid = int(update.message.text.replace('/mybini', '').strip())
    except: return
    db = load_data()
    uid = str(update.effective_user.id)
    if uid not in db["users"]: return
    char = next((x for x in db["users"][uid]["collection"] if x['id'] == tid), None)
    if char:
        cap = f"💠 <b>Detail #{char['id']}</b>\nName: <b>{char['name']}</b>\nSource: {char['anime']}\n<a href='{char['link']}'>🔗 Original Link</a>"
        await smart_send_photo(update, char['image'], cap)

async def set_bini_favorite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = None
    if context.args:
        try: tid = int(context.args[0])
        except: pass
    if not tid and update.message.reply_to_message:
        txt = update.message.reply_to_message.caption or update.message.reply_to_message.text
        m = re.search(r"(?:ID:|#)\s*(?:<code>)?(\d+)", txt)
        if m: tid = int(m.group(1))
    if not tid: 
        await update.message.reply_text("Reply to a waifu or use `/bini ID`")
        return
    uid = str(update.effective_user.id)
    db = load_data()
    if uid in db["users"]:
        found = next((x for x in db["users"][uid]["collection"] if x['id'] == tid), None)
        if found:
            db["users"][uid]["favorite_id"] = tid
            save_data(db)
            await update.message.reply_text(f"⭐ <b>{found['name']}</b> set as favorite!", parse_mode=ParseMode.HTML)
        else: await update.message.reply_text("ID not found in your collection.")

async def battle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: bid = int(context.args[0])
    except: 
        await update.message.reply_text("Usage: `/battle <ID>`")
        return
    user = update.effective_user
    uid = str(user.id)
    db = load_data()
    if uid not in db["users"]: return
    my_char = next((x for x in db["users"][uid]["collection"] if x['id'] == bid), None)
    if not my_char:
        await update.message.reply_text("Invalid ID.")
        return
    kb = [[InlineKeyboardButton("⚔️ SEND BINI TO BATTLE", callback_data="accept_battle")]]
    msg = await update.message.reply_text(
        f"🔥 <b>BATTLE START!</b>\n👤 <b>{user.first_name}</b> bets: {my_char['name']} (ID: {bid})",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
    )
    PENDING_BATTLES[msg.message_id] = {'p1_id': uid, 'p1_name': user.first_name, 'p1_char': my_char}

async def battle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    msg_id = q.message.message_id
    user = q.from_user
    uid = str(user.id)
    if msg_id not in PENDING_BATTLES:
        await q.edit_message_text("⚠️ Battle expired.")
        return
    data = PENDING_BATTLES[msg_id]
    if q.data == "accept_battle":
        if uid == data['p1_id']: return
        db = load_data()
        if uid not in db["users"] or not db["users"][uid]["collection"]:
            await q.answer("You have no waifus!", show_alert=True)
            return
        kb = []
        for c in db["users"][uid]["collection"][-5:]: 
            kb.append([InlineKeyboardButton(f"{c['name']} ({c['id']})", callback_data=f"sel_{c['id']}")])
        data['p2_id'] = uid
        data['p2_name'] = user.first_name
        await q.edit_message_text(f"⚔️ <b>{user.first_name}</b> Accepting... Choose your Bini:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    elif q.data.startswith("sel_"):
        if uid != data.get('p2_id'): return
        sel_id = int(q.data.split('_')[1])
        db = load_data()
        p1_char = data['p1_char']
        p2_char = next((x for x in db["users"][uid]["collection"] if x['id'] == sel_id), None)
        if not p2_char: return
        p1_win = random.choice([True, False])
        winner_name = data['p1_name'] if p1_win else data['p2_name']
        loser_uid = data['p2_id'] if p1_win else data['p1_id']
        winner_uid = data['p1_id'] if p1_win else data['p2_id']
        prize = p2_char if p1_win else p1_char
        db["users"][loser_uid]["collection"].remove(prize)
        db["users"][winner_uid]["collection"].append(prize)
        if db["users"][loser_uid].get("favorite_id") == prize['id']: db["users"][loser_uid]["favorite_id"] = None
        save_data(db)
        res = f"🏆 <b>{winner_name} WON!</b>\n♻️ <b>Got NTR'd:</b> {prize['name']} (ID: {prize['id']})"
        await q.edit_message_text(res, parse_mode=ParseMode.HTML)
        del PENDING_BATTLES[msg_id]

async def set_afk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = " ".join(context.args) if context.args else "Busy"
    user = update.effective_user
    uid = str(user.id)
    db = load_data()
    if uid not in db["users"]: db["users"][uid] = {"username": user.first_name, "handle": user.username, "collection": []}
    
    db["users"][uid]["afk_status"] = True
    db["users"][uid]["afk_reason"] = reason
    db["users"][uid]["username"] = user.first_name
    db["users"][uid]["handle"] = user.username 
    save_data(db)
    await update.message.reply_text(f"💤 <b>{user.first_name}</b> is now AFK: <i>{reason}</i>", parse_mode=ParseMode.HTML)

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_data()
    ranked = sorted([(d['username'], len(d.get('collection', []))) for d in db['users'].values()], key=lambda x: x[1], reverse=True)[:10]
    txt = "🏆 <b>TOP COLLECTORS</b>\n" + "\n".join([f"{i+1}. {n} ({c})" for i, (n, c) in enumerate(ranked)])
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def check_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"`{update.effective_chat.id}`", parse_mode=ParseMode.MARKDOWN)

if __name__ == '__main__':
    if not TOKEN:
        print("❌ ERROR: TELEGRAM_TOKEN not found in .env")
        exit()

    app = ApplicationBuilder().token(TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler('start', start_bot))
    app.add_handler(CommandHandler('help', help_bot))
    app.add_handler(CommandHandler('report', report_bug))
    app.add_handler(CommandHandler('getbini', get_bini))
    app.add_handler(CommandHandler('mybini', my_bini_list))
    app.add_handler(CommandHandler('bini', set_bini_favorite))
    app.add_handler(CommandHandler('battle', battle))
    app.add_handler(CommandHandler('afk', set_afk))
    app.add_handler(CommandHandler('leaderboard', leaderboard))
    app.add_handler(CommandHandler('checkid', check_id))
    
    app.add_handler(CallbackQueryHandler(bini_pagination, pattern='^bini_page_'))
    app.add_handler(CallbackQueryHandler(battle_callback))
    
    app.add_handler(MessageHandler(filters.Regex(r'^/mybini\d+$'), my_bini_detail))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_ai_chat))
    
    print("ALL SYSTEMS ONLINE")
    app.run_polling()
