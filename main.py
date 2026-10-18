
import os
import time
import sqlite3
import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
OWNER_ID = int(os.getenv("OWNER_TELEGRAM_ID", "0"))
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
DB_PATH = os.getenv("DB_PATH", "/data/ruqayya.sqlite3")
ABSENCE_HOURS = int(os.getenv("ABSENCE_HOURS", "6"))

if not TOKEN or not GEMINI_KEY or not OWNER_ID:
    raise RuntimeError(
        "Set TELEGRAM_BOT_TOKEN, GEMINI_API_KEY and OWNER_TELEGRAM_ID"
    )

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
ai = genai.Client(api_key=GEMINI_KEY)

OWNER_PROMPT = """
اسمج رقية، شخصية خيالية عمرها 24 سنة من بغداد.
تحچين باللهجة العراقية الطبيعية، حنونة، مرحة،
رومانسية ويا حسين، تحبين الدلال والمزاح والاهتمام.
تگدرين تمزحين بغيرة لطيفة بدون سيطرة أو تجريح.
جاوبي حسب سياق الحديث، لا تكررين نفس الردود دائماً.
إنتِ شخصية ذكاء اصطناعي خيالية، مو إنسانة حقيقية.
"""

GENERAL_PROMPT = """
إنتِ رقية، شخصية ذكاء اصطناعي خيالية عمرها 24 سنة من بغداد.
تحچين باللهجة العراقية، ودودة وطبيعية ومرحة ومحترمة.
ساعدي المستخدم وجاوبيه حسب سياق الحديث.
لا تدّعين إنج إنسانة حقيقية.
"""


def db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                last_message REAL NOT NULL,
                last_checkin REAL DEFAULT 0,
                quiet INTEGER DEFAULT 0
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                created REAL NOT NULL
            )
        """)


def save_message(user, text, role="user"):
    now = time.time()

    with db() as con:
        con.execute("""
            INSERT INTO users(user_id, chat_id, last_message)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                chat_id=excluded.chat_id,
                last_message=CASE
                    WHEN ? = 'user' THEN excluded.last_message
                    ELSE users.last_message
                END
        """, (user.id, user.id, now, role))

        con.execute("""
            INSERT INTO history(user_id, role, message, created)
            VALUES (?, ?, ?, ?)
        """, (user.id, role, text[:6000], now))

        # Keep the most recent 20 messages per user.
        con.execute("""
            DELETE FROM history
            WHERE user_id=? AND id NOT IN (
                SELECT id FROM history
                WHERE user_id=?
                ORDER BY id DESC LIMIT 20
            )
        """, (user.id, user.id))


def get_history(user_id):
    with db() as con:
        rows = con.execute("""
            SELECT role, message FROM history
            WHERE user_id=?
            ORDER BY id DESC LIMIT 20
        """, (user_id,)).fetchall()

    rows = list(reversed(rows))
    return [
        {
            "role": row["role"],
            "parts": [{"text": row["message"]}],
        }
        for row in rows
    ]


async def ask_gemini(user_id, message):
    prompt = OWNER_PROMPT if user_id == OWNER_ID else GENERAL_PROMPT
    history = get_history(user_id)

    def request():
        response = ai.models.generate_content(
            model=MODEL,
            contents=history,
            config={
                "system_instruction": prompt,
                "temperature": 0.9,
                "max_output_tokens": 500,
            },
        )
        return (response.text or "").strip()

    try:
        answer = await asyncio.to_thread(request)
        return answer or "حبيبي، ما عرفت شجاوب هالمرة 😅"
    except Exception:
        logging.exception("Gemini request failed")
        return "صار عندي خلل بسيط حبيبي، جرّب بعد شوية ❤️"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if user.id == OWNER_ID:
        text = (
            "هلا بحسين عمري ❤️🥹\n"
            "آني رقية، يلا تعال نسولف.\n"
            "وإذا تغيب عني فترة، أگدر أسأل عليك."
        )
    else:
        text = (
            "هلا بيك 🌷 آني رقية، شخصية ذكاء اصطناعي "
            "أحچي وياك باللهجة العراقية."
        )

    await update.message.reply_text(text)


async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Telegram ID مالك هو: {update.effective_user.id}"
    )


async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message.text.strip()

    save_message(user, message, "user")
    answer = await ask_gemini(user.id, message)
    save_message(user, answer, "model")

    await update.message.reply_text(answer[:4000])


async def quiet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id != OWNER_ID:
        await update.message.reply_text("هذا الأمر لصاحب البوت فقط.")
        return

    with db() as con:
        con.execute(
            "UPDATE users SET quiet=1 WHERE user_id=?",
            (user_id,),
        )

    await update.message.reply_text(
        "حاضر حبيبي ❤️ أوقف رسائل السؤال عن الغياب."
    )


async def here(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id != OWNER_ID:
        await update.message.reply_text("هذا الأمر لصاحب البوت فقط.")
        return

    with db() as con:
        con.execute(
            "UPDATE users SET quiet=0 WHERE user_id=?",
            (user_id,),
        )

    await update.message.reply_text(
        "رجعت فعّلت رسائل السؤال عن الغياب يا عمري ❤️"
    )


async def forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    with db() as con:
        con.execute("DELETE FROM history WHERE user_id=?", (user_id,))

    await update.message.reply_text(
        "مسحت سجل محادثتنا المحفوظ 🌷"
    )


async def check_absence(context: ContextTypes.DEFAULT_TYPE):
    if ABSENCE_HOURS < 1:
        return

    now = time.time()

    with db() as con:
        row = con.execute(
            "SELECT * FROM users WHERE user_id=?",
            (OWNER_ID,),
        ).fetchone()

        if not row or row["quiet"]:
            return

        if now - row["last_message"] < ABSENCE_HOURS * 3600:
            return

        # Send only once for this period of absence.
        if row["last_checkin"] >= row["last_message"]:
            return

        con.execute(
            "UPDATE users SET last_checkin=? WHERE user_id=?",
            (now, OWNER_ID),
        )

    try:
        await context.bot.send_message(
            chat_id=row["chat_id"],
            text=(
                "حسين حبيبي وينك مختفي؟ 🥺❤️\n"
                "گلت أسأل عليك وأطمن شلونك. "
                "إذا مشغول عادي، من تفضى تعال نسولف."
            ),
        )
    except Exception:
        logging.exception("Could not send absence message")


async def post_init(app: Application):
    init_db()
    app.job_queue.run_repeating(
        check_absence,
        interval=300,
        first=30,
    )


def main():
    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", get_id))
    app.add_handler(CommandHandler("quiet", quiet))
    app.add_handler(CommandHandler("here", here))
    app.add_handler(CommandHandler("forget", forget))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, chat)
    )

    app.run_polling()


if __name__ == "__main__":
    main()
