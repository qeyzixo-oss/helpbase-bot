import logging
import asyncio
import sqlite3
import os
import re
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    MessageEntity, ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)


# ─── ПРЕМ ЭМОДЗИ ──────────────────────────────────────────────
def build_msg(parts: list) -> tuple:
    """
    parts — список строк или (emoji_id, placeholder).
    Возвращает (text, entities) для отправки с прем эмодзи.
    """
    text = ""
    entities = []
    for p in parts:
        if isinstance(p, str):
            text += p
        else:
            emoji_id, placeholder = p
            entities.append(MessageEntity(
                type=MessageEntity.CUSTOM_EMOJI,
                offset=len(text),
                length=len(placeholder),
                custom_emoji_id=emoji_id,
            ))
            text += placeholder
    return text, entities


# Emoji ID константы (emoji_id, placeholder)
E_PC      = ("5330483571763203568", "💻")
E_IPHONE  = ("5220087772496280642", "🍏")
E_STAR    = ("5463215554611405905", "⭐️")
E_ANDROID = ("6048857619848761040", "👽")
E_DOWN    = ("5301038027601098171", "👇")
E_GREEN   = ("5339112148175959615", "🟢")
E_DOC     = ("5370604433233177619", "📄")
E_INBOX   = ("5879884569812931912", "📥")
E_BELL    = ("5404516769152916225", "🔔")
E_CHECK   = ("5237699328843200968", "✅")
E_GEAR    = ("5974104203688152439", "⚙️")
E_CROWN   = ("6129805886383723340", "👑")

# ─── НАСТРОЙКИ ────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8049793687:AAHWbzhlKMZH4P1btZ5qNNcMtDR1jdus4OU")
ADMIN_ID   = int(os.environ.get("ADMIN_ID", "8658447894"))
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
            InlineKeyboardButton("👽 Android", callback_data="device_android"),
        ]
    ])


def kb_main(is_admin=False):
    buttons = []
    if is_admin:
        buttons.append([InlineKeyboardButton("🗂 Внести в лист",    callback_data="menu_add")])
        buttons.append([InlineKeyboardButton("🗑 Вынести из листа", callback_data="menu_remove")])
    buttons.append([InlineKeyboardButton("🔍 Проверить юзера",  callback_data="menu_check")])
    buttons.append([InlineKeyboardButton("📩 Предложить юзера", callback_data="menu_suggest")])
    buttons.append([InlineKeyboardButton("🤖 Подключить бота",  callback_data="menu_connect")])
    buttons.append([InlineKeyboardButton("📱 Тип устройства",   callback_data="menu_device")])
    return InlineKeyboardMarkup(buttons)


def kb_reply():
    """Нижняя кнопка под полем ввода."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("👤 Проверить пользователя")]],
        resize_keyboard=True,
        one_time_keyboard=False
    )


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
    is_admin = user.id == ADMIN_ID
    if device:
        await update.message.reply_text(
            "👋 Главное меню. Выбирай нужную кнопку.",
            reply_markup=kb_main(is_admin)
        )
        # Показываем нижнюю кнопку
        await update.message.reply_text(
            "👇 Или просто отправь @username для быстрой проверки",
            reply_markup=kb_reply()
        )
    else:
        text, entities = build_msg([
            E_PC, " Какой у тебя смартфон?\n\n",
            E_DOWN, " Выбери нужную кнопку",
        ])
        await update.message.reply_text(
            text=text, entities=entities, reply_markup=kb_device()
        )


# ─── CALLBACK ОБРАБОТЧИК ──────────────────────────────────────
async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    data = q.data

    is_admin = user.id == ADMIN_ID

    # ── Выбор устройства ──
    if data.startswith("device_"):
        device = "iphone" if data == "device_iphone" else "android"
        save_device(user.id, device)
        user_devices[user.id] = device
        label = "🍏 iPhone" if device == "iphone" else "👽 Android"
        await q.edit_message_text(
            f"✅ Устройство сохранено: {label}\n\n"
            "👋 Главное меню. Выбирай нужную кнопку.",
            reply_markup=kb_main(is_admin)
        )
        await ctx.bot.send_message(
            user.id,
            "👇 Или просто отправь @username для быстрой проверки",
            reply_markup=kb_reply()
        )

    # ── Меню назад ──
    elif data == "menu_back":
        await q.edit_message_text(
            "👋 Главное меню. Выбирай нужную кнопку.",
            reply_markup=kb_main(is_admin)
        )

    # ── Предложить юзера (для всех) ──
    elif data == "menu_suggest":
        user_states[user.id] = "awaiting_suggest"
        await q.edit_message_text(
            "📩 Отправь @username того, кого хочешь предложить в лист.\n"
            "Я проверю и добавлю если нужно.",
            reply_markup=kb_back()
        )

    # ── Смена устройства ──
    elif data == "menu_device":
        text, entities = build_msg([
            E_PC, " Выбери тип устройства:",
        ])
        await q.edit_message_text(
            text=text, entities=entities, reply_markup=kb_device()
        )

    # ── Внести в лист ──
    elif data == "menu_add":
        user_states[user.id] = "awaiting_add"
        text, entities = build_msg([
            E_GREEN, " Выбрана категория «", E_DOC, " Внести в лист».\n"
            "Отправь @username или числовой ID мошенника:",
        ])
        await q.edit_message_text(
            text=text, entities=entities, reply_markup=kb_back()
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
        text, entities = build_msg([
            E_GREEN, " Выбрана категория «🗑 Вынести из листа».\n"
            "Кого удаляем? Отправь @username или ID:",
        ])
        await q.edit_message_text(
            text=text, entities=entities, reply_markup=kb_back()
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
        # Нажата нижняя кнопка "Проверить пользователя"
        if text == "👤 Проверить пользователя":
            user_states[user.id] = "awaiting_check"
            await update.message.reply_text(
                "🔍 Напиши @username или ID человека, которого подозреваешь:",
                reply_markup=kb_back()
            )
            return

        # Если текст начинается с @ — автопроверка
        if text.startswith("@"):
            username = text.lstrip("@").lower()
            row = db_check(username=username)
            if row:
                db_username, db_uid = row
                display = f"ЮЗ @{db_username}" if db_username else f"АЙДИ {db_uid}"
                device = get_device(user.id) or "iphone"
                link = profile_link(db_username, db_uid, device)
                link_text = f"\n🔗 {link}" if link else ""
                await update.message.reply_text(
                    f"☢️ МОШЕННИК ПОД {display}\n"
                    f"Есть в нашем листе! Блокируй — не трать время ☢️"
                    f"{link_text}",
                    reply_markup=kb_main(user.id == ADMIN_ID)
                )
            else:
                await update.message.reply_text(
                    f"✅ @{username} — чисто. В нашем листе отсутствует.",
                    reply_markup=kb_main(user.id == ADMIN_ID)
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

        # Пытаемся получить числовой ID через Telegram
        resolved_uid = uid
        if username and not uid:
            try:
                chat_info = await ctx.bot.get_chat(f"@{username}")
                resolved_uid = str(chat_info.id)
            except Exception:
                resolved_uid = None

        added = db_add(username=username, uid=resolved_uid, added_by=user.id)
        display = f"@{username}" if username else f"ID {uid}"
        id_info = f" (ID: {resolved_uid})" if resolved_uid else ""

        if added:
            # Если добавил не админ — уведомляем админа
            if user.id != ADMIN_ID:
                await ctx.bot.send_message(
                    ADMIN_ID,
                    f"📩 @{user.username or user.id} добавил в лист:\n"
                    f"{display}{id_info}"
                )
            text_msg, entities = build_msg([
                E_BELL, " Уведомления! ",
                E_CHECK, f" Успешно — добавлен в лист: {display}{id_info}",
            ])
            await update.message.reply_text(
                text=text_msg, entities=entities, reply_markup=kb_main(user.id == ADMIN_ID)
            )
        else:
            await update.message.reply_text(
                f"⚠️ {display} уже есть в листе.",
                reply_markup=kb_main(user.id == ADMIN_ID)
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

    # ── Предложить юзера (все пользователи) ──
    elif state == "awaiting_suggest":
        user_states.pop(user.id, None)
        suggested = text.lstrip("@").lower()
        display = f"@{suggested}"

        # Уведомляем админа
        await ctx.bot.send_message(
            ADMIN_ID,
            f"📩 Пользователь @{user.username or user.id} предлагает добавить в лист:\n"
            f"{display}\n\n"
            f"Чтобы добавить — нажми «Внести в лист» и введи этот юз.",
        )
        await update.message.reply_text(
            f"✅ Отправлено! Я проверю и добавлю если нужно.",
            reply_markup=kb_main(user.id == ADMIN_ID)
        )

    else:
        user_states.pop(user.id, None)


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


# ─── ЧТЕНИЕ ИЗ КАНАЛА ────────────────────────────────────────
CHANNEL_USERNAME = "ListKon4enixEblanov"

async def channel_post_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Читает посты из канала @ListKon4enixEblanov.
    Если пост содержит @username — автоматически добавляет в базу.
    """
    msg = update.channel_post
    if not msg or not msg.text:
        return

    # Проверяем что это наш канал
    chat = msg.chat
    if chat.username and chat.username.lower() != CHANNEL_USERNAME.lower():
        return

    text = msg.text.strip()
    added_users = []

    # Ищем все @username в тексте
    import re
    usernames = re.findall(r'@(\w+)', text)

    for username in usernames:
        if len(username) < 3:
            continue
        added = db_add(username=username.lower(), added_by=0)
        if added:
            added_users.append(f"@{username}")

    # Уведомляем админа
    if added_users:
        names = ", ".join(added_users)
        await ctx.bot.send_message(
            ADMIN_ID,
            f"📥 Из канала добавлены в лист:\n{names}"
        )


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

    # Посты из канала — автодобавление в базу
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.CHANNEL,
        channel_post_handler
    ))

    print("✅ Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
