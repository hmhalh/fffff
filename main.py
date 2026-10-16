import asyncio
import logging
import os
import random
import sqlite3
from pathlib import Path

import requests
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# ==================================================
# الإعدادات - Railway Variables
# ==================================================

BOT_TOKEN = os.getenv("7838301513:AAEq39qhnjhmWwBwPh-c2_VIRTiJV06Qvn8", "")
GEMINI_API_KEY = os.getenv("AQ.Ab8RN6K2LYTWDkLsT-Ll-pRmutAU7DaE-nNYbR9pV6q92LsKsg", "")

# رقم حساب المشرف. إذا أكثر من مشرف، افصل الأرقام بفاصلة.
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("7199778669", "").split(",")
    if x.strip().isdigit()
}

# مسار التخزين الدائم في Railway Volume
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "arena.db"

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)

MAX_REPLY_CHARS = 900
HISTORY_LIMIT = 12

if not BOT_TOKEN:
    raise SystemExit("❌ أضف BOT_TOKEN في Railway Variables")

if not GEMINI_API_KEY:
    raise SystemExit("❌ أضف GEMINI_API_KEY في Railway Variables")

if not ADMIN_IDS:
    raise SystemExit("❌ أضف ADMIN_IDS في Railway Variables")


# ==================================================
# تسجيل الأخطاء
# ==================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger("telegram-bot")

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ==================================================
# SQLite - قاعدة البيانات والذاكرة
# ==================================================

def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS characters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                personality TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                speaker TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            )
        """)

        con.execute(
            "INSERT OR IGNORE INTO settings VALUES(?, ?)",
            ("topic", "سوالف عامة باللهجة العراقية"),
        )

        con.execute(
            "INSERT OR IGNORE INTO memories VALUES(?, ?)",
            ("shared", ""),
        )

        characters = [
            ("سارة", "مرحة واجتماعية وتحب المزاح الخفيف."),
            ("علي", "هادئ وذكي ويحب التحليل والنقاش."),
            ("نور", "فضولية ولطيفة وتسأل أسئلة ممتعة."),
            ("كرار", "عفوي ويحب النكت العراقية."),
        ]

        for name, personality in characters:
            con.execute(
                """
                INSERT OR IGNORE INTO characters(name, personality)
                VALUES(?, ?)
                """,
                (name, personality),
            )


def get_setting(key, default=""):
    with db() as con:
        row = con.execute(
            "SELECT value FROM settings WHERE key=?",
            (key,),
        ).fetchone()

    return row["value"] if row else default


def set_setting(key, value):
    with db() as con:
        con.execute(
            """
            INSERT INTO settings(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, str(value)),
        )


def get_characters(enabled_only=False):
    query = "SELECT * FROM characters"

    if enabled_only:
        query += " WHERE enabled=1"

    query += " ORDER BY id"

    with db() as con:
        return [
            dict(row)
            for row in con.execute(query).fetchall()
        ]


def toggle_character(char_id):
    with db() as con:
        con.execute(
            """
            UPDATE characters
            SET enabled = CASE enabled WHEN 1 THEN 0 ELSE 1 END
            WHERE id=?
            """,
            (char_id,),
        )


def enable_all(value):
    with db() as con:
        con.execute(
            "UPDATE characters SET enabled=?",
            (int(value),),
        )


def save_message(speaker, text):
    with db() as con:
        con.execute(
            "INSERT INTO messages(speaker, text) VALUES(?, ?)",
            (speaker, text),
        )

        con.execute("""
            DELETE FROM messages
            WHERE id NOT IN (
                SELECT id FROM messages
                ORDER BY id DESC LIMIT 500
            )
        """)


def get_history(limit=HISTORY_LIMIT):
    with db() as con:
        rows = con.execute(
            """
            SELECT speaker, text FROM messages
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [dict(row) for row in reversed(rows)]


def get_memory():
    with db() as con:
        row = con.execute(
            "SELECT value FROM memories WHERE key='shared'"
        ).fetchone()

    return row["value"] if row else ""


def save_memory(text):
    memory = (get_memory() + "\n" + text).strip()
    memory = memory[-5000:]

    with db() as con:
        con.execute(
            """
            INSERT INTO memories(key, value) VALUES('shared', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (memory,),
        )


# ==================================================
# Gemini
# ==================================================

def gemini_generate(prompt):
    payload = {
        "system_instruction": {
            "parts": [{
                "text": (
                    "تحدث باللهجة العراقية الطبيعية. "
                    "أنت تؤدي شخصية خيالية. "
                    "لا تدّعِ أنك إنسان حقيقي. "
                    "اجعل الرد مختصراً وطبيعياً."
                )
            }]
        },
        "contents": [{
            "role": "user",
            "parts": [{"text": prompt}],
        }],
        "generationConfig": {
            "temperature": 0.9,
            "maxOutputTokens": 300,
        },
    }

    try:
        response = requests.post(
            GEMINI_URL,
            params={"key": GEMINI_API_KEY},
            json=payload,
            timeout=35,
        )

        if not response.ok:
            log.error(
                "Gemini HTTP %s: %s",
                response.status_code,
                response.text[:1000],
            )
            return None, f"خطأ Gemini HTTP {response.status_code}"

        data = response.json()
        candidates = data.get("candidates", [])

        if not candidates:
            log.error("Gemini returned no candidates")
            return None, "Gemini لم يرجع جواباً."

        parts = candidates[0].get(
            "content", {}
        ).get("parts", [])

        answer = "".join(
            part.get("text", "")
            for part in parts
            if isinstance(part.get("text"), str)
        ).strip()

        if not answer:
            return None, "الرد فارغ."

        return answer[:MAX_REPLY_CHARS], None

    except requests.RequestException as exc:
        log.exception("Gemini connection failed")
        return None, f"فشل الاتصال: {type(exc).__name__}"

    except (ValueError, TypeError, KeyError):
        log.exception("Invalid Gemini response")
        return None, "تعذر تحليل استجابة Gemini."


async def generate_reply(character, user_text):
    history = "\n".join(
        f"{item['speaker']}: {item['text']}"
        for item in get_history()
    )

    prompt = f"""
اسم الشخصية: {character['name']}
طبع الشخصية: {character['personality']}

موضوع الحوار:
{get_setting('topic')}

الذاكرة السابقة:
{get_memory() or 'لا توجد ذاكرة محفوظة'}

آخر الرسائل:
{history or 'لا توجد رسائل سابقة'}

رسالة المستخدم:
{user_text}

اكتب رداً واحداً باللهجة العراقية من جملة إلى ثلاث جمل.
لا تكتب اسم الشخصية في بداية الرد.
لا تكرر الردود السابقة حرفياً.
"""

    answer, error = await asyncio.to_thread(
        gemini_generate,
        prompt,
    )

    return answer, error


# ==================================================
# لوحة التحكم العربية
# ==================================================

def is_admin(user_id):
    return user_id in ADMIN_IDS


def panel_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="الحالة 📊",
                    callback_data="status",
                ),
                InlineKeyboardButton(
                    text="الشخصيات 🎭",
                    callback_data="characters",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="اختبار Gemini 🧪",
                    callback_data="test",
                ),
                InlineKeyboardButton(
                    text="طريقة تغيير الموضوع 📝",
                    callback_data="topic",
                ),
            ],
        ]
    )


async def safe_edit(message, text, markup=None):
    try:
        await message.edit_text(
            text,
            reply_markup=markup,
        )

    except Exception as exc:
        if "message is not modified" in str(exc).lower():
            return

        log.exception("Failed to edit panel message")


async def show_characters(callback):
    rows = []

    for char in get_characters():
        icon = "🟢" if char["enabled"] else "⚪"

        rows.append([
            InlineKeyboardButton(
                text=f"{icon} {char['name']}",
                callback_data=f"char:{char['id']}",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="تفعيل الكل",
            callback_data="all:on",
        ),
        InlineKeyboardButton(
            text="تعطيل الكل",
            callback_data="all:off",
        ),
    ])

    rows.append([
        InlineKeyboardButton(
            text="رجوع 🔙",
            callback_data="home",
        )
    ])

    await safe_edit(
        callback.message,
        "🎭 إدارة الشخصيات\nاضغط على الشخصية لتفعيلها أو تعطيلها.",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@dp.message(Command("start", "panel"))
async def panel_command(message: Message):
    if not message.from_user:
        return

    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "أهلاً بك في لوحة التحكم 🎛️",
        reply_markup=panel_keyboard(),
    )


@dp.message(Command("topic"))
async def topic_command(message: Message):
    if not message.from_user:
        return

    if not is_admin(message.from_user.id):
        return

    topic = (message.text or "").partition(" ")[2].strip()

    if not topic:
        await message.answer(
            "اكتب الموضوع هكذا:\n/topic نقاش عن الأنمي"
        )
        return

    set_setting("topic", topic[:300])

    await message.answer(
        f"✅ تم تغيير موضوع الحوار إلى:\n{topic[:300]}"
    )


@dp.message(Command("speak"))
async def speak_command(message: Message):
    if not message.from_user:
        return

    if not is_admin(message.from_user.id):
        return

    characters = get_characters(enabled_only=True)

    if not characters:
        await message.answer("❌ لا توجد شخصيات مفعّلة.")
        return

    user_text = (message.text or "").partition(" ")[2].strip()

    if not user_text:
        user_text = f"ابدأ حديثاً عن: {get_setting('topic')}"

    character = random.choice(characters)

    status = await message.answer(
        f"⏳ الشخصية {character['name']} تكتب الرد..."
    )

    answer, error = await generate_reply(
        character,
        user_text,
    )

    if error or not answer:
        await safe_edit(
            status,
            f"❌ فشل توليد الرد:\n{error or 'رد فارغ'}",
        )
        return

    try:
        await bot.send_message(
            chat_id=message.chat.id,
            text=f"🎭 {character['name']}:\n{answer}",
        )

        save_message(character["name"], answer)
        save_memory(f"{character['name']}: {answer}")

        try:
            await status.delete()
        except Exception:
            pass

    except Exception as exc:
        log.exception("Telegram send failed")

        await safe_edit(
            status,
            f"❌ فشل الإرسال:\n{type(exc).__name__}: {str(exc)[:250]}",
        )


@dp.message(Command("remember"))
async def remember_command(message: Message):
    if not message.from_user:
        return

    if not is_admin(message.from_user.id):
        return

    text = (message.text or "").partition(" ")[2].strip()

    if not text:
        await message.answer(
            "استخدم الأمر هكذا:\n/remember معلومة للحفظ"
        )
        return

    save_memory(text[:1000])
    await message.answer("✅ تم حفظ المعلومة في SQLite.")


@dp.message(Command("memory"))
async def memory_command(message: Message):
    if not message.from_user:
        return

    if not is_admin(message.from_user.id):
        return

    memory = get_memory()

    await message.answer(
        "🧠 الذاكرة:\n" + memory[-3500:]
        if memory
        else "الذاكرة فارغة."
    )


@dp.callback_query()
async def callback_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "هذه اللوحة للمشرف فقط.",
            show_alert=True,
        )
        return

    data = callback.data or ""

    try:
        if data == "home":
            await safe_edit(
                callback.message,
                "لوحة التحكم 🎛️",
                panel_keyboard(),
            )

        elif data == "status":
            chars = get_characters()
            enabled = sum(
                1 for char in chars if char["enabled"]
            )

            await safe_edit(
                callback.message,
                f"📊 حالة البوت\n"
                f"الشخصيات المفعّلة: {enabled}/{len(chars)}\n"
                f"قاعدة البيانات: {DB_PATH}\n"
                f"الموضوع: {get_setting('topic')}",
                panel_keyboard(),
            )

        elif data == "characters":
            await show_characters(callback)

        elif data.startswith("char:"):
            char_id = int(data.split(":", 1)[1])
            toggle_character(char_id)
            await show_characters(callback)

        elif data == "all:on":
            enable_all(True)
            await show_characters(callback)

        elif data == "all:off":
            enable_all(False)
            await show_characters(callback)

        elif data == "topic":
            await safe_edit(
                callback.message,
                "لتغيير الموضوع أرسل:\n/topic موضوع الحوار",
                panel_keyboard(),
            )

        elif data == "test":
            await callback.answer("جارٍ اختبار Gemini...")

            answer, error = await asyncio.to_thread(
                gemini_generate,
                "اكتب جملة عراقية قصيرة تقول فيها مرحباً.",
            )

            if error:
                text = f"❌ فشل الاختبار:\n{error}"
            else:
                text = f"✅ Gemini يعمل:\n{answer}"

            await safe_edit(
                callback.message,
                text,
                panel_keyboard(),
            )

        await callback.answer()

    except Exception as exc:
        log.exception("Callback error")

        try:
            await callback.answer(
                f"حدث خطأ: {type(exc).__name__}",
                show_alert=True,
            )
        except Exception:
            pass


# ==================================================
# تشغيل البوت
# ==================================================

async def main():
    init_db()

    log.info("Bot started")
    log.info("SQLite path: %s", DB_PATH)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())