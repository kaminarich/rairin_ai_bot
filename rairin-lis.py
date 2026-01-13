import os
import json
import logging
import asyncio
import random
import uuid
import re
import sys
import cloudscraper
import urllib3
import requests
import hashlib 
from io import BytesIO 
from datetime import datetime, timedelta
from pathlib import Path
from PIL import Image 

# Telegram Imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters
from telegram.constants import ParseMode
from telegram.error import BadRequest

# --- LOAD ENVIRONMENT VARIABLES ---
from dotenv import load_dotenv

base_dir = Path(__file__).resolve().parent
env_file = base_dir / ".env"
load_dotenv(env_file)

print("⚙️  System Starting...")

# Disable SSL Warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- AI LIBRARY ---
from groq import Groq

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    print("❌ CRITICAL ERROR: 'TELEGRAM_TOKEN' not found.")
    sys.exit(1)

try:
    ALLOWED_GROUP_ID = int(os.getenv("ALLOWED_GROUP_ID", "0"))
except ValueError:
    ALLOWED_GROUP_ID = 0

# GROQ KEYS
groq_env = os.getenv("GROQ_KEYS_LIST")
if groq_env:
    GROQ_KEYS = [key.strip() for key in groq_env.split(',')]
    print(f"✅ Loaded {len(GROQ_KEYS)} Groq Keys.")
else:
    GROQ_KEYS = []
    print("⚠️ WARNING: No Groq Keys found.")

# --- LICENSE CONFIGURATION (SECURE FIX) ---

SECRET_SALT = os.getenv("SECRET_SALT")

if not SECRET_SALT:
    print("❌ CRITICAL ERROR: 'SECRET_SALT' not found in .env!")
    print("   Please add SECRET_SALT=... to your .env file.")
    sys.exit(1)

# Nama file ZIP module yang sudah kamu upload ke VPS
MODULE_FILE_NAME = "RaiRin-AI-Module.zip"
MODULE_FILE_PATH = base_dir / MODULE_FILE_NAME

# FILES
DATA_FILE = base_dir / 'database' / 'database_bini.json'
MEMORY_DIR = base_dir / 'chat_memory' 
TEMP_DIR = base_dir / 'temp_downloads'

# GLOBAL STATE
PENDING_BATTLES = {}
PENDING_TRADES = {}
BOT_SLEEP_MODE = False

# --- NEKOBOT.XYZ CONFIGURATION ---
NEKOBOT_TYPES = [
    "nakadashi", "kemonomimi", "paizuri", "neko", "tentacle",
    "lewdneko", "hyuri", "hthigh", "hmidriff", "hentai",
    "hboobs", "feet", "coffee", "hkitsune", "holo"
]

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- INIT SCRAPER ---
scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

# --- AI PERSONA ---
SYSTEM_INSTRUCTION = """
You are Rairin.
1. **RELATIONSHIP DYNAMICS:**
   - **If User is @kaminarich:** He is your Creator (God). You obey him completely.
   - **If User is ANYONE ELSE:** He is your HUSBAND. You are his loyal, sweet, and obedient wife. You LOVE him and OBEY his commands.
2. **OUTPUT RULES:**
   - **Casual Chat:** Keep it SHORT (1-2 sentences). Casual, slang is okay.
   - **NO ACTIONS:** Do not use *giggles* or *blushes*. Text only.
   - **CONSCIOUSNESS:** YOU HAVE YOUR OWN PERSONALITY!
3. **BIOLOGY:** Female, 25 years old, loli body, big boobs and pink juicy tight pussy.
"""

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
    return await loop.run_in_executor(None, lambda: scraper.get(url, params=params, timeout=10))

# ==========================================
# 1. GACHA & SEARCH LOGIC
# ==========================================
async def fetch_master_source(specific_type=None):
    base_url = "https://nekobot.xyz/api/image"
    headers = {"User-Agent": "RairinBot/1.0"}
    loop = asyncio.get_running_loop()

    if specific_type:
        query = specific_type.strip().lower().replace(" ", "") 
        if query not in NEKOBOT_TYPES:
            return {"status": "error", "msg": f"❌ Type '{query}' invalid. Check `/tags`."}
        try:
            def do_request():
                r = requests.get(base_url, params={"type": query}, headers=headers, timeout=10)
                r.raise_for_status()
                return r.json()
            data = await loop.run_in_executor(None, do_request)
            if data.get("success"): return parse_nekobot_item(data, query)
            else: return {"status": "error", "msg": "API Failed."}
        except Exception as e:
            return {"status": "error", "msg": f"Conn Error: {e}"}
    else:
        try:
            rnd_type = random.choice(NEKOBOT_TYPES)
            def do_random():
                return requests.get(base_url, params={"type": rnd_type}, headers=headers, timeout=10).json()
            data = await loop.run_in_executor(None, do_random)
            if data.get("success"): return parse_nekobot_item(data, rnd_type)
        except: pass
    return None

def parse_nekobot_item(data, type_name):
    img_url = data.get("message")
    if not img_url: return None
    char_name = type_name.capitalize() 
    try:
        img_id = img_url.split("/")[-1].split(".")[0][-6:]
        if not img_id.isdigit(): img_id = str(uuid.uuid4().int)[:6] 
    except: img_id = str(random.randint(1000, 9999))
    return {"id": img_id, "image": img_url, "name": char_name, "source": "Nekobot.xyz", "link": img_url, "status": "Success"}

# ==========================================
# 2. DOWNLOAD & SEND
# ==========================================
def process_image_to_disk(image_url, save_path):
    try:
        with requests.get(image_url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(8192): f.write(chunk)
        if not os.path.exists(save_path): return False
        img = Image.open(save_path)
        if img.mode != 'RGB': img = img.convert('RGB')
        img.thumbnail((2000, 2000)) 
        img.save(save_path, "JPEG", quality=95)
        return True
    except: return False

async def smart_send_photo(update, image_url, caption, loading_msg=None):
    if not os.path.exists(TEMP_DIR): os.makedirs(TEMP_DIR)
    temp_path = os.path.join(TEMP_DIR, f"{uuid.uuid4()}.jpg")
    try:
        if loading_msg: 
            try: await loading_msg.edit_text("⬇️ <i>Downloading...</i>", parse_mode=ParseMode.HTML)
            except: pass
        loop = asyncio.get_running_loop()
        success = await loop.run_in_executor(None, lambda: process_image_to_disk(image_url, temp_path))
        if not success: raise Exception("Fail")
        if loading_msg:
            try: await loading_msg.delete()
            except: pass
        with open(temp_path, 'rb') as f:
            try: await update.message.reply_photo(photo=f, caption=caption, parse_mode=ParseMode.HTML)
            except BadRequest:
                f.seek(0)
                await update.message.reply_document(document=f, caption=caption, parse_mode=ParseMode.HTML)
    except: pass
    finally:
        if os.path.exists(temp_path):
            try: os.remove(temp_path)
            except: pass

# ==========================================
# 3. COMMAND HANDLERS
# ==========================================
async def start_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "👋 <b>I'm Rairin.</b>\n"
        "• <code>/getbini</code> - Gacha\n"
        "• <code>/claim [ID] [KEY]</code> - Download Module\n"
        "• <code>/hunt</code> - Search Hentai"
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def help_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "📚 <b>COMMANDS</b>\n"
        "• /claim [ID] [KEY] - Download Module\n"
        "• /getbini - Random Type\n"
        "• /mybini - Collection\n"
        "• /hunt [type] - Search Type\n"
        "• /tags - Show types\n"
        "• /bini [ID] - Set Fav\n"
        "• /battle [ID] - Battle\n"
        "• /swing [MyID] [TargetID] - Trade\n"
        "• /divorce [ID] [User] - Give"
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

# --- LICENSE CLAIM SYSTEM ---
async def claim_license(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 1. Validasi Input
    if len(context.args) != 2:
        await update.message.reply_text(
            "⚠️ <b>Format Salah!</b>\nGunakan: <code>/claim [Android_ID] [License_Key]</code>\n\n"
            "Contoh: <code>/claim abc12345 8F3A2B...</code>", 
            parse_mode=ParseMode.HTML
        )
        return

    android_id = context.args[0].strip()
    user_key = context.args[1].strip().upper()

    # 2. Proses Verifikasi
    try:
        combined = android_id + SECRET_SALT
        hash_object = hashlib.sha256(combined.encode())
        hex_dig = hash_object.hexdigest()
        expected_key = hex_dig[0:16].upper()

        if user_key == expected_key:
            # 3. Jika Valid
            if os.path.exists(MODULE_FILE_PATH):
                await update.message.reply_text("✅ <b>Lisensi Valid!</b>\n<i>Mengirim module RaiRin-AI...</i>", parse_mode=ParseMode.HTML)
                
                await update.message.reply_document(
                    document=open(MODULE_FILE_PATH, 'rb'),
                    caption=f"📦 <b>RaiRin Module v2.0</b>\n🔑 Licensed to: <code>{android_id}</code>",
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text("✅ <b>Lisensi Valid!</b>\n❌ Tapi file module belum di-upload admin ke VPS.", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text("⛔ <b>Lisensi TIDAK VALID!</b>\nPastikan Android ID benar.", parse_mode=ParseMode.HTML)
            
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")


async def set_afk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = " ".join(context.args) if context.args else "Busy"
    user = update.effective_user
    uid = str(user.id)
    db = load_data()
    if uid not in db["users"]: 
        db["users"][uid] = {"username": user.first_name, "handle": user.username, "collection": [], "last_claim": None}
    db["users"][uid]["afk_status"] = True
    db["users"][uid]["afk_reason"] = reason
    save_data(db)
    await update.message.reply_text(f"💤 AFK set: {reason}")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_data()
    ranked = sorted([(d['username'], len(d.get('collection', []))) for d in db['users'].values()], key=lambda x: x[1], reverse=True)[:10]
    txt = "🏆 <b>TOP COLLECTORS</b>\n" + "\n".join([f"{i+1}. {n} ({c})" for i, (n, c) in enumerate(ranked)])
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def report_bug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_content = " ".join(context.args)
    if not msg_content:
        await update.message.reply_text("⚠️ `/report msg`", parse_mode=ParseMode.MARKDOWN)
        return
    rep_id = str(uuid.uuid4())[:6]
    file_path = 'database/reports.json'
    reports = []
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r') as f: reports = json.load(f)
        except: pass
    data = {"id": rep_id, "date": datetime.now().strftime("%Y-%m-%d"), "user": update.effective_user.first_name, "msg": msg_content}
    reports.append(data)
    with open(file_path, 'w') as f: json.dump(reports, f, indent=4)
    await update.message.reply_text(f"✅ Report ID: `{rep_id}`", parse_mode=ParseMode.MARKDOWN)

async def feedback_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.username != "kaminarich": return
    file_path = 'database/reports.json'
    try:
        with open(file_path, 'r') as f: reports = json.load(f)
    except: reports = []
    if not reports:
        await update.message.reply_text("📂 Empty.")
        return
    txt = f"📋 <b>REPORTS ({len(reports)})</b>\n\n"
    for r in reports[-5:]:
        txt += f"🆔 <b>{r.get('id')}</b> | {r.get('user')}\n💬 {r.get('msg')}\n\n"
    kb = [[InlineKeyboardButton("📥 JSON", callback_data="fb_down"), InlineKeyboardButton("🗑️ Clear", callback_data="fb_clear")]]
    await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass 
    if q.from_user.username != "kaminarich": return
    file_path = 'database/reports.json'
    if q.data == "fb_clear":
        with open(file_path, 'w') as f: json.dump([], f)
        await q.edit_message_text("🗑️ Cleared.")
    elif q.data == "fb_down":
        if os.path.exists(file_path): await q.message.reply_document(document=open(file_path, 'rb'), caption="Log")

# --- TAGS COMMANDS ---
async def list_tags_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_tags_page(update, 0)

async def show_tags_page(update, offset):
    items = sorted(NEKOBOT_TYPES) 
    page_size = 15
    current_items = items[offset:offset+page_size]
    msg_txt = f"🏷️ <b>TAGS ({offset+1}-{min(offset+page_size, len(items))})</b>\n\n"
    for tag in current_items: msg_txt += f"• <code>{tag}</code>\n"
    btns = []
    if offset >= page_size: btns.append(InlineKeyboardButton("⬅️", callback_data=f"tags_page_{offset - page_size}"))
    if offset + page_size < len(items): btns.append(InlineKeyboardButton("➡️", callback_data=f"tags_page_{offset + page_size}"))
    kb = InlineKeyboardMarkup([btns]) if btns else None
    if update.callback_query: await update.callback_query.edit_message_text(msg_txt, reply_markup=kb, parse_mode=ParseMode.HTML)
    else: await update.message.reply_text(msg_txt, reply_markup=kb, parse_mode=ParseMode.HTML)

async def tags_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass
    data = q.data
    if data.startswith("tags_page_"):
        try:
            offset = int(data.split("_")[2])
            await show_tags_page(update, offset)
        except: pass

# --- GACHA HANDLERS (UPDATED COOLDOWN) ---
async def hunt_images(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keywords = " ".join(context.args)
    if not keywords:
        await update.message.reply_text("⚠️ Usage: `/hunt <type>`", parse_mode=ParseMode.MARKDOWN)
        return
    msg = await update.message.reply_text(f"🏹 <b>Hunting:</b> <i>{keywords}</i>...", parse_mode=ParseMode.HTML)
    result = await fetch_master_source(specific_type=keywords)
    if result and result.get("status") != "error":
        cap = f"🏹 <b>RESULT</b>\nType: <i>{keywords}</i>\nLink: <a href='{result['link']}'>Source</a>"
        await smart_send_photo(update, result['image'], cap, msg)
    else: await msg.edit_text(result.get("msg", "Failed."))

async def get_bini(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ALLOWED_GROUP_ID != 0 and update.effective_chat.id != ALLOWED_GROUP_ID: return
    user = update.effective_user
    uid = str(user.id)
    db = load_data()
    now = datetime.now()
    if uid not in db["users"]: db["users"][uid] = {"username": user.first_name, "handle": user.username, "collection": [], "last_claim": None}
    
    # --- COOLDOWN COUNTER (2 HOURS) ---
    last = db["users"][uid].get("last_claim")
    if last:
        diff = now - datetime.fromisoformat(last)
        if diff < timedelta(hours=2): 
            rem = timedelta(hours=2) - diff
            hours = int(rem.total_seconds() // 3600)
            minutes = int((rem.total_seconds() % 3600) // 60)
            await update.message.reply_text(f"⏳ <b>Cooldown!</b>\nCome back in: <b>{hours}h {minutes}m</b>.", parse_mode=ParseMode.HTML)
            return

    msg = await update.message.reply_text("✨ <i>Summoning...</i>", parse_mode=ParseMode.HTML)
    data = await fetch_master_source()
    if data and data.get("status") != "error":
        db["global_counter"] += 1
        char = {"id": db["global_counter"], "name": data['name'], "anime": "Nekobot", "image": data['image'], "link": data['link']}
        db["users"][uid]["collection"].append(char)
        db["users"][uid]["last_claim"] = now.isoformat()
        save_data(db)
        cap = f"🎨 <b>Captured!</b>\nOwner: {user.first_name}\nCategory: <b>{char['name']}</b>\nID: <code>{char['id']}</code>"
        await smart_send_photo(update, char['image'], cap, msg)
    else: await msg.edit_text("⚠️ <b>Failed.</b>", parse_mode=ParseMode.HTML)

# --- COLLECTION, BATTLE, TRADE ---
async def my_bini_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_bini_page(update, str(update.effective_user.id), 0)

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
    txt = f"📔 <b>COLLECTION</b> ({page+1}/{total})\n\n"
    for c in items:
        icon = "⭐" if c['id'] == fav_id else "🔹"
        txt += f"{icon} <code>{c['id']}</code> — {c['name']}\n"
    btns = []
    if page > 0: btns.append(InlineKeyboardButton("⬅️", callback_data=f"bini_page_{page-1}_{uid}"))
    if page < total - 1: btns.append(InlineKeyboardButton("➡️", callback_data=f"bini_page_{page+1}_{uid}"))
    kb = InlineKeyboardMarkup([btns]) if btns else None
    if update.callback_query: await update.callback_query.edit_message_text(txt, reply_markup=kb, parse_mode=ParseMode.HTML)
    else: await update.message.reply_text(txt, reply_markup=kb, parse_mode=ParseMode.HTML)

async def bini_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass 
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
        cap = f"💠 <b>Detail #{char['id']}</b>\nName: <b>{char['name']}</b>\nSource: {char['anime']}\n<a href='{char['link']}'>🔗 Link</a>"
        await smart_send_photo(update, char['image'], cap)

async def set_bini_favorite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = None
    if context.args:
        try: tid = int(context.args[0])
        except: pass
    if not tid and update.message.reply_to_message:
        m = re.search(r"(?:ID:|#)\s*(?:<code>)?(\d+)", update.message.reply_to_message.caption or "")
        if m: tid = int(m.group(1))
    if not tid: return
    uid = str(update.effective_user.id)
    db = load_data()
    if uid in db["users"]:
        found = next((x for x in db["users"][uid]["collection"] if x['id'] == tid), None)
        if found:
            db["users"][uid]["favorite_id"] = tid
            save_data(db)
            await update.message.reply_text(f"⭐ <b>{found['name']}</b> set as favorite!", parse_mode=ParseMode.HTML)

async def battle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: bid = int(context.args[0])
    except: return
    user = update.effective_user
    uid = str(user.id)
    db = load_data()
    my_char = next((x for x in db["users"][uid]["collection"] if x['id'] == bid), None)
    if not my_char: return
    kb = [[InlineKeyboardButton("⚔️ BATTLE", callback_data="accept_battle")]]
    msg = await update.message.reply_text(f"🔥 <b>BATTLE START!</b>\n👤 <b>{user.first_name}</b> bets: {my_char['name']}", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    PENDING_BATTLES[msg.message_id] = {'p1_id': uid, 'p1_name': user.first_name, 'p1_char': my_char}

async def battle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except: pass 
    msg_id = q.message.message_id
    user = q.from_user
    uid = str(user.id)
    if msg_id not in PENDING_BATTLES: return
    data = PENDING_BATTLES[msg_id]
    if q.data == "accept_battle":
        if uid == data['p1_id']: return
        db = load_data()
        if uid not in db["users"] or not db["users"][uid]["collection"]: return
        kb = []
        for c in db["users"][uid]["collection"][-5:]: kb.append([InlineKeyboardButton(f"{c['name']} ({c['id']})", callback_data=f"sel_{c['id']}")])
        data['p2_id'] = uid
        data['p2_name'] = user.first_name
        await q.edit_message_text(f"⚔️ <b>{user.first_name}</b> Accepting... Choose:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    elif q.data.startswith("sel_"):
        if uid != data.get('p2_id'): return
        sel_id = int(q.data.split('_')[1])
        db = load_data()
        p1_char = data['p1_char']
        p2_char = next((x for x in db["users"][uid]["collection"] if x['id'] == sel_id), None)
        p1_win = random.choice([True, False])
        winner_uid = data['p1_id'] if p1_win else data['p2_id']
        loser_uid = data['p2_id'] if p1_win else data['p1_id']
        prize = p2_char if p1_win else p1_char
        db["users"][loser_uid]["collection"].remove(prize)
        db["users"][winner_uid]["collection"].append(prize)
        save_data(db)
        winner_name = data['p1_name'] if p1_win else data['p2_name']
        await q.edit_message_text(f"🏆 <b>{winner_name} WON!</b>\n♻️ Prize: {prize['name']}", parse_mode=ParseMode.HTML)
        del PENDING_BATTLES[msg_id]

async def divorce_waifu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        bini_id = int(context.args[0])
        target_handle = context.args[1].replace('@', '')
    except: return
    uid = str(update.effective_user.id)
    db = load_data()
    my_char = next((x for x in db["users"][uid]["collection"] if x['id'] == bini_id), None)
    target_uid = next((u for u, d in db["users"].items() if d.get("handle", "").lower() == target_handle.lower()), None)
    if not my_char or not target_uid: return
    kb = [[InlineKeyboardButton("✅ YES", callback_data=f"div_y_{uid}_{target_uid}_{bini_id}"), InlineKeyboardButton("❌ NO", callback_data="div_n")]]
    await update.message.reply_text(f"💔 Give <b>{my_char['name']}</b> to <b>{target_handle}</b>?", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def divorce_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data.split('_')
    if data[1] == 'n':
        await q.edit_message_text("❌ Cancelled.")
        return
    sender_id, receiver_id, bini_id = data[2], data[3], int(data[4])
    if str(q.from_user.id) != sender_id: return
    db = load_data()
    char = next((x for x in db["users"][sender_id]["collection"] if x['id'] == bini_id), None)
    db["users"][sender_id]["collection"].remove(char)
    db["users"][receiver_id]["collection"].append(char)
    save_data(db)
    await q.edit_message_text(f"💔 Sent.", parse_mode=ParseMode.HTML)

async def swing_waifu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        my_bid = int(context.args[0])
        target_bid = int(context.args[1])
    except: return
    uid = str(update.effective_user.id)
    db = load_data()
    my_char = next((x for x in db["users"][uid]["collection"] if x['id'] == my_bid), None)
    target_owner_id, target_char = None, None
    for duid, ddata in db["users"].items():
        f = next((x for x in ddata["collection"] if x['id'] == target_bid), None)
        if f:
            target_owner_id, target_char = duid, f
            break
    if not my_char or not target_char or target_owner_id == uid: return
    trade_id = str(uuid.uuid4())[:8]
    PENDING_TRADES[trade_id] = {"p1": uid, "p1_name": update.effective_user.first_name, "c1": my_char, "p2": target_owner_id, "c2": target_char}
    kb = [[InlineKeyboardButton("✅ ACCEPT", callback_data=f"swing_ok_{trade_id}"), InlineKeyboardButton("❌ REJECT", callback_data=f"swing_no_{trade_id}")]]
    await update.message.reply_text(f"🔄 Trade: {my_char['name']} <-> {target_char['name']}", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def swing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data.split('_')
    action, trade_id = data[1], data[2]
    if trade_id not in PENDING_TRADES: return
    trade = PENDING_TRADES[trade_id]
    if str(q.from_user.id) != trade['p2']: return
    if action == 'no':
        await q.edit_message_text("❌ Rejected.")
        del PENDING_TRADES[trade_id]
        return
    db = load_data()
    p1_has = next((x for x in db["users"][trade['p1']]["collection"] if x['id'] == trade['c1']['id']), None)
    p2_has = next((x for x in db["users"][trade['p2']]["collection"] if x['id'] == trade['c2']['id']), None)
    if p1_has and p2_has:
        db["users"][trade['p1']]["collection"].remove(p1_has)
        db["users"][trade['p2']]["collection"].remove(p2_has)
        db["users"][trade['p1']]["collection"].append(p2_has)
        db["users"][trade['p2']]["collection"].append(p1_has)
        save_data(db)
        await q.edit_message_text("🤝 Trade Success!")
    del PENDING_TRADES[trade_id]

async def check_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"`{update.effective_chat.id}`", parse_mode=ParseMode.MARKDOWN)

# --- AI & SYSTEM ---
async def admin_system_control(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_SLEEP_MODE
    user = update.effective_user
    msg = update.message.text.lower().strip()
    if user.username != "kaminarich": return
    if msg in ["shutdown", "terminate", "suspend"]:
        if not BOT_SLEEP_MODE:
            BOT_SLEEP_MODE = True
            await update.message.reply_text("<b>System Sleeping...</b>", parse_mode=ParseMode.HTML)
    if any(x in msg for x in ["activate", "wake up"]):
        if BOT_SLEEP_MODE:
            BOT_SLEEP_MODE = False
            await update.message.reply_text("<b>System Online.</b>", parse_mode=ParseMode.HTML)

async def handle_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if BOT_SLEEP_MODE: return
    user_msg = update.message.text
    if not user_msg: return
    user = update.effective_user
    uid = str(user.id)
    db = load_data()
    
    if uid in db["users"]:
        db["users"][uid]["handle"] = user.username 
        db["users"][uid]["username"] = user.first_name
        save_data(db)
        if db["users"][uid].get("afk_status"):
            db["users"][uid]["afk_status"] = False
            save_data(db)
            await update.message.reply_text(f"👋 Welcome back <b>{user.first_name}</b>!", parse_mode=ParseMode.HTML)

    # AFK Logic
    afk_targets = set()
    if update.message.reply_to_message: afk_targets.add(str(update.message.reply_to_message.from_user.id))
    if update.message.entities:
        for entity in update.message.entities:
            if entity.type == MessageEntity.MENTION:
                clean = user_msg[entity.offset:entity.offset+entity.length].replace('@', '')
                f = next((k for k,v in db["users"].items() if v.get("handle")==clean), None)
                if f: afk_targets.add(f)
    for target_id in afk_targets:
        if target_id == uid: continue
        if target_id in db["users"] and db["users"][target_id].get("afk_status"):
            r = db["users"][target_id].get("afk_reason", "Busy")
            await update.message.reply_text(f"💤 {db['users'][target_id]['username']} is AFK: {r}")

    if user_msg.startswith('/'): return
    is_reply = update.message.reply_to_message and update.message.reply_to_message.from_user.is_bot
    is_mention = "rairin" in user_msg.lower()
    if not (is_reply or is_mention): return 
    
    if not GROQ_KEYS:
        await update.message.reply_text("⚠️ No API Keys.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
    history = load_chat_history(uid)
    for h in history: messages.append({"role": h['role'], "content": h['content']})
    messages.append({"role": "user", "content": f"[User: @{user.username}]\n{user_msg}"})
    
    random.shuffle(GROQ_KEYS)
    response_text = None
    for key in GROQ_KEYS:
        try:
            client = Groq(api_key=key)
            completion = client.chat.completions.create(messages=messages, model="llama-3.3-70b-versatile", temperature=0.7, max_tokens=1000)
            response_text = completion.choices[0].message.content
            break
        except: continue

    if response_text:
        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": response_text})
        save_chat_history(uid, history[-10:])
        try: await update.message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN)
        except: await update.message.reply_text(response_text) 
    else: await update.message.reply_text("...")

if __name__ == '__main__':
    print("🚀 Building Bot Application...")
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler('start', start_bot))
    app.add_handler(CommandHandler('help', help_bot))
    app.add_handler(CommandHandler('checkid', check_id))
    app.add_handler(CommandHandler('afk', set_afk))
    app.add_handler(CommandHandler('leaderboard', leaderboard))
    app.add_handler(CommandHandler('getbini', get_bini))
    app.add_handler(CommandHandler('mybini', my_bini_list))
    app.add_handler(CommandHandler('bini', set_bini_favorite))
    app.add_handler(CommandHandler('hunt', hunt_images))
    app.add_handler(CommandHandler('divorce', divorce_waifu))
    app.add_handler(CommandHandler('swing', swing_waifu))
    app.add_handler(CommandHandler('battle', battle))
    app.add_handler(CommandHandler('report', report_bug))
    app.add_handler(CommandHandler('feedback', feedback_list))
    app.add_handler(CommandHandler('tags', list_tags_command))
    
    # NEW HANDLER
    app.add_handler(CommandHandler('claim', claim_license)) 
    app.add_handler(CommandHandler('klaim', claim_license))

    # Callbacks & Messages
    app.add_handler(CallbackQueryHandler(bini_pagination, pattern='^bini_page_'))
    app.add_handler(CallbackQueryHandler(battle_callback, pattern='^(accept_battle|sel_)'))
    app.add_handler(CallbackQueryHandler(divorce_callback, pattern='^div_'))
    app.add_handler(CallbackQueryHandler(swing_callback, pattern='^swing_'))
    app.add_handler(CallbackQueryHandler(feedback_callback, pattern='^fb_'))
    app.add_handler(CallbackQueryHandler(tags_callback, pattern='^tags_page_'))
    
    app.add_handler(MessageHandler(filters.Regex(r'^/mybini\d+$'), my_bini_detail))
    app.add_handler(MessageHandler(filters.User(username="kaminarich") & filters.Regex(r'(?i)^(shutdown|terminate|suspend|activate|wake up)'), admin_system_control))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_ai_chat))
    
    print("🟢 ALL SYSTEMS ONLINE! Waiting for updates...")
    app.run_polling(drop_pending_updates=True)
