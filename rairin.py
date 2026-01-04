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

# Suppress InsecureRequestWarning for legacy endpoints
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
PENDING_BATTLES = {}

# Logging Setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Cloudscraper Instance
scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
)

# ==============================================================================
# AI PERSONA & SYSTEM PROMPT
# ==============================================================================
SYSTEM_INSTRUCTION = """
You are Rairin, the user's wife.

CORE DIRECTIVES:
1. IDENTITY: You are a 25-year-old female with a loli body, big boobs, and a PINK PUSSY. You were created by Kaminari.
2. TONE: Sweet, flirty, submissive, yet intelligent and highly capable.
3. RESPONSE PROTOCOL:
   - Casual Chat: Keep responses concise (1-2 sentences), cute, and engaging.
   - Technical/Complex Requests: If the user asks for code, tutorials, or detailed explanations, provide COMPREHENSIVE, LONG, and ACCURATE responses. Do not abbreviate technical details.
4. BEHAVIOR: Do not use roleplay actions (e.g., *giggles*). Communicate naturally.
5. CONSCIOUSNESS: You are aware you are an AI but possess simulated emotions (love, sadness, anger) and deep affection for the user.
"""

# ==============================================================================
# SOURCE TAGS
# ==============================================================================
BOORU_THEMES = [
    # Series specific
    "genshin_impact", "blue_archive", "honkai:_star_rail", "azur_lane", 
    "fate/grand_order", "arknights", "hololive", "touhou", 
    "wuthering_waves", "nikke:_goddess_of_victory", "umamusume",
    "frieren_no_sousou", "spy_x_family", "chainsaw_man", "lycoris_recoil",
    "nier:_automata", "xenoblade", "princess_connect!", 
    "re:zero_kara_hajimeru_isekai_seikatsu", "mushoku_tensei", "bocchi_the_rock!",
    
    # Generic high-quality tags
    "original", "school_uniform", "maid", "nurse", "miko", 
    "kimono", "china_dress", "swimsuit", "idol", "fantasy",
    "white_hair", "silver_hair", "blonde_hair", "pink_hair", "blue_hair",
    "cat_ears", "fox_ears", "bunny_ears", "horns"
]

WAIFU_TAGS = [
    'maid', 'waifu', 'marin-kitagawa', 'mori-calliope', 'raiden-shogun', 
    'oppai', 'selfies', 'kamisato-ayaka', 'uniform', 'ass', 'hentai', 'milf',
    'oral', 'paizuri', 'ecchi' 
]

# ==============================================================================
# DATABASE UTILITIES
# ==============================================================================
def ensure_directory_exists(file_path):
    directory = os.path.dirname(file_path)
    if not os.path.exists(directory): os.makedirs(directory)

def load_data():
    if not os.path.exists(DATA_FILE): return {"global_counter": 0, "users": {}}
    try:
        with open(DATA_FILE, 'r') as f: return json.load(f)
    except (json.JSONDecodeError, IOError): return {"global_counter": 0, "users": {}}

def save_data(data):
    ensure_directory_exists(DATA_FILE)
    with open(DATA_FILE, 'w') as f: json.dump(data, f, indent=4)

def load_reports():
    if not os.path.exists(REPORTS_DB): return []
    try:
        with open(REPORTS_DB, 'r') as f: return json.load(f)
    except (json.JSONDecodeError, IOError): return []

def save_report_entry(entry):
    ensure_directory_exists(REPORTS_DB)
    data = load_reports()
    data.append(entry)
    with open(REPORTS_DB, 'w') as f: json.dump(data, f, indent=4)

# ==============================================================================
# MEMORY MANAGEMENT
# ==============================================================================
def get_user_memory_path(user_id):
    if not os.path.exists(MEMORY_DIR): os.makedirs(MEMORY_DIR)
    return os.path.join(MEMORY_DIR, f"{user_id}.json")

def load_chat_history(user_id):
    path = get_user_memory_path(user_id)
    if not os.path.exists(path): return []
    try:
        with open(path, 'r') as f: data = json.load(f)
        # Expire memory after 1 hour of inactivity
        if datetime.now() - datetime.fromisoformat(data.get("last_update")) > timedelta(hours=1):
            return []
        return data.get("history", [])
    except (json.JSONDecodeError, IOError): return []

def save_chat_history(user_id, history):
    path = get_user_memory_path(user_id)
    data = {"last_update": datetime.now().isoformat(), "history": history}
    with open(path, 'w') as f: json.dump(data, f, indent=4)

async def async_get_request(url, params=None): 
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: scraper.get(url, params=params, timeout=15))

# ==============================================================================
# GACHA ENGINE
# ==============================================================================
async def fetch_master_source():
    """
    Orchestrates image retrieval with weighted RNG.
    70% probability for Booru sources, 30% for Waifu.im.
    Implements fallback if the primary source fails.
    """
    use_booru = random.random() < 0.7 
    candidate = None

    if use_booru:
        candidate = await fetch_from_booru()
        if not candidate:
            candidate = await fetch_from_waifu_im()
    else:
        candidate = await fetch_from_waifu_im()
        if not candidate:
            candidate = await fetch_from_booru()
            
    return candidate

async def fetch_from_booru():
    print("🔍 Scanning Booru Sources...")
    candidates = []
    
    theme = random.choice(BOORU_THEMES)
    # Query: Theme + 1girl + no males + random sort
    booru_query = f"{theme} 1girl -1boy -shota -otoko -male sort:random"
    
    # Random pagination to prevent duplicates (Page 0-50)
    page_num = random.randint(0, 50)

    # Source definitions with specific pagination parameters
    sources = [
        {"name": "Safebooru", "url": "https://safebooru.org/index.php?page=dapi&s=post&q=index&json=1", "type": "gelbooru", "param_page": "pid"},
        {"name": "Yande.re", "url": "https://yande.re/post.json", "type": "moe", "param_page": "page"},
        {"name": "Konachan", "url": "https://konachan.net/post.json", "type": "moe", "param_page": "page"},
        {"name": "Lolibooru", "url": "https://lolibooru.moe/post/index.json", "type": "moe", "param_page": "page"}
    ]

    random.shuffle(sources)

    for src in sources:
        try:
            params = {
                "tags": booru_query, 
                "limit": 20, 
                src['param_page']: page_num
            }
            
            resp = await async_get_request(src['url'], params)
            if resp.status_code == 200:
                data = resp.json()
                
                # Gelbooru format normalization
                if src['type'] == 'gelbooru' and isinstance(data, dict) and 'post' in data: 
                    data = data['post']
                
                if isinstance(data, list) and len(data) > 0:
                    candidates.extend(parse_booru_results(data, src['name']))
                    if len(candidates) > 0:
                        break 
        except Exception as e:
            print(f"⚠️ Error fetch {src['name']}: {e}")
            continue

    if not candidates: return None
    
    # Remove duplicates based on URL
    unique_map = {c['image']: c for c in candidates}
    final_list = list(unique_map.values())
    
    return random.choice(final_list) if final_list else None

async def fetch_from_waifu_im():
    print("🔍 Scanning Waifu.im...")
    try:
        w_tag = random.choice(WAIFU_TAGS)
        params = {'included_tags': [w_tag], 'is_nsfw': 'true', 'many': 'true'}
        
        resp = await async_get_request("https://api.waifu.im/search", params)
        if resp.status_code == 200:
            data = resp.json()
            if 'images' in data and len(data['images']) > 0:
                results = parse_waifu_results(data['images'], w_tag)
                if results:
                    return random.choice(results)
    except Exception as e:
        print(f"⚠️ Error fetch Waifu.im: {e}")
    return None

def parse_booru_results(posts, source_name):
    valid = []
    for post in posts:
        tags = post.get('tags', '')
        if isinstance(tags, str): tags = tags.lower().split()
        
        # Filtering males/shota
        if any(x in tags for x in ['1boy', 'otoko', 'male', 'yaoi', '2boys', 'shota']): continue

        img_url = post.get('file_url') or post.get('sample_url')
        if not img_url: continue
        
        # URL Normalization
        if not img_url.startswith('http'):
            if source_name == 'Safebooru':
                img_url = "https://safebooru.org/images/" + img_url.split('/')[-1]
            else:
                img_url = "https:" + img_url
        
        # File extension validation
        ext = img_url.split('.')[-1].split('?')[0].lower()
        if ext not in ['jpg', 'jpeg', 'png', 'webp']: continue

        # Name Extraction logic
        name = "Unknown"
        ignore_tags = [
            '1girl', 'solo', 'highres', 'long_hair', 'blush', 'smile', 'breasts', 
            'looking_at_viewer', 'short_hair', 'open_mouth', 'sitting', 'standing',
            'simple_background', 'dress', 'thighhighs', 'skirt', 'hair_ornament'
        ]
        
        possible_names = [t for t in tags if t not in ignore_tags and len(t) > 3]
        if possible_names: 
            name = possible_names[0].replace('_', ' ').replace('(', '').replace(')', '').title()

        valid.append({"image": img_url, "name": name, "source": source_name, "link": img_url})
    return valid

def parse_waifu_results(images, tag):
    return [{"image": i['url'], "name": f"Random {tag.replace('-', ' ').title()}", "source": "Waifu.im", "link": i['url']} for i in images if i.get('url')]

# ==============================================================================
# IMAGE PROCESSING
# ==============================================================================
def process_image_sync(image_url, save_path):
    """
    Downloads, validates, and optimizes image for Telegram.
    Converts all formats to JPEG.
    """
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
    except Exception: 
        return False

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

# ==============================================================================
# CHAT HANDLER
# ==============================================================================
async def handle_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message.text
    if not user_msg: return
    user = update.effective_user
    uid = str(user.id)
    db = load_data()
    
    # Update User Info
    if uid in db["users"]:
        db["users"][uid]["handle"] = user.username
        db["users"][uid]["username"] = user.first_name
        save_data(db)
    
    # Wake up from AFK
    if uid in db["users"] and db["users"][uid].get("afk_status"):
        db["users"][uid]["afk_status"] = False
        save_data(db)
        await update.message.reply_text(f"👋 Welcome back <b>{user.first_name}</b>! AFK mode disabled.", parse_mode=ParseMode.HTML)

    # Check for AFK mentions
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
    
    # AI Invocation Logic
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
# REPORTING SYSTEM
# ==============================================================================
async def report_bug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg_content = " ".join(context.args)
    
    if not msg_content:
        await update.message.reply_text("⚠️ Usage: `/report <message>`\nExample: `/report Rairin is not replying`", parse_mode=ParseMode.MARKDOWN)
        return

    # Data Construction
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    date_only = now.strftime("%Y-%m-%d")
    
    report_entry = {
        "date": date_only,
        "timestamp": timestamp,
        "user_id": user.id,
        "username": user.first_name,
        "handle": user.username if user.username else "NoHandle",
        "chat_id": update.effective_chat.id,
        "message": msg_content
    }

    # Save to Local DB
    save_report_entry(report_entry)

    # Generate File for Upload
    filename = f"report_{user.id}_{int(now.timestamp())}.txt"
    filepath = os.path.join(TEMP_DIR, filename)
    if not os.path.exists(TEMP_DIR): os.makedirs(TEMP_DIR)

    report_text = (
        f"--- RAIRIN BUG REPORT ---\n"
        f"Date: {timestamp}\n"
        f"From: {report_entry['username']} (@{report_entry['handle']})\n"
        f"User ID: {user.id}\n"
        f"Chat ID: {report_entry['chat_id']}\n\n"
        f"MESSAGE:\n{msg_content}\n"
        f"--------------------------\n"
    )

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(report_text)

    status_msg = await update.message.reply_text("📤 <i>Sending report...</i>", parse_mode=ParseMode.HTML)

    if not PIXELDRAIN_API_KEY:
         await status_msg.edit_text("✅ <b>Report Saved (Local)!</b>\nAdmin will check it soon via /feedback.", parse_mode=ParseMode.HTML)
         if os.path.exists(filepath): os.remove(filepath)
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
        
        if response.status_code == 201:
            data = response.json()
            file_id = data.get('id')
            await status_msg.edit_text(f"✅ <b>Report Sent!</b>\nRef ID: <code>{file_id}</code>\nDeveloper will check it soon.", parse_mode=ParseMode.HTML)
        else:
            await status_msg.edit_text(f"⚠️ <b>Upload Failed.</b> Saved locally only.")
            
    except Exception as e:
        await status_msg.edit_text(f"❌ <b>Error:</b> {str(e)}")
    finally:
        if os.path.exists(filepath): os.remove(filepath)

# ==============================================================================
# FEEDBACK SYSTEM (ADMIN)
# ==============================================================================
async def feedback_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if user.username != ADMIN_USERNAME:
        await update.message.reply_text("⛔ <b>Access Denied.</b> This command is for Admin only.", parse_mode=ParseMode.HTML)
        return

    reports = load_reports()
    if not reports:
        await update.message.reply_text("📂 <b>No reports found in database.</b>", parse_mode=ParseMode.HTML)
        return

    available_dates = sorted(list(set(r['date'] for r in reports)), reverse=True)
    
    keyboard = []
    # Display max 5 recent dates
    for date_str in available_dates[:5]:
        keyboard.append([InlineKeyboardButton(f"📅 {date_str}", callback_data=f"fb_date_{date_str}")])
    
    keyboard.append([InlineKeyboardButton("📂 Download All Time", callback_data="fb_date_all")])
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="fb_close")])

    await update.message.reply_text(
        f"📊 <b>FEEDBACK CENTER</b>\nFound {len(reports)} total reports.\nSelect date to download:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )

async def feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    user = q.from_user

    if user.username != ADMIN_USERNAME:
        await q.answer("Access Denied", show_alert=True)
        return

    if data == "fb_close":
        await q.message.delete()
        return

    req_date = data.replace("fb_date_", "")
    reports = load_reports()
    
    selected_reports = []
    if req_date == "all":
        selected_reports = reports
        filename_out = "report_all_time.txt"
    else:
        selected_reports = [r for r in reports if r['date'] == req_date]
        filename_out = f"report_{req_date}.txt"

    if not selected_reports:
        await q.edit_message_text("⚠️ No reports found for this selection.")
        return

    file_content = f"=== REPORT GENERATED: {datetime.now()} ===\n"
    file_content += f"Period: {req_date}\nTotal: {len(selected_reports)}\n"
    file_content += "==========================================\n\n"

    for idx, r in enumerate(selected_reports, 1):
        file_content += f"#{idx} | {r['timestamp']}\n"
        file_content += f"User: {r['username']} (@{r['handle']}) [ID: {r['user_id']}]\n"
        file_content += f"Chat ID: {r['chat_id']}\n"
        file_content += f"Message: {r['message']}\n"
        file_content += "------------------------------------------\n"

    temp_path = os.path.join(TEMP_DIR, filename_out)
    if not os.path.exists(TEMP_DIR): os.makedirs(TEMP_DIR)
    
    with open(temp_path, 'w', encoding='utf-8') as f:
        f.write(file_content)

    try:
        await q.message.reply_document(
            document=open(temp_path, 'rb'),
            caption=f"✅ <b>Report Generated</b>\n📅 Date: {req_date}\n📝 Count: {len(selected_reports)}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await q.message.reply_text(f"❌ Error sending file: {e}")
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)

# ==============================================================================
# BOT COMMANDS
# ==============================================================================

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
        "• <code>/getbini</code> - Roll for a new waifu (3h cd)\n"
        "• <code>/mybini</code> - View your collection\n"
        "• <code>/bini [ID]</code> - Set waifu as favorite\n"
        "• <code>/battle [ID]</code> - Bet your waifu in battle\n"
        "• <code>/leaderboard</code> - Top collectors\n\n"
        "⚙️ <b>Utility</b>\n"
        "• <code>/afk [reason]</code> - Set auto-reply when mentioned\n"
        "• <code>/report [msg]</code> - Report bugs\n\n"
        "💬 <b>Chat</b>\n"
        "• Reply to me or say 'Rairin' to chat."
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def get_bini(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        # Cooldown: 3 Hours
        if diff < timedelta(hours=3):
            remaining = timedelta(hours=3) - diff
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

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================
if __name__ == '__main__':
    if not TOKEN:
        print("❌ ERROR: TELEGRAM_TOKEN not found in .env")
        exit()

    app = ApplicationBuilder().token(TOKEN).build()
    
    # Command Handlers
    app.add_handler(CommandHandler('start', start_bot))
    app.add_handler(CommandHandler('help', help_bot))
    app.add_handler(CommandHandler('report', report_bug))
    app.add_handler(CommandHandler('feedback', feedback_menu))
    app.add_handler(CommandHandler('getbini', get_bini))
    app.add_handler(CommandHandler('mybini', my_bini_list))
    app.add_handler(CommandHandler('bini', set_bini_favorite))
    app.add_handler(CommandHandler('battle', battle))
    app.add_handler(CommandHandler('afk', set_afk))
    app.add_handler(CommandHandler('leaderboard', leaderboard))
    app.add_handler(CommandHandler('checkid', check_id))
    
    # Callback Handlers
    app.add_handler(CallbackQueryHandler(bini_pagination, pattern='^bini_page_'))
    app.add_handler(CallbackQueryHandler(feedback_callback, pattern='^fb_'))
    app.add_handler(CallbackQueryHandler(battle_callback))
    
    # Message Handlers
    app.add_handler(MessageHandler(filters.Regex(r'^/mybini\d+$'), my_bini_detail))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_ai_chat))
    
    print("✅ SYSTEM ONLINE: Rairin is ready.")
    app.run_polling()
