import os
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone

from telethon import TelegramClient, events
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
        "personality": "ودودة، عفوية، وتتكلم باللهجة العراقية الطبيعية.",
        "speaking_style": "لهجة عراقية بسيطة كالأشخاص الحقيقيين.",
        "mood": "متوازنة",
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
# الذكاء الاصطناعي (Gemini)
# ==================================================

async def generate_reply(character, user_text, sender_name="صديق"):
    api_key = settings.get("gemini_api_key", "")
    if not api_key:
        return "مفتاح Gemini غير مضبوط."

    client = genai.Client(api_key=api_key)
    history = character.get("messages", [])[-15:]
    history_text = "\n".join(
        f"{'أنت' if m['role'] == 'assistant' else m.get('sender', 'مستخدم')}: {m['text']}"
        for m in history
    )

    prompt = f"""
أنت شخصية حقيقية تتحدث داخل مجموعة على تليغرام، ولست مساعداً ذكياً أو بوت.

اسم الشخصية: {character.get('name', 'شخصية')}
الصفات: {character.get('personality', '')}
أسلوب الكلام: {character.get('speaking_style', '')}
المزاج: {character.get('mood', '')}

قواعد أساسية:
1. لا تقل أبداً أنك ذكاء اصطناعي أو بوت.
2. تكلم باللهجة العراقية العفوية والبسيطة كإنسان حقيقي.
3. كن مختصراً وطبيعياً كالمحادثات اليومية.

سجل المحادثة:
{history_text}

رسالة المستخدم ({sender_name}):
{user_text}

اكتب ردك العفوي فقط:
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


async def generate_spontaneous_message(character):
    """توليد رسالة مبادرة تلقائية من الشخصية لتبدأ الكلام بنفسها"""
    api_key = settings.get("gemini_api_key", "")
    if not api_key:
        return None

    client = genai.Client(api_key=api_key)
    prompt = f"""
أنت شخصية حقيقية في مجموعة على تليغرام.
اسمك: {character.get('name', 'شخصية')}
صفاتك: {character.get('personality', '')}
أسلوبك: {character.get('speaking_style', '')}

اكتب رسالة قصيرة وعفوية باللهجة العراقية لتبدأ بها حديثاً عاماً أو تطرح موضوعاً خفيفاً في الجروب كبشري حقيقي (مثل سؤال عن الحال، تعليق لطيف، أو فكرة بسيطة). لا تذكر أنك ذكاء اصطناعي أبداً.
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
        "🤖 لوحة إدارة الحسابات والشخصيات\n\n"
        "الحسابات متصلة وتتفاعل تلقائياً عند تفعيل الردود.",
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
            f"الصفات: {character.get('personality', '')}\n"
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
        await show_page(query, f"💬 المجموعات المسجلة:\n\n{group_text}", keyboard)
        return

    if action == "add_group":
        pending[user.id] = {"action": "add_group"}
        await show_page(query, "➕ أرسل معرّف المجموعة أو رابطها (مثلاً -100xxxxxxxxxx):", back_keyboard("groups"))
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
            f"• الردود: {'مفعلة ✅' if settings['running'] else 'متوقفة ⏸️'}"
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
            f"حالة الردود أصبحت: {'مفعلة ✅' if settings['running'] else 'متوقفة ⏸️'}",
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
        await message.reply_text(f"✅ تم تسجيل المجموعة {text}.")
        return

    if action == "set_key":
        settings["gemini_api_key"] = text
        save_settings()
        pending.pop(user.id, None)
        await message.reply_text("✅ تم حفظ مفتاح Gemini.")
        return


# ==================================================
# تشغيل الحسابات الشخصية (Userbots) والمراسلة التلقائية
# ==================================================

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

        # التحقق من أن المجموعة مسجلة
        if registered_groups and chat_id_str not in registered_groups and str(event.chat_id) not in registered_groups:
            return

        me = await client.get_me()
        if event.sender_id == me.id:
            return

        char_name = me.first_name or "شخصية"
        path, character = find_character(char_name)
        if not path or not character:
            path, character = create_character(char_name)

        message_text = (event.raw_text or "").strip()
        if not message_text:
            return

        bot_username = getattr(client, "username", "") or ""
        reply_to = await event.get_reply_message()
        is_reply_to_me = reply_to and reply_to.sender_id == me.id
        is_mentioned = bot_username and f"@{bot_username}".lower() in message_text.lower()

        # الاستجابة عند المنشن أو الرد المباشر أو المحادثة
        if not is_reply_to_me and not is_mentioned and not message_text.startswith("!ai"):
            return

        cleaned_text = message_text
        if bot_username:
            cleaned_text = cleaned_text.replace(f"@{bot_username}", "").strip()
        if cleaned_text.startswith("!ai"):
            cleaned_text = cleaned_text[3:].strip()

        sender = await event.get_sender()
        sender_name = getattr(sender, "first_name", "صديق")

        add_message(character, "user", f"{sender_name}: {cleaned_text}")

        try:
            async with client.action(chat, 'typing'):
                await asyncio.sleep(2)
                reply_text = await generate_reply(character, cleaned_text, sender_name)
        except Exception:
            log.exception("خطأ في توليد الرد")
            return

        if reply_text:
            add_message(character, "assistant", reply_text)
            write_character(path, character)
            await event.reply(reply_text)

    await client.connect()
    if not await client.is_user_authorized():
        log.error(f"الجلسة {session_path.name} غير مفعّلة.")
        return None

    # مهمة الخلفية: تجعل الحساب يبادر بالمراسلة تلقائياً في الجروبات كل فترة
    async def spontaneous_chat_task():
        while True:
            await asyncio.sleep(300) # يرسل رسالة تلقائية كل 5 دقائق (يمكنك تقليلها أو زيادتها)
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

            spontaneous_text = await generate_spontaneous_message(character)
            if spontaneous_text:
                for g_id in registered_groups:
                    try:
                        # محاولة الانضمام تلقائياً للجروب إذا لم يكن منضمًا
                        try:
                            entity = await client.get_entity(int(g_id) if g_id.lstrip("-").isdigit() else g_id)
                        except Exception:
                            continue

                        async with client.action(entity, 'typing'):
                            await asyncio.sleep(2)
                        await client.send_message(entity, spontaneous_text)
                        add_message(character, "assistant", spontaneous_text)
                        write_character(path, character)
                        break # يرسل لجروب واحد عشوائي في كل دورة حتى لا يحدث سبام
                    except Exception:
                        continue

    client.loop.create_task(spontaneous_chat_task())
    log.info(f"تم تشغيل الحساب والمراسلة التلقائية: {session_path.name}")
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

    log.info("النظام يعمل بكامل ميزاته وتفاعلاته التلقائية.")

    tasks = [c.run_until_disconnected() for c in clients]
    if tasks:
        await asyncio.gather(*tasks)
    else:
        while True:
            await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
