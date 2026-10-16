import os
import json
import logging
from pathlib import Path

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
from google import genai

# =========================
# الإعدادات الأساسية
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

BASE_DIR = Path(os.getenv("DATA_DIR", "/app"))
SESSIONS_DIR = BASE_DIR / "sessions"
DATA_DIR = BASE_DIR / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"

SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("admin-bot")

# =========================
# حفظ الإعدادات
# =========================

def load_settings():
    default = {
        "admins": [],
        "groups": [],
        "selected_sessions": [],
        "running": False,
        "gemini_api_key": "",
        "pending": {},
    }

    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(
                SETTINGS_FILE.read_text(encoding="utf-8")
            )
            default.update(saved)
        except (OSError, ValueError):
            log.exception("تعذر قراءة ملف الإعدادات")

    return default


settings = load_settings()


def save_settings():
    temp = SETTINGS_FILE.with_suffix(".tmp")
    temp.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(SETTINGS_FILE)


def is_admin(user_id):
    return (
        user_id == OWNER_ID
        or user_id in settings["admins"]
    )


# =========================
# أدوات الواجهة
# =========================

def button(text, callback):
    return InlineKeyboardButton(text, callback_data=callback)


def main_keyboard():
    status = "إيقاف النظام ⏹" if settings["running"] else "تشغيل النظام ▶️"

    return InlineKeyboardMarkup([
        [button("👥 المستخدمون", "users")],
        [button("📊 الجلسات والإحصائيات", "stats")],
        [button("⚙️ الإعدادات", "settings")],
        [button(status, "toggle")],
        [button("💬 إدارة المجموعات", "groups")],
    ])


def back_keyboard(destination="home"):
    return InlineKeyboardMarkup([
        [button("⬅️ رجوع", destination)]
    ])


async def show_page(query, text, keyboard):
    await query.edit_message_text(
        text=text,
        reply_markup=keyboard,
    )


async def send_home(update: Update):
    await update.effective_message.reply_text(
        "🤖 لوحة إدارة البوت\n\n"
        "اختر القسم المطلوب من القائمة:",
        reply_markup=main_keyboard(),
    )


# =========================
# البداية والصلاحيات
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not user or not is_admin(user.id):
        await update.effective_message.reply_text(
            "⛔ هذا البوت مخصص للمشرفين."
        )
        return

    await send_home(update)


async def handle_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    user = query.from_user

    await query.answer()

    if not is_admin(user.id):
        await query.answer(
            "غير مصرح لك باستخدام لوحة الإدارة.",
            show_alert=True,
        )
        return

    action = query.data

    # القائمة الرئيسية
    if action == "home":
        await show_page(
            query,
            "🤖 لوحة الإدارة الرئيسية",
            main_keyboard(),
        )
        return

    # المستخدمون وملفات الجلسات
    if action == "users":
        files = sorted(SESSIONS_DIR.glob("*.session"))

        if not files:
            text = (
                "👥 المستخدمون\n\n"
                "لم أجد ملفات .session داخل مجلد sessions.\n\n"
                "ارفع ملفات الجلسات إلى مجلد التخزين الصحيح "
                "ثم اضغط تحديث."
            )
        else:
            text = (
                f"👥 ملفات الجلسات: {len(files)}\n\n"
                "حدد الملفات التي تريد وضعها ضمن قائمة الاختيار.\n"
                "هذا يحدّدها إدارياً فقط ولا يسجّل دخول الحساب."
            )

        rows = []
        for file in files[:30]:
            selected = file.name in settings["selected_sessions"]
            mark = "✅" if selected else "▫️"
            rows.append([
                button(
                    f"{mark} {file.stem}",
                    f"sel:{file.name}",
                )
            ])

        if len(files) > 30:
            text += "\n\nتُعرض أول 30 جلسة فقط في هذه الصفحة."

        rows.extend([
            [button("☑️ تحديد كل الملفات", "select_all")],
            [button("❎ إلغاء التحديد", "select_none")],
            [button("🔄 تحديث القائمة", "users")],
            [button("⬅️ رجوع", "home")],
        ])

        await show_page(
            query,
            text,
            InlineKeyboardMarkup(rows),
        )
        return

    if action.startswith("sel:"):
        name = action[4:]

        # منع أي مسار ملف غير آمن
        if (
            Path(name).name != name
            or not name.endswith(".session")
            or not (SESSIONS_DIR / name).is_file()
        ):
            await query.answer(
                "ملف الجلسة غير موجود.",
                show_alert=True,
            )
            return

        selected = settings["selected_sessions"]

        if name in selected:
            selected.remove(name)
        else:
            selected.append(name)

        save_settings()
        await show_users_page(query)
        return

    if action == "select_all":
        settings["selected_sessions"] = [
            p.name for p in SESSIONS_DIR.glob("*.session")
        ]
        save_settings()
        await show_users_page(query)
        return

    if action == "select_none":
        settings["selected_sessions"] = []
        save_settings()
        await show_users_page(query)
        return

    # الإحصائيات
    if action == "stats":
        files = list(SESSIONS_DIR.glob("*.session"))
        selected = settings["selected_sessions"]

        text = (
            "📊 إحصائيات النظام\n\n"
            f"📁 عدد ملفات الجلسات: {len(files)}\n"
            f"☑️ الملفات المحددة: {len(selected)}\n"
            f"💬 المجموعات المحفوظة: {len(settings['groups'])}\n"
            f"👮 المشرفون الإضافيون: {len(settings['admins'])}\n"
            f"⚙️ حالة النظام: "
            f"{'مفعّل' if settings['running'] else 'متوقف'}\n\n"
            "ملاحظة: وجود ملف جلسة لا يثبت أن الحساب متصل "
            "بتيليجرام. هذه إحصائيات الملفات والإعدادات."
        )

        await show_page(query, text, back_keyboard())
        return

    # التشغيل والإيقاف
    if action == "toggle":
        settings["running"] = not settings["running"]
        save_settings()

        status = "مفعّل ▶️" if settings["running"] else "متوقف ⏹"

        await show_page(
            query,
            f"تم تحديث حالة النظام إلى: {status}\n\n"
            "هذا المفتاح يحفظ حالة لوحة الإدارة فقط. "
            "لا يشغّل حسابات تيليجرام أو يرسل رسائل تلقائياً.",
            main_keyboard(),
        )
        return

    # الإعدادات
    if action == "settings":
        keyboard = InlineKeyboardMarkup([
            [button("🔑 تغيير مفتاح Gemini", "set_key")],
            [button("➕ إضافة أدمن", "add_admin")],
            [button("➖ حذف أدمن", "remove_admin")],
            [button("📋 عرض الأدمن", "list_admins")],
            [button("⬅️ رجوع", "home")],
        ])

        await show_page(
            query,
            "⚙️ الإعدادات\n\nاختر الإجراء المطلوب:",
            keyboard,
        )
        return

    if action in ("set_key", "add_admin", "remove_admin", "add_group"):
        prompts = {
            "set_key": (
                "🔑 أرسل مفتاح Gemini الجديد برسالة واحدة.\n"
                "لن أعرضه مرة أخرى داخل المحادثة."
            ),
            "add_admin": (
                "➕ أرسل Telegram user ID الرقمي للشخص "
                "الذي تريد إضافته كمشرف."
            ),
            "remove_admin": (
                "➖ أرسل Telegram user ID الرقمي للمشرف "
                "الذي تريد حذفه."
            ),
            "add_group": (
                "💬 أرسل معرّف المجموعة أو رابطها.\n"
                "هذه الخطوة تحفظ المجموعة في القائمة فقط."
            ),
        }

        settings["pending"][str(user.id)] = action
        save_settings()

        await show_page(
            query,
            prompts[action],
            back_keyboard(
                "settings" if action != "add_group" else "groups"
            ),
        )
        return

    if action == "list_admins":
        admins = settings["admins"]
        text = "👮 المشرفون الإضافيون:\n\n"

        if admins:
            text += "\n".join(str(x) for x in admins)
        else:
            text += "لا يوجد مشرفون إضافيون."

        text += f"\n\nمالك البوت: {OWNER_ID}"

        await show_page(query, text, back_keyboard("settings"))
        return

    # المجموعات
    if action == "groups":
        groups = settings["groups"]
        text = "💬 المجموعات المحفوظة:\n\n"

        if groups:
            text += "\n".join(
                f"{i + 1}. {g}" for i, g in enumerate(groups)
            )
        else:
            text += "لا توجد مجموعات مضافة."

        keyboard = InlineKeyboardMarkup([
            [button("➕ إضافة مجموعة", "add_group")],
        ])

        for group in groups[:20]:
            keyboard.inline_keyboard.append([
                button(f"🗑 حذف {group[:30]}", f"delgroup:{groups.index(group)}")
            ])

        keyboard.inline_keyboard.append([
            button("⬅️ رجوع", "home")
        ])

        await show_page(query, text, keyboard)
        return

    if action.startswith("delgroup:"):
        try:
            index = int(action.split(":", 1)[1])
            settings["groups"].pop(index)
            save_settings()
        except (ValueError, IndexError):
            await query.answer("تعذر حذف المجموعة.", show_alert=True)
            return

        await show_page(
            query,
            "تم حذف المجموعة من القائمة.",
            back_keyboard("groups"),
        )
        return


async def show_users_page(query):
    files = sorted(SESSIONS_DIR.glob("*.session"))

    text = (
        f"👥 ملفات الجلسات: {len(files)}\n"
        f"المحدد: {len(settings['selected_sessions'])}\n\n"
        "اضغط على اسم الملف لتغيير تحديده."
    )

    rows = []
    for file in files[:30]:
        mark = (
            "✅"
            if file.name in settings["selected_sessions"]
            else "▫️"
        )
        rows.append([
            button(f"{mark} {file.stem}", f"sel:{file.name}")
        ])

    rows.extend([
        [button("☑️ تحديد الكل", "select_all")],
        [button("❎ إلغاء التحديد", "select_none")],
        [button("🔄 تحديث", "users")],
        [button("⬅️ رجوع", "home")],
    ])

    await show_page(query, text, InlineKeyboardMarkup(rows))


# =========================
# استقبال رسائل الإدارة
# =========================

async def receive_admin_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    key = str(user.id)
    action = settings["pending"].get(key)

    if not action:
        return

    value = update.effective_message.text.strip()

    if action == "set_key":
        if len(value) < 15 or len(value) > 500:
            await update.effective_message.reply_text(
                "المفتاح يبدو غير صالح. أرسل مفتاحاً صحيحاً."
            )
            return

        settings["gemini_api_key"] = value
        settings["pending"].pop(key, None)
        save_settings()

        await update.effective_message.reply_text(
            "✅ تم حفظ المفتاح في ملف الإعدادات على التخزين الدائم.\n"
            "لن أعرض قيمته مجدداً."
        )
        return

    if action in ("add_admin", "remove_admin"):
        try:
            admin_id = int(value)
            if admin_id <= 0:
                raise ValueError
        except ValueError:
            await update.effective_message.reply_text(
                "أرسل معرّف تيليجرام رقمي صحيح."
            )
            return

        if admin_id == OWNER_ID:
            await update.effective_message.reply_text(
                "لا يمكن إضافة المالك الأساسي أو حذفه من قائمة الأدمن الإضافيين."
            )
            return

        admins = settings["admins"]

        if action == "add_admin":
            if admin_id not in admins:
                admins.append(admin_id)
            result = "✅ تمت إضافة المشرف."
        else:
            if admin_id in admins:
                admins.remove(admin_id)
            result = "✅ تم حذف المشرف من القائمة."

        settings["pending"].pop(key, None)
        save_settings()
        await update.effective_message.reply_text(result)
        return

    if action == "add_group":
        if len(value) > 300:
            await update.effective_message.reply_text(
                "المدخل طويل جداً."
            )
            return

        if value not in settings["groups"]:
            settings["groups"].append(value)

        settings["pending"].pop(key, None)
        save_settings()

        await update.effective_message.reply_text(
            "✅ تم حفظ المجموعة في القائمة. "
            "لم يتم الانضمام إليها أو إرسال رسائل."
        )


# =========================
# تشغيل البوت
# =========================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "أضف BOT_TOKEN داخل Railway Variables."
        )

    if OWNER_ID <= 0:
        raise RuntimeError(
            "أضف OWNER_ID داخل Railway Variables."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            receive_admin_text,
        )
    )

    log.info("Admin bot starting")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
