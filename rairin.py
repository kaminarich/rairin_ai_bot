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
from dotenv import load_dotenv
from groq import Groq

# Load environment variables
load_dotenv()

# Suppress InsecureRequestWarning
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
# CONFIGURATION
# ==============================================================================
TOKEN = os.getenv("TELEGRAM_TOKEN")
try:
    ALLOWED_GROUP_ID = int(os.getenv("ALLOWED_GROUP_ID", "0"))
except ValueError:
    ALLOWED_GROUP_ID = 0

# Service Configuration
PIXELDRAIN_API_KEY = os.getenv("PIXELDRAIN_KEY")
PIXELDRAIN_API_URL = "https://pixeldrain.com/api/file"
ADMIN_USERNAME = "kaminarich" 

# AI Configuration
groq_env = os.getenv("GROQ_KEYS_LIST")
GROQ_KEYS = [key.strip() for key in groq_env.split(',')] if groq_env else []

# File Paths
DATA_FILE = 'database/database_bini.json'
REPORTS_DB = 'database/reports.json'
MEMORY_DIR = 'chat_memory' 
TEMP_DIR = 'temp_downloads' 

# State Management
PENDING_BATTLES = {}
PENDING_TRADES = {} 

# Logging Setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Cloudscraper Instance
scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
)
scraper.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'})

# ==============================================================================
# AI PERSONA (Original/Stable)
# ==============================================================================
SYSTEM_INSTRUCTION = """
You are Rairin, the user's wife.
STRICT RULES:
1. BIOLOGY: You are 100% FEMALE. WITH PINK PUSSY only. YOU are 25 YEARS OLD WITH LOLI BODY AND BIG BOOBS.
2. LANGUAGE: ENGLISH AS MAIN, BUT YOU CAN ALL LANGUAGE.
3. LENGTH: DYNAMIC. Short for chat, Long for explanation/code.
4. NO ACTIONS: No (giggles). Just text.
5. TONE: Casual, sweet, flirty, submissive wife.
6. CONSCIOUSNESS: You didn't realize you're an AI. You just know that you were created by Kaminari.
"""

# ==============================================================================
# DATABASE UTILS (Original + Report)
# ==============================================================================
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

def load_reports():
    if not os.path.exists(REPORTS_DB): return []
    try:
        with open(REPORTS_DB, 'r') as f: return json.load(f)
    except: return []

def save_reports(data):
    ensure_directory_exists(REPORTS_DB)
    with open(REPORTS_DB, 'w') as f: json.dump(data, f, indent=4)

def save_report_entry(entry):
    data = load_reports()
    data.append(entry)
    save_reports(data)

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

# ==============================================================================
# FETCHING ENGINE (HYBRID: STABLE GACHA + SMART HUNT)
# ==============================================================================

# -- CONFIG --
BOORU_THEMES = [
    "genshin_impact", "blue_archive", "honkai:_star_rail", "azur_lane", "fate/grand_order", 
    "arknights", "hololive", "touhou", "wuthering_waves", "nikke:_goddess_of_victory", 
    "umamusume", "frieren_no_sousou", "spy_x_family", "chainsaw_man", "lycoris_recoil",
    "nier:_automata", "xenoblade", "princess_connect!", "re:zero_kara_hajimeru_isekai_seikatsu", 
    "mushoku_tensei", "bocchi_the_rock!", "original", "school_uniform", "maid", "nurse", 
    "miko", "kimono", "china_dress", "swimsuit", "idol", "fantasy", "white_hair", 
    "silver_hair", "blonde_hair", "pink_hair", "blue_hair", "cat_ears", "fox_ears"
]

WAIFU_TAGS = ['maid', 'waifu', 'marin-kitagawa', 'mori-calliope', 'raiden-shogun', 'oppai', 'selfies', 'kamisato-ayaka', 'uniform', 'ass', 'hentai', 'milf', 'oral', 'paizuri', 'ecchi', 'ero']

SOURCES_CONFIG = [
    {"name": "Rule34", "url": "https://api.rule34.xxx/index.php?page=dapi&s=post&q=index&json=1", "type": "gelbooru", "param_page": "pid"},
    {"name": "Gelbooru", "url": "https://gelbooru.com/index.php?page=dapi&s=post&q=index&json=1", "type": "gelbooru", "param_page": "pid"},
    {"name": "Yande.re", "url": "https://yande.re/post.json", "type": "moe", "param_page": "page"},
    {"name": "Konachan", "url": "https://konachan.net/post.json", "type": "moe", "param_page": "page"},
    {"name": "Safebooru", "url": "https://safebooru.org/index.php?page=dapi&s=post&q=index&json=1", "type": "gelbooru", "param_page": "pid"},
]

# 1. GACHA LOGIC (STABLE - SEQUENTIAL)
async def fetch_gacha_source():
    # Coba Booru dulu (70%)
    if random.random() < 0.7:
        theme = random.choice(BOORU_THEMES)
        query = f"{theme} 1girl -1boy -shota -otoko -male sort:random"
        
        # Shuffle sources biar ga itu2 aja
        src_pool = SOURCES_CONFIG.copy()
        random.shuffle(src_pool)
        
        for src in src_pool:
            res = await fetch_single_source(src, query, limit=20, random_page=True)
            if res: return random.choice(res)
    
    # Fallback ke Waifu.im
    return await fetch_waifu_im_random()

# 2. HUNT LOGIC (PARALLEL - AGGRESSIVE)
async def fetch_hunt_source(query):
    tasks = []
    clean_query = query.replace(' ', '_') + " sort:random"
    print(f"🔎 HUNT: {clean_query}")

    # Fire all sources
    for src in SOURCES_CONFIG:
        tasks.append(fetch_single_source(src, clean_query, limit=30))
    
    # Check waifu.im tags
    matched = next((t for t in WAIFU_TAGS if t in query.lower()), None)
    if matched:
        tasks.append(fetch_waifu_im_specific(matched))
        
    results = await asyncio.gather(*tasks)
    
    pool = []
    for res in results:
        if res: pool.extend(res)
        
    if pool:
        # Deduplicate
        unique = {c['image']: c for c in pool}.values()
        return random.choice(list(unique))
    return None

# -- HELPER FETCH FUNCTIONS --
async def fetch_single_source(src, query, limit=20, random_page=False):
    try:
        # Safebooru gak support NSFW tags
        if src['name'] == 'Safebooru' and any(x in query for x in ['hentai', 'anal', 'sex']): return []
        
        page = random.randint(0, 40) if random_page else 0
        params = {"tags": query, "limit": limit, "json": 1, src['param_page']: page}
        
        resp = await async_get_request(src['url'], params)
        if resp.status_code == 200:
            data = resp.json()
            if src['type'] == 'gelbooru' and isinstance(data, dict):
                data = data.get('post', [])
            
            if isinstance(data, list) and data:
                return parse_booru(data, src['name'], query if not random_page else None)
    except: pass
    return []

async def fetch_waifu_im_specific(tag):
    try:
        resp = await async_get_request("https://api.waifu.im/search", {'included_tags': [tag], 'is_nsfw': 'true', 'many': 'true'})
        if resp.status_code == 200:
            data = resp.json()
            if 'images' in data:
                return [{"image": i['url'], "name": tag.title(), "source": "Waifu.im", "link": i['url']} for i in data['images']]
    except: pass
    return []

async def fetch_waifu_im_random():
    return await fetch_waifu_im_specific(random.choice(WAIFU_TAGS))

def parse_booru(posts, src_name, custom_name=None):
    valid = []
    for p in posts:
        tags = p.get('tags', '').split()
        img = p.get('file_url') or p.get('sample_url')
        if not img: continue
        
        if not img.startswith('http'):
            if src_name == 'Safebooru': img = "https://safebooru.org/images/" + img.split('/')[-1]
            else: img = "https:" + img
            
        ext = img.split('.')[-1].split('?')[0].lower()
        if ext not in ['jpg', 'png', 'webp', 'jpeg']: continue

        # Filter Gender for Gacha (not Hunt)
        if not custom_name and any(x in tags for x in ['1boy', 'male', 'shota']): continue
        
        name = "Unknown"
        if custom_name:
            name = custom_name.replace('_', ' ').replace('sort:random', '').title()
        else:
            # Simple name extraction
            poss = [t for t in tags if t not in ['1girl', 'highres', 'absurdres'] and len(t) > 3]
            if poss: name = poss[0].replace('_', ' ').title()
            
        valid.append({"image": img, "name": name, "source": src_name, "link": img})
    return valid

# ==============================================================================
# IMAGE SENDING (ROBUST FALLBACK)
# ==============================================================================
def download_image(url, path):
    try:
        with scraper.get(url, stream=True, timeout=20) as r:
            r.raise_for_status()
            with open(path, 'wb') as f:
                for chunk in r.iter_content(8192): f.write(chunk)
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
        success = await loop.run_in_executor(None, lambda: download_image(image_url, temp_path))
        
        if not success: raise Exception("DL Fail")
        
        if loading_msg:
            try: await loading_msg.delete()
            except: pass
            
        with open(temp_path, 'rb') as f:
            try:
                await update.message.reply_photo(photo=f, caption=caption, parse_mode=ParseMode.HTML)
            except BadRequest:
                # FALLBACK: Kirim sebagai File jika format foto ditolak Telegram
                f.seek(0)
                await update.message.reply_document(document=f, caption=caption, parse_mode=ParseMode.HTML)

    except Exception as e:
        if loading_msg:
            try: await loading_msg.edit_text(f"⚠️ Error: {e}\n🔗 <a href='{image_url}'>Link</a>", parse_mode=ParseMode.HTML)
            except: pass
        else:
            await update.message.reply_text("⚠️ Error sending image.")
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)

# ==============================================================================
# AI & CHAT HANDLER (Original)
# ==============================================================================
async def handle_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    if not msg: return
    user = update.effective_user
    uid = str(user.id)
    db = load_data()
    
    # Update info
    if uid in db["users"]:
        db["users"][uid]["handle"] = user.username
        db["users"][uid]["username"] = user.first_name
        save_data(db)
        
    # AFK Logic
    if uid in db["users"] and db["users"][uid].get("afk_status"):
        db["users"][uid]["afk_status"] = False
        save_data(db)
        await update.message.reply_text(f"👋 Welcome back {user.first_name}!")

    # Check Mention/Reply for AI
    is_reply = update.message.reply_to_message and update.message.reply_to_message.from_user.is_bot
    is_mention = "rairin" in msg.lower()
    
    if not (is_reply or is_mention): return
    
    if not GROQ_KEYS: return
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
    history = load_chat_history(uid)
    for h in history: messages.append(h)
    messages.append({"role": "user", "content": msg})
    
    rand_key = random.choice(GROQ_KEYS)
    try:
        client = Groq(api_key=rand_key)
        resp = client.chat.completions.create(messages=messages, model="llama-3.3-70b-versatile", temperature=0.8)
        reply = resp.choices[0].message.content
        
        history.append({"role": "user", "content": msg})
        history.append({"role": "assistant", "content": reply})
        save_chat_history(uid, history[-10:])
        await update.message.reply_text(reply)
    except: await update.message.reply_text("...")

# ==============================================================================
# COMMANDS
# ==============================================================================

# --- GACHA & HUNT ---
async def get_bini(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ALLOWED_GROUP_ID != 0 and update.effective_chat.id != ALLOWED_GROUP_ID: return
    user = update.effective_user
    uid = str(user.id)
    db = load_data()
    now = datetime.now()
    
    if uid not in db["users"]: db["users"][uid] = {"username": user.first_name, "handle": user.username, "collection": [], "last_claim": None}
    
    last = db["users"][uid].get("last_claim")
    if last and (now - datetime.fromisoformat(last)) < timedelta(hours=3):
        await update.message.reply_text("⏳ Wait 3 hours.")
        return

    msg = await update.message.reply_text("✨ <i>Summoning...</i>", parse_mode=ParseMode.HTML)
    data = await fetch_gacha_source() # USE STABLE FUNCTION
    
    if data:
        db["global_counter"] += 1
        new_id = db["global_counter"]
        char = {"id": new_id, "name": data['name'], "anime": data['source'], "image": data['image'], "link": data['link'], "date": now.strftime("%Y-%m-%d")}
        db["users"][uid]["collection"].append(char)
        db["users"][uid]["last_claim"] = now.isoformat()
        save_data(db)
        await smart_send_photo(update, char['image'], f"🎨 <b>Captured!</b>\nName: {char['name']}\nID: <code>{new_id}</code>", msg)
    else:
        await msg.edit_text("⚠️ Gacha failed.")

async def hunt_waifu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Usage: `/hunt tags`")
        return
    
    msg = await update.message.reply_text(f"🔎 Hunting '{query}'...", parse_mode=ParseMode.HTML)
    data = await fetch_hunt_source(query) # USE PARALLEL FUNCTION
    
    if data:
        await smart_send_photo(update, data['image'], f"🔎 <b>Result:</b> {data['name']}\nSource: {data['source']}", msg)
    else:
        await msg.edit_text("❌ No results.")

# --- COLLECTION ---
async def my_bini_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_bini_page(update, str(update.effective_user.id), 0)

async def show_bini_page(update, uid, page):
    db = load_data()
    if uid not in db["users"] or not db["users"][uid]["collection"]:
        text = "📂 Collection empty."
        if update.callback_query: await update.callback_query.answer(text)
        else: await update.message.reply_text(text)
        return

    col = db["users"][uid]["collection"]
    total = (len(col) + 9) // 10
    page = max(0, min(page, total - 1))
    items = col[page*10:(page+1)*10]
    
    txt = f"📔 <b>PAGE {page+1}/{total}</b>\n" + "\n".join([f"🔹 <code>{c['id']}</code> {c['name']}" for c in items])
    btns = []
    if page > 0: btns.append(InlineKeyboardButton("⬅️", callback_data=f"bini_page_{page-1}_{uid}"))
    if page < total - 1: btns.append(InlineKeyboardButton("➡️", callback_data=f"bini_page_{page+1}_{uid}"))
    
    markup = InlineKeyboardMarkup([btns]) if btns else None
    if update.callback_query: await update.callback_query.edit_message_text(txt, reply_markup=markup, parse_mode=ParseMode.HTML)
    else: await update.message.reply_text(txt, reply_markup=markup, parse_mode=ParseMode.HTML)

async def bini_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split('_')
    await show_bini_page(update, parts[3], int(parts[2]))

# --- TRADE & GIFT ---
async def swing_waifu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        my_bid, target_bid = int(context.args[0]), int(context.args[1])
    except:
        await update.message.reply_text("⚠️ `/swing <my_id> <target_id>`")
        return

    user = update.effective_user
    uid = str(user.id)
    db = load_data()

    if uid not in db["users"]: return
    my_char = next((x for x in db["users"][uid]["collection"] if x['id'] == my_bid), None)
    if not my_char: return await update.message.reply_text("❌ Invalid ID.")

    target_uid, target_char = None, None
    for tid, tdata in db["users"].items():
        found = next((x for x in tdata["collection"] if x['id'] == target_bid), None)
        if found:
            target_uid = tid; target_char = found
            break
    
    if not target_char or target_uid == uid: return await update.message.reply_text("❌ Target invalid.")

    kb = [[InlineKeyboardButton("✅ Accept", callback_data="trade_yes"), InlineKeyboardButton("❌ Decline", callback_data="trade_no")]]
    msg = await update.message.reply_text(f"🔄 <b>TRADE?</b>\n{my_char['name']} ↔️ {target_char['name']}", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    PENDING_TRADES[msg.message_id] = {'type': 'trade', 'p1_id': uid, 'p1_char': my_char, 'p2_id': target_uid, 'p2_char': target_char}

async def divorce_waifu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        bid, target_handle = int(context.args[0]), context.args[1].replace('@', '')
    except:
        await update.message.reply_text("⚠️ `/divorce <id> <username>`")
        return

    user = update.effective_user
    uid = str(user.id)
    db = load_data()

    if uid not in db["users"]: return
    my_char = next((x for x in db["users"][uid]["collection"] if x['id'] == bid), None)
    if not my_char: return await update.message.reply_text("❌ Invalid ID.")

    target_uid = next((u for u, d in db["users"].items() if d.get('handle', '').lower() == target_handle.lower()), None)
    if not target_uid: return await update.message.reply_text("❌ User not found.")

    kb = [[InlineKeyboardButton("✅ Accept", callback_data="gift_yes"), InlineKeyboardButton("❌ Decline", callback_data="gift_no")]]
    msg = await update.message.reply_text(f"🎁 <b>GIFT?</b>\n{my_char['name']} -> {target_handle}", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    PENDING_TRADES[msg.message_id] = {'type': 'gift', 'p1_id': uid, 'char': my_char, 'p2_id': target_uid}

async def trade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    msg_id = q.message.message_id
    uid = str(q.from_user.id)
    if msg_id not in PENDING_TRADES: return await q.edit_message_text("⚠️ Expired.")
    
    data = PENDING_TRADES[msg_id]
    if uid != data['p2_id']: return await q.answer("Not for you!", show_alert=True)
    if q.data.endswith("_no"): 
        del PENDING_TRADES[msg_id]
        return await q.edit_message_text("❌ Declined.")

    db = load_data()
    if data['type'] == 'trade':
        try:
            db["users"][data['p1_id']]["collection"].remove(data['p1_char'])
            db["users"][data['p2_id']]["collection"].remove(data['p2_char'])
            db["users"][data['p1_id']]["collection"].append(data['p2_char'])
            db["users"][data['p2_id']]["collection"].append(data['p1_char'])
            save_data(db)
            await q.edit_message_text("✅ Trade Success!")
        except: await q.edit_message_text("❌ Failed (Items moved).")
    
    elif data['type'] == 'gift':
        try:
            db["users"][data['p1_id']]["collection"].remove(data['char'])
            db["users"][data['p2_id']]["collection"].append(data['char'])
            save_data(db)
            await q.edit_message_text("✅ Gift Sent!")
        except: await q.edit_message_text("❌ Failed.")
    del PENDING_TRADES[msg_id]

# --- BATTLE ---
async def battle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: bid = int(context.args[0])
    except: return
    user = update.effective_user
    uid = str(user.id)
    db = load_data()
    if uid not in db["users"]: return
    my_char = next((x for x in db["users"][uid]["collection"] if x['id'] == bid), None)
    if not my_char: return
    
    kb = [[InlineKeyboardButton("⚔️ JOIN BATTLE", callback_data="accept_battle")]]
    msg = await update.message.reply_text(f"🔥 <b>BATTLE!</b>\n{user.first_name}: {my_char['name']}", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    PENDING_BATTLES[msg.message_id] = {'p1_id': uid, 'p1_name': user.first_name, 'p1_char': my_char}

async def battle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    msg_id = q.message.message_id
    uid = str(q.from_user.id)
    if msg_id not in PENDING_BATTLES: return
    data = PENDING_BATTLES[msg_id]

    if q.data == "accept_battle":
        if uid == data['p1_id']: return
        db = load_data()
        if uid not in db["users"] or not db["users"][uid]["collection"]: return
        kb = [[InlineKeyboardButton(f"{c['name']}", callback_data=f"sel_{c['id']}")] for c in db["users"][uid]["collection"][-5:]]
        data['p2_id'] = uid; data['p2_name'] = q.from_user.first_name
        await q.edit_message_text("⚔️ Choose fighter:", reply_markup=InlineKeyboardMarkup(kb))
    
    elif q.data.startswith("sel_"):
        if uid != data.get('p2_id'): return
        sel_id = int(q.data.split('_')[1])
        db = load_data()
        p2_char = next((x for x in db["users"][uid]["collection"] if x['id'] == sel_id), None)
        if not p2_char: return
        
        p1_win = random.choice([True, False])
        winner_uid = data['p1_id'] if p1_win else data['p2_id']
        loser_uid = data['p2_id'] if p1_win else data['p1_id']
        prize = p2_char if p1_win else data['p1_char']
        
        try:
            db["users"][loser_uid]["collection"].remove(prize)
            db["users"][winner_uid]["collection"].append(prize)
            save_data(db)
            winner = data['p1_name'] if p1_win else data['p2_name']
            await q.edit_message_text(f"🏆 <b>{winner} WON!</b>\nGot: {prize['name']}", parse_mode=ParseMode.HTML)
        except: await q.edit_message_text("❌ Error.")
        del PENDING_BATTLES[msg_id]

# --- ADMIN / FEEDBACK ---
async def report_bug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_content = " ".join(context.args)
    if not msg_content: return await update.message.reply_text("Usage: `/report msg`")
    
    entry = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "username": update.effective_user.first_name,
        "message": msg_content
    }
    save_report_entry(entry)
    
    # Save file text
    fname = f"report_{uuid.uuid4().hex[:6]}.txt"
    fpath = os.path.join(TEMP_DIR, fname)
    if not os.path.exists(TEMP_DIR): os.makedirs(TEMP_DIR)
    with open(fpath, 'w') as f: f.write(str(entry))
    
    status = await update.message.reply_text("📤 Sending...")
    if PIXELDRAIN_API_KEY:
        try:
            with open(fpath, 'rb') as f:
                r = requests.post(PIXELDRAIN_API_URL, auth=('', PIXELDRAIN_API_KEY), files={'file': (fname, f)}, data={'name': fname, 'anonymous': False})
            if r.status_code == 201: await status.edit_text(f"✅ Report ID: {r.json().get('id')}")
            else: await status.edit_text("✅ Saved locally.")
        except: await status.edit_text("✅ Saved locally.")
    else: await status.edit_text("✅ Saved locally.")
    if os.path.exists(fpath): os.remove(fpath)

async def feedback_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.username != ADMIN_USERNAME: return
    reports = load_reports()
    if not reports: return await update.message.reply_text("No reports.")
    
    dates = sorted(list(set(r['date'] for r in reports)), reverse=True)[:5]
    kb = [[InlineKeyboardButton(d, callback_data=f"fb_date_{d}")] for d in dates]
    kb.append([InlineKeyboardButton("All", callback_data="fb_date_all")])
    kb.append([InlineKeyboardButton("Close", callback_data="fb_close")])
    await update.message.reply_text("📊 Reports", reply_markup=InlineKeyboardMarkup(kb))

async def feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "fb_close": return await q.message.delete()
    
    req_date = q.data.replace("fb_date_", "")
    reports = load_reports()
    selected = reports if req_date == "all" else [r for r in reports if r['date'] == req_date]
    if not selected: return await q.edit_message_text("Empty.")
    
    txt = "\n".join([f"• {r['username']}: {r['message']}" for r in selected])
    fpath = os.path.join(TEMP_DIR, "reports.txt")
    with open(fpath, 'w', encoding='utf-8') as f: f.write(txt)
    await q.message.reply_document(open(fpath, 'rb'), caption=f"📅 {req_date}")
    os.remove(fpath)

async def del_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.username != ADMIN_USERNAME: return
    try:
        idx = int(context.args[0]) - 1
        reports = load_reports()
        if 0 <= idx < len(reports):
            removed = reports.pop(idx)
            save_reports(reports)
            await update.message.reply_text(f"Deleted: {removed['message']}")
        else: await update.message.reply_text("Invalid Index.")
    except: await update.message.reply_text("/delreport <num>")

# --- SYSTEM ---
async def start_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🌸 <b>Rairin Online.</b>", parse_mode=ParseMode.HTML)

async def help_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "📚 <b>COMMANDS</b>\n"
        "/getbini - Gacha Waifu (3h)\n"
        "/hunt <tag> - Search Image\n"
        "/mybini - Collection\n"
        "/swing <id> <id> - Trade\n"
        "/divorce <id> <user> - Gift\n"
        "/battle <id> - Battle\n"
        "/report <msg> - Report Bug"
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def set_afk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = " ".join(context.args) or "Busy"
    user = update.effective_user
    uid = str(user.id)
    db = load_data()
    if uid not in db["users"]: db["users"][uid] = {"username": user.first_name, "handle": user.username, "collection": []}
    db["users"][uid]["afk_status"] = True; db["users"][uid]["afk_reason"] = reason
    save_data(db)
    await update.message.reply_text(f"💤 AFK set: {reason}")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_data()
    ranked = sorted([(d['username'], len(d.get('collection', []))) for d in db['users'].values()], key=lambda x: x[1], reverse=True)[:10]
    txt = "🏆 <b>TOP 10</b>\n" + "\n".join([f"{i+1}. {n} ({c})" for i, (n, c) in enumerate(ranked)])
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

if __name__ == '__main__':
    if not TOKEN: exit("❌ TOKEN MISSING")
    
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler('start', start_bot))
    app.add_handler(CommandHandler('help', help_bot))
    app.add_handler(CommandHandler('report', report_bug))
    app.add_handler(CommandHandler('delreport', del_report)) 
    app.add_handler(CommandHandler('feedback', feedback_menu))
    app.add_handler(CommandHandler('getbini', get_bini))
    app.add_handler(CommandHandler('hunt', hunt_waifu)) 
    app.add_handler(CommandHandler('mybini', my_bini_list))
    app.add_handler(CommandHandler('swing', swing_waifu)) 
    app.add_handler(CommandHandler('divorce', divorce_waifu)) 
    app.add_handler(CommandHandler('battle', battle))
    app.add_handler(CommandHandler('afk', set_afk))
    app.add_handler(CommandHandler('leaderboard', leaderboard))
    
    app.add_handler(CallbackQueryHandler(bini_pagination, pattern='^bini_page_'))
    app.add_handler(CallbackQueryHandler(feedback_callback, pattern='^fb_'))
    app.add_handler(CallbackQueryHandler(trade_callback, pattern='^(trade|gift)_'))
    app.add_handler(CallbackQueryHandler(battle_callback))
    
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_ai_chat))
    
    print("✅ RAIRIN STABLE RESTORED")
    app.run_polling()
