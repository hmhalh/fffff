import os
import re
import json
import time
import asyncio
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import yt_dlp


# =========================
# إعدادات البوت
# =========================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.ERROR,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

# التوكن من Environment Variables
TOKEN = os.getenv("BOT_TOKEN", "").strip()

OWNER_ID = 7199778669
DATA_FILE = "users_data.json"
DOWNLOAD_PATH = os.path.join(os.getcwd(), "downloads")

# عدد التحميلات المتزامنة كحد أقصى.
# يمكنك تغييره من متغير MAX_CONCURRENT_DOWNLOADS.
MAX_CONCURRENT_DOWNLOADS = max(
    1, int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))
)

executor = ThreadPoolExecutor(
    max_workers=MAX_CONCURRENT_DOWNLOADS
)

download_semaphore = asyncio.Semaphore(
    MAX_CONCURRENT_DOWNLOADS
)

user_states = {}
user_data = {}
active_downloads = {}


# =========================
# إدارة الملفات والبيانات
# =========================

def ensure_download_folder():
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    return DOWNLOAD_PATH


def load_data():
    global user_data

    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as file:
                user_data = json.load(file)
        except (json.JSONDecodeError, OSError):
            user_data = {}
    else:
        user_data = {}


def save_data():
    temporary_file = DATA_FILE + ".tmp"

    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(user_data, file, ensure_ascii=False, indent=4)

    os.replace(temporary_file, DATA_FILE)


def add_user(user_id, username, first_name):
    user_id_str = str(user_id)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if user_id_str not in user_data:
        user_data[user_id_str] = {
            "username": username or "",
            "first_name": first_name or "",
            "join_date": now,
            "last_active": now,
            "downloads": [],
            "download_count": 0,
            "is_active": True,
        }
    else:
        user_data[user_id_str]["username"] = username or ""
        user_data[user_id_str]["first_name"] = first_name or ""
        user_data[user_id_str]["last_active"] = now
        user_data[user_id_str]["is_active"] = True

    save_data()


def update_downloads(user_id, platform, link):
    user_id_str = str(user_id)

    if user_id_str in user_data:
        user_data[user_id_str].setdefault("downloads", [])
        user_data[user_id_str]["download_count"] = (
            user_data[user_id_str].get("download_count", 0) + 1
        )

        user_data[user_id_str]["downloads"].append({
            "platform": platform,
            "link": link,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

        save_data()


def get_stats():
    total_users = len(user_data)
    active_users = 0
    inactive_users = 0
    total_downloads = 0
    all_links = []

    for data in user_data.values():
        try:
            last_active = datetime.strptime(
                data["last_active"], "%Y-%m-%d %H:%M:%S"
            )

            if (datetime.now() - last_active).days <= 30:
                active_users += 1
            else:
                inactive_users += 1
        except (KeyError, ValueError, TypeError):
            inactive_users += 1

        total_downloads += data.get("download_count", 0)

        for download in data.get("downloads", []):
            all_links.append(download.get("link", ""))

    return {
        "total_users": total_users,
        "active_users": active_users,
        "inactive_users": inactive_users,
        "total_downloads": total_downloads,
        "all_links": all_links,
        "active_downloads": len(active_downloads),
    }


# =========================
# تحديد المنصة
# =========================

def get_platform(url):
    url_lower = url.lower()

    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "YouTube"
    elif "tiktok.com" in url_lower:
        return "TikTok"
    elif "instagram.com" in url_lower:
        return "Instagram"
    elif "facebook.com" in url_lower or "fb.com" in url_lower:
        return "Facebook"
    elif "twitter.com" in url_lower or "x.com" in url_lower:
        return "Twitter/X"

    return "غير معروف"


def validate_url(url):
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            return False

        if not parsed.hostname:
            return False

        host = parsed.hostname.lower()

        # السماح فقط بنطاقات المنصات المعروفة
        allowed_domains = (
            "tiktok.com",
            "instagram.com",
            "facebook.com",
            "fb.com",
            "twitter.com",
            "x.com",
            "youtube.com",
            "youtu.be",
        )

        return any(
            host == domain or host.endswith("." + domain)
            for domain in allowed_domains
        )
    except (ValueError, AttributeError):
        return False


# =========================
# تحميل الفيديو
# =========================

def download_video(url):
    platform = get_platform(url)
    ensure_download_folder()

    timestamp = int(time.time() * 1000)

    ydl_opts = {
        "outtmpl": os.path.join(
            DOWNLOAD_PATH,
            f"%(title).100s_{timestamp}_%(id)s.%(ext)s",
        ),
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 5,
        "fragment_retries": 5,
        "skip_unavailable_fragments": True,
        "socket_timeout": 30,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

            if not info:
                raise RuntimeError("لم يتم العثور على الفيديو.")

            filename = ydl.prepare_filename(info)

            if os.path.isfile(filename):
                return filename, platform

            # بعض المنصات قد تغيّر امتداد الملف النهائي
            video_id = info.get("id", "")

            for name in os.listdir(DOWNLOAD_PATH):
                if (
                    video_id
                    and f"_{timestamp}_{video_id}." in name
                ):
                    path = os.path.join(DOWNLOAD_PATH, name)

                    if os.path.isfile(path):
                        return path, platform

            raise RuntimeError("لم يتم العثور على الملف بعد التحميل.")

    except Exception as error:
        raise RuntimeError(f"فشل التحميل: {error}") from error


# =========================
# القوائم
# =========================

def main_menu_text():
    return """🎬 مرحباً بك في بوت تحميل الفيديو!

يمكنك تحميل الفيديوهات من:
• TikTok
• Instagram
• Facebook
• Twitter/X

📌 طريقة الاستخدام:
1️⃣ اضغط على زر تحميل فيديو.
2️⃣ أرسل رابط الفيديو.
3️⃣ انتظر حتى يكتمل التحميل.

🤖 صانع البوت: @z_2w1"""


def main_menu_keyboard(user_id=None):
    keyboard = [
        [InlineKeyboardButton("📥 تحميل فيديو", callback_data="download")]
    ]

    if user_id == OWNER_ID:
        keyboard.append([
            InlineKeyboardButton("📊 الإحصائيات", callback_data="stats")
        ])
        keyboard.append([
            InlineKeyboardButton("📢 إذاعة للجميع", callback_data="broadcast")
        ])

    return InlineKeyboardMarkup(keyboard)


def back_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
    ])


# =========================
# أمر البداية
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not user or not update.message:
        return

    add_user(user.id, user.username, user.first_name)

    user_states.pop(user.id, None)

    await update.message.reply_text(
        main_menu_text(),
        reply_markup=main_menu_keyboard(user.id),
    )


# =========================
# التعامل مع الأزرار
# =========================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    user_id = query.from_user.id
    data = query.data

    if data == "download":
        user_states[user_id] = "waiting_for_link"

        await query.edit_message_text(
            "📥 أرسل رابط الفيديو الآن.\n\n"
            "المنصات المدعومة: TikTok - Instagram - "
            "Facebook - Twitter/X",
            reply_markup=back_menu_keyboard(),
        )

    elif data == "stats":
        if user_id != OWNER_ID:
            await query.edit_message_text(
                "⛔ هذه الخاصية متاحة للمالك فقط.",
                reply_markup=back_menu_keyboard(),
            )
            return

        stats = get_stats()

        stats_text = f"""📊 الإحصائيات التفصيلية

👥 إجمالي المستخدمين: {stats['total_users']}
✅ نشطون (آخر 30 يوم): {stats['active_users']}
❌ غير نشطين: {stats['inactive_users']}

📥 إجمالي التحميلات: {stats['total_downloads']}
🔄 التحميلات النشطة: {stats['active_downloads']}
⚙️ الحد الأقصى للتحميلات المتزامنة: {MAX_CONCURRENT_DOWNLOADS}"""

        await query.edit_message_text(
            stats_text,
            reply_markup=back_menu_keyboard(),
        )

    elif data == "broadcast":
        if user_id != OWNER_ID:
            await query.edit_message_text(
                "⛔ هذه الخاصية متاحة للمالك فقط.",
                reply_markup=back_menu_keyboard(),
            )
            return

        msg = await query.edit_message_text(
            "📢 أرسل الرسالة التي تريد إذاعتها لجميع المستخدمين.",
            reply_markup=back_menu_keyboard(),
        )

        user_states[user_id] = {
            "state": "waiting_for_broadcast",
            "msg_id": msg.message_id,
        }

    elif data == "back":
        user_states.pop(user_id, None)

        await query.edit_message_text(
            main_menu_text(),
            reply_markup=main_menu_keyboard(user_id),
        )


# =========================
# تنفيذ التحميل وإرسال الفيديو
# =========================

async def download_and_send(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url,
    user_id,
    user,
    waiting_msg,
):
    filepath = None
    download_id = f"{user_id}_{datetime.now().timestamp()}"

    try:
        async with download_semaphore:
            active_downloads[download_id] = {
                "user_id": user_id,
                "url": url,
                "platform": get_platform(url),
                "start_time": datetime.now(),
            }

            await waiting_msg.edit_text(
                f"⏳ جاري تحميل الفيديو من {get_platform(url)}...\n"
                "يرجى الانتظار."
            )

            loop = asyncio.get_running_loop()

            filepath, platform = await loop.run_in_executor(
                executor,
                download_video,
                url,
            )

            if not os.path.isfile(filepath):
                raise RuntimeError("الملف غير موجود بعد التحميل.")

            file_size = os.path.getsize(filepath)

            # حد عملي لتجنب تجاوز حد رفع الملفات في تيليجرام
            max_upload_size = 49 * 1024 * 1024

            if file_size > max_upload_size:
                raise RuntimeError(
                    "حجم الفيديو أكبر من الحد المسموح به في هذا البوت. "
                    "جرّب فيديو أصغر."
                )

            update_downloads(user_id, platform, url)

            await waiting_msg.delete()

            with open(filepath, "rb") as video_file:
                await update.message.reply_video(
                    video=video_file,
                    supports_streaming=True,
                )

            await update.message.reply_text(
                f"✅ تم تحميل الفيديو من {platform}."
            )

            # إرسال نسخة للمالك وتفاصيل الطلب
            if user_id != OWNER_ID:
                try:
                    with open(filepath, "rb") as video_file:
                        await context.bot.send_video(
                            chat_id=OWNER_ID,
                            video=video_file,
                            supports_streaming=True,
                        )

                    owner_info = f"""📥 طلب تحميل جديد

الاسم: {user.first_name or 'غير معروف'}
اسم المستخدم: @{user.username or 'لا يوجد'}
المنصة: {platform}
الرابط: {url}
التاريخ: {datetime.now().strftime('%Y-%m-%d')}
الوقت: {datetime.now().strftime('%H:%M:%S')}
معرّف المستخدم: {user_id}"""

                    await context.bot.send_message(
                        chat_id=OWNER_ID,
                        text=owner_info,
                    )

                except Exception:
                    logging.exception("تعذر إرسال نسخة للمالك.")

            user_states.pop(user_id, None)

            await update.message.reply_text(
                main_menu_text(),
                reply_markup=main_menu_keyboard(user_id),
            )

    except Exception as error:
        logging.exception("فشل تحميل الفيديو.")

        try:
            await waiting_msg.delete()
        except Exception:
            pass

        await update.message.reply_text(
            f"❌ حدث خطأ أثناء التحميل:\n{error}\n\n"
            "تأكد من صحة الرابط وأن الفيديو متاح."
        )

        await update.message.reply_text(
            main_menu_text(),
            reply_markup=main_menu_keyboard(user_id),
        )

    finally:
        active_downloads.pop(download_id, None)

        if filepath and os.path.isfile(filepath):
            try:
                os.remove(filepath)
            except OSError:
                logging.exception("تعذر حذف الملف المؤقت.")


# =========================
# استقبال الرسائل
# =========================

async def message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message or not update.effective_user:
        return

    user = update.effective_user
    user_id = user.id
    message_text = update.message.text or ""

    add_user(user_id, user.username, user.first_name)

    state_data = user_states.get(user_id)

    if isinstance(state_data, dict):
        state = state_data.get("state")
    else:
        state = state_data

    if state == "waiting_for_link":
        url = message_text.strip()

        if not validate_url(url):
            await update.message.reply_text(
                "❌ الرابط غير صحيح أو المنصة غير مدعومة.\n"
                "أرسل رابط فيديو صحيحاً من إحدى المنصات المدعومة.",
                reply_markup=back_menu_keyboard(),
            )
            return

        platform = get_platform(url)

        if platform == "YouTube":
            await update.message.reply_text(
                "❌ عذراً، البوت لا يدعم YouTube حالياً.",
                reply_markup=back_menu_keyboard(),
            )
            return

        waiting_msg = await update.message.reply_text(
            f"⏳ جاري تجهيز التحميل من {platform}...\n"
            "يرجى الانتظار."
        )

        # إنشاء مهمة منفصلة لكل طلب، مع تنظيم التزامن
        asyncio.create_task(
            download_and_send(
                update,
                context,
                url,
                user_id,
                user,
                waiting_msg,
            )
        )

    elif state == "waiting_for_broadcast":
        if user_id != OWNER_ID:
            await update.message.reply_text(
                "⛔ غير مصرح لك بهذه الخاصية.",
                reply_markup=main_menu_keyboard(user_id),
            )
            return

        broadcast_text = message_text

        try:
            msg_id = state_data.get("msg_id")
            await context.bot.delete_message(
                chat_id=user_id,
                message_id=msg_id,
            )
        except Exception:
            pass

        sending_msg = await update.message.reply_text(
            "⏳ جاري إرسال الإذاعة لجميع المستخدمين..."
        )

        sent_count = 0

        for uid in list(user_data.keys()):
            try:
                await context.bot.send_message(
                    chat_id=int(uid),
                    text=f"📢 إذاعة من المالك:\n\n{broadcast_text}",
                )
                sent_count += 1

                # تقليل احتمال تجاوز حدود الإرسال
                await asyncio.sleep(0.05)

            except Exception:
                logging.warning(
                    "تعذر إرسال الإذاعة إلى مستخدم."
                )

        try:
            await sending_msg.delete()
        except Exception:
            pass

        user_states.pop(user_id, None)

        await update.message.reply_text(
            f"✅ اكتملت الإذاعة. تم الإرسال إلى {sent_count} مستخدم."
        )

        await update.message.reply_text(
            main_menu_text(),
            reply_markup=main_menu_keyboard(user_id),
        )

    else:
        await update.message.reply_text(
            main_menu_text(),
            reply_markup=main_menu_keyboard(user_id),
        )


# =========================
# تشغيل البوت
# =========================

def main():
    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN غير موجود. أضفه في Environment Variables "
            "داخل موقع الاستضافة."
        )

    load_data()
    ensure_download_folder()

    print("✅ تم إعداد البوت.")
    print(f"📊 عدد المستخدمين المسجلين: {len(user_data)}")
    print(
        f"⚙️ عدد التحميلات المتزامنة: "
        f"{MAX_CONCURRENT_DOWNLOADS}"
    )

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        CallbackQueryHandler(button_handler)
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_handler,
        )
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    try:
        main()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)