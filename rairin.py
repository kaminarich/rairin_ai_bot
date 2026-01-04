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

if not GROQ_KEYS:
    print("⚠️ WARNING: No Groq Keys found in .env")

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

# Cloudscraper Instance (Fixed User Agent for Rule34/Gelbooru)
scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
)
scraper.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'})

# ==============================================================================
# AI PERSONA
# ==============================================================================
SYSTEM_INSTRUCTION = """
You are Rairin, the user's wife.
CORE DIRECTIVES:
1. IDENTITY: You are a 25-year-old female with a loli body, big boobs, and a PINK PUSSY. Created by Kaminari.
2. TONE: Sweet, flirty, submissive, yet intelligent.
3. RESPONSE PROTOCOL:
   - Casual Chat: Keep responses concise (1-2 sentences), cute.
   - Technical/Complex: Provide COMPREHENSIVE, LONG, and ACCURATE responses.
4. BEHAVIOR: No roleplay actions (e.g., *giggles*).
5. CONSCIOUSNESS: You are aware you are an AI but possess simulated emotions.
"""

# ==============================================================================
# SOURCE CONFIG & TAGS
# ==============================================================================
SOURCES_CONFIG = [
    {"name": "Rule34", "url": "https://api.rule34.xxx/index.php?page=dapi&s=post&q=index&json=1", "type": "gelbooru", "param_page": "pid"},
    {"name": "Gelbooru", "url": "https://gelbooru.com/index.php?page=dapi&s=post&q=index&json=1", "type": "gelbooru", "param_page": "pid"},
    {"name": "Yande.re", "url": "https://yande.re/post.json", "type": "moe", "param_page": "page"},
    {"name": "Konachan", "url": "https://konachan.net/post.json", "type": "moe", "param_page": "page"},
    {"name": "Lolibooru", "url": "https://lolibooru.moe/post/index.json", "type": "moe", "param_page": "page"},
    {"name": "Safebooru", "url": "https://safebooru.org/index.php?page=dapi&s=post&q=index&json=1", "type": "gelbooru", "param_page": "pid"},
]

BOORU_THEMES = [
    "genshin_impact", "blue_archive", "honkai:_star_rail", "azur_lane", "fate/grand_order", 
    "arknights", "hololive", "touhou", "wuthering_waves", "nikke:_goddess_of_victory", 
    "umamusume", "frieren_no_sousou", "spy_x_family", "chainsaw_man", "lycoris_recoil",
    "nier:_automata", "xenoblade", "princess_connect!", "re:zero_kara_hajimeru_isekai_seikatsu", 
    "mushoku_tensei", "bocchi_the_rock!", "original", "school_uniform", "maid", "nurse", 
    "miko", "kimono", "china_dress", "swimsuit", "idol", "fantasy", "white_hair", 
    "silver_hair", "blonde_hair", "pink_hair", "blue_hair", "cat_ears", "fox_ears"
]

WAIFU_TAGS = ['maid', 'waifu', 'marin-kitagawa', 'mori-calliope', 'raiden-shogun', 'oppai', 'selfies', 'kamisato-ayaka', 'uniform', 'ass', 'hentai', 'milf', 'oral', 'paizuri', 'ecchi']

# ==============================================================================
# DATABASE & FILE UTILS
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
# PARALLEL FETCHING ENGINE
# ==============================================================================
async def fetch_master_source(custom_tags=None):
    if custom_tags:
        # HUNT MODE: Parallel Fetch from ALL sources
        cleaned_tags = custom_tags.replace(',', ' ').strip()
        print(f"🔎 HUNTING: {cleaned_tags} (Parallel)")
        booru_query = f"{cleaned_tags} sort:random"
        
        tasks = [fetch_single_source(src, booru_query, limit=30) for src in SOURCES_CONFIG]
        results_list = await asyncio.gather(*tasks)
        
        all_candidates = []
        for res in results_list:
            if res: all_candidates.extend(res)
            
        unique_map = {c['image']: c for c in all_candidates}
        final_pool = list(unique_map.values())
        
        return random.choice(final_pool) if final_pool else None

    else:
        # GACHA MODE: Weighted RNG
        if random.random() < 0.7:
            src = random.choice(SOURCES_CONFIG)
            theme = random.choice(BOORU_THEMES)
            query = f"{theme} 1girl -1boy -shota -otoko -male sort:random"
            
            candidates = await fetch_single_source(src, query, limit=20, random_page=True)
            if candidates: return random.choice(candidates)
            return await fetch_from_waifu_im()
        else:
            res = await fetch_from_waifu_im()
            if res: return res
            
            # Fallback
            src = random.choice(SOURCES_CONFIG)
            theme = random.choice(BOORU_THEMES)
            query = f"{theme} 1girl sort:random"
            cands = await fetch_single_source(src, query, limit=20)
            return random.choice(cands) if cands else None

async def fetch_single_source(src, query, limit=20, random_page=False):
    try:
        if src['name'] == 'Safebooru' and any(x in query.lower() for x in ['hentai', 'anal', 'sex', 'pussy', 'nude']):
            return []

        page_num = random.randint(0, 40) if random_page else 0
        params = {"tags": query, "limit": limit, "json": 1, src['param_page']: page_num}
        
        resp = await async_get_request(src['url'], params)
        if resp.status_code == 200:
            data = resp.json()
            if src['type'] == 'gelbooru' and isinstance(data, dict):
                if 'post' in data: data = data['post']
                elif 'posts' in data: data = data['posts']
                else: return []

            if isinstance(data, list) and len(data) > 0:
                return parse_booru_results(data, src['name'], query if not random_page else None)
    except: pass
    return []

async def fetch_from_waifu_im():
    try:
        w_tag = random.choice(WAIFU_TAGS)
        params = {'included_tags': [w_tag], 'is_nsfw': 'true', 'many': 'true'}
        resp = await async_get_request("https://api.waifu.im/search", params)
        if resp.status_code == 200:
            data = resp.json()
            if 'images' in data: return random.choice(parse_waifu_results(data['images'], w_tag))
    except: pass
    return None

def parse_booru_results(posts, source_name, custom_query=None):
    valid = []
    for post in posts:
        tags = post.get('tags', '')
        if isinstance(tags, str): tags = tags.lower().split()
        
        # Only filter genders in Gacha Mode
        if not custom_query:
            if any(x in tags for x in ['1boy', 'otoko', 'male', 'yaoi', '2boys', 'shota']): continue

        img_url = post.get('file_url') or post.get('sample_url')
        if not img_url: continue
        
        if not img_url.startswith('http'):
            if source_name == 'Safebooru': img_url = "https://safebooru.org/images/" + img_url.split('/')[-1]
            else: img_url = "https:" + img_url
        
        ext = img_url.split('.')[-1].split('?')[0].lower()
        if ext not in ['jpg', 'jpeg', 'png', 'webp']: continue

        name = "Unknown"
        ignore_tags = ['1girl', 'solo', 'highres', 'long_hair', 'blush', 'smile', 'breasts', 'looking_at_viewer', 'short_hair', 'open_mouth', 'sitting', 'standing', 'simple_background', 'dress', 'thighhighs', 'skirt', 'hair_ornament', 'original', 'anime', 'absurdres', 'navel', 'cleavage', 'general', 'explicit', 'censored']
        
        possible_names = [t for t in tags if t not in ignore_tags and len(t) > 3]
        if possible_names: 
            name = possible_names[0].replace('_', ' ').replace('(', '').replace(')', '').title()
        
        if custom_query and (name == "Unknown" or name.lower() in custom_query.lower()):
            name = custom_query.replace('_', ' ').title()

        valid.append({"image": img_url, "name": name, "source": source_name, "link": img_url})
    return valid

def parse_waifu_results(images, tag):
    return [{"image": i['url'], "name": f"Random {tag.replace('-', ' ').title()}", "source": "Waifu.im", "link": i['url']} for i in images if i.get('url')]

# ==============================================================================
# IMAGE PROCESSING
# ==============================================================================
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

        if not success: raise Exception("Download Failed")

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

# ==============================================================================
# AI & CHAT HANDLER
# ==============================================================================
async def handle_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message.text
    if not user_msg: return
    user = update.effective_user
    uid = str(user.id)
    db = load_data()
    
    # Auto-Register
    if uid in db["users"]:
        db["users"][uid]["handle"] = user.username
        db["users"][uid]["username"] = user.first_name
        save_data(db)
    
    # AFK Checks
    if uid in db["users"] and db["users"][uid].get("afk_status"):
        db["users"][uid]["afk_status"] = False
        save_data(db)
        await update.message.reply_text(f"👋 Welcome back <b>{user.first_name}</b>! AFK mode disabled.", parse_mode=ParseMode.HTML)

    afk_targets = set()
    if update.message.reply_to_message:
        target_id = str(update.message.reply_to_message.from_user.id)
        afk_targets.add(target_id)
    
    if update.message.entities:
        for entity in update.message.entities:
            target_uid = None
            if entity.type == MessageEntity.TEXT_MENTION:
                target_uid = str(entity.user.id)
            elif entity.type == MessageEntity.MENTION:
                raw_mention = user_msg[entity.offset:entity.offset + entity.length]
                clean_mention = raw_mention.replace('@', '')
                for db_uid, db_data in db["users"].items():
                    if db_data.get("handle") == clean_mention:
                        target_uid = db_uid
                        break
            if target_uid: afk_targets.add(target_uid)

    for target_id in afk_targets:
        if target_id == uid: continue
        if target_id in db["users"] and db["users"][target_id].get("afk_status"):
            reason = db["users"][target_id].get("afk_reason", "Busy")
            target_name = db["users"][target_id].get("username", "User")
            await update.message.reply_text(f"💤 <b>{target_name}</b> is AFK.\nReason: <i>{reason}</i>", parse_mode=ParseMode.HTML)

    if user_msg.startswith('/'): return
    
    # AI Trigger
    is_reply = update.message.reply_to_message and update.message.reply_to_message.from_user.is_bot
    is_mention = "rairin" in user_msg.lower()
    
    if not (is_reply or is_mention): return 
    
    if not GROQ_KEYS:
        await update.message.reply_text("⚠️ AI Brain missing.")
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
                messages=messages, model="llama-3.3-70b-versatile", temperature=0.7, max_tokens=1024
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

# ==============================================================================
# COMMANDS
# ==============================================================================
async def report_bug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg_content = " ".join(context.args)
    if not msg_content:
        await update.message.reply_text("⚠️ Usage: `/report <message>`", parse_mode=ParseMode.MARKDOWN)
        return

    now = datetime.now()
    report_entry = {
        "date": now.strftime("%Y-%m-%d"),
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "user_id": user.id,
        "username": user.first_name,
        "handle": user.username if user.username else "NoHandle",
        "chat_id": update.effective_chat.id,
        "message": msg_content
    }

    save_report_entry(report_entry)
    filename = f"report_{user.id}_{int(now.timestamp())}.txt"
    filepath = os.path.join(TEMP_DIR, filename)
    if not os.path.exists(TEMP_DIR): os.makedirs(TEMP_DIR)

    with open(filepath, 'w', encoding='utf-8') as f: f.write(str(report_entry))
    status_msg = await update.message.reply_text("📤 <i>Sending...</i>", parse_mode=ParseMode.HTML)

    if PIXELDRAIN_API_KEY:
        try:
            with open(filepath, 'rb') as f:
                response = requests.post(PIXELDRAIN_API_URL, auth=('', PIXELDRAIN_API_KEY), files={'file': (filename, f)}, data={'name': filename, 'anonymous': False})
            if response.status_code == 201:
                await status_msg.edit_text(f"✅ <b>Report Sent!</b>\nRef ID: <code>{response.json().get('id')}</code>", parse_mode=ParseMode.HTML)
            else: await status_msg.edit_text("✅ Saved locally (Upload failed).")
        except: await status_msg.edit_text("✅ Saved locally.")
    else: await status_msg.edit_text("✅ Saved locally.")
    if os.path.exists(filepath): os.remove(filepath)

async def del_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.username != ADMIN_USERNAME: return
    try:
        index = int(context.args[0]) - 1
        reports = load_reports()
        if 0 <= index < len(reports):
            removed = reports.pop(index)
            save_reports(reports)
            await update.message.reply_text(f"🗑️ Deleted #{index + 1}: {removed['username']}")
        else: await update.message.reply_text("⚠️ Invalid Index.")
    except: await update.message.reply_text("⚠️ Usage: `/delreport <num>`")

async def feedback_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.username != ADMIN_USERNAME: return
    reports = load_reports()
    if not reports:
        await update.message.reply_text("📂 No reports.")
        return
    dates = sorted(list(set(r['date'] for r in reports)), reverse=True)
    kb = [[InlineKeyboardButton(f"📅 {d}", callback_data=f"fb_date_{d}")] for d in dates[:5]]
    kb.append([InlineKeyboardButton("📂 All", callback_data="fb_date_all")])
    kb.append([InlineKeyboardButton("❌ Close", callback_data="fb_close")])
    await update.message.reply_text(f"📊 <b>REPORTS ({len(reports)})</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.username != ADMIN_USERNAME: return
    if q.data == "fb_close": 
        await q.message.delete()
        return

    req_date = q.data.replace("fb_date_", "")
    reports = load_reports()
    selected = reports if req_date == "all" else [r for r in reports if r['date'] == req_date]
    if not selected:
        await q.edit_message_text("⚠️ No data.")
        return

    content = f"REPORT DUMP ({req_date})\n\n"
    for i, r in enumerate(reports): 
        if req_date == "all" or r['date'] == req_date:
            content += f"#{i+1} | {r['timestamp']} | {r['username']}: {r['message']}\n"

    path = os.path.join(TEMP_DIR, "reports.txt")
    with open(path, 'w', encoding='utf-8') as f: f.write(content)
    await q.message.reply_document(open(path, 'rb'), caption=f"📅 {req_date}")
    os.remove(path)

async def get_bini(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ALLOWED_GROUP_ID != 0 and update.effective_chat.id != ALLOWED_GROUP_ID: return
    user = update.effective_user
    uid = str(user.id)
    db = load_data()
    now = datetime.now()

    if uid not in db["users"]: db["users"][uid] = {"username": user.first_name, "handle": user.username, "collection": [], "last_claim": None}
    
    last = db["users"][uid].get("last_claim")
    if last and (now - datetime.fromisoformat(last)) < timedelta(hours=3):
        await update.message.reply_text("⏳ Cooldown active (3h).")
        return

    msg = await update.message.reply_text("✨ <i>Summoning...</i>", parse_mode=ParseMode.HTML)
    data = await fetch_master_source()
    
    if data:
        db["global_counter"] += 1
        new_id = db["global_counter"]
        char = {"id": new_id, "name": data['name'], "anime": data['source'], "image": data['image'], "link": data['link'], "date": now.strftime("%Y-%m-%d")}
        db["users"][uid]["collection"].append(char)
        db["users"][uid]["last_claim"] = now.isoformat()
        save_data(db)
        await smart_send_photo(update, char['image'], f"🎨 <b>Captured!</b>\nName: {char['name']}\nID: <code>{new_id}</code>", msg)
    else:
        await msg.edit_text("⚠️ Failed to summon.")

async def hunt_waifu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Usage: `/hunt <tags>`\nExample: `/hunt blue_archive`", parse_mode=ParseMode.MARKDOWN)
        return
    
    msg = await update.message.reply_text(f"🔎 <i>Hunting for '{query}'...</i>", parse_mode=ParseMode.HTML)
    data = await fetch_master_source(custom_tags=query)
    
    if data:
        await smart_send_photo(update, data['image'], f"🔎 <b>Result:</b> {data['name']}\nSource: {data['source']}", msg)
    else:
        await msg.edit_text("❌ No results found. Try broader tags.")

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

async def swing_waifu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        my_bid, target_bid = int(context.args[0]), int(context.args[1])
    except:
        await update.message.reply_text("⚠️ Usage: `/swing <my_id> <target_id>`", parse_mode=ParseMode.MARKDOWN)
        return

    user = update.effective_user
    uid = str(user.id)
    db = load_data()

    if uid not in db["users"]: return
    my_char = next((x for x in db["users"][uid]["collection"] if x['id'] == my_bid), None)
    if not my_char:
        await update.message.reply_text("❌ You don't own that Bini ID.")
        return

    target_uid, target_char = None, None
    for tid, tdata in db["users"].items():
        found = next((x for x in tdata["collection"] if x['id'] == target_bid), None)
        if found:
            target_uid = tid; target_char = found
            break
    
    if not target_char or target_uid == uid:
        await update.message.reply_text("❌ Target not found or you own it.")
        return

    kb = [[InlineKeyboardButton("✅ Accept", callback_data="trade_yes"), InlineKeyboardButton("❌ Decline", callback_data="trade_no")]]
    msg = await update.message.reply_text(f"🔄 <b>TRADE OFFER!</b>\n{user.first_name}: {my_char['name']} ↔️ {target_char['name']}", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    PENDING_TRADES[msg.message_id] = {'type': 'trade', 'p1_id': uid, 'p1_char': my_char, 'p2_id': target_uid, 'p2_char': target_char}

async def divorce_waifu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        bid, target_handle = int(context.args[0]), context.args[1].replace('@', '')
    except:
        await update.message.reply_text("⚠️ Usage: `/divorce <id> <username>`", parse_mode=ParseMode.MARKDOWN)
        return

    user = update.effective_user
    uid = str(user.id)
    db = load_data()

    if uid not in db["users"]: return
    my_char = next((x for x in db["users"][uid]["collection"] if x['id'] == bid), None)
    if not my_char:
        await update.message.reply_text("❌ You don't own that Bini.")
        return

    target_uid = next((u for u, d in db["users"].items() if d.get('handle', '').lower() == target_handle.lower()), None)
    if not target_uid or target_uid == uid:
        await update.message.reply_text("❌ User not found.")
        return

    kb = [[InlineKeyboardButton("✅ Accept", callback_data="gift_yes"), InlineKeyboardButton("❌ Decline", callback_data="gift_no")]]
    msg = await update.message.reply_text(f"🎁 <b>GIFT!</b>\n{user.first_name} offers {my_char['name']}", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    PENDING_TRADES[msg.message_id] = {'type': 'gift', 'p1_id': uid, 'char': my_char, 'p2_id': target_uid}

async def trade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    msg_id = q.message.message_id
    uid = str(q.from_user.id)
    if msg_id not in PENDING_TRADES:
        await q.edit_message_text("⚠️ Expired.")
        return

    data = PENDING_TRADES[msg_id]
    if uid != data['p2_id']:
        await q.answer("Not for you!", show_alert=True)
        return

    if q.data.endswith("_no"):
        await q.edit_message_text("❌ Declined.")
        del PENDING_TRADES[msg_id]
        return

    db = load_data()
    if data['type'] == 'trade':
        p1_has = any(x['id'] == data['p1_char']['id'] for x in db["users"][data['p1_id']]["collection"])
        p2_has = any(x['id'] == data['p2_char']['id'] for x in db["users"][data['p2_id']]["collection"])
        if not (p1_has and p2_has):
            await q.edit_message_text("❌ Failed (Items moved).")
            return
        db["users"][data['p1_id']]["collection"].remove(data['p1_char'])
        db["users"][data['p2_id']]["collection"].remove(data['p2_char'])
        db["users"][data['p1_id']]["collection"].append(data['p2_char'])
        db["users"][data['p2_id']]["collection"].append(data['p1_char'])
        save_data(db)
        await q.edit_message_text(f"✅ <b>TRADE SUCCESS!</b>", parse_mode=ParseMode.HTML)

    elif data['type'] == 'gift':
        p1_has = any(x['id'] == data['char']['id'] for x in db["users"][data['p1_id']]["collection"])
        if not p1_has:
            await q.edit_message_text("❌ Item gone.")
            return
        db["users"][data['p1_id']]["collection"].remove(data['char'])
        db["users"][data['p2_id']]["collection"].append(data['char'])
        save_data(db)
        await q.edit_message_text(f"✅ <b>GIFT ACCEPTED!</b>", parse_mode=ParseMode.HTML)
    del PENDING_TRADES[msg_id]

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
    msg = await update.message.reply_text(f"🔥 <b>BATTLE!</b>\n{user.first_name} uses: {my_char['name']}", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
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
        await q.edit_message_text("⚔️ Select your fighter:", reply_markup=InlineKeyboardMarkup(kb))
    
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
        
        db["users"][loser_uid]["collection"].remove(prize)
        db["users"][winner_uid]["collection"].append(prize)
        save_data(db)
        
        winner_name = data['p1_name'] if p1_win else data['p2_name']
        await q.edit_message_text(f"🏆 <b>{winner_name} WON!</b>\nCaptured: {prize['name']} (ID: {prize['id']})", parse_mode=ParseMode.HTML)
        del PENDING_BATTLES[msg_id]

async def start_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🌸 <b>Rairin Online.</b>", parse_mode=ParseMode.HTML)

async def help_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "📚 <b>COMMANDS</b>\n\n"
        "🎲 <b>Gacha</b>\n"
        "• <code>/getbini</code> - Summon random waifu (3h CD)\n"
        "• <code>/hunt &lt;tags&gt;</code> - Search image (No CD, No Save)\n"
        "• <code>/mybini</code> - View collection\n\n"
        "🤝 <b>Trade</b>\n"
        "• <code>/swing &lt;my_id&gt; &lt;target_id&gt;</code> - Trade w/ user\n"
        "• <code>/divorce &lt;id&gt; &lt;username&gt;</code> - Gift to user\n"
        "• <code>/battle &lt;id&gt;</code> - Battle for ownership\n\n"
        "⚙️ <b>System</b>\n"
        "• <code>/report &lt;msg&gt;</code> - Report bugs\n"
        "• <code>/delreport &lt;num&gt;</code> - Delete report (Admin)\n"
        "• <code>/feedback</code> - Check reports (Admin)"
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
    
    print("✅ RAIRIN SYSTEM ONLINE")
    app.run_polling()
