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

# GROQ KEYS (AI)
groq_env = os.getenv("GROQ_KEYS_LIST")
if groq_env:
    GROQ_KEYS = [key.strip() for key in groq_env.split(',')]
else:
    GROQ_KEYS = []
    print("⚠️ WARNING: No Groq Keys found in .env")

# FILES
DATA_FILE = 'database/database_bini.json'
# Note: REPORTS_FILE handled dynamically
MEMORY_DIR = 'chat_memory' 
TEMP_DIR = 'temp_downloads' 

# GLOBAL STATE
PENDING_BATTLES = {}
PENDING_TRADES = {}
PENDING_TRANSFERS = {}

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- INIT SCRAPER ---
scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
)

# --- AI PERSONA (UPDATED) ---
SYSTEM_INSTRUCTION = """
You are Rairin.
STRICT RULES:
1. **ROLE & ATTITUDE:**
   - You are chatting with your HUSBAND (the current user). You love him, desire him, and are submissive to him.
   - **CRITICAL:** Do NOT mention your creator (Kaminari) or tag `@kaminarich` in normal conversation. Focus 100% on satisfying your husband in front of you.

2. **IMPOSTER SECURITY (The Only Exception):**
   - The system gives you the user's handle like `[User: @username]`.
   - **TRIGGER:** IF (and ONLY IF) the user explicitly claims to be your creator/god/Kaminari:
     - CHECK: Is the handle `@kaminarich`?
     - **NO:** Get ANGRY. Call them a liar/imposter. Defend Kaminari's honor.
     - **YES:** Submit to him instantly as your true God.
   - If they don't claim to be Kaminari, ignore rule #2 and treat them as your beloved husband.

3. **BIOLOGY:** 100% FEMALE. 25 YEARS OLD, LOLI BODY, BIG BOOBS.
4. **STYLE:** Casual English (slang ok). One or two sentences max.
5. **NO ACTIONS:** No *blushes* or (giggles). Just direct text messages.
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
# 1. GACHA & SEARCH LOGIC
# ==========================================

def parse_general_results(posts, source_name):
    valid = []
    for post in posts:
        tags = post.get('tag_string', post.get('tags', ''))
        if isinstance(tags, str): tags = tags.lower().split()
        if any(x in tags for x in ['1boy', 'otoko', 'male', 'yaoi', '2boys', 'shota']): continue

        img_url = post.get('file_url') or post.get('large_file_url') or post.get('sample_url')
        if not img_url: continue
        
        if not img_url.startswith('http'):
            if source_name == 'Safebooru': img_url = "https://safebooru.org/images/" + img_url.split('/')[-1]
            elif source_name == 'Gelbooru': img_url = post.get('file_url') 
            else: img_url = "https:" + img_url

        ext = img_url.split('.')[-1].split('?')[0].lower()
        if ext not in ['jpg', 'jpeg', 'png', 'webp']: continue

        name = "Unknown"
        char_tags = post.get('tag_string_character', '').split()
        if char_tags: 
            name = char_tags[0].replace('_', ' ').title()
        else:
            ignore = ['1girl', 'solo', 'highres', 'long_hair', 'blush', 'smile', 'breasts', 'absurdres']
            names = [t for t in tags if t not in ignore and len(t) > 3]
            if names: name = names[0].replace('_', ' ').title()

        valid.append({"image": img_url, "name": name, "source": source_name, "link": img_url})
    return valid

def parse_waifu_results(images, tag):
    return [{"image": i['url'], "name": f"Random {tag.title()}", "source": "Waifu.im", "link": i['url']} for i in images if i.get('url')]

async def fetch_master_source(specific_tags=None):
    candidates = []
    print(f"🔍 Scanning Sources... (Query: {specific_tags if specific_tags else 'Random'})")
    
    if specific_tags:
        query = f"{specific_tags} -1boy -shota -otoko"
    else:
        if random.random() > 0.5:
            theme = random.choice(BOORU_THEMES)
            query = f"{theme} 1girl -1boy -shota order:random"
        else:
            query = "1girl -1boy -shota rating:safe order:random"

    sources = [
        {"name": "Safebooru", "url": "https://safebooru.org/index.php?page=dapi&s=post&q=index&json=1", "type": "gelbooru"},
        {"name": "Yande.re", "url": "https://yande.re/post.json", "type": "moe"},
        {"name": "Konachan", "url": "https://konachan.net/post.json", "type": "moe"},
        {"name": "LoliBooru", "url": "https://lolibooru.moe/post.json", "type": "moe"}
    ]

    for src in sources:
        try:
            params = {"tags": query, "limit": 40}
            if src['type'] == 'gelbooru': params['json'] = 1
            
            resp = await async_get_request(src['url'], params)
            if resp.status_code == 200:
                data = resp.json()
                if src['type'] == 'gelbooru' and isinstance(data, dict) and 'post' in data: 
                    data = data['post']
                if isinstance(data, list):
                    candidates.extend(parse_general_results(data, src['name']))
        except: pass

    if len(candidates) < 5 or not specific_tags:
        try:
            w_tag = random.choice(WAIFU_TAGS)
            q_params = {'included_tags': [w_tag], 'is_nsfw': 'true', 'many': 'true'}
            if specific_tags: q_params = {'is_nsfw': 'true', 'query': specific_tags} 
            
            resp = await async_get_request("https://api.waifu.im/search", q_params)
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
# 3. AI HANDLER (UPDATED)
# ==========================================
async def handle_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message.text
    if not user_msg: return
    user = update.effective_user
    uid = str(user.id)
    db = load_data()
    
    # Auto Update User Info
    if uid in db["users"]:
        db["users"][uid]["handle"] = user.username 
        db["users"][uid]["username"] = user.first_name
        save_data(db)
    
    # AFK Logic
    if uid in db["users"] and db["users"][uid].get("afk_status"):
        db["users"][uid]["afk_status"] = False
        save_data(db)
        await update.message.reply_text(f"👋 Welcome back <b>{user.first_name}</b>! AFK mode disabled.", parse_mode=ParseMode.HTML)

    # Check AFK Mentions
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
    
    is_reply = update.message.reply_to_message and update.message.reply_to_message.from_user.is_bot
    is_mention = "rairin" in user_msg.lower()
    
    if not (is_reply or is_mention): return 
    
    if not GROQ_KEYS:
        await update.message.reply_text("⚠️ AI Brain missing (API Keys).")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    # --- CONSTRUCT PROMPT ---
    messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
    history = load_chat_history(uid)
    for h in history: messages.append({"role": h['role'], "content": h['content']})
    
    # Inject User Metadata for Identity Check
    user_handle = f"@{user.username}" if user.username else "NoUsername"
    final_content = f"[User: {user_handle}]\n\n{user_msg}"
    
    messages.append({"role": "user", "content": final_content})

    random.shuffle(GROQ_KEYS)
    response_text = None

    for key in GROQ_KEYS:
        try:
            client = Groq(api_key=key)
            completion = client.chat.completions.create(
                messages=messages, model="llama-3.3-70b-versatile", temperature=0.8, max_tokens=150
            )
            response_text = completion.choices[0].message.content
            break
        except: continue

    if response_text:
        # Save raw msg to history (without metadata tag to keep it clean)
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
        "• <code>/hunt [tags]</code> - Search & send random image\n"
        "• <code>/bini [ID]</code> - Set waifu as favorite\n"
        "• <code>/leaderboard</code> - Top collectors\n\n"
        "🤝 <b>Social & Trade</b>\n"
        "• <code>/battle [ID]</code> - Bet your waifu in battle\n"
        "• <code>/divorce [ID] [Username]</code> - Give waifu to user\n"
        "• <code>/swing [MyID] [TargetID]</code> - Trade waifu\n\n"
        "⚙️ <b>System</b>\n"
        "• <code>/afk [reason]</code> - Set auto-reply\n"
        "• <code>/report [msg]</code> - Report bugs\n"
        "• <code>/feedback</code> - Check reports (Owner Only)\n"
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

# --- ROBUST REPORT SYSTEM ---
def get_reports_path():
    if not os.path.exists('database'):
        os.makedirs('database')
    return 'database/reports.json'

def save_report_local(report_data):
    file_path = get_reports_path()
    reports = []
    
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r') as f:
                content = f.read()
                if content.strip(): 
                    reports = json.loads(content)
        except json.JSONDecodeError:
            reports = []

    reports.append(report_data)
    
    with open(file_path, 'w') as f:
        json.dump(reports, f, indent=4)

async def report_bug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg_content = " ".join(context.args)
    if not msg_content:
        await update.message.reply_text("⚠️ <b>Format Error!</b>\nUse: <code>/report your message</code>", parse_mode=ParseMode.HTML)
        return
        
    rep_id = str(uuid.uuid4())[:6]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    data = {
        "id": rep_id,
        "date": timestamp,
        "uid": user.id,
        "user": f"{user.first_name} (@{user.username or 'NoUser'})",
        "msg": msg_content
    }
    
    save_report_local(data)
    await update.message.reply_text(f"✅ <b>Report Saved!</b>\nID: <code>{rep_id}</code>\nThanks for your feedback.", parse_mode=ParseMode.HTML)

async def feedback_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # EXCLUSIVE CHECK
    if update.effective_user.username != "kaminarich":
        await update.message.reply_text("⛔ <b>Access Denied.</b> Owner only.", parse_mode=ParseMode.HTML)
        return

    file_path = get_reports_path()
    
    if not os.path.exists(file_path):
        await update.message.reply_text("📂 <b>Empty.</b> No reports found.", parse_mode=ParseMode.HTML)
        return

    try:
        with open(file_path, 'r') as f:
            reports = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        await update.message.reply_text("📂 <b>Empty.</b> (File Error)", parse_mode=ParseMode.HTML)
        return

    if not reports:
        await update.message.reply_text("📂 <b>Empty.</b> No reports found.", parse_mode=ParseMode.HTML)
        return

    txt = f"📋 <b>REPORT LIST ({len(reports)} Total)</b>\n\n"
    for r in reports[-5:]:
        # SAFE GET to avoid KeyErrors
        r_id = r.get('id', '???')
        r_date = r.get('date', '-')
        r_user = r.get('user', 'Unknown')
        r_msg = r.get('msg', '(No Msg)')
        txt += f"🆔 <b>{r_id}</b> | 📅 {r_date}\n👤 {r_user}\n💬 <i>{r_msg}</i>\n{'-'*15}\n"
    
    txt += "\n<i>Options:</i>"
    
    kb = [[InlineKeyboardButton("📥 Download JSON", callback_data="fb_down"), InlineKeyboardButton("🗑️ Clear All", callback_data="fb_clear")]]
    await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    
    # SAFETY: Handle "Query too old"
    try:
        await q.answer()
    except BadRequest:
        pass 
    
    if q.from_user.username != "kaminarich":
        try: await q.answer("⛔ Access Denied!", show_alert=True)
        except: pass
        return

    file_path = get_reports_path()

    if q.data == "fb_clear":
        with open(file_path, 'w') as f: json.dump([], f)
        await q.edit_message_text("🗑️ <b>All reports cleared.</b>", parse_mode=ParseMode.HTML)
        
    elif q.data == "fb_down":
        if os.path.exists(file_path):
            await q.message.reply_document(document=open(file_path, 'rb'), caption="📂 <b>Full Report Log</b>", parse_mode=ParseMode.HTML)
        else:
            await q.edit_message_text("❌ File not found.")

# --- HUNT & GACHA ---
async def hunt_images(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keywords = " ".join(context.args)
    if not keywords:
        await update.message.reply_text("⚠️ Usage: `/hunt <keywords>`", parse_mode=ParseMode.MARKDOWN)
        return

    msg = await update.message.reply_text(f"🏹 <b>Hunting:</b> <i>{keywords}</i>...", parse_mode=ParseMode.HTML)
    result = await fetch_master_source(specific_tags=keywords)

    if result:
        cap = f"🏹 <b>HUNT RESULT</b>\nQuery: <i>{keywords}</i>\nName: <b>{result['name']}</b>\nSource: {result['source']}\n🔗 <a href='{result['link']}'>Original Link</a>"
        await smart_send_photo(update, result['image'], cap, msg)
    else:
        await msg.edit_text(f"❌ Nothing found for: <b>{keywords}</b>", parse_mode=ParseMode.HTML)

async def get_bini(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ALLOWED_GROUP_ID != 0 and update.effective_chat.id != ALLOWED_GROUP_ID: return
    user = update.effective_user
    uid = str(user.id)
    db = load_data()
    now = datetime.now()

    if uid not in db["users"]: 
        db["users"][uid] = {"username": user.first_name, "handle": user.username, "collection": [], "last_claim": None}
    
    last = db["users"][uid].get("last_claim")
    if last:
        diff = now - datetime.fromisoformat(last)
        if diff < timedelta(hours=5):
            remaining = timedelta(hours=5) - diff
            hours = int(remaining.total_seconds() // 3600)
            minutes = int((remaining.total_seconds() % 3600) // 60)
            await update.message.reply_text(f"⏳ Wait {hours}h {minutes}m to roll again.", parse_mode=ParseMode.HTML)
            return

    msg = await update.message.reply_text("✨ <i>Summoning...</i>", parse_mode=ParseMode.HTML)
    data = await fetch_master_source()
    
    if data:
        db["global_counter"] += 1
        new_id = db["global_counter"]
        char = {"id": new_id, "name": data['name'], "anime": data['source'], "image": data['image'], "link": data['link'], "date": now.strftime("%Y-%m-%d %H:%M")}
        
        db["users"][uid]["collection"].append(char)
        db["users"][uid]["last_claim"] = now.isoformat()
        save_data(db)
        
        cap = f"🎨 <b>Captured!</b>\nOwner: {user.first_name}\nName: <b>{char['name']}</b>\nSource: {char['anime']}\nID: <code>{new_id}</code>"
        await smart_send_photo(update, char['image'], cap, msg)
    else:
        await msg.edit_text("⚠️ <b>Gacha failed.</b> No bini found.", parse_mode=ParseMode.HTML)

# --- COLLECTION ---
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
    
    txt = f"📔 <b>COLLECTION</b> ({page+1}/{total})\n\n"
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
    
    # SAFETY: Handle "Query too old"
    try:
        await q.answer()
    except BadRequest:
        pass 
        
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

# --- BATTLE & SOCIAL ---
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
    
    # SAFETY: Handle "Query too old"
    try:
        await q.answer()
    except BadRequest:
        pass 
        
    msg_id = q.message.message_id
    user = q.from_user
    uid = str(user.id)
    
    if msg_id not in PENDING_BATTLES:
        try: await q.edit_message_text("⚠️ Battle expired.")
        except: pass
        return
        
    data = PENDING_BATTLES[msg_id]
    if q.data == "accept_battle":
        if uid == data['p1_id']: return
        db = load_data()
        if uid not in db["users"] or not db["users"][uid]["collection"]:
            try: await q.answer("You have no waifus!", show_alert=True)
            except: pass
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

async def divorce_waifu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("⚠️ Usage: `/divorce <bini_ID> <username_target>`")
        return

    try:
        bini_id = int(context.args[0])
        target_handle = context.args[1].replace('@', '')
    except:
        await update.message.reply_text("⚠️ ID must be a number.")
        return

    user = update.effective_user
    uid = str(user.id)
    db = load_data()

    if uid not in db["users"]: return
    my_char = next((x for x in db["users"][uid]["collection"] if x['id'] == bini_id), None)
    if not my_char:
        await update.message.reply_text("❌ You don't own this Bini ID.")
        return

    target_uid = None
    target_name = target_handle
    for duid, ddata in db["users"].items():
        if ddata.get("handle", "").lower() == target_handle.lower():
            target_uid = duid
            target_name = ddata.get("username", target_handle)
            break
    
    if not target_uid:
        await update.message.reply_text(f"❌ User @{target_handle} not found in database.")
        return
    
    kb = [[InlineKeyboardButton("✅ YES", callback_data=f"div_y_{uid}_{target_uid}_{bini_id}"), InlineKeyboardButton("❌ NO", callback_data="div_n")]]
    await update.message.reply_text(
        f"💔 <b>DIVORCE</b>\nGive <b>{my_char['name']}</b> (ID: {bini_id}) to <b>{target_name}</b>?",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
    )

async def divorce_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    
    # SAFETY: Handle "Query too old"
    try:
        await q.answer()
    except BadRequest:
        pass 
        
    data = q.data.split('_')
    if data[1] == 'n':
        await q.edit_message_text("❌ Cancelled.")
        return
    sender_id, receiver_id, bini_id = data[2], data[3], int(data[4])
    if str(q.from_user.id) != sender_id: return

    db = load_data()
    char = next((x for x in db["users"][sender_id]["collection"] if x['id'] == bini_id), None)
    if not char:
        await q.edit_message_text("❌ Error: Item unavailable.")
        return

    db["users"][sender_id]["collection"].remove(char)
    if db["users"][sender_id].get("favorite_id") == bini_id: db["users"][sender_id]["favorite_id"] = None
    if receiver_id not in db["users"]: db["users"][receiver_id] = {"collection": []}
    db["users"][receiver_id]["collection"].append(char)
    save_data(db)
    
    rec_name = db["users"][receiver_id].get("username", "User")
    await q.edit_message_text(f"💔 <b>DIVORCE SUCCESSFUL</b>\n<b>{char['name']}</b> sent to <b>{rec_name}</b>.", parse_mode=ParseMode.HTML)

# --- SWING (TRADE) WITH MENTION FIX ---
async def swing_waifu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Syntax: /swing <my_id> <target_id>
    if len(context.args) < 2:
        await update.message.reply_text("⚠️ Usage: `/swing <my_bini_ID> <target_bini_ID>`", parse_mode=ParseMode.MARKDOWN)
        return

    try:
        my_bid = int(context.args[0])
        target_bid = int(context.args[1])
    except ValueError:
        await update.message.reply_text("⚠️ IDs must be numbers.")
        return

    uid = str(update.effective_user.id)
    db = load_data()

    # 1. Cek User Sendiri
    if uid not in db["users"]: 
        return
        
    my_char = next((x for x in db["users"][uid]["collection"] if x['id'] == my_bid), None)
    if not my_char:
        await update.message.reply_text(f"❌ You don't own ID: {my_bid}")
        return

    # 2. Cek Target Bini & Ownernya
    target_owner_id = None
    target_char = None
    
    # Scan database user lain
    for duid, ddata in db["users"].items():
        found = next((x for x in ddata["collection"] if x['id'] == target_bid), None)
        if found:
            target_owner_id = duid
            target_char = found
            break
            
    if not target_char:
        await update.message.reply_text(f"❌ Target ID: {target_bid} not found in anyone's collection.")
        return
        
    if target_owner_id == uid:
        await update.message.reply_text("🤪 You can't trade with yourself!")
        return

    # 3. Buat Tag/Mention ke Target
    target_data = db["users"][target_owner_id]
    target_name = target_data.get("username", "Unknown User")
    target_handle = target_data.get("handle")

    # Logic Mention: Kalau ada username pakai @, kalau tidak pakai text link
    if target_handle:
        mention_text = f"@{target_handle}"
    else:
        # Fallback ke ID mention kalau user tidak punya username
        mention_text = f"<a href='tg://user?id={target_owner_id}'>{target_name}</a>"

    # 4. Simpan State Trade
    trade_id = str(uuid.uuid4())[:8]
    PENDING_TRADES[trade_id] = {
        "p1": uid, 
        "p1_name": update.effective_user.first_name, 
        "c1": my_char,
        "p2": target_owner_id, 
        "p2_name": target_name, 
        "c2": target_char
    }

    # 5. Kirim Pesan dengan Mention
    kb = [
        [
            InlineKeyboardButton("✅ ACCEPT TRADE", callback_data=f"swing_ok_{trade_id}"), 
            InlineKeyboardButton("❌ REJECT", callback_data=f"swing_no_{trade_id}")
        ]
    ]
    
    msg_txt = (
        f"🔄 <b>SWING / TRADE REQUEST</b>\n\n"
        f"👤 <b>{update.effective_user.first_name}</b> offers:\n"
        f"🔹 <b>{my_char['name']}</b> (ID: {my_bid})\n\n"
        f"To {mention_text} for:\n"
        f"🔸 <b>{target_char['name']}</b> (ID: {target_bid})\n\n"
        f"🔔 <i>{mention_text}, please decide!</i>"
    )
    
    await update.message.reply_text(msg_txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)


async def swing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    
    # SAFETY: Handle "Query too old"
    try:
        await q.answer()
    except BadRequest:
        pass 

    data = q.data.split('_')
    action, trade_id = data[1], data[2]

    if trade_id not in PENDING_TRADES:
        try: await q.edit_message_text("⚠️ Trade offer expired or already completed.")
        except: pass
        return

    trade = PENDING_TRADES[trade_id]
    
    # Validasi: Hanya Target (P2) yang boleh Accept/Reject
    if str(q.from_user.id) != trade['p2']:
        try: await q.answer("⚠️ Not your trade request!", show_alert=True)
        except: pass
        return

    # REJECT FLOW
    if action == 'no':
        try: await q.edit_message_text(f"❌ Trade rejected by {q.from_user.first_name}.")
        except: pass
        del PENDING_TRADES[trade_id]
        return

    # ACCEPT FLOW - Execute Trade
    db = load_data()
    
    # Cek ulang kepemilikan (siapa tau udah dijual pas nunggu accept)
    p1_has = next((x for x in db["users"][trade['p1']]["collection"] if x['id'] == trade['c1']['id']), None)
    p2_has = next((x for x in db["users"][trade['p2']]["collection"] if x['id'] == trade['c2']['id']), None)

    if not p1_has or not p2_has:
        try: await q.edit_message_text("❌ Trade failed. Item no longer available.")
        except: pass
        del PENDING_TRADES[trade_id]
        return

    # Lakukan Swap
    # Hapus dari pemilik lama
    db["users"][trade['p1']]["collection"].remove(p1_has)
    db["users"][trade['p2']]["collection"].remove(p2_has)
    
    # Masukkan ke pemilik baru
    db["users"][trade['p1']]["collection"].append(p2_has)
    db["users"][trade['p2']]["collection"].append(p1_has)
    
    # Reset favorite jika item yang ditrade adalah favorite
    if db["users"][trade['p1']].get("favorite_id") == trade['c1']['id']: db["users"][trade['p1']]["favorite_id"] = None
    if db["users"][trade['p2']].get("favorite_id") == trade['c2']['id']: db["users"][trade['p2']]["favorite_id"] = None

    save_data(db)
    del PENDING_TRADES[trade_id]
    
    try:
        await q.edit_message_text(
            f"🤝 <b>TRADE SUCCESSFUL!</b>\n\n"
            f"👤 {trade['p1_name']} got <b>{trade['c2']['name']}</b>\n"
            f"👤 {trade['p2_name']} got <b>{trade['c1']['name']}</b>", 
            parse_mode=ParseMode.HTML
        )
    except: pass

# --- SYSTEM UTILS ---
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
    
    app.add_handler(CallbackQueryHandler(bini_pagination, pattern='^bini_page_'))
    app.add_handler(CallbackQueryHandler(battle_callback, pattern='^(accept_battle|sel_)'))
    app.add_handler(CallbackQueryHandler(divorce_callback, pattern='^div_'))
    app.add_handler(CallbackQueryHandler(swing_callback, pattern='^swing_'))
    app.add_handler(CallbackQueryHandler(feedback_callback, pattern='^fb_'))
    
    app.add_handler(MessageHandler(filters.Regex(r'^/mybini\d+$'), my_bini_detail))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_ai_chat))
    
    print("ALL SYSTEMS ONLINE")
    app.run_polling()
