import logging
import asyncio
import sqlite3
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# ─── НАСТРОЙКИ ────────────────────────────────────────────────
BOT_TOKEN = "8049793687:AAGMQYh13OOeUNJwoeoV4RMTS_4oxJSaMXM"
ADMIN_ID   = 8658447894  # Вставь свой Telegram user ID (число) 
# ──────────────────────────────────────────────────────────────

DB_PATH = "scammers.db"
logging.basicConfig(level=logging.INFO)

# Храним состояния пользователей (ожидание ввода)
user_states = {}
# Храним тип устройства пользователя
user_devices = {}
# Храним активные задачи спама
spam_tasks = {}
# Храним message_id тревожных сообщений для редактирования
alert_messages = {}


# ─── БАЗА ДАННЫХ ──────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS scammers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT COLLATE NOCASE,
            user_id TEXT,
            added_by INTEGER
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS user_devices (
            user_id INTEGER PRIMARY KEY,
            device TEXT
        )
    """)
    con.commit()
    con.close()


def db_add(username: str = None, uid: str = None, added_by: int = 0) -> bool:
    con = sqlite3.connect(DB_PATH)
    # Проверяем дубликат
    if username:
        row = con.execute("SELECT 1 FROM scammers WHERE username=?", (username.lower().lstrip("@"),)).fetchone()
    else:
        row = con.execute("SELECT 1 FROM scammers WHERE user_id=?", (uid,)).fetchone()
    if row:
        con.close()
        return False
    con.execute(
        "INSERT INTO scammers (username, user_id, added_by) VALUES (?,?,?)",
        (username.lower().lstrip("@") if username else None, uid, added_by)
    )
    con.commit()
    con.close()
    return True


def db_remove(query: str) -> bool:
    con = sqlite3.connect(DB_PATH)
    q = query.lstrip("@").lower()
    cur = con.execute(
        "DELETE FROM scammers WHERE username=? OR user_id=?", (q, q)
    )
    con.commit()
    con.close()
    return cur.rowcount > 0


def db_check(username: str = None, uid: str = None):
    """Возвращает запись если найдена, иначе None."""
    con = sqlite3.connect(DB_PATH)
    row = None
    if username:
        row = con.execute(
            "SELECT username, user_id FROM scammers WHERE username=?",
            (username.lower().lstrip("@"),)
        ).fetchone()
    if not row and uid:
        row = con.execute(
            "SELECT username, user_id FROM scammers WHERE user_id=?", (uid,)
        ).fetchone()
    con.close()
    return row


def db_list():
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT username, user_id FROM scammers ORDER BY id").fetchall()
    con.close()
    return rows


def save_device(user_id: int, device: str):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT OR REPLACE INTO user_devices (user_id, device) VALUES (?,?)",
        (user_id, device)
    )
    con.commit()
    con.close()


def get_device(user_id: int) -> str:
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT device FROM user_devices WHERE user_id=?", (user_id,)
    ).fetchone()
    con.close()
    return row[0] if row else None


# ─── КЛАВИАТУРЫ ───────────────────────────────────────────────
def kb_device():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🍏 iPhone", callback_data="device_iphone"),
            InlineKeyboardButton("👾 Android", callback_data="device_android"),
        ]
    ])


def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗂 Внести в лист",    callback_data="menu_add")],
        [InlineKeyboardButton("🗑 Вынести из листа", callback_data="menu_remove")],
        [InlineKeyboardButton("🔍 Проверить юзера",  callback_data="menu_check")],
        [InlineKeyboardButton("🤖 Подключить бота",  callback_data="menu_connect")],
        [InlineKeyboardButton("📱 Тип устройства",   callback_data="menu_device")],
    ])


def kb_understood():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Понял", callback_data="alert_understood")]
    ])


def kb_back():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Меню", callback_data="menu_back")]
    ])


# ─── ВСПОМОГАТЕЛЬНЫЕ ──────────────────────────────────────────
def profile_link(username: str = None, uid: str = None, device: str = "iphone") -> str:
    if uid and device == "android":
        return f"tg://openmessage?user_id={uid}"
    if username:
        return f"https://t.me/{username}"
    if uid:
        return f"tg://user?id={uid}"
    return ""


def scammer_display(row) -> str:
    username, uid = row
    parts = []
    if username:
        parts.append(f"@{username}")
    if uid:
        parts.append(f"(ID {uid})")
    return " ".join(parts) if parts else "неизвестный"


# ─── /start ───────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # Проверяем — не мошенник ли этот пользователь
    row = db_check(username=user.username, uid=str(user.id))
    if row and user.id != ADMIN_ID:
        await update.message.reply_text(
            "❌ Бот недоступен для тебя."
        )
        return

    device = get_device(user.id)
    if device:
        # Устройство уже выбрано — сразу меню
        await update.message.reply_text(
            "👋 Привет, выбирай нужную кнопку.",
            reply_markup=kb_main()
        )
    else:
        await update.message.reply_text(
            "💻 Какой у тебя смартфон?\n\n"
            "👇 Выбери нужную кнопку",
            reply_markup=kb_device()
        )


# ─── CALLBACK ОБРАБОТЧИК ──────────────────────────────────────
async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    data = q.data

    # ── Выбор устройства ──
    if data.startswith("device_"):
        device = "iphone" if data == "device_iphone" else "android"
        save_device(user.id, device)
        user_devices[user.id] = device
        label = "🍏 iPhone" if device == "iphone" else "👾 Android"
        await q.edit_message_text(
            f"✅ Устройство сохранено: {label}\n\n"
            "👋 Привет, выбирай нужную кнопку.",
            reply_markup=kb_main()
        )

    # ── Меню назад ──
    elif data == "menu_back":
        await q.edit_message_text(
            "👋 Главное меню. Выбирай нужную кнопку.",
            reply_markup=kb_main()
        )

    # ── Смена устройства ──
    elif data == "menu_device":
        await q.edit_message_text(
            "💻 Выбери тип устройства:",
            reply_markup=kb_device()
        )

    # ── Внести в лист ──
    elif data == "menu_add":
        if user.id != ADMIN_ID:
            await q.edit_message_text(
                "❌ Доступ запрещён. Только создатель бота может редактировать список.",
                reply_markup=kb_back()
            )
            return
        user_states[user.id] = "awaiting_add"
        await q.edit_message_text(
            "🟢 Выбрана категория «🗂 Внести в лист».\n"
            "Слушаю — какого пользователя нужно занести?\n\n"
            "Отправь @username или числовой ID",
            reply_markup=kb_back()
        )

    # ── Вынести из листа ──
    elif data == "menu_remove":
        if user.id != ADMIN_ID:
            await q.edit_message_text(
                "❌ Доступ запрещён. Только создатель бота может редактировать список.",
                reply_markup=kb_back()
            )
            return
        user_states[user.id] = "awaiting_remove"
        await q.edit_message_text(
            "🟢 Выбрана категория «🗑 Вынести из листа».\n"
            "Кого удаляем? Отправь @username или ID:",
            reply_markup=kb_back()
        )

    # ── Проверить юзера ──
    elif data == "menu_check":
        user_states[user.id] = "awaiting_check"
        await q.edit_message_text(
            "🔍 Напиши @username или ID человека, которого подозреваешь:",
            reply_markup=kb_back()
        )

    # ── Подключить бота ──
    elif data == "menu_connect":
        await q.edit_message_text(
            "🤖 Инструкция по подключению:\n\n"
            "1. Перейди в @BotFather\n"
            "2. Выбери /mybots → твой бот → Bot Settings → Allow Groups\n"
            "3. Добавь бота в нужный чат как администратора\n\n"
            "После этого бот будет работать в группе.",
            reply_markup=kb_back()
        )

    # ── ✅ Понял (прекратить спам) ──
    elif data == "alert_understood":
        # Останавливаем спам-задачу для этого пользователя
        key = user.id
        if key in spam_tasks:
            spam_tasks[key].cancel()
            del spam_tasks[key]
        await q.edit_message_text("✅ Принято. Будь осторожен!")


# ─── ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ ──────────────────────────
async def message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()
    state = user_states.get(user.id)

    if not state:
        # Если нет состояния — показываем меню или подсказку
        await update.message.reply_text(
            "👋 Используй меню ниже:",
            reply_markup=kb_main()
        )
        return

    # ── Добавить мошенника ──
    if state == "awaiting_add":
        user_states.pop(user.id, None)
        username = None
        uid = None

        if text.startswith("@"):
            username = text.lstrip("@").lower()
        elif text.isdigit():
            uid = text
        else:
            username = text.lower().lstrip("@")

        added = db_add(username=username, uid=uid, added_by=user.id)
        display = f"@{username}" if username else f"ID {uid}"

        if added:
            await update.message.reply_text(
                f"🔔 Уведомление!\n"
                f"✅ Успешно — добавлен в лист: {display}",
                reply_markup=kb_main()
            )
        else:
            await update.message.reply_text(
                f"⚠️ {display} уже есть в листе.",
                reply_markup=kb_main()
            )

    # ── Удалить из листа ──
    elif state == "awaiting_remove":
        user_states.pop(user.id, None)
        removed = db_remove(text)
        display = text if text.startswith("@") else f"ID {text}"
        if removed:
            await update.message.reply_text(
                f"✅ {display} удалён из листа.",
                reply_markup=kb_main()
            )
        else:
            await update.message.reply_text(
                f"❌ {display} не найден в листе.",
                reply_markup=kb_main()
            )

    # ── Проверить пользователя ──
    elif state == "awaiting_check":
        user_states.pop(user.id, None)
        username = None
        uid = None

        if text.startswith("@"):
            username = text.lstrip("@").lower()
        elif text.isdigit():
            uid = text
        else:
            username = text.lower().lstrip("@")

        row = db_check(username=username, uid=uid)

        if row:
            db_username, db_uid = row
            if db_username:
                display = f"ЮЗ @{db_username}"
            else:
                display = f"АЙДИ {db_uid}"

            device = get_device(user.id) or "iphone"
            link = profile_link(db_username, db_uid, device)
            link_text = f"\n🔗 {link}" if link else ""

            await update.message.reply_text(
                f"☢️ МОШЕННИК ПОД {display}\n"
                f"Есть в нашем листе! Блокируй — не трать время ☢️"
                f"{link_text}",
                reply_markup=kb_main()
            )
        else:
            await update.message.reply_text(
                "✅ Чисто. В нашем листе отсутствует.",
                reply_markup=kb_main()
            )

    else:
        user_states.pop(user.id, None)
        await update.message.reply_text(
            "Используй меню:", reply_markup=kb_main()
        )


# ─── ПРОВЕРКА НОВЫХ УЧАСТНИКОВ/СООБЩЕНИЙ В ГРУППАХ ───────────
async def check_new_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Срабатывает на любое сообщение в группе.
    Проверяет отправителя по базе.
    """
    user = update.effective_user
    if not user:
        return

    row = db_check(username=user.username, uid=str(user.id))
    if not row:
        return

    display = scammer_display(row)
    chat = update.effective_chat

    # Уведомляем админа
    async def send_alert():
        text = (
            f"🚨 Тебе написал мошенник!\n"
            f"☢️ {display} — есть в листе!\n"
            f"Чат: {chat.title or 'личка'}\n"
            f"Блокируй, не трать время!"
        )
        while True:
            try:
                msg = await ctx.bot.send_message(
                    ADMIN_ID, text, reply_markup=kb_understood()
                )
                alert_messages[ADMIN_ID] = msg.message_id
            except Exception:
                pass
            await asyncio.sleep(60)

    # Отменяем предыдущий спам если был
    if ADMIN_ID in spam_tasks:
        spam_tasks[ADMIN_ID].cancel()

    task = asyncio.create_task(send_alert())
    spam_tasks[ADMIN_ID] = task


# ─── ЗАПУСК ───────────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Личные сообщения
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        message_handler
    ))

    # Групповые сообщения — проверка по базе
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
        check_new_message
    ))

    print("✅ Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
