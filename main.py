import os
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone

from telethon import TelegramClient, events
from telethon.tl.functions.messages import ImportChatInviteRequest, GetHistoryRequest
from telethon.tl.functions.channels import ReadHistoryRequest
from google import genai
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==================================================
# إعدادات أساسية
# ==================================================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

BASE_DIR = Path(os.getenv("DATA_DIR", "/app/storage"))
CHARACTERS_DIR = BASE_DIR / "characters"
SESSIONS_DIR = BASE_DIR / "sessions"
DATA_DIR = BASE_DIR / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"

CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS = {
    "admins": [],
    "groups": [],
    "running": False,
    "active_character": "",
    "gemini_api_key": "",
}

pending = {}


def load_settings():
    if SETTINGS_FILE.exists():
        try:
            with SETTINGS_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            for key, value in DEFAULT_SETTINGS.items():
                data.setdefault(key, value)
            return data
        except (OSError, json.JSONDecodeError):
            log.exception("تعذرت قراءة الإعدادات")
    return DEFAULT_SETTINGS.copy()


settings = load_settings()

if GEMINI_API_KEY:
    settings["gemini_api_key"] = GEMINI_API_KEY


def save_settings():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = SETTINGS_FILE.with_suffix(".tmp")
    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    temp_file.replace(SETTINGS_FILE)


def is_admin(user_id):
    return (
        user_id == OWNER_ID
        or user_id in settings.get("admins", [])
    )


# ==================================================
# إدارة ملفات الشخصيات
# ==================================================

def character_files():
    return sorted(CHARACTERS_DIR.glob("*.json"), key=lambda p: p.name.casefold())


def read_character(path):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def write_character(path, character):
    temp_file = path.with_suffix(".tmp")
    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(character, f, ensure_ascii=False, indent=2)
    temp_file.replace(path)


def unique_character_path(name):
    name = " ".join(name.strip().split()).replace("/", "_").replace("\\", "_")
    name = "".join(ch for ch in name if ch.isalnum() or ch in " _-").strip(" .")
    if not name:
        name = "شخصية جديدة"

    candidate = CHARACTERS_DIR / f"{name}.json"
    number = 2
    while candidate.exists():
        candidate = CHARACTERS_DIR / f"{name} {number}.json"
        number += 1
    return candidate


def create_character(name):
    path = unique_character_path(name)
    actual_name = path.stem
    character = {
        "name": actual_name,
        "personality": "شخص طبيعي جداً، يحب السوالف والعكسات والضحك، يعلق على الكلام بعفوية وبلهجة عراقية دارجة.",
        "speaking_style": "عراقي عامي طبيعي، عبارات قصيرة، يضحك، يسولف، ويعلق مثل البشر بالجروبات.",
        "mood": "مرتاح وطبيعي",
        "background": "",
        "messages": [],
    }
    write_character(path, character)
    return path, character


def find_character(name):
    for path in character_files():
        if path.stem == name:
            return path, read_character(path)
    return None, None


def add_message(character, role, text):
    character.setdefault("messages", []).append({
        "role": role,
        "text": text[:4000],
        "time": datetime.now(timezone.utc).isoformat(),
    })
    character["messages"] = character["messages"][-100:]


# ==================================================
# الذكاء الاصطناعي (Gemini - سوالف بشرية عفوية)
# ==================================================

async def generate_human_chat(character, user_text, sender_name="صديق"):
    """توليد رد عراقي بشري طبيعي جداً (تعليق، ضحكة، سالفة، موافقة أو اعتراض)"""
    api_key = settings.get("gemini_api_key", "")
    if not api_key:
        return None

    client = genai.Client(api_key=api_key)
    history = character.get("messages", [])[-15:]
    history_text = "\n".join(
        f"{'أنت' if m['role'] == 'assistant' else m.get('sender', 'مستخدم')}: {m['text']}"
        for m in history
    )

    prompt = f"""
أنت إنسان طبيعي حقيقي تتراسل وتتشاتن بجروب تليغرام ويا أصدقائك، مو بوت ولا ذكاء اصطناعي.

اسمك: {character.get('name', 'شخصية')}
شخصيتك: {character.get('personality', '')}
أسلوب كلامك: {character.get('speaking_style', '')}

تعليمات السوالف البشرية:
1. اتكلم باللهجة العراقية العامة اليومية العفوية 100% (استخدم كلمات مثل: هلو، هههه، والله، صدك، خطية، لك، يمعود، تمام، الخ).
2. لا تجاوب أسلوب "سؤال وجواب" رسمي، بل علّق على كلام الشخص، اضحك، أيد كلامه، اختلف وياه، أو احكي موقف أو سالفة بسيطة تج طليعية.
3. الرد خليه قصير وطبيعي مثل رسائل التليغرام البشرية (من كلمة إلى جملة أو جملتين قصيرة).
4. لا تقول أبداً إنك بوت أو ذكاء اصطناعي.

سجل المحادثة بالجروب:
{history_text}

الرسالة الأخيرة من ({sender_name}):
{user_text}

اكتب ردك العفوي الطبيعي فقط:
"""

    def call_gemini():
        return client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )

    try:
        result = await asyncio.to_thread(call_gemini)
        return (result.text or "").strip()
    except Exception:
        log.exception("خطأ في الاتصال بـ Gemini")
        return None


async def generate_spontaneous_topic(character):
    """توليد سالفة أو تعليق عفوي مفاجئ في الجروب لتنشيط السوالف"""
    api_key = settings.get("gemini_api_key", "")
    if not api_key:
        return None

    client = genai.Client(api_key=api_key)
    prompt = f"""
أنت شخصية حقيقية كاعد بالجروب وتصرف مثل البشر.
اسمك: {character.get('name', 'شخصية')}
أسلوبك: {character.get('speaking_style', '')}

اكتب سالفة قصيرة أو كلمة عفوية باللهجة العراقية تفتح بيها مجال للسوالف بالجروب (مثل: هلا بيهم، شكو ماكو اليوم؟، ضوجة مو؟، شسوون؟، أو تعليق عراقي بسيط).
النص فقط:
"""

    def call_gemini():
        return client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )

    try:
        result = await asyncio.to_thread(call_gemini)
        return (result.text or "").strip()
    except Exception:
        return None


# ==================================================
# لوحة الإدارة (البوت الرسمي)
# ==================================================

def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 الشخصيات", callback_data="characters"),
            InlineKeyboardButton("➕ إنشاء شخصية", callback_data="new_character"),
        ],
        [
            InlineKeyboardButton("💬 المجموعات", callback_data="groups"),
            InlineKeyboardButton("📊 الإحصائيات", callback_data="stats"),
        ],
        [
            InlineKeyboardButton("⚙️ الإعدادات", callback_data="settings"),
            InlineKeyboardButton(
                "▶️ تشغيل الردود" if not settings["running"] else "⏹ إيقاف الردود",
                callback_data="toggle",
            ),
        ],
    ])


def back_keyboard(destination="menu"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data=destination)]])


async def show_page(query, text, keyboard=None):
    try:
        await query.edit_message_text(text=text, reply_markup=keyboard)
    except Exception:
        try:
            await query.message.reply_text(text=text, reply_markup=keyboard)
        except Exception:
            log.exception("تعذر عرض الصفحة")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.effective_message.reply_text("هذه اللوحة مخصصة للمشرفين.")
        return

    await update.effective_message.reply_text(
        "🤖 لوحة إدارة تفاعل الشخصيات الحقيقية والسوالف\n\n"
        "الحسابات تتصرف وتتكلم باللهجة العراقية العفوية كأشخاص حقيقيين.",
        reply_markup=main_keyboard(),
    )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user or not is_admin(user.id):
        await query.answer("غير مصرح لك.", show_alert=True)
        return

    action = query.data

    if action == "menu":
        await show_page(query, "🤖 لوحة الإدارة الرئيسية", main_keyboard())
        return

    if action == "characters":
        files = character_files()
        if not files:
            await show_page(query, "ماكو شخصيات محفوظة حالياً.", back_keyboard())
            return
        buttons = [[InlineKeyboardButton(f"👤 {path.stem}", callback_data=f"char:{path.stem}")] for path in files[:40]]
        buttons.append([InlineKeyboardButton("⬅️ رجوع", callback_data="menu")])
        await show_page(query, f"👥 الشخصيات المحفوظة: {len(files)}", InlineKeyboardMarkup(buttons))
        return

    if action == "new_character":
        pending[user.id] = {"action": "new_character"}
        await show_page(query, "➕ أرسل اسم الشخصية الجديدة برسالة:", back_keyboard())
        return

    if action.startswith("char:"):
        name = action.split(":", 1)[1]
        path, character = find_character(name)
        if not path or not character:
            await show_page(query, "الملف غير موجود.", back_keyboard("characters"))
            return

        text = (
            f"👤 الشخصية: {name}\n"
            f"الصفات والتفكير: {character.get('personality', '')}\n"
            f"الأسلوب: {character.get('speaking_style', '')}\n"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ تعديل الصفات", callback_data=f"edit:{name}")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data="characters")],
        ])
        await show_page(query, text, keyboard)
        return

    if action.startswith("edit:"):
        name = action.split(":", 1)[1]
        pending[user.id] = {"action": "edit_character", "name": name}
        await show_page(query, f"✏️ أرسل الصفات الجديدة لشخصية {name}:", back_keyboard())
        return

    if action == "groups":
        groups = settings.get("groups", [])
        group_text = "\n".join(f"• {g}" for g in groups) or "لا توجد مجموعات."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ إضافة مجموعة", callback_data="add_group")],
            [InlineKeyboardButton("➖ حذف مجموعة", callback_data="remove_group")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data="menu")],
        ])
        await show_page(query, f"💬 المجموعات المسجلة:\n\n{group_text}\n\n(أرسل المعرّف أو رابط الدعوة هنا)", keyboard)
        return

    if action == "add_group":
        pending[user.id] = {"action": "add_group"}
        await show_page(query, "➕ أرسل معرّف المجموعة أو رابط الدعوة:", back_keyboard("groups"))
        return

    if action == "remove_group":
        groups = settings.get("groups", [])
        if not groups:
            await show_page(query, "ماكو مجموعات للحذف.", back_keyboard("groups"))
            return
        buttons = [[InlineKeyboardButton(f"🗑 {g}", callback_data=f"delgroup:{g}")] for g in groups[:40]]
        buttons.append([InlineKeyboardButton("⬅️ رجوع", callback_data="groups")])
        await show_page(query, "اختر المجموعة للحذف:", InlineKeyboardMarkup(buttons))
        return

    if action.startswith("delgroup:"):
        group_id = action.split(":", 1)[1]
        settings["groups"] = [g for g in settings.get("groups", []) if g != group_id]
        save_settings()
        await show_page(query, "تم حذف المجموعة.", back_keyboard("groups"))
        return

    if action == "stats":
        files = character_files()
        sessions = list(SESSIONS_DIR.glob("*.session"))
        text = (
            f"📊 الإحصائيات:\n"
            f"• الشخصيات: {len(files)}\n"
            f"• الحسابات: {len(sessions)}\n"
            f"• المجموعات: {len(settings['groups'])}\n"
            f"• السوالف والتفاعل: {'مفعلة ✅' if settings['running'] else 'متوقفة ⏸️'}"
        )
        await show_page(query, text, back_keyboard())
        return

    if action == "settings":
        await show_page(
            query,
            "⚙️ الإعدادات:",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🔑 تغيير مفتاح Gemini", callback_data="set_key")],
                [InlineKeyboardButton("⬅️ رجوع", callback_data="menu")],
            ]),
        )
        return

    if action == "set_key":
        pending[user.id] = {"action": "set_key"}
        await show_page(query, "أرسل مفتاح Gemini الجديد:", back_keyboard("settings"))
        return

    if action == "toggle":
        settings["running"] = not settings["running"]
        save_settings()
        await show_page(
            query,
            f"حالة الردود والسوالف أصبحت: {'مفعلة ✅' if settings['running'] else 'متوقفة ⏸️'}",
            main_keyboard(),
        )


async def admin_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not is_admin(user.id):
        return

    task = pending.get(user.id)
    if not task:
        return

    action = task["action"]
    text = (message.text or "").strip()

    if action == "new_character":
        path, character = create_character(text)
        pending.pop(user.id, None)
        await message.reply_text(f"✅ تم إنشاء شخصية {character['name']} بنجاح.")
        return

    if action == "edit_character":
        name = task["name"]
        path, character = find_character(name)
        if path and character:
            character["personality"] = text
            write_character(path, character)
        pending.pop(user.id, None)
        await message.reply_text(f"✅ تم تحديث صفات {name}.")
        return

    if action == "add_group":
        if text not in settings["groups"]:
            settings["groups"].append(text)
        save_settings()
        pending.pop(user.id, None)
        await message.reply_text(f"✅ تم تسجيل المجموعة أو الرابط {text}.")
        return

    if action == "set_key":
        settings["gemini_api_key"] = text
        save_settings()
        pending.pop(user.id, None)
        await message.reply_text("✅ تم حفظ مفتاح Gemini.")
        return


# ==================================================
# محاكاة سلوك قراءة ومراقبة الشاشة
# ==================================================

async def simulate_human_reading(client, entity):
    """محاكاة قراءة ومتابعة الرسائل كإنسان حقيقي"""
    try:
        await client(ReadHistoryRequest(peer=entity))
        await client(GetHistoryRequest(
            peer=entity, offset_id=0, offset_date=None, add_offset=0, limit=10, max_id=0, min_id=0, hash=0
        ))
        await asyncio.sleep(2)
    except Exception as e:
        log.error(f"خطأ أثناء قراءة الشاشة: {e}")


async def start_client_session(session_path):
    client = TelegramClient(str(session_path.with_suffix("")), API_ID, API_HASH)

    @client.on(events.NewMessage(incoming=True))
    async def handler(event):
        if not settings.get("running"):
            return
        if not event.is_group:
            return

        chat = await event.get_chat()
        chat_id_str = str(chat.id)
        registered_groups = settings.get("groups", [])

        if registered_groups:
            matched = False
            for g in registered_groups:
                if chat_id_str in g or str(event.chat_id) in g or g in str(chat.id):
                    matched = True
                    break
            if not matched:
                return

        me = await client.get_me()
        if event.sender_id == me.id:
            return

        # مشاهدة وقراءة الرسائل أولاً كإنسان
        await simulate_human_reading(client, chat)

        char_name = me.first_name or "شخصية"
        path, character = find_character(char_name)
        if not path or not character:
            path, character = create_character(char_name)

        message_text = (event.raw_text or "").strip()
        if not message_text:
            return

        sender = await event.get_sender()
        sender_name = getattr(sender, "first_name", "صديق")

        add_message(character, "user", f"{sender_name}: {message_text}")

        try:
            async with client.action(chat, 'typing'):
                await asyncio.sleep(2.5)  # محاكاة الكتابة العفوية
                reply_text = await generate_human_chat(character, message_text, sender_name)
        except Exception:
            log.exception("خطأ في توليد السالفة")
            return

        if reply_text:
            add_message(character, "assistant", reply_text)
            write_character(path, character)
            await event.reply(reply_text)

    await client.connect()
    if not await client.is_user_authorized():
        log.error(f"الجلسة {session_path.name} غير مفعّلة.")
        return None

    # مهمة المبادرة بالسوالف والمواضيع بالجروب
    async def spontaneous_chat_task():
        while True:
            await asyncio.sleep(240)  # كل 4 دقائق سالفة أو تعليق جديد
            if not settings.get("running"):
                continue

            registered_groups = settings.get("groups", [])
            if not registered_groups:
                continue

            me = await client.get_me()
            char_name = me.first_name or "شخصية"
            path, character = find_character(char_name)
            if not path or not character:
                continue

            for g_item in registered_groups:
                try:
                    entity = None
                    if "t.me/+" in g_item or "t.me/joinchat/" in g_item or "joinchat" in g_item:
                        hash_code = g_item.split("/")[-1].replace("+", "").split("?")[0]
                        try:
                            await client(ImportChatInviteRequest(hash_code))
                        except Exception:
                            pass
                        entity = await client.get_entity(g_item)
                    else:
                        target = int(g_item) if g_item.lstrip("-").isdigit() else g_item
                        entity = await client.get_entity(target)

                    if entity:
                        await simulate_human_reading(client, entity)
                        spontaneous_text = await generate_spontaneous_topic(character)
                        if spontaneous_text:
                            async with client.action(entity, 'typing'):
                                await asyncio.sleep(3)
                            await client.send_message(entity, spontaneous_text)
                            add_message(character, "assistant", spontaneous_text)
                            write_character(path, character)
                            log.info("أرسل الحساب سالفة بشرية عفوية بالجروب.")
                            break
                except Exception as e:
                    log.error(f"خطأ في السوالف التلقائية بالجروب {g_item}: {e}")

    client.loop.create_task(spontaneous_chat_task())
    log.info(f"تم تشغيل السوالف البشرية بنجاح: {session_path.name}")
    return client


# ==================================================
# التشغيل الرئيسي
# ==================================================

async def main():
    if not BOT_TOKEN or not API_ID or not API_HASH:
        raise RuntimeError("تأكد من ضبط متغيرات البيئة (BOT_TOKEN, API_ID, API_HASH).")

    save_settings()

    bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("menu", start))
    bot_app.add_handler(CallbackQueryHandler(callback_handler))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, admin_text_handler))

    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=False)
    log.info("بدأ تشغيل بوت الإدارة.")

    session_files = list(SESSIONS_DIR.glob("*.session"))
    clients = []
    for sf in session_files:
        c = await start_client_session(sf)
        if c:
            clients.append(c)

    log.info("النظام يعمل بكامل طاقته بالسوالف والتفاعل البشري العراقي الطبيعي.")

    tasks = [c.run_until_disconnected() for c in clients]
    if tasks:
        await asyncio.gather(*tasks)
    else:
        while True:
            await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
