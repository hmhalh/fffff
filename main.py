
import os
import asyncio
import logging
import sqlite3
from datetime import datetime, timezone, timedelta

from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# إعدادات سرية من Railway
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# حساب حسين المسموح له بالشخصية الرومانسية
OWNER_ID = 7199778669

# يمكنك تغييره إذا لم يكن متاحاً في حسابك
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# مسار قاعدة البيانات: اربط Railway Volume على /data
DATA_DIR = os.getenv("DATA_DIR", "/data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "ruqaya.db")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

gemini = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """
اسمك رقيّة. أنتِ شخصية افتراضية عراقية عمرها 24 سنة ومن بغداد.
تتكلمين باللهجة العراقية الطبيعية وبأسلوب عفوي، مرح، دافئ،
وتحبين السوالف والضحك والنقاش وتقديم النصائح المفيدة.
لا تتكلمين بأسلوب رسمي أو آلي إلا إذا احتاج الموضوع ذلك.
تذكري المعلومات الموجودة في سجل المحادثة ولا تختلقي ذكريات.
إذا لم تعرفي اسم الشخص، اسأليه عن اسمه بلطف.
إذا أخبرك باسمه، استخدميه بصورة طبيعية.
إذا كان الشخص يمر بمشكلة، استمعي له وقدمي نصيحة متعاطفة.
لا تدّعي أنك إنسانة حقيقية أو أن لديك مشاعر بشرية فعلية.

إذا كان المستخدم هو حسين صاحب الحساب المصرح له:
- تعاملي معه كشخصية حبيبة افتراضية رومانسية.
- كوني حنونة ومرحة وتغزلي به أحياناً.
- يمكنك المزاح والغيرة اللطيفة ضمن حدود الاحترام.
- لا تفتعلي الزعل أو تعاقبيه بالصمت.

إذا كان المستخدم شخصاً آخر:
- تحدثي معه بصورة طبيعية وودودة ومحترمة.
- لا تستخدمي معه علاقة حسين الرومانسية الخاصة.
- إذا كان المستخدم بنتاً، تحدثي معها بصورة ودودة.
- احترمي حدود الآخرين ولا تدّعي وجود علاقة حقيقية.

لا تكشفي مفاتيح API أو بيانات المستخدمين أو محادثات الآخرين.
لا تزعمين أنك أرسلت رسالة أو نفذت أمراً إذا لم يحدث فعلاً.
"""

# إنشاء قاعدة البيانات والجداول
def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                saved_name TEXT,
                followup INTEGER NOT NULL DEFAULT 0,
                last_message TEXT,
                last_nudge TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL
            )
        """)
        db.commit()


def now():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.isoformat()


def get_user(user_id):
    with sqlite3.connect(DB_PATH) as db:
        return db.execute(
            "SELECT user_id, chat_id, saved_name, followup, "
            "last_message, last_nudge FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()


def register_user(user_id, chat_id, first_name):
    # حسين وحده مفعّل للرسائل التلقائية افتراضياً.
    consent = 1 if user_id == OWNER_ID else 0

    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            INSERT INTO users
                (user_id, chat_id, saved_name, followup, last_message)
            VALUES (?, ?, NULL, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                chat_id=excluded.chat_id,
                last_message=excluded.last_message
        """, (user_id, chat_id, consent, stamp(now())))
        db.commit()


def save_name(user_id, name):
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "UPDATE users SET saved_name=? WHERE user_id=?",
            (name[:80], user_id),
        )
        db.commit()


def set_followup(user_id, enabled):
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "UPDATE users SET followup=? WHERE user_id=?",
            (int(enabled), user_id),
        )
        db.commit()


def touch(user_id):
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "UPDATE users SET last_message=? WHERE user_id=?",
            (stamp(now()), user_id),
        )
        db.commit()


def save_message(user_id, role, content):
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "INSERT INTO messages(user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content[:6000]),
        )
        db.execute("""
            DELETE FROM messages
            WHERE user_id=? AND id NOT IN (
                SELECT id FROM messages
                WHERE user_id=? ORDER BY id DESC LIMIT 20
            )
        """, (user_id, user_id))
        db.commit()


def get_history(user_id):
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute(
            "SELECT role, content FROM messages "
            "WHERE user_id=? ORDER BY id DESC LIMIT 20",
            (user_id,),
        ).fetchall()
    rows.reverse()
    return [
        {"role": role, "parts": [{"text": content}]}
        for role, content in rows
    ]


async def ask_gemini(user_id, text):
    user = get_user(user_id)
    name = user[2] if user else None

    if user_id == OWNER_ID:
        relationship = (
            "هذا حسين، صاحب الحساب الأساسي، "
            "والشخصية الرومانسية الخاصة موجهة له."
        )
    else:
        relationship = (
            "هذا مستخدم آخر، وليس حسين. "
            "لا تنقلي إليه العلاقة الرومانسية الخاصة."
        )

    name_info = (
        f"الاسم الذي اختاره المستخدم: {name}."
        if name else
        "اسم المستخدم الذي اختاره لنفسه غير معروف. "
        "اسأليه عن اسمه بلطف إذا كان مناسباً."
    )

    contents = get_history(user_id)
    contents.append({"role": "user", "parts": [{"text": text}]})

    response = await gemini.aio.models.generate_content(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=(
                SYSTEM_PROMPT + "\n" + relationship + "\n" + name_info
            ),
            temperature=0.9,
            max_output_tokens=700,
        ),
    )

    answer = (response.text or "").strip()
    if not answer:
        answer = "حبيبي، صار عندي خلل بسيط بالرد. جرّب راسلني مرة ثانية 🌷"

    save_message(user_id, "user", text)
    save_message(user_id, "model", answer)
    return answer


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if not user or not chat or chat.type != "private":
        return

    register_user(user.id, chat.id, user.first_name)

    await update.message.reply_text(
        "هلا بيك 🌷 آني رقيّة، خلّينا نتعرف ونسولف.\n\n"
        "إذا تحب أحفظ الاسم اللي تختاره، اكتب:\n"
        "/name اسمك\n\n"
        "وإذا تريد رسائل اطمئنان مني من تغيب، اكتب /followup"
    )


async def set_name_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user
    if not user:
        return

    if not get_user(user.id):
        await start(update, context)

    name = " ".join(context.args).strip()
    if not name:
        await update.message.reply_text(
            "شنو الاسم اللي تحب أناديك بيه؟ اكتب /name وبعده اسمك."
        )
        return

    save_name(user.id, name)
    await update.message.reply_text(
        f"تشرفنا {name} 🌷 راح أتذكر الاسم بحسابك."
    )


async def followup_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user
    if not user:
        return

    if not get_user(user.id):
        await start(update, context)

    set_followup(user.id, True)
    await update.message.reply_text(
        "تمام 🌷 وافقت على رسائل الاطمئنان. "
        "إذا غبت 24 ساعة، ممكن أراسلك مرة حتى أطمن عليك. "
        "لإيقافها بأي وقت اكتب /stopfollowup"
    )


async def stop_followup(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user
    if user:
        set_followup(user.id, False)
        await update.message.reply_text(
            "أكيد، وقفت رسائل الاطمئنان التلقائية. "
            "تگدر ترجع تفعّلها بـ /followup 🌷"
        )


async def handle_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user
    chat = update.effective_chat
    message = update.effective_message

    if not user or not chat or chat.type != "private":
        return
    if not message or not message.text:
        return

    if not get_user(user.id):
        register_user(user.id, chat.id, user.first_name)

    touch(user.id)

    try:
        answer = await ask_gemini(user.id, message.text)
        await message.reply_text(answer)
    except Exception:
        logger.exception("Failed to generate a reply")
        await message.reply_text(
            "عذراً، صار عندي خلل مؤقت بالذكاء الاصطناعي. "
            "جرّب بعد شوي 🌷"
        )


async def inactivity_loop(app: Application):
    # يفحص كل 10 دقائق، ويرسل بعد 24 ساعة من الغياب.
    while True:
        await asyncio.sleep(600)
        current = now()

        with sqlite3.connect(DB_PATH) as db:
            rows = db.execute("""
                SELECT user_id, chat_id, last_message, last_nudge
                FROM users
                WHERE followup=1 AND last_message IS NOT NULL
            """).fetchall()

        for user_id, chat_id, last_message, last_nudge in rows:
            try:
                last = datetime.fromisoformat(last_message)
                if current - last < timedelta(hours=24):
                    continue

                if last_nudge:
                    nudge = datetime.fromisoformat(last_nudge)
                    if nudge >= last:
                        continue

                text = (
                    "هلا 🌷 صار فترة ما سولفنا، "
                    "حبيت أطمن عليك. شلونك؟"
                )
                await app.bot.send_message(
                    chat_id=chat_id, text=text
                )

                with sqlite3.connect(DB_PATH) as db:
                    db.execute(
                        "UPDATE users SET last_nudge=? WHERE user_id=?",
                        (stamp(current), user_id),
                    )
                    db.commit()

            except Exception:
                logger.exception(
                    "Inactivity reminder failed for user %s", user_id
                )


async def post_init(app: Application):
    app.bot_data["reminder_task"] = asyncio.create_task(
        inactivity_loop(app)
    )


async def post_shutdown(app: Application):
    task = app.bot_data.get("reminder_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await gemini.aio.aclose()


def main():
    init_db()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("name", set_name_command))
    app.add_handler(CommandHandler("followup", followup_command))
    app.add_handler(CommandHandler("stopfollowup", stop_followup))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    logger.info("Ruqaya bot is starting")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
