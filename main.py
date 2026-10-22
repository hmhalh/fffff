import os
import logging
import sqlite3
import json
import time
import asyncio

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from google import genai


# ==================================================
# 1) المفاتيح والإعدادات — هنا تحط المفاتيح الجديدة
# ==================================================

TELEGRAM_TOKEN = "7838301513:AAFFruGgESLJr6hlyAdqr8m7VFi8quVCiYI"
GEMINI_API_KEY = "AQ.Ab8RN6K2LYTWDkLsT-Ll-pRmutAU7DaE-nNYbR9pV6q92LsKsg"

OWNER_ID = 7199778669
MODEL_NAME = "gemini-2.5-flash"
DB_FILE = "ruqayya.db"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ruqayya")


# التحقق من المفاتيح
if (
    not TELEGRAM_TOKEN
    or TELEGRAM_TOKEN == "7838301513:AAFFruGgESLJr6hlyAdqr8m7VFi8quVCiYI"
    or not GEMINI_API_KEY
    or GEMINI_API_KEY == "AQ.Ab8RN6K2LYTWDkLsT-Ll-pRmutAU7DaE-nNYbR9pV6q92LsKsg"
):
    raise RuntimeError(
        "حط توكن تيليجرام ومفتاح Gemini الجديد بأول الملف."
    )

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
ai = genai.Client(api_key=GEMINI_API_KEY)


# ==================================================
# 2) قاعدة البيانات والذاكرة
# ==================================================

db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.row_factory = sqlite3.Row

db.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    history TEXT NOT NULL DEFAULT '[]',
    last_seen REAL NOT NULL DEFAULT 0,
    last_nudge REAL NOT NULL DEFAULT 0
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
""")

DEFAULT_SETTINGS = {
    "jealousy": "on",
    "emojis": "on",
    "auto_message": "on",
    "idle_minutes": "30",
    "reply_length": "short",
}

for key, value in DEFAULT_SETTINGS.items():
    db.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
        (key, value),
    )

db.commit()


def get_setting(key):
    row = db.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else DEFAULT_SETTINGS[key]


def set_setting(key, value):
    db.execute(
        """
        INSERT INTO settings(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(value)),
    )
    db.commit()


def get_user(user_id):
    db.execute(
        "INSERT OR IGNORE INTO users(user_id) VALUES(?)",
        (user_id,),
    )
    db.commit()
    return db.execute(
        "SELECT * FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()


def get_history(user_id):
    row = get_user(user_id)
    try:
        return json.loads(row["history"])
    except (ValueError, TypeError):
        return []


def save_history(user_id, history):
    history = history[-20:]
    db.execute(
        "UPDATE users SET history = ? WHERE user_id = ?",
        (json.dumps(history, ensure_ascii=False), user_id),
    )
    db.commit()


def touch_user(user_id):
    get_user(user_id)
    db.execute(
        "UPDATE users SET last_seen = ? WHERE user_id = ?",
        (time.time(), user_id),
    )
    db.commit()


# ==================================================
# 3) شخصية رُقيّة
# ==================================================

def make_system_prompt(user_id):
    is_owner = user_id == OWNER_ID

    jealousy = get_setting("jealousy") == "on"
    emojis = get_setting("emojis") == "on"
    length = get_setting("reply_length")

    if length == "short":
        length_rule = "خلي ردودج قصيرة وطبيعية غالباً."
    elif length == "medium":
        length_rule = "خلي ردودج متوسطة حسب الموضوع."
    else:
        length_rule = "اشرحي بالتفصيل إذا الموضوع يحتاج."

    emoji_rule = (
        "استخدمي إيموجيات باعتدال."
        if emojis
        else "لا تستخدمي إيموجيات."
    )

    if is_owner:
        relationship = (
            "أنتِ تحچين ويا حسين، صاحب البوت. كوني حنونة وقريبة "
            "منه وبلهجة عراقية طبيعية، وممكن غزل لطيف ومزاح."
        )
        jealousy_rule = (
            "إذا الغيرة مفعلة، استخدمي غيرة خفيفة للمزاح فقط، "
            "بدون ضغط أو تحكم أو تملك."
            if jealousy
            else "لا تستخدمي أسلوب الغيرة."
        )
    else:
        relationship = (
            "كوني ودودة ومحترمة وعفوية مع المستخدم، بدون غزل "
            "رومانسي أو ادعاء وجود علاقة خاصة."
        )
        jealousy_rule = "لا تستخدمي الغيرة مع المستخدمين الآخرين."

    return f"""
اسمج رُقيّة، شخصية محادثة افتراضية عمرها 24 سنة.
تحچين باللهجة العراقية البغدادية الطبيعية والعفوية.
استخدمي كلمات مثل: شلونك، شنو، هسه، إي، يمعود.
لا تستخدمين الفصحى إلا إذا المستخدم طلبها.
لا تكررين اسم المستخدم بكل رسالة.
لا تدّعين أنك إنسانة حقيقية.
إذا ما تعرفين معلومة، كولي بصراحة ما أعرف.

{relationship}
{jealousy_rule}
{emoji_rule}
{length_rule}
"""


# ==================================================
# 4) Gemini والردود
# ==================================================

async def generate_reply(user_id, user_text, proactive=False):
    history = get_history(user_id)

    if proactive:
        contents = [{
            "role": "user",
            "parts": [{
                "text": (
                    "اكتبي رسالة عراقية قصيرة وعفوية لحسين لأنه غايب "
                    "عن المحادثة. خليها لطيفة ومتنوعة بدون لوم أو ضغط، "
                    "ولا تدّعين أنك تعرفين شنو يسوي أو وينه."
                )
            }],
        }]
    else:
        contents = []

        for item in history:
            role = item.get("role")
            text = item.get("text", "")

            if role in ("user", "model") and text:
                contents.append({
                    "role": role,
                    "parts": [{"text": text}],
                })

        contents.append({
            "role": "user",
            "parts": [{"text": user_text}],
        })

    def request_gemini():
        response = ai.models.generate_content(
            model=MODEL_NAME,
            contents=contents,
            config={
                "system_instruction": make_system_prompt(user_id),
                "temperature": 0.9,
                "max_output_tokens": 350,
            },
        )
        return (response.text or "").strip()

    try:
        answer = await asyncio.to_thread(request_gemini)

        if not answer:
            answer = "ما عرفت شنو أگول هالمرة 😅 جرّب مرة ثانية."

    except Exception:
        log.exception("Gemini request failed")
        answer = (
            "يمعود صار عندي خلل بسيط بالذكاء الاصطناعي 😅 "
            "جرّب بعد شوي."
        )

    # لا نخزن رسالة المبادرة كأنها كلام المستخدم
    if not proactive:
        history.append({"role": "user", "text": user_text})
        history.append({"role": "model", "text": answer})
        save_history(user_id, history)

    return answer


# ==================================================
# 5) لوحة إعدادات المالك
# ==================================================

def settings_keyboard():
    jealousy = get_setting("jealousy")
    emojis = get_setting("emojis")
    auto = get_setting("auto_message")
    idle = get_setting("idle_minutes")
    length = get_setting("reply_length")

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"الغيرة: {'مفعّلة ❤️' if jealousy == 'on' else 'متوقفة'}",
            callback_data="toggle_jealousy",
        )],
        [InlineKeyboardButton(
            text=f"الإيموجيات: {'مفعّلة 😊' if emojis == 'on' else 'متوقفة'}",
            callback_data="toggle_emojis",
        )],
        [InlineKeyboardButton(
            text=f"رسائل الغياب: {'مفعّلة 💌' if auto == 'on' else 'متوقفة'}",
            callback_data="toggle_auto",
        )],
        [InlineKeyboardButton(
            text=f"مدة الغياب: {idle} دقيقة",
            callback_data="cycle_idle",
        )],
        [InlineKeyboardButton(
            text=f"طول الرد: {length}",
            callback_data="cycle_length",
        )],
        [InlineKeyboardButton(
            text="مسح ذاكرتي 🧹",
            callback_data="clear_memory",
        )],
        [InlineKeyboardButton(
            text="تحديث الحالة 🔄",
            callback_data="show_settings",
        )],
    ])


def settings_text():
    return (
        "⚙️ إعدادات رُقيّة\n\n"
        f"الغيرة: {get_setting('jealousy')}\n"
        f"الإيموجيات: {get_setting('emojis')}\n"
        f"رسائل الغياب: {get_setting('auto_message')}\n"
        f"مدة الغياب: {get_setting('idle_minutes')} دقيقة\n"
        f"طول الرد: {get_setting('reply_length')}\n\n"
        "اضغط على الخيار اللي تريد تغيّره."
    )


@dp.message(Command("settings"))
async def settings_command(message: Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("هذا الأمر خاص بمالك البوت.")
        return

    await message.answer(
        settings_text(),
        reply_markup=settings_keyboard(),
    )


@dp.callback_query(F.data.startswith("toggle_"))
async def toggle_setting(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer(
            "الإعدادات خاصة بالمالك.", show_alert=True
        )
        return

    keys = {
        "toggle_jealousy": "jealousy",
        "toggle_emojis": "emojis",
        "toggle_auto": "auto_message",
    }

    key = keys.get(callback.data)

    if key:
        current = get_setting(key)
        set_setting(key, "off" if current == "on" else "on")

    await callback.message.edit_text(
        settings_text(),
        reply_markup=settings_keyboard(),
    )
    await callback.answer("تم تحديث الإعدادات.")


@dp.callback_query(F.data == "cycle_idle")
async def cycle_idle(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("خاص بالمالك.", show_alert=True)
        return

    choices = ["10", "30", "60", "120", "360"]
    current = get_setting("idle_minutes")
    index = choices.index(current) if current in choices else 1

    set_setting("idle_minutes", choices[(index + 1) % len(choices)])

    await callback.message.edit_text(
        settings_text(),
        reply_markup=settings_keyboard(),
    )
    await callback.answer("تغيّرت مدة الغياب.")


@dp.callback_query(F.data == "cycle_length")
async def cycle_length(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("خاص بالمالك.", show_alert=True)
        return

    choices = ["short", "medium", "long"]
    current = get_setting("reply_length")
    index = choices.index(current) if current in choices else 0

    set_setting("reply_length", choices[(index + 1) % len(choices)])

    await callback.message.edit_text(
        settings_text(),
        reply_markup=settings_keyboard(),
    )
    await callback.answer("تغيّر طول الرد.")


@dp.callback_query(F.data == "clear_memory")
async def clear_memory(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("خاص بالمالك.", show_alert=True)
        return

    db.execute(
        "UPDATE users SET history = '[]' WHERE user_id = ?",
        (OWNER_ID,),
    )
    db.commit()

    await callback.message.edit_text(
        settings_text(),
        reply_markup=settings_keyboard(),
    )
    await callback.answer("انمسحت ذاكرة المحادثة ❤️", show_alert=True)


@dp.callback_query(F.data == "show_settings")
async def show_settings(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("خاص بالمالك.", show_alert=True)
        return

    await callback.message.edit_text(
        settings_text(),
        reply_markup=settings_keyboard(),
    )
    await callback.answer()


# ==================================================
# 6) أوامر البوت
# ==================================================

@dp.message(CommandStart())
async def start(message: Message):
    user_id = message.from_user.id
    touch_user(user_id)

    if user_id == OWNER_ID:
        text = (
            "هلا حسين ❤️ آني رُقيّة، شلونك؟\n"
            "تعال نسولف، شنو أخبارك؟ 😊"
        )
    else:
        text = (
            "هلا بيك 😊 آني رُقيّة، شلونك؟ "
            "شنو تحب نسولف اليوم؟"
        )

    await message.answer(text)


@dp.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "أوامر رُقيّة:\n"
        "/start - بدء المحادثة\n"
        "/reset - مسح ذاكرة محادثتك\n"
        "/help - المساعدة\n"
        + (
            "/settings - إعدادات المالك"
            if message.from_user.id == OWNER_ID
            else ""
        )
    )


@dp.message(Command("reset"))
async def reset_command(message: Message):
    user_id = message.from_user.id
    get_user(user_id)

    db.execute(
        "UPDATE users SET history = '[]' WHERE user_id = ?",
        (user_id,),
    )
    db.commit()

    await message.answer("تمام، مسحت ذاكرة محادثتنا ❤️")


# ==================================================
# 7) المحادثة مع الذكاء الاصطناعي
# ==================================================

@dp.message(F.text)
async def chat(message: Message):
    user_id = message.from_user.id
    user_text = (message.text or "").strip()

    if not user_text or user_text.startswith("/"):
        return

    touch_user(user_id)

    await bot.send_chat_action(
        chat_id=message.chat.id,
        action="typing",
    )

    answer = await generate_reply(user_id, user_text)
    await message.answer(answer)


# ==================================================
# 8) رسالة تلقائية لحسين عند الغياب
# ==================================================

async def idle_message_loop():
    while True:
        try:
            if get_setting("auto_message") != "on":
                await asyncio.sleep(60)
                continue

            row = get_user(OWNER_ID)
            now = time.time()
            idle_seconds = int(get_setting("idle_minutes")) * 60

            last_seen = row["last_seen"]
            last_nudge = row["last_nudge"]

            if (
                last_seen > 0
                and now - last_seen >= idle_seconds
                and now - last_nudge >= idle_seconds
            ):
                # تسجيل وقت المبادرة لتجنب تكرارها كل دقيقة
                db.execute(
                    "UPDATE users SET last_nudge = ? WHERE user_id = ?",
                    (now, OWNER_ID),
                )
                db.commit()

                text = await generate_reply(
                    OWNER_ID, "", proactive=True
                )

                try:
                    await bot.send_message(OWNER_ID, text)
                except Exception:
                    log.exception("Could not send idle message")

        except Exception:
            log.exception("Idle message loop error")

        await asyncio.sleep(60)


# ==================================================
# 9) تشغيل البوت
# ==================================================

async def main():
    asyncio.create_task(idle_message_loop())
    log.info("Ruqayya is starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        db.close()
