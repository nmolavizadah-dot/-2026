import os
import json
import logging
import asyncio
import secrets
from pathlib import Path
from typing import Optional

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InputFile, InputMediaVideo
)
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
DATA_FILE = Path(os.environ.get("DATA_FILE", "data.json"))
INITIAL_ADMINS = {8800277333, 7812937031}
DATA_LOCK = asyncio.Lock()

DEFAULT_DATA = {
    "admins": sorted(INITIAL_ADMINS),
    "required_channels": [],
    "collections": {},
    "videos": {},
    "pending": {}
}

def load_data():
    if DATA_FILE.exists():
        try:
            loaded = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            data = DEFAULT_DATA.copy()
            data.update(loaded)
            data["admins"] = sorted(set(int(x) for x in data.get("admins", [])) | INITIAL_ADMINS)
            return data
        except Exception:
            log.exception("Could not read data file; starting with defaults.")
    return json.loads(json.dumps(DEFAULT_DATA))

DATA = load_data()

async def save_data():
    async with DATA_LOCK:
        temp = DATA_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(DATA, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(DATA_FILE)

def is_admin(user_id: int) -> bool:
    return user_id in set(int(x) for x in DATA.get("admins", []))

def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 دریافت لینک همه فایل‌ها", callback_data="admin:all_link")],
        [InlineKeyboardButton("➖ برداشتن عضویت اجباری", callback_data="admin:remove_channel")],
        [InlineKeyboardButton("➕ گذاشتن عضویت اجباری", callback_data="admin:add_channel")],
        [InlineKeyboardButton("👤 افزودن مدیر", callback_data="admin:add_admin"),
         InlineKeyboardButton("🗑 برداشتن مدیر", callback_data="admin:remove_admin")],
    ])

def user_video_keyboard(file_id: str):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔗 دریافت لینک", callback_data=f"video:link:{file_id}"),
        InlineKeyboardButton("🖼 تغییر کاور فیلم", callback_data=f"video:cover:{file_id}")
    ]])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    args = context.args
    if is_admin(user.id):
        await update.effective_message.reply_text("پنل مدیریت ربات:", reply_markup=admin_keyboard())
        return
    if args and args[0].startswith("get_"):
        token = args[0][4:]
        coll = DATA.get("collections", {}).get(token)
        if not coll:
            await update.effective_message.reply_text("این لینک نامعتبر است یا مجموعه‌ای برای آن پیدا نشد.")
            return
        if not await check_membership(user.id, context):
            await update.effective_message.reply_text("برای دریافت فایل‌ها ابتدا در کانال‌های زیر عضو شوید و سپس /start را دوباره بزنید:\n" + "\n".join(coll.get("required_channels", DATA.get("required_channels", []))))
            return
        await send_collection(update.effective_message, context, coll)
        return
    await update.effective_message.reply_text(
        "سلام! برای دریافت فایل، لینک اختصاصی دریافت را باز کنید. "
        "اگر مدیر هستید از حساب مدیر /start را بزنید."
    )

async def check_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    for channel in DATA.get("required_channels", []):
        try:
            member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                return False
        except TelegramError:
            # Fail closed: don't bypass a channel if bot cannot verify membership.
            log.warning("Unable to verify membership for channel %s", channel)
            return False
    return True

async def send_collection(message, context, coll):
    sent = 0
    for item in coll.get("items", []):
        try:
            await context.bot.copy_message(
                chat_id=message.chat_id,
                from_chat_id=item["chat_id"],
                message_id=item["message_id"]
            )
            sent += 1
            await asyncio.sleep(0.08)
        except TelegramError:
            log.exception("Failed to copy item %s", item)
    if sent == 0:
        await message.reply_text("ارسال فایل‌ها ناموفق بود؛ ممکن است فایل‌ها دیگر در دسترس نباشند.")
    else:
        await message.reply_text(f"ارسال مجموعه تمام شد. {sent} فایل ارسال شد.")

async def admin_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    if not is_admin(user_id):
        await q.edit_message_text("شما اجازه دسترسی به پنل مدیریت را ندارید.")
        return
    action = q.data.split(":")[1]
    if action == "all_link":
        context.user_data["collecting_all"] = True
        await q.message.reply_text(
            "اکنون فایل‌ها/ویدیوهای مجموعه را یکی‌یکی برای ربات بفرستید. "
            "وقتی تمام شد، دستور /finish را ارسال کنید. سپس لینک یکجا ساخته می‌شود."
        )
    elif action == "add_channel":
        context.user_data["await_channel"] = True
        await q.message.reply_text("لینک یا شناسه کانال را بفرستید. ابتدا ربات را به کانال اضافه کنید و مدیرش کنید.")
    elif action == "remove_channel":
        channels = DATA.get("required_channels", [])
        if not channels:
            await q.message.reply_text("هیچ کانالی برای عضویت اجباری ثبت نشده است.")
        else:
            keyboard = [[InlineKeyboardButton(str(ch), callback_data=f"channel:remove:{i}")]
                        for i, ch in enumerate(channels)]
            await q.message.reply_text("کانال موردنظر را برای حذف انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif action == "add_admin":
        context.user_data["await_admin_add"] = True
        await q.message.reply_text("شناسه عددی تلگرام مدیر جدید را بفرستید.")
    elif action == "remove_admin":
        removable = [int(x) for x in DATA.get("admins", []) if int(x) not in INITIAL_ADMINS]
        if not removable:
            await q.message.reply_text("مدیر اضافه‌شده‌ای برای حذف وجود ندارد. دو مدیر اصلی قابل حذف نیستند.")
        else:
            keyboard = [[InlineKeyboardButton(str(x), callback_data=f"admin:remove_id:{x}")] for x in removable]
            await q.message.reply_text("مدیری را برای حذف انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))

async def channel_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    parts = q.data.split(":")
    if parts[1] == "remove":
        try:
            idx = int(parts[2])
            channel = DATA["required_channels"].pop(idx)
            await save_data()
            await q.edit_message_text(f"عضویت اجباری کانال {channel} برداشته شد.")
        except (ValueError, IndexError):
            await q.edit_message_text("کانال پیدا نشد؛ پنل را دوباره باز کنید.")
    elif parts[1] == "remove_id":
        target = int(parts[2])
        if target in INITIAL_ADMINS:
            await q.edit_message_text("مدیران اصلی قابل حذف نیستند.")
            return
        DATA["admins"] = [int(x) for x in DATA["admins"] if int(x) != target]
        await save_data()
        await q.edit_message_text(f"مدیر {target} حذف شد.")

async def video_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split(":")
    file_id = parts[2]
    record = DATA.get("videos", {}).get(file_id)
    if not record:
        await q.message.reply_text("اطلاعات ویدیو پیدا نشد؛ لطفاً ویدیو را دوباره ارسال کنید.")
        return
    if parts[1] == "link":
        # This link sends a single video via Telegram deep-link.
        token = secrets.token_urlsafe(9)
        DATA["collections"][token] = {
            "items": [{"chat_id": record["chat_id"], "message_id": record["message_id"]}],
            "required_channels": list(DATA.get("required_channels", []))
        }
        await save_data()
        me = await context.bot.get_me()
        await q.message.reply_text(f"لینک دریافت ویدیو:\nhttps://t.me/{me.username}?start=get_{token}")
    elif parts[1] == "cover":
        if not is_admin(q.from_user.id):
            await q.message.reply_text("تغییر کاور فقط برای مدیران فعال است.")
            return
        context.user_data["cover_for"] = file_id
        await q.message.reply_text("لطفاً عکس کاور جدید را به‌صورت عکس (Photo) ارسال کنید.")

async def finish_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.user_data.get("collecting_all"):
        await update.effective_message.reply_text("ابتدا از پنل مدیریت گزینه دریافت لینک همه فایل‌ها را انتخاب کنید.")
        return
    items = context.user_data.get("collection_items", [])
    if not items:
        await update.effective_message.reply_text("هنوز فایلی دریافت نشده است.")
        return
    token = secrets.token_urlsafe(10)
    DATA["collections"][token] = {"items": items, "required_channels": list(DATA.get("required_channels", []))}
    await save_data()
    context.user_data["collecting_all"] = False
    context.user_data["collection_items"] = []
    me = await context.bot.get_me()
    await update.effective_message.reply_text(f"لینک دریافت همه فایل‌ها:\nhttps://t.me/{me.username}?start=get_{token}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    # Admin state flows
    if is_admin(user.id) and context.user_data.get("await_admin_add"):
        try:
            target = int(msg.text.strip())
            if target <= 0:
                raise ValueError
            DATA["admins"] = sorted(set(int(x) for x in DATA["admins"]) | {target})
            await save_data()
            context.user_data["await_admin_add"] = False
            await msg.reply_text(f"مدیر {target} اضافه شد.", reply_markup=admin_keyboard())
        except (ValueError, AttributeError):
            await msg.reply_text("لطفاً شناسه عددی معتبر بفرستید.")
        return

    if is_admin(user.id) and context.user_data.get("await_channel"):
        channel = (msg.text or "").strip()
        if not channel:
            await msg.reply_text("لطفاً لینک یا شناسه کانال را بفرستید.")
            return
        channel_id = channel
        if "t.me/" in channel:
            tail = channel.split("t.me/", 1)[1].strip("/")
            if tail.startswith("+") or tail.startswith("joinchat/"):
                await msg.reply_text("برای عضویت اجباری، کانال باید لینک عمومی یا شناسه قابل‌دسترسی داشته باشد.")
                return
            channel_id = "@" + tail.split("/")[0].lstrip("@")
        try:
            chat = await context.bot.get_chat(channel_id)
            me = await context.bot.get_me()
            bot_member = await context.bot.get_chat_member(chat.id, me.id)
            if bot_member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                await msg.reply_text("ربات هنوز مدیر این کانال نیست. ربات را مدیر کنید و دوباره لینک را بفرستید.")
                return
            if chat.id not in DATA["required_channels"]:
                DATA["required_channels"].append(chat.id)
                await save_data()
            context.user_data["await_channel"] = False
            await msg.reply_text(f"عضویت اجباری برای {chat.title} فعال شد.", reply_markup=admin_keyboard())
        except TelegramError:
            await msg.reply_text("کانال پیدا نشد یا ربات به آن دسترسی ندارد. لینک/شناسه را بررسی کنید و ربات را مدیر کانال کنید.")
        return

    # Cover photo flow
    if context.user_data.get("cover_for"):
        if not is_admin(user.id):
            context.user_data.pop("cover_for", None)
            return
        if not msg.photo:
            await msg.reply_text("لطفاً عکس را به‌صورت Photo بفرستید، نه به شکل فایل.")
            return
        file_id = context.user_data.pop("cover_for")
        record = DATA["videos"].get(file_id)
        if not record:
            await msg.reply_text("ویدیو پیدا نشد.")
            return
        cover_id = msg.photo[-1].file_id
        record["cover_file_id"] = cover_id
        await save_data()
        try:
            await context.bot.copy_message(
                chat_id=msg.chat_id,
                from_chat_id=record["chat_id"],
                message_id=record["message_id"],
                caption=record.get("caption") or None
            )
            # Telegram copy_message cannot reliably apply a new thumbnail to a copied video.
            # Re-send the video with the cover as thumbnail when a file can be downloaded/re-uploaded.
            await msg.reply_text(
                "عکس کاور ذخیره شد. برای اعمال قطعی کاور جدید، ویدیو باید با فایل قابل‌دانلود دوباره ارسال شود. "
                "این ربات در این نسخه فایل ویدیوی اصلی را دانلود و بازکدگذاری نمی‌کند."
            )
        except TelegramError:
            await msg.reply_text("کاور ذخیره شد، اما ارسال مجدد ویدیو ناموفق بود.")
        return

    # Collection capture: admin sends any media/document while collection mode is active.
    if is_admin(user.id) and context.user_data.get("collecting_all"):
        if msg.photo or msg.video or msg.document or msg.audio or msg.animation:
            context.user_data.setdefault("collection_items", []).append({
                "chat_id": msg.chat_id, "message_id": msg.message_id
            })
            await msg.reply_text(f"فایل اضافه شد ({len(context.user_data['collection_items'])}). برای پایان /finish را بفرستید.")
            return

    # Save incoming videos and display buttons; users won't see cover button.
    if msg.video:
        key = msg.video.file_unique_id
        DATA["videos"][key] = {
            "chat_id": msg.chat_id,
            "message_id": msg.message_id,
            "caption": msg.caption or "",
            "file_id": msg.video.file_id,
            "file_unique_id": msg.video.file_unique_id
        }
        await save_data()
        if is_admin(user.id):
            await msg.reply_text("ویدیو دریافت شد. یکی از گزینه‌ها را انتخاب کنید:", reply_markup=user_video_keyboard(key))
        else:
            await msg.reply_text("ویدیو دریافت شد.", reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔗 دریافت لینک", callback_data=f"video:link:{key}")
            ]]))

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Unhandled bot error:", exc_info=context.error)

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing. Set it in Render Environment.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("finish", finish_collection))
    app.add_handler(CallbackQueryHandler(admin_callbacks, pattern=r"^admin:(?:all_link|remove_channel|add_channel|add_admin|remove_admin)$"))
    app.add_handler(CallbackQueryHandler(channel_callbacks, pattern=r"^channel:|^admin:remove_id:"))
    app.add_handler(CallbackQueryHandler(video_callbacks, pattern=r"^video:"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    log.info("Bot starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)

if __name__ == "__main__":
    main()
