import sqlite3
import json
import telebot
from telebot import types
from urllib.parse import quote
import requests
import time
import threading
import random
from enum import Enum
import sys
import io
import os

# Устанавливаем UTF-8 кодировку для вывода в Windows
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass  # IDE перехватывает stdout, пропускаем

import os

BOT_TOKEN = os.environ.get('BOT_TOKEN', '').strip()
print(f"🔑 Токен (первые 10 символов): {BOT_TOKEN[:10]}...")

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN не найден в переменных окружения!")
ADMIN_IDS = [8764234910]
LINK_COOLDOWN = 300      # Задержка на генерацию ссылок (в секундах, 300 = 5 минут)
SECRET_CHANNEL_LINK = "https://t.me/+KRsICQIFXV01Yzkx"
LOOTBOX_COOLDOWN = 60  # 60 секунд между открытиями кейса
SECRET_CHANNEL_ID = -1003560284356
bot = telebot.TeleBot(BOT_TOKEN)

# Состояния последовательного onboarding
FUNNEL_LANGUAGE = 1      # Выбор языка
FUNNEL_WELCOME = 2      # Приветствие
FUNNEL_BONUS = 3        # Ежедневный бонус
FUNNEL_SUBSCRIPTION = 4  # Подписка на каналы
FUNNEL_MENU = 5         # Главное меню

# ================= FSM STATE MANAGEMENT =================
class FSMState(Enum):
    CHOOSING_LANGUAGE = "choosing_language"
    ONBOARDING_COMPLETE = "onboarding_complete"

# Хранилище состояний пользователей (in-memory FSM)
fsm_states = {}

def set_fsm_state(user_id, state):
    """Устанавливает состояние FSM для пользователя"""
    fsm_states[user_id] = state
    print(f"🔄 FSM: User {user_id} state set to {state}")

def get_fsm_state(user_id):
    """Получает текущее состояние FSM пользователя"""
    return fsm_states.get(user_id)

def clear_fsm_state(user_id):
    """Очищает состояние FSM пользователя"""
    if user_id in fsm_states:
        del fsm_states[user_id]
        print(f"🗑️ FSM: User {user_id} state cleared")

# Хранилище активных reengage-таймеров
active_reengage_timers = {}

def reengage_user(user_id):
    time.sleep(600)  # 10 минут

    try:
        # ✅ Проверяем, что таймер всё ещё активен
        if user_id not in active_reengage_timers:
            return

        # ✅ Удаляем из активных после выполнения
        del active_reengage_timers[user_id]

        user = ensure_user(user_id)

        if len(user['referrals']) < 3:
            bot.send_message(
                user_id,
                "🎰 Ты не докрутил кейс...\n\n"
                "🔥 Вернись — там сейчас больше шансов"
            )
    except Exception:
        pass

def start_reengage(user_id):
    # ✅ Если уже есть активный таймер - не запускаем новый
    if user_id in active_reengage_timers:
        return

    # ✅ Добавляем в активные
    active_reengage_timers[user_id] = True

    threading.Thread(
        target=reengage_user,
        args=(user_id,),
        daemon=True
    ).start()


# ================= ПОСЛЕДОВАТЕЛЬНЫЙ ONBOARDING =================
def start_sequential_onboarding(user_id, source='start'):
    """Запускает последовательный onboarding для пользователя"""
    user = ensure_user(user_id)

    print(f"🚀 Запуск последовательного onboarding для пользователя {user_id} (источник: {source})")

    # Если пользователь уже прошел onboarding - показываем меню
    if user.get('funnel_step', 0) >= FUNNEL_MENU:
        print(f"✅ Пользователь {user_id} уже прошел onboarding, показываем меню")
        show_menu(user_id, user_id)
        return

    # Начинаем с выбора языка
    print(f"📝 Этап 1: Показываем выбор языка пользователю {user_id}")
    show_lang(user_id, user_id)
    update_user(user_id, funnel_step=FUNNEL_LANGUAGE, source=source)
    print(f"✅ Ожидаем выбора языка от пользователя {user_id}")


def process_onboarding_step(user_id, step):
    """Обрабатывает следующий шаг onboarding с проверкой подписки"""
    user = ensure_user(user_id)

    print(f"🔄 Обработка этапа {step} для пользователя {user_id}")

    if step == FUNNEL_LANGUAGE:
        # После выбора языка - приветствие
        print(f"📝 Этап 2: Отправляем приветствие пользователю {user_id}")
        send_welcome_message(user_id)
        update_user(user_id, funnel_step=FUNNEL_WELCOME)

    elif step == FUNNEL_WELCOME:
        # После приветствия - ежедневный бонус
        print(f"📝 Этап 3: Отправляем ежедневный бонус пользователю {user_id}")
        send_daily_bonus_message(user_id)
        update_user(user_id, funnel_step=FUNNEL_BONUS)

    elif step == FUNNEL_BONUS:
        # После бонуса - подписка на каналы
        print(f"📝 Этап 4: Отправляем каналы для подписки пользователю {user_id}")
        show_sub(user_id, user_id)
        update_user(user_id, funnel_step=FUNNEL_SUBSCRIPTION)

    elif step == FUNNEL_SUBSCRIPTION:
        # ✅ НЕ показываем главное меню здесь!
        # Главное меню показывается только после подтверждения подписки через callback
        print(f"📝 Этап 4 завершён: ожидаем подтверждения подписки от пользователя {user_id}")
        # Не обновляем funnel_step - остаём на FUNNEL_SUBSCRIPTION


def send_welcome_message(user_id):
    """Отправляет приветственное сообщение"""
    welcome_text = t(user_id, 'welcome')

    # ✅ Отправляем сообщение и ждём завершения
    bot.send_message(user_id, welcome_text, parse_mode='HTML')
    print(f"✅ Отправлено приветствие пользователю {user_id}")


def send_daily_bonus_message(user_id):
    """Отправляет сообщение о ежедневном бонусе"""
    user = ensure_user(user_id)
    lang = user.get('lang') or 'en'

    if lang == 'ru':
        msg = "🎁 Ежедневный бонус доступен!\n\nНажми кнопку для получения:"
        btn_text = "🎁 Получить бонус"
    elif lang == 'es':
        msg = "🎁 ¡Bono diario disponible!\n\nPresiona el botón para recibirlo:"
        btn_text = "🎁 Recibir bono"
    else:
        msg = "🎁 Daily bonus available!\n\nPress button to receive:"
        btn_text = "🎁 Get bonus"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(btn_text, callback_data='claim_daily_bonus'))

    # ✅ Отправляем сообщение и ждём завершения
    bot.send_message(user_id, msg, reply_markup=markup, parse_mode='HTML')
    print(f"✅ Отправлено сообщение о бонусе пользователю {user_id}")

def is_real_user(obj):
    """
    Проверяет, что это не бот
    """
    user = getattr(obj, 'from_user', None)
    return bool(user) and not user.is_bot

try:
    BOT_USERNAME = bot.get_me().username
except Exception:
    BOT_USERNAME = "my_bot"

def create_one_time_invite_link(chat_id, expire_seconds=86400):
    """
    Создаёт одноразовую ссылку-приглашение в чат.
    chat_id: ID чата (отрицательное число, например -1001234567890)
    expire_seconds: время жизни ссылки в секундах (по умолчанию 24 часа)
    Возвращает строку со ссылкой или None при ошибке.
    """
    url = "https://api.telegram.org/bot" + BOT_TOKEN + "/createChatInviteLink"
    payload = {
        "chat_id": chat_id,
        "member_limit": 1,
        "expire_date": int(time.time() + expire_seconds)
    }
    try:
        print(f"🔗 Создание ссылки для чата {chat_id}...")
        resp = requests.post(url, json=payload).json()
        print(f"📊 Ответ API: {resp}")

        if resp.get("ok"):
            link = resp["result"]["invite_link"]
            print(f"✅ Ссылка успешно создана: {link}")
            return link
        else:
            print(f"❌ Ошибка создания ссылки: {resp}")
            return None
    except Exception as e:
        print(f"❌ Исключение при создании ссылки: {e}")
        return None


def create_invite_link_for_user(chat_id, user_id):
    """Создаёт инвайт-ссылку для конкретного пользователя с запросом на вступление"""
    url = "https://api.telegram.org/bot" + BOT_TOKEN + "/createChatInviteLink"
    payload = {
        "chat_id": chat_id,
        "member_limit": 1,  # Одноразовая ссылка
        "expire_date": int(time.time() + 86400),  # 24 часа
        "creates_join_request": True  # Создаёт запрос на вступление
    }
    try:
        print(f"🔗 Создание инвайт-ссылки с запросом для пользователя {user_id} в канал {chat_id}...")
        resp = requests.post(url, json=payload).json()
        print(f"📊 Ответ API: {resp}")

        if resp.get("ok"):
            link = resp["result"]["invite_link"]
            print(f"✅ Ссылка успешно создана: {link}")
            return link
        else:
            print(f"❌ Ошибка создания ссылки: {resp}")
            return None
    except Exception as e:
        print(f"❌ Исключение при создании ссылки: {e}")
        return None
    """
    Создаёт одноразовую ссылку-приглашение в чат.
    chat_id: ID чата (отрицательное число, например -1001234567890)
    expire_seconds: время жизни ссылки в секундах (по умолчанию 24 часа)
    Возвращает строку со ссылкой или None при ошибке.
    """
    url = "https://api.telegram.org/bot" + BOT_TOKEN + "/createChatInviteLink"
    payload = {
        "chat_id": chat_id,
        "member_limit": 1,
        "expire_date": int(time.time() + expire_seconds)
    }
    try:
        print(f"🔗 Создание ссылки для чата {chat_id}...")
        resp = requests.post(url, json=payload).json()
        print(f"📊 Ответ API: {resp}")

        if resp.get("ok"):
            link = resp["result"]["invite_link"]
            print(f"✅ Ссылка успешно создана: {link}")
            return link
        else:
            print(f"❌ Ошибка создания ссылки: {resp}")
            return None
    except Exception as e:
        print(f"❌ Исключение при создании ссылки: {e}")
        return None

# ================= DATABASE (SQLite) =================
def init_db():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        lang TEXT,
        ref_by INTEGER,
        referrals TEXT,
        bonus INTEGER DEFAULT 0,
        reward_given INTEGER DEFAULT 0,
        subscribed INTEGER DEFAULT 0,
        last_link_time REAL DEFAULT 0,
        created_at REAL DEFAULT 0,
        ref_count_today INTEGER DEFAULT 0,
        last_ref_time REAL DEFAULT 0,
        secret_unlocked INTEGER DEFAULT 0,
        paid_until REAL DEFAULT 0,
        paid_forever INTEGER DEFAULT 0,
        is_clean_start INTEGER DEFAULT 0,
        source TEXT DEFAULT 'telegram',
        funnel_step INTEGER DEFAULT 0
    )
    ''')

    # Миграция: добавляем колонки, если их нет
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN is_clean_start INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # Колонка уже существует

    try:
        cursor.execute("ALTER TABLE users ADD COLUMN source TEXT DEFAULT 'telegram'")
    except sqlite3.OperationalError:
        pass  # Колонка уже существует

    try:
        cursor.execute("ALTER TABLE users ADD COLUMN funnel_step INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # Колонка уже существует

    # Таблица подписок
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        granted_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        active INTEGER DEFAULT 1,
        FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    conn.commit()
    conn.close()
    
def migrate_db():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()

    # ✅ Проверяем, существует ли поле last_daily
    cursor.execute("PRAGMA table_info(users)")
    columns_info = cursor.fetchall()
    column_names = [col[1] for col in columns_info]

    print(f"[DB] Current columns in users table: {column_names}")

    if 'last_daily' not in column_names:
        print("[DB] last_daily field not found, adding...")
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN last_daily REAL DEFAULT 0")
            print("[DB] last_daily field successfully added")
        except sqlite3.OperationalError as e:
            print(f"[DB] Error adding last_daily field: {e}")
    else:
        print("[DB] last_daily field already exists")

    columns = [
        ("created_at", "REAL DEFAULT 0"),
        ("ref_count_today", "INTEGER DEFAULT 0"),
        ("last_ref_time", "REAL DEFAULT 0"),
        ("secret_unlocked", "INTEGER DEFAULT 0"),
        ("paid_until", "REAL DEFAULT 0"),
        ("paid_forever", "INTEGER DEFAULT 0"),
        ("source", "TEXT DEFAULT 'telegram'"),
        ("funnel_step", "INTEGER DEFAULT 0"),
        ("last_lootbox", "REAL DEFAULT 0"),
        ("lootbox_uses", "INTEGER DEFAULT 0"),
        ("lose_streak", "INTEGER DEFAULT 0"),
        ("is_clean_start", "INTEGER DEFAULT 0")
    ]

    for col_name, col_type in columns:
        if col_name not in column_names:
            try:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
                print(f"[DB] Добавлена колонка: {col_name}")
            except sqlite3.OperationalError:
                pass

    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()

    # ✅ Получаем информацию о колонках
    cursor.execute("PRAGMA table_info(users)")
    columns_info = cursor.fetchall()
    column_names = [col[1] for col in columns_info]

    print(f"📊 Структура таблицы users: {column_names}")

    # ✅ Находим индекс поля last_daily
    try:
        last_daily_index = column_names.index('last_daily')
        print(f"✅ Индекс поля last_daily: {last_daily_index}")
    except ValueError:
        print(f"❌ Поле last_daily не найдено в таблице!")
        last_daily_index = None

    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()

    def safe_get(row, index, default=None):
        return row[index] if len(row) > index else default

    if row:
        user_data = {
            'id': row[0],
            'lang': row[1],
            'ref_by': row[2],
            'referrals': json.loads(row[3]) if row[3] else [],
            'bonus': row[4],
            'reward_given': bool(row[5]),
            'subscribed': bool(row[6]),
            'last_link_time': row[7],
            'created_at': safe_get(row, 8, 0),
            'ref_count_today': safe_get(row, 9, 0),
            'last_ref_time': safe_get(row, 10, 0),
            'secret_unlocked': safe_get(row, 11, 0),
            'source': safe_get(row, 14, 'telegram'),
            'funnel_step': safe_get(row, 15, 0),
            'is_clean_start': safe_get(row, 16, 0),
            'last_daily': safe_get(row, last_daily_index if last_daily_index is not None else 17, 0),
            'last_lootbox': safe_get(row, 18, 0),
            'lootbox_uses': safe_get(row, 19, 0),
            'lose_streak': safe_get(row, 20, 0),
        }

        # ✅ Прямая проверка в базе данных
        conn_check = sqlite3.connect('bot_database.db')
        cursor_check = conn_check.cursor()
        cursor_check.execute("SELECT last_daily, bonus FROM users WHERE id = ?", (user_id,))
        result_check = cursor_check.fetchone()
        conn_check.close()

        if result_check:
            db_last_daily, db_bonus = result_check
            print(f"🔍 get_user для {user_id}:")
            print(f"   Из функции: last_daily={user_data['last_daily']}, bonus={user_data['bonus']}")
            print(f"   Из базы напрямую: last_daily={db_last_daily}, bonus={db_bonus}")

            if user_data['last_daily'] != db_last_daily:
                print(f"   ❌ РАСХОЖДЕНИЕ: last_daily не совпадает! Используем значение из базы.")
                user_data['last_daily'] = db_last_daily
            if user_data['bonus'] != db_bonus:
                print(f"   ❌ РАСХОЖДЕНИЕ: bonus не совпадает! Используем значение из базы.")
                user_data['bonus'] = db_bonus

        return user_data
    return None

def update_user(user_id, **kwargs):
    # ✅ Используем WAL режим для лучшей совместимости
    conn = sqlite3.connect('bot_database.db', timeout=30.0)
    cursor = conn.cursor()

    # ✅ Включаем WAL режим
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
    except:
        pass  # Игнорируем ошибки WAL режима

    # ✅ Начинаем транзакцию
    try:
        for key, value in kwargs.items():
            if isinstance(value, (list, dict)):
                value = json.dumps(value)
            elif isinstance(value, bool):
                value = int(value)
            cursor.execute(f"UPDATE users SET {key} = ? WHERE id = ?", (value, user_id))

        conn.commit()
        print(f"✅ Обновлён пользователь {user_id}: {list(kwargs.keys())}")

        # ✅ Проверяем, что изменения сохранились
        if 'last_daily' in kwargs:
            cursor.execute("SELECT last_daily FROM users WHERE id = ?", (user_id,))
            saved_value = cursor.fetchone()
            if saved_value:
                print(f"   Проверка: last_daily в базе = {saved_value[0]}, должно быть = {kwargs['last_daily']}")
                if saved_value[0] != kwargs['last_daily']:
                    print(f"   ❌ ОШИБКА: Значение не сохранилось!")

    except Exception as e:
        conn.rollback()
        print(f"❌ Ошибка обновления пользователя {user_id}: {e}")
        raise
    finally:
        conn.close()

init_db()
migrate_db()

# ✅ Проверка целостности базы данных
def check_db_integrity():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()

    # Проверяем, есть ли пользователи с last_daily = 0 но с бонусами
    cursor.execute("SELECT id, bonus, last_daily FROM users WHERE bonus > 0 AND last_daily = 0")
    users = cursor.fetchall()

    if users:
        print(f"⚠️ Найдено {len(users)} пользователей с бонусами но без last_daily:")
        for user_id, bonus, last_daily in users:
            print(f"   Пользователь {user_id}: бонус={bonus}, last_daily={last_daily}")

    conn.close()

check_db_integrity()

# ================= ADMIN PANEL =================
@bot.message_handler(commands=['test_channels'])
def test_channels(message):
    """Тестовая команда для проверки работы с каналами"""
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Только для администраторов")
        return

    user_id = message.from_user.id

    # Показываем информацию о каналах
    hub_channel_ids = [ch.get('chat_id') for ch in HUB_CHANNELS[:2] if 'chat_id' in ch]

    msg = f"🧪 Тестирование каналов:\n\n"
    msg += f"📋 Хаб-каналы: {hub_channel_ids}\n"
    msg += f"🔒 Секретный канал: {SECRET_CHANNEL_ID}\n\n"
    msg += f"ℹ️ Отправьте запрос на вступление в любой канал\n"
    msg += f"ℹ️ Бот автоматически обработает его"

    bot.reply_to(message, msg)

    # Проверяем права бота
    for chat_id in hub_channel_ids:
        try:
            bot_info = bot.get_me()
            bot_member = bot.get_chat_member(chat_id, bot_info.id)
            status_msg = f"✅ Бот админ в канале {chat_id}: {bot_member.status}"
            bot.reply_to(message, status_msg)
        except Exception as e:
            error_msg = f"❌ Ошибка проверки канала {chat_id}: {e}"
            bot.reply_to(message, error_msg)


@bot.message_handler(commands=['test_subscriptions'])
def test_subscriptions(message):
    """Тестовая команда для проверки системы подписок"""
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Только для администраторов")
        return

    user_id = message.from_user.id

    # Показываем активные подписки
    active_subs = get_active_subscriptions(user_id)

    msg = f"🧪 Тестирование подписок для пользователя {user_id}:\n\n"

    if active_subs:
        msg += f"✅ Активные подписки: {len(active_subs)}\n\n"
        for sub in active_subs:
            expires = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(sub['expires_at']))
            msg += f"• Тип: {sub['type']}\n"
            msg += f"  Истекает: {expires}\n"
            msg += f"  Канал: {sub['channel_id']}\n\n"
    else:
        msg += "❌ Нет активных подписок\n\n"

    # Проверяем доступ
    has_access = has_active_subscription(user_id)
    msg += f"🔒 Доступ к секретному каналу: {'✅ Есть' if has_access else '❌ Нет'}"

    bot.reply_to(message, msg)


@bot.message_handler(commands=['grant_bonus'])
def grant_bonus_sub(message):
    """Тестовая команда для выдачи бонусной подписки"""
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Только для администраторов")
        return

    user_id = message.from_user.id

    success, msg = grant_bonus_subscription(user_id)
    bot.reply_to(message, msg)


@bot.message_handler(commands=['grant_referral'])
def grant_referral_sub(message):
    """Тестовая команда для выдачи реферальной подписки"""
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Только для администраторов")
        return

    user_id = message.from_user.id

    success, msg = grant_referral_subscription(user_id)
    bot.reply_to(message, msg)


@bot.message_handler(commands=['check_expired'])
def check_expired_manual(message):
    """Тестовая команда для ручной проверки истекших подписок"""
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Только для администраторов")
        return

    bot.reply_to(message, "🔍 Проверка истекших подписок...")
    check_and_remove_expired_subscriptions()
    bot.reply_to(message, "✅ Проверка завершена")
def admin_panel(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "❌ У вас нет прав доступа к админ-панели.")
        return
        
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📊 Статистика", callback_data="adm_stats"),
        types.InlineKeyboardButton("📢 Рассылка", callback_data="adm_broadcast"),
        types.InlineKeyboardButton("👥 Список пользователей", callback_data="adm_users")
    )
    bot.send_message(message.chat.id, "👑 Админ-панель\nВыберите действие:", reply_markup=markup, parse_mode='HTML')

@bot.callback_query_handler(func=lambda call: call.data.startswith('adm_'))
def admin_callback(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "Нет прав!", show_alert=True)
        return
        
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    if call.data == 'adm_stats':
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT SUM(bonus) FROM users")
        total_bonuses = cursor.fetchone()[0] or 0
        
        text = f"📊 Статистика бота:\n\n👥 Всего пользователей: {total_users}\n💎 Всего бонусов у игроков: {total_bonuses}"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='HTML')
        
    elif call.data == 'adm_broadcast':
        msg = bot.send_message(call.message.chat.id, "Введите сообщение для рассылки всем пользователям:")
        bot.register_next_step_handler(msg, process_broadcast)
        
    elif call.data == 'adm_users':
        cursor.execute("SELECT id, lang, bonus FROM users LIMIT 20")
        users_list = cursor.fetchall()
        text = "👥 Последние пользователи (до 20 шт):\n\n"
        for u in users_list:
            text += f"• ID: {u[0]} | Язык: {u[1] or 'Нет'} | Бонусы: {u[2]}\n"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='HTML')
        
    conn.close()

def process_broadcast(message):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users")
    users = cursor.fetchall()
    conn.close()
    
    success = 0
    for u in users:
        try:
            bot.send_message(u[0], message.text)
            success += 1
        except Exception:
            pass
            
    bot.send_message(message.chat.id, f"✅ Рассылка завершена!\nУспешно отправлено: {success}/{len(users)}")

# ================= CONFIG =================
# Каналы ОБЯЗАТЕЛЬНОЙ подписки
MANDATORY_CHANNELS = [
    {
        'id': -1003775482230,
        'link': 'https://t.me/+M0BltRQLobA4MmYx',
        'name': {
            'en': '🔥 Upgrade appearance',
            'es': '🔥 Mejorar apariencia',
            'ru': '🔥 Прокачай внешку',
            'hi': '🔥 अपना लुक अपग्रेड करो',
            'id': '🔥 Tingkatkan penampilan'
        }
    },
    {
        'id': -1003782226692,
        'link': 'https://t.me/+FyWYIZfzzysyNzg5',
        'name': {
            'en': '🤑 Threads',
            'es': '🤑 Threads',
            'ru': '🤑 Темки',
            'hi': '🤑 थ्रेड्स',
            'id': '🤑 Threads'
        }
    }
]

# Каналы хаба (показываются в главном меню)
HUB_CHANNELS = [
    {
        'link': 'https://t.me/+9pKhPtLW0-0zYTE5',
        'chat_id': -1003842551362,  # ✅ Добавь сюда реальный ID канала SD_FETISH_HUB
        'name': {
            'en': '🔞 SD_FETISH_HUB 🔞',
            'es': '🔞 SD_FETISH_HUB 🔞',
            'ru': '🔞 SD_FETISH_HUB 🔞',
            'hi': '🔞 अपना लुक अपग्रेड करो 🔞',
            'id': '🔞 Tingkatkan penampilan 🔞'
        }
    },
    {
        'link': 'https://t.me/+7X6BGlfykrI3MGVh',
        'chat_id': -1003603436010,  # ✅ Добавь сюда реальный ID канала MUSIC
        'name': {
            'en': '🎧 MUSIC 🎧',
            'es': '🎧 MÚSICA 🎧',
            'ru': '🎧 музыка 🎧',
            'hi': '🎧 थ्रेड्स 🎧',
            'id': '🎧 Threads 🎧'
        }
    },
    # ... другие каналы с URL ...
    {
        'type': 'chat',                 # помечаем, что это чат
        'chat_id': -1003752884922,      # ID твоего приватного чата (обязательно отрицательный)
        'name': {
            'en': '💬 Chat',
            'es': '💬 Chat',
            'ru': '💬 Чат',
            'hi': '💬 चैट',
            'id': '💬 Chat'
        }
    },

    # Добавь сюда свои остальные каналы хаба:
    # {
    #     'link': 'https://t.me/your_channel',
    #     'chat_id': -1001234567892,  # ID канала
    #     'name': {
    #         'en': '📚 Channel name',
    #         'es': '📚 Nombre del canal',
    #         'ru': '📚 Название канала',
    #         'hi': '📚 चैनल का नाम',
    #         'id': '📚 Nama channel'
    #     }
    # },
]

# ✅ Проверка настройки каналов при запуске
def check_hub_channels_setup():
    print("🔍 Проверка настройки хаб-каналов...")

    target_channels = HUB_CHANNELS[:2]  # SD_FETISH_HUB и MUSIC

    for ch in target_channels:
        if 'chat_id' in ch and ch['chat_id']:
            chat_id = ch['chat_id']
            print(f"📋 Проверка канала {chat_id}...")

            try:
                # Проверяем информацию о канале
                chat_info = bot.get_chat(chat_id)
                print(f"✅ Канал найден: {chat_info.title}")
                print(f"🔒 Тип канала: {'Приватный' if chat_info.type == 'channel' else 'Публичный'}")

                # Проверяем права бота
                bot_info = bot.get_me()
                bot_member = bot.get_chat_member(chat_id, bot_info.id)
                print(f"👤 Статус бота: {bot_member.status}")

                if bot_member.status not in ['administrator', 'creator']:
                    print(f"❌ Бот НЕ является администратором канала {chat_id}!")
                    print(f"⚠️ Добавьте бота как администратора в канал {chat_info.title}")
                    print(f"⚠️ Без прав администратора бот не сможет одобрять заявки!")
                else:
                    print(f"✅ Бот является администратором канала {chat_id}")
                    print(f"✅ Автоодобрение заявок будет работать")

            except Exception as e:
                print(f"❌ Ошибка проверки канала {chat_id}: {e}")
                print(f"⚠️ Убедитесь, что ID канала {chat_id} правильный и бот добавлен в канал")
        else:
            print(f"⚠️ Канал не имеет chat_id: {ch}")

    print("✅ Настройка хаб-каналов завершена")
    print("ℹ️ Для приватных каналов используется автоматическое одобрение заявок")
    print("ℹ️ Обработчик заявок будет перехватывать все запросы на вступление")

check_hub_channels_setup()

# ================= СИСТЕМА ПОДПИСОК =================
def add_subscription(user_id, sub_type, duration_days, channel_id=SECRET_CHANNEL_ID):
    """Добавляет подписку пользователю"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()

    now = time.time()
    expires_at = now + (duration_days * 86400)  # Конвертируем дни в секунды

    cursor.execute('''
        INSERT INTO subscriptions (user_id, type, channel_id, granted_at, expires_at, active)
        VALUES (?, ?, ?, ?, ?, 1)
    ''', (user_id, sub_type, str(channel_id), now, expires_at))

    sub_id = cursor.lastrowid
    conn.commit()
    conn.close()

    print(f"✅ Добавлена подписка {sub_type} для пользователя {user_id} до {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expires_at))}")
    return sub_id


def get_active_subscriptions(user_id, channel_id=SECRET_CHANNEL_ID):
    """Получает активные подписки пользователя"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()

    now = time.time()
    cursor.execute('''
        SELECT id, type, channel_id, granted_at, expires_at, active
        FROM subscriptions
        WHERE user_id = ? AND channel_id = ? AND active = 1 AND expires_at > ?
        ORDER BY expires_at DESC
    ''', (user_id, str(channel_id), now))

    rows = cursor.fetchall()
    conn.close()

    subscriptions = []
    for row in rows:
        subscriptions.append({
            'id': row[0],
            'type': row[1],
            'channel_id': row[2],
            'granted_at': row[3],
            'expires_at': row[4],
            'active': bool(row[5])
        })

    return subscriptions


def get_max_expiration(user_id, channel_id=SECRET_CHANNEL_ID):
    """Получает максимальную дату истечения активных подписок"""
    subscriptions = get_active_subscriptions(user_id, channel_id)

    if not subscriptions:
        return 0

    return max(sub['expires_at'] for sub in subscriptions)


def has_active_subscription(user_id, channel_id=SECRET_CHANNEL_ID):
    """Проверяет, есть ли у пользователя активная подписка"""
    max_expiration = get_max_expiration(user_id, channel_id)
    now = time.time()
    return max_expiration > now


def deactivate_subscription(sub_id):
    """Деактивирует подписку"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()

    cursor.execute('UPDATE subscriptions SET active = 0 WHERE id = ?', (sub_id,))

    conn.commit()
    conn.close()

    print(f"✅ Подписка {sub_id} деактивирована")


def deactivate_all_user_subscriptions(user_id, channel_id=SECRET_CHANNEL_ID):
    """Деактивирует все подписки пользователя"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()

    cursor.execute('''
        UPDATE subscriptions SET active = 0
        WHERE user_id = ? AND channel_id = ?
    ''', (user_id, str(channel_id)))

    conn.commit()
    conn.close()

    print(f"✅ Все подписки пользователя {user_id} деактивированы")


def kick_user_from_channel(user_id, channel_id):
    """Удаляет пользователя из канала (kick)"""
    try:
        # Уведомляем пользователя об истечении подписки
        user = get_user(user_id)
        lang = user.get('lang') or 'en' if user else 'en'

        msgs = {
            'ru': "⏰ Ваша подписка на SD FETISH GOLD истекла.\n\nДля продления выберите тариф в меню бота.",
            'en': "⏰ Your SD FETISH GOLD subscription has expired.\n\nTo renew, select a plan in the bot menu.",
            'es': "⏰ Tu suscripción a SD FETISH GOLD ha expirado.\n\nPara renovar, selecciona un plan en el menú del bot.",
            'hi': "⏰ आपकी SD FETISH GOLD सब्सक्रिप्शन समाप्त हो गई है।\n\nनवीनीकरण के लिए, बॉट मेनू में एक योजना चुनें।",
            'id': "⏰ Langganan SD FETISH GOLD Anda telah kedaluwarsa.\n\nUntuk memperbarui, pilih paket di menu bot."
        }

        try:
            bot.send_message(user_id, msgs.get(lang, msgs['en']))
            print(f"✅ Уведомление отправлено пользователю {user_id} об истечении подписки")
        except Exception as msg_error:
            print(f"⚠️ Не удалось отправить уведомление пользователю {user_id}: {msg_error}")

        # Сначала баним пользователя
        bot.ban_chat_member(channel_id, user_id)
        print(f"✅ Пользователь {user_id} забанен в канале {channel_id}")

        # Сразу разбанируем (kick)
        bot.unban_chat_member(channel_id, user_id)
        print(f"✅ Пользователь {user_id} разбанен в канале {channel_id} (kick выполнен)")

        return True
    except Exception as e:
        print(f"❌ Ошибка kick пользователя {user_id} из канала {channel_id}: {e}")
        return False


def check_and_remove_expired_subscriptions():
    """Проверяет и удаляет истекшие подписки каждые 10 минут"""
    print(f"🔍 Проверка истекших подписок... {time.strftime('%Y-%m-%d %H:%M:%S')}")

    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()

    now = time.time()

    # Находим истекшие подписки
    cursor.execute('''
        SELECT id, user_id, type, channel_id, expires_at
        FROM subscriptions
        WHERE active = 1 AND expires_at <= ?
        ORDER BY expires_at ASC
    ''', (now,))

    expired_subs = cursor.fetchall()
    conn.close()

    if not expired_subs:
        print("✅ Нет истекших подписок")
        return

    print(f"📊 Найдено {len(expired_subs)} истекших подписок")

    # Группируем по пользователям
    user_subscriptions = {}
    for sub_id, user_id, sub_type, channel_id, expires_at in expired_subs:
        if user_id not in user_subscriptions:
            user_subscriptions[user_id] = []
        user_subscriptions[user_id].append({
            'id': sub_id,
            'type': sub_type,
            'channel_id': channel_id,
            'expires_at': expires_at
        })

    # Обрабатываем каждого пользователя
    for user_id, subs in user_subscriptions.items():
        # Деактивируем истекшие подписки
        for sub in subs:
            deactivate_subscription(sub['id'])

        # Проверяем, есть ли ещё активные подписки
        if not has_active_subscription(user_id):
            # Если нет активных подписок - удаляем из канала
            print(f"🚫 У пользователя {user_id} нет активных подписок, удаляем из канала")
            kick_user_from_channel(user_id, SECRET_CHANNEL_ID)
        else:
            # Если есть активные подписки - оставляем в канале
            max_exp = get_max_expiration(user_id)
            print(f"✅ У пользователя {user_id} есть активные подписки до {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(max_exp))}")


def start_subscription_checker():
    """Запускает проверку подписок каждые 10 минут"""
    print("🚀 Запуск проверки подписок каждые 10 минут...")

    def check_loop():
        while True:
            try:
                check_and_remove_expired_subscriptions()
            except Exception as e:
                print(f"❌ Ошибка проверки подписок: {e}")

            # Ждём 10 минут
            time.sleep(600)

    # Запускаем в отдельном потоке
    thread = threading.Thread(target=check_loop, daemon=True)
    thread.start()
    print("✅ Проверка подписок запущена в фоновом режиме")


# ================= ТИПЫ ПОДПИСОК =================
SUBSCRIPTION_TYPES = {
    'bonus': {
        'duration_days': 5,
        'cost_bonuses': 50,
        'description': 'Бонусная подписка (5 дней)'
    },
    'short': {
        'duration_days': 3,
        'cost_stars': 50,
        'description': 'Короткая подписка (3 дня)'
    },
    'monthly': {
        'duration_days': 30,
        'cost_stars': 500,
        'description': 'Месячная подписка (30 дней)'
    },
    'referral': {
        'duration_days': 10,
        'description': 'Реферальная подписка (10 дней)'
    }
}


def grant_bonus_subscription(user_id):
    """Выдаёт бонусную подписку за 50 бонусов"""
    user = ensure_user(user_id)

    if user['bonus'] < 50:
        return False, "Недостаточно бонусов"

    # Списываем бонусы
    update_user(user_id, bonus=user['bonus'] - 50)

    # Добавляем подписку
    add_subscription(user_id, 'bonus', SUBSCRIPTION_TYPES['bonus']['duration_days'])

    return True, "Подписка выдана!"


def grant_referral_subscription(user_id):
    """Выдаёт реферальную подписку на 10 дней"""
    add_subscription(user_id, 'referral', SUBSCRIPTION_TYPES['referral']['duration_days'])
    return True, "Реферальная подписка выдана!"


def grant_paid_subscription(user_id, sub_type):
    """Выдаёт платную подписку"""
    if sub_type not in ['short', 'monthly']:
        return False, "Неверный тип подписки"

    duration = SUBSCRIPTION_TYPES[sub_type]['duration_days']
    add_subscription(user_id, sub_type, duration)

    return True, f"Подписка {sub_type} выдана!"

# ✅ Запуск проверки подписок (после определения всех функций)
start_subscription_checker()

# Запускаем проверку сразу при старте, не ждём 10 минут
threading.Thread(target=check_and_remove_expired_subscriptions, daemon=True).start()


# Изображение для главного меню (замените на свой file_id после первой отправки)
MENU_PHOTO = 'AgACAgIAAxkBAAIDcmn4cSKaSCJ5JTVfQi3AL_ECAeK9AALMFmsbT2TIS3LlQcmlLZJwAQADAgADeQADOwQ'  # или file_id после загрузки
MENU_PHOTO_2 = 'AgACAgIAAxkBAAIDlGn4kfrDN5qBpbnenDcDw5wiWNKjAALmF2sbT2TIS3t_t2HFU79cAQADAgADeQADOwQ'  # вторая фото для редактирования (замените на свой file_id)

# Временное решение: используем текст вместо фото, если URL недоступен
USE_PHOTO_MENU = True  # Измените на True, когда фото будет работать

LANG_PROMPT = (
    '🌐 Choose your language / Elige tu idioma\n'
    '🌐 Выбери язык / Pilih bahasa\n'
    '🌐 भाषा चुनें'
)

TEXTS = {
    'welcome': {
        'en': '🌟 Welcome to the SD ecosystem',
        'es': '🌟 Bienvenido al ecosistema SD',
        'ru': '🌟 Добро пожаловать в экосистему SD',
        'hi': '🌟 SD इकोसिस्टम में आपका स्वागत है',
        'id': '🌟 Selamat datang di ekosistem SD'
    },
    'tamagotchi_btn': {
        'en': '💃 Tamagotchi bot 💃',
        'es': '💃 Bot Tamagotchi 💃',
        'ru': '💃 Тамагочи бот 💃',
        'hi': '💃 तमागोची बॉट 💃',
        'id': '💃 Bot Tamagotchi 💃'
    },
    'buy_btn': {
        'en': '💳 Buy access',
        'es': '💳 Comprar acceso',
        'ru': '💳 Купить доступ',
        'hi': '💳 एक्सेस खरीदें',
        'id': '💳 Beli akses'
    },
    'lootbox_btn': {
        'en': '🎁 Open case',
        'es': '🎁 Abrir caja',
        'ru': '🎁 Открыть кейс',
        'hi': '🎁 केस खोलें',
        'id': '🎁 Buka case'
    },
    'back': {
        'en': '🔙 Back',
        'es': '🔙 Volver',
        'ru': '🔙 Назад',
        'hi': '🔙 वापस',
        'id': '🔙 Kembali'
    },
    'sub': {
        'en': '📢 Subscribe to these channels (Upgrade appearance & Threads)',
        'es': '📢 Suscríbete a estos canales (Mejorar apariencia & Threads)',
        'ru': '📢 Подпишись на эти каналы (прокачай внешку и темки)',
        'hi': '📢 इन चैनलों को सब्सक्राइब करें (अपना लुक अपग्रेड करो & थ्रेड्स)',
        'id': '📢 Subscribe channel ini (Tingkatkan penampilan & Threads)'
    },
    'confirm_sub': {
        'en': '✅ I subscribed',
        'es': '✅ Me suscribí',
        'ru': '✅ Я подписался',
        'hi': '✅ मैंने सब्सक्राइब किया',
        'id': '✅ Saya sudah subscribe'
    },
    'sub_ok': {
        'en': '✅ Subscription confirmed!',
        'es': '✅ ¡Suscripción confirmada!',
        'ru': '✅ Подписка подтверждена!',
        'hi': '✅ सब्सक्रिप्शन की पुष्टि हो गई!',
        'id': '✅ Langganan dikonfirmasi!'
    },
    'not_sub': {
        'en': '❌ You are not subscribed yet',
        'es': '❌ Aún no estás suscrito',
        'ru': '❌ Ты пока не подписан',
        'hi': '❌ आपने अभी सब्सक्राइब नहीं किया',
        'id': '❌ Kamu belum subscribe'
    },
    'menu': {
        'en': '🏠 Main menu\n\nWelcome to the hub! Choose an action below.',
        'es': '🏠 Menú principal\n\n¡Bienvenido al hub! Elige una acción abajo.',
        'ru': '🏠 Главное меню\n\nДобро пожаловать в хаб! Выбери действие ниже.',
        'hi': '🏠 मुख्य मेनू\n\nहब में आपका स्वागत है! नीचे एक विकल्प चुनें।',
        'id': '🏠 Menu utama\n\nSelamat datang di hub! Pilih tindakan di bawah.'
    },
    'ref_program': {
        'en': (
            '🎁 Referral program\n\n'
            'Invite friends and earn bonuses!\n\n'
            '🔗 Your link:\n{link}\n\n'
            '👥 Invited: {count}\n'
            '💎 Bonuses: {bonus}\n\n'
            'When your friend subscribes to the channels — the counter updates automatically.'
        ),
        'es': (
            '🎁 Programa de referidos\n\n'
            '¡Invita amigos y gana bonificaciones!\n\n'
            '🔗 Tu enlace:\n{link}\n\n'
            '👥 Invitados: {count}\n'
            '💎 Bonificaciones: {bonus}\n\n'
            'Cuando tu amigo se suscriba a los canales — el contador se actualiza automáticamente.'
        ),
        'ru': (
            '🎁 Реферальная программа\n\n'
            'Приглашай друзей и получай бонусы!\n\n'
            '🔗 Твоя ссылка:\n{link}\n\n'
            '👥 Приглашено: {count}\n'
            '💎 Бонусов: {bonus}\n\n'
            'Как только друг подпишется на каналы — счётчик обновится автоматически.'
        ),
        'hi': (
            '🎁 रेफरल प्रोग्राम\n\n'
            'दोस्तों को आमंत्रित करें और बोनस कमाएं!\n\n'
            '🔗 आपका लिंक:\n{link}\n\n'
            '👥 आमंत्रित: {count}\n'
            '💎 बोनस: {bonus}\n\n'
            'जब आपका दोस्त चैनल सब्सक्राइब करे — काउंटर अपने आप अपडेट होगा।'
        ),
        'id': (
            '🎁 Program referral\n\n'
            'Undang teman dan dapatkan bonus!\n\n'
            '🔗 Link kamu:\n{link}\n\n'
            '👥 Diundang: {count}\n'
            '💎 Bonus: {bonus}\n\n'
            'Ketika temanmu subscribe channel — counter otomatis terupdate.'
        )
    },
    'share_text': {
        'en': 'Join the hub using my referral link:',
        'es': 'Únete al hub usando mi enlace de referido:',
        'ru': 'Заходи в хаб по моей реферальной ссылке:',
        'hi': 'मेरे रेफरल लिंक से हब में शामिल हों:',
        'id': 'Bergabunglah ke hub dengan link referral saya:'
    },
    'language_saved': {
        'en': '✅ Language changed',
        'es': '✅ Idioma cambiado',
        'ru': '✅ Язык изменён',
        'hi': '✅ भाषा बदली गई',
        'id': '✅ Bahasa diubah'
    },
    'new_ref': {
        'en': '🎉 New referral! User {name} subscribed.\nTotal referrals: {count}\nBonuses: {bonus}',
        'es': '🎉 ¡Nuevo referido! Usuario {name} suscrito.\nTotal referidos: {count}\nBonificaciones: {bonus}',
        'ru': '🎉 Новый реферал! Пользователь {name} подписался.\nВсего рефералов: {count}\nБонусов: {bonus}',
        'hi': '🎉 नया रेफरल! {name} ने सब्सक्राइब किया।\nकुल रेफरल: {count}\nबोनस: {bonus}',
        'id': '🎉 Referral baru! {name} subscribe.\nTotal referral: {count}\nBonus: {bonus}'
    },
    'hub_channels': {
        'en': '📡 Hub channels',
        'es': '📡 Canales del hub',
        'ru': '📡 Каналы хаба',
        'hi': '📡 हब चैनल',
        'id': '📡 Channel hub'
    },
    'ref_btn': {
        'en': '🎁 Referral program',
        'es': '🎁 Programa de referidos',
        'ru': '🎁 Реферальная программа',
        'hi': '🎁 रेफरल प्रोग्राम',
        'id': '🎁 Program referral'
    },
    'lang_btn': {
        'en': '🌐 Language',
        'es': '🌐 Idioma',
        'ru': '🌐 Язык',
        'hi': '🌐 भाषा',
        'id': '🌐 Bahasa'
    },
    'share_btn': {
        'en': '🔗 Share link',
        'es': '🔗 Compartir enlace',
        'ru': '🔗 Поделиться ссылкой',
        'hi': '🔗 लिंक शेयर करें',
        'id': '🔗 Bagikan link'
    },
    'exchange_bonus_week': {
        'en': '🎁 Exchange 50 bonuses for week access',
        'es': '🎁 Cambiar 50 bonos por acceso semanal',
        'ru': '🎁 Обменять 50 бонусов на неделю доступа',
        'hi': '🎁 50 बोनस को एक सप्ताह की पहुंच के लिए एक्सचेंज करें',
        'id': '🎁 Tukar 50 bonus untuk akses mingguan'
    },
    'refresh_btn': {
        'en': '🔄 Refresh stats',
        'es': '🔄 Actualizar estadísticas',
        'ru': '🔄 Обновить статистику',
        'hi': '🔄 आँकड़े अपडेट करें',
        'id': '🔄 Perbarui statistik'
    },
    'back_btn': {
        'en': '🏠 Menu',
        'es': '🏠 Menú',
        'ru': '🏠 Меню',
        'hi': '🏠 मेनू',
        'id': '🏠 Menu'
    },
    'sub_btn': {
        'en': '📢 Subscribe to channels',
        'es': '📢 Suscríbete a los canales',
        'ru': '📢 Подпишись на каналы',
        'hi': '📢 चैनलों को सब्सक्राइब करें',
        'id': '📢 Subscribe channel'
    },
}


def ensure_user(user_id):
    user = get_user(user_id)

    if user:
        return user

    # если пользователя нет — создаём
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO users (id, lang, ref_by, referrals, bonus, reward_given, subscribed, last_link_time, created_at, last_daily)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        None,
        None,
        json.dumps([]),
        0,
        0,
        0,
        0,
        int(time.time()),
        0  # ✅ Добавляем last_daily = 0 для новых пользователей
    ))

    conn.commit()
    conn.close()

    print(f"✅ Создан новый пользователь {user_id} с last_daily=0")

    return get_user(user_id)

def t(user_id, key, **kwargs):
    user = ensure_user(user_id)
    lang = user.get('lang') or 'en'
    texts = TEXTS[key]
    value = texts.get(lang, texts.get('en', ''))
    return value.format(**kwargs) if kwargs else value


def get_ref_link(user_id):
    return f'https://t.me/{BOT_USERNAME}?start={user_id}'


def get_share_url(user_id):
    link = get_ref_link(user_id)
    text = t(user_id, 'share_text')
    return f'https://t.me/share/url?url={quote(link)}&text={quote(text)}'


# ================= KEYBOARDS =================
def lang_markup(user_id=None):
    markup = types.InlineKeyboardMarkup(row_width=2)
    en = '🇬🇧 English' + (' ✅' if user_id and ensure_user(user_id).get('lang') == 'en' else '')
    es = '🇪🇸 Español' + (' ✅' if user_id and ensure_user(user_id).get('lang') == 'es' else '')
    ru = '🇷🇺 Русский' + (' ✅' if user_id and ensure_user(user_id).get('lang') == 'ru' else '')
    hi = '🇮🇳 हिन्दी' + (' ✅' if user_id and ensure_user(user_id).get('lang') == 'hi' else '')
    id_ = '🇮🇩 Indonesia' + (' ✅' if user_id and ensure_user(user_id).get('lang') == 'id' else '')
    markup.row(
        types.InlineKeyboardButton(en, callback_data='lang_en'),
        types.InlineKeyboardButton(es, callback_data='lang_es')
    )
    markup.row(
        types.InlineKeyboardButton(ru, callback_data='lang_ru'),
        types.InlineKeyboardButton(hi, callback_data='lang_hi')
    )
    markup.row(
        types.InlineKeyboardButton(id_, callback_data='lang_id')
    )
    return markup


def sub_markup(user_id):
    markup = types.InlineKeyboardMarkup()
    for ch in MANDATORY_CHANNELS:
        name = ch['name'].get(ensure_user(user_id).get('lang') or 'en', ch['name']['en'])
        markup.add(types.InlineKeyboardButton(name, url=ch['link']))
    return markup


def menu_markup(user_id):
    markup = types.InlineKeyboardMarkup()

    for ch in HUB_CHANNELS:
        lang = ensure_user(user_id).get('lang') or 'en'
        name = ch['name'].get(lang, ch['name']['en'])

        if ch.get('type') == 'chat':
            markup.add(types.InlineKeyboardButton(name, callback_data='get_chat_link'))
        else:
            markup.add(types.InlineKeyboardButton(name, url=ch['link']))

    markup.add(types.InlineKeyboardButton(
        t(user_id, 'tamagotchi_btn'),
        url='https://t.me/sd_girl_bot'
    ))

    markup.add(types.InlineKeyboardButton(
        t(user_id, 'buy_btn'),
        callback_data='buy_access'
    ))

    # ❌ УДАЛЕНО: кнопка "🔒 Приватный канал"

    markup.add(types.InlineKeyboardButton(
        t(user_id, 'lootbox_btn'),
        callback_data='lootbox'
    ))

    markup.row(
        types.InlineKeyboardButton(t(user_id, 'ref_btn'), callback_data='ref_program'),
        types.InlineKeyboardButton(t(user_id, 'lang_btn'), callback_data='language')
    )

    return markup
#==============оплата========

def buy_menu(user_id):
    markup = types.InlineKeyboardMarkup()
    lang = ensure_user(user_id).get('lang') or 'en'

    if lang == 'ru':
        text_30 = "⭐ 50 (3 дня)"
        text_250 = "⭐ 500 (30 дней)"
        text_1000 = "⭐ 1500 (навсегда)"
    elif lang == 'es':
        text_30 = "⭐ 50 (3 días)"
        text_250 = "⭐ 500 (30 días)"
        text_1000 = "⭐ 1500 (para siempre)"
    else:
        text_30 = "⭐ 50 (3 days)"
        text_250 = "⭐ 500 (30 days)"
        text_1000 = "⭐ 1500 (forever)"

    markup.add(types.InlineKeyboardButton(
        text_30,
        callback_data='buy_30'
    ))

    markup.add(types.InlineKeyboardButton(
        text_250,
        callback_data='buy_250'
    ))

    markup.add(types.InlineKeyboardButton(
        text_1000,
        callback_data='buy_1000'
    ))

    markup.add(types.InlineKeyboardButton(
        t(user_id, 'back'),
        callback_data='main_menu'
    ))

    return markup


# ================= SENDERS =================
def send_or_edit(chat_id, text, markup, message_id=None, photo=False):
    """Отправляет новое сообщение или редактирует существующее."""
    if message_id and not photo:
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=markup,
                disable_web_page_preview=True,
                parse_mode='HTML'
            )
            return
        except Exception as e:
            if 'message is not modified' in str(e).lower():
                return
            # Если не удалось отредактировать, удаляем старое сообщение
            try:
                bot.delete_message(chat_id, message_id)
            except Exception:
                pass
    bot.send_message(chat_id, text, reply_markup=markup,
                     disable_web_page_preview=True, parse_mode='HTML')


def show_lang(chat_id, user_id=None, message_id=None):
    send_or_edit(chat_id, LANG_PROMPT, lang_markup(user_id), message_id)


def send_language_selection(chat_id, user_id):
    """Отправляет сообщение с выбором языка и устанавливает FSM состояние"""
    set_fsm_state(user_id, FSMState.CHOOSING_LANGUAGE)
    show_lang(chat_id, user_id)
    print(f"🌐 Отправлен выбор языка для пользователя {user_id}, FSM состояние: choosing_language")


def show_sub(chat_id, user_id, message_id=None):
    """Показывает сообщение с каналами для подписки"""
    # ✅ Отправляем сообщение и ждём завершения
    send_or_edit(chat_id, t(user_id, 'sub'), sub_markup(user_id), message_id)
    print(f"✅ Отправлено сообщение с каналами пользователю {user_id}")


def show_welcome(chat_id, user_id):
    """Показывает провоцирующее приветственное сообщение"""
    welcome_text = t(user_id, 'welcome')
    bot.send_message(chat_id, welcome_text, parse_mode='HTML')


def show_menu(chat_id, user_id, message_id=None, force_new=False):
    """Главное меню с фото сверху."""
    text = t(user_id, 'menu')

    print(f"🏠 show_menu вызван: chat_id={chat_id}, user_id={user_id}, force_new={force_new}")

    # Всегда удаляем старое сообщение если есть
    if message_id:
        try:
            bot.delete_message(chat_id, message_id)
            print(f"✅ Старое сообщение удалено")
        except Exception:
            pass

    # Отправляем меню (с фото или текстом)
    print(f"📸 Отправка нового меню... USE_PHOTO_MENU={USE_PHOTO_MENU}")
    if USE_PHOTO_MENU:
        try:
            bot.send_photo(
                chat_id,
                photo=MENU_PHOTO,
                caption=text,
                reply_markup=menu_markup(user_id),
                parse_mode='HTML'
            )
            print(f"✅ Меню отправлено с фото")
        except Exception as photo_error:
            print(f"❌ Ошибка отправки фото: {photo_error}")
            # Если фото не загрузилось, шлём текст
            bot.send_message(chat_id, text, reply_markup=menu_markup(user_id),
                             disable_web_page_preview=True, parse_mode='HTML')
            print(f"✅ Меню отправлено с текстом")
    else:
        # Отправляем только текст
        bot.send_message(chat_id, text, reply_markup=menu_markup(user_id),
                         disable_web_page_preview=True, parse_mode='HTML')
        print(f"✅ Меню отправлено с текстом")
        
# ================= SENDERS =================

def get_level(ref_count):
    if ref_count >= 20:
        return "legend"
    elif ref_count >= 10:
        return "elite"
    elif ref_count >= 5:
        return "pro"
    elif ref_count >= 3:
        return "active"
    elif ref_count >= 1:
        return "newbie"
    return "none"
#========== RANDOM ==============
def give_reward(user_id):
    reward = random.choices(
        [1, 2, 3, 5, 10],
        weights=[50, 25, 15, 8, 2]
    )[0]

    user = ensure_user(user_id)
    update_user(user_id, bonus=user['bonus'] + reward)

    bot.send_message(user_id,
        f"🎁 Ты получил {reward} бонусов!\n🔥 Иногда выпадает больше..."
    )


def daily_bonus(user_id):
    user = ensure_user(user_id)
    now = time.time()

    # ✅ Проверяем, прошел ли день с последнего бонуса
    last_daily = user.get('last_daily', 0)

    print(f"🔍 Проверка ежедневного бонуса для пользователя {user_id}:")
    print(f"   Текущее время: {now}")
    print(f"   Последний бонус: {last_daily}")
    print(f"   Прошло времени: {now - last_daily} секунд")
    print(f"   Нужно пройти: 86400 секунд (24 часа)")

    # Если уже получал бонус сегодня - выходим
    if last_daily > 0 and now - last_daily < 86400:
        remaining = int(86400 - (now - last_daily))
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        print(f"ℹ️ Пользователь {user_id} уже получал бонус сегодня. Осталось: {hours}ч {minutes}мин")
        return

    reward = random.randint(1, 5)

    # ✅ Получаем актуальный бонус перед обновлением
    current_bonus = user.get('bonus', 0)

    print(f"✅ Выдаём ежедневный бонус пользователю {user_id}: +{reward} (было: {current_bonus}, станет: {current_bonus + reward})")

    update_user(
        user_id,
        bonus=current_bonus + reward,
        last_daily=now
    )

    # ✅ Прямая проверка в базе данных
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT last_daily, bonus FROM users WHERE id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()

    if result:
        db_last_daily, db_bonus = result
        print(f"🔍 ПРЯМАЯ ПРОВЕРКА В БАЗЕ:")
        print(f"   last_daily в базе: {db_last_daily}")
        print(f"   bonus в базе: {db_bonus}")
        print(f"   Ожидалось last_daily: {now}")
        print(f"   Ожидалось bonus: {current_bonus + reward}")

        if db_last_daily != now:
            print(f"❌ ОШИБКА: last_daily не сохранился в базе!")
        if db_bonus != current_bonus + reward:
            print(f"❌ ОШИБКА: bonus не сохранился в базе!")

    lang = user.get('lang') or 'en'
    if lang == 'ru':
        msg = f"🎁 Ежедневный бонус: +{reward}\n⏳ Приходи завтра"
    elif lang == 'es':
        msg = f"🎁 Bono diario: +{reward}\n⏳ Vuelve mañana"
    else:
        msg = f"🎁 Daily bonus: +{reward}\n⏳ Come back tomorrow"

    bot.send_message(user_id, msg)


def progress_bar(current, total=20):
    filled = int((current / total) * 10)
    return "🟩" * filled + "⬜" * (10 - filled)
#======== дожим на оплатц ========

def push_payment(user_id):
    user = ensure_user(user_id)
    lang = user.get('lang') or 'en'

    remaining = 20 - len(user['referrals'])

    if lang == 'ru':
        msg = (
            f"⚡ Ты почти открыл доступ\n\n"
            f"Осталось: {remaining}\n\n"
            "🔥 Добей рефералов\n"
            "или\n"
            "💳 Открой сразу за звёзды"
        )
    elif lang == 'es':
        msg = (
            f"⚡ Casi abres el acceso\n\n"
            f"Restan: {remaining}\n\n"
            "🔥 Consigue referidos\n"
            "o\n"
            "💳 Abre de inmediato por estrellas"
        )
    else:
        msg = (
            f"⚡ You almost unlocked access\n\n"
            f"Remaining: {remaining}\n\n"
            "🔥 Get referrals\n"
            "or\n"
            "💳 Unlock immediately for stars"
        )

    bot.send_message(user_id, msg)

#---------функция счёта=========
def send_invoice(user_id, amount, payload):
    lang = ensure_user(user_id).get('lang') or 'en'

    if lang == 'ru':
        title = "Доступ к секретному каналу"
        description = "Оплата доступа"
        label = "Доступ"
    elif lang == 'es':
        title = "Acceso al canal secreto"
        description = "Pago de acceso"
        label = "Acceso"
    else:
        title = "Access to secret channel"
        description = "Access payment"
        label = "Access"

    prices = [types.LabeledPrice(label=label, amount=amount)]

    bot.send_invoice(
        user_id,
        title=title,
        description=description,
        invoice_payload=payload,
        provider_token="",  # ДЛЯ STARS ПУСТОЙ
        currency="XTR",     # Stars
        prices=prices
    )
    
def show_ref(chat_id, user_id, message_id=None):
    user = ensure_user(user_id)

    # ✅ СНАЧАЛА считаем
    ref_count = len(user['referrals'])

    # ✅ ПОТОМ используем
    level = get_level(ref_count)

    bar = progress_bar(ref_count)

    levels_text = {
        "none": "😴 Нет уровня",
        "newbie": "🥉 Новичок",
        "active": "🥈 Активный",
        "pro": "🥇 Продвинутый",
        "elite": "💎 Элита",
        "legend": "🔥 ЛЕГЕНДА"
    }

    remaining = max(0, 20 - ref_count)

    lang = ensure_user(user_id).get('lang') or 'en'
    if lang == 'ru':
        text = (
            f"🎮 ТВОЙ ПРОГРЕСС\n\n"
            f"{bar}\n\n"
            f"👥 Рефералы: {ref_count}/20\n"
            f"🏆 Уровень: {levels_text.get(level)}\n"
            f"💎 Бонусы: {user['bonus']}\n\n"
            f"🔒 СЕКРЕТНЫЙ КАНАЛ на 20\n"
            f"🔥 Осталось: {remaining}\n\n"
            f"🔗 Твоя ссылка:\n{get_ref_link(user_id)}"
        )
        if ref_count < 20:
            if random.random() < 0.3:
                text += "\n🔥 Ты быстрее среднего игрока"
        if ref_count < 20:
            text += f"\n🔥 Осталось: {remaining}"
        else:
            text += "\n👑 Ты входишь в 1% пользователей"
    elif lang == 'es':
        levels_text_es = {
            "none": "😴 Sin nivel",
            "newbie": "🥉 Novato",
            "active": "🥈 Activo",
            "pro": "🥇 Avanzado",
            "elite": "💎 Élite",
            "legend": "🔥 LEYENDA"
        }
        text = (
            f"🎮 TU PROGRESO\n\n"
            f"{bar}\n\n"
            f"👥 Referidos: {ref_count}/20\n"
            f"🏆 Nivel: {levels_text_es.get(level)}\n"
            f"💎 Bonos: {user['bonus']}\n\n"
            f"🔒 CANAL SECRETO a 20\n"
            f"🔥 Restan: {remaining}\n\n"
            f"🔗 Tu enlace:\n{get_ref_link(user_id)}"
        )
        if ref_count < 20:
            if random.random() < 0.3:
                text += "\n🔥 Eres más rápido que el jugador promedio"
        if ref_count < 20:
            text += f"\n🔥 Restan: {remaining}"
        else:
            text += "\n👑 Estás en el 1% de usuarios"
    else:
        levels_text_en = {
            "none": "😴 No level",
            "newbie": "🥉 Newbie",
            "active": "🥈 Active",
            "pro": "🥇 Pro",
            "elite": "💎 Elite",
            "legend": "🔥 LEGEND"
        }
        text = (
            f"🎮 YOUR PROGRESS\n\n"
            f"{bar}\n\n"
            f"👥 Referrals: {ref_count}/20\n"
            f"🏆 Level: {levels_text_en.get(level)}\n"
            f"💎 Bonuses: {user['bonus']}\n\n"
            f"🔒 SECRET CHANNEL at 20\n"
            f"🔥 Remaining: {remaining}\n\n"
            f"🔗 Your link:\n{get_ref_link(user_id)}"
        )
        if ref_count < 20:
            if random.random() < 0.3:
                text += "\n🔥 You're faster than average player"
        if ref_count < 20:
            text += f"\n🔥 Remaining: {remaining}"
        else:
            text += "\n👑 You're in the top 1% of users"

    markup = types.InlineKeyboardMarkup()

    markup.add(types.InlineKeyboardButton(
        t(user_id, 'exchange_bonus_week'),
        callback_data='exchange_bonus_week'
    ))

    markup.add(types.InlineKeyboardButton(
        t(user_id, 'share_btn'),
        url=get_share_url(user_id)
    ))

    markup.add(types.InlineKeyboardButton(
        t(user_id, 'back_btn'),
        callback_data='main_menu'
    ))

    # ✅ ИСПРАВЛЕНО: Редактируем существующее сообщение, если есть message_id
    if message_id:
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=markup,
                disable_web_page_preview=True,
                parse_mode='HTML'
            )
            return
        except Exception as e:
            if 'message is not modified' in str(e).lower():
                return
            # Если не удалось отредактировать, удаляем старое сообщение
            try:
                bot.delete_message(chat_id, message_id)
            except Exception:
                pass

    # Отправляем новое сообщение только если не удалось отредактировать
    bot.send_message(chat_id, text, reply_markup=markup,
                     disable_web_page_preview=True, parse_mode='HTML')
# ================= LOGIC =================
def check_sub(user_id):
    try:
        for ch in MANDATORY_CHANNELS:
            member = bot.get_chat_member(ch['id'], user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                return False
        return True
    except Exception as e:
        print(f'Ошибка проверки подписки: {e}')
        return False


def auto_add_to_hub_channels(user_id):
    """Отправляет инвайт-ссылки для приватных каналов и настраивает автоодобрение"""
    try:
        target_channels = HUB_CHANNELS[:2]  # SD_FETISH_HUB и MUSIC

        print(f"🔍 Отправка инвайт-ссылок пользователю {user_id} для {len(target_channels)} каналов...")

        for ch in target_channels:
            if 'chat_id' in ch and ch['chat_id']:
                chat_id = ch['chat_id']
                print(f"📋 Обработка канала {chat_id}...")

                try:
                    # ✅ Проверяем, является ли бот администратором
                    bot_info = bot.get_me()
                    bot_member = bot.get_chat_member(chat_id, bot_info.id)

                    if bot_member.status not in ['administrator', 'creator']:
                        print(f"❌ Бот не администратор канала {chat_id}")
                        continue

                    print(f"✅ Бот является администратором канала {chat_id}")

                    # ✅ Проверяем, есть ли пользователь уже в канале
                    try:
                        user_member = bot.get_chat_member(chat_id, user_id)
                        if user_member.status in ['member', 'administrator', 'creator']:
                            print(f"✅ Пользователь {user_id} уже в канале {chat_id}")
                            continue
                    except:
                        print(f"ℹ️ Пользователь {user_id} не в канале {chat_id}")

                    # ✅ Создаём инвайт-ссылку
                    invite_link = create_one_time_invite_link(chat_id, expire_seconds=86400)

                    if invite_link:
                        print(f"✅ Инвайт-ссылка создана: {invite_link}")

                        # ✅ Отправляем ссылку пользователю
                        lang = ensure_user(user_id).get('lang') or 'en'
                        channel_name = ch['name'].get(lang, ch['name'].get('en', 'Channel'))

                        if lang == 'ru':
                            msg = f"🔗 Ссылка для входа в {channel_name}:\n{invite_link}\n\n⚡ Бот автоматически одобрит ваш запрос!"
                            btn_text = "🚀 Войти в канал"
                        elif lang == 'es':
                            msg = f"🔗 Enlace para entrar a {channel_name}:\n{invite_link}\n\n⚡ ¡El bot aprobará tu solicitud automáticamente!"
                            btn_text = "🚀 Entrar al canal"
                        else:
                            msg = f"🔗 Link to enter {channel_name}:\n{invite_link}\n\n⚡ Bot will approve your request automatically!"
                            btn_text = "🚀 Enter channel"

                        # Отправляем сообщение с кнопкой-ссылкой
                        markup = types.InlineKeyboardMarkup()
                        markup.add(types.InlineKeyboardButton(btn_text, url=invite_link))

                        bot.send_message(user_id, msg, reply_markup=markup)
                        print(f"✅ Отправлена ссылка пользователю {user_id} для канала {chat_id}")

                    else:
                        print(f"❌ Не удалось создать инвайт-ссылку для {chat_id}")

                except Exception as e:
                    print(f"❌ Ошибка обработки канала {chat_id}: {e}")

    except Exception as e:
        print(f"❌ Ошибка в auto_add_to_hub_channels: {e}")


def handle_ref(message):
    user_id = message.from_user.id
    user = ensure_user(user_id)
    args = message.text.split(maxsplit=1)

    if len(args) > 1:
        payload = args[1]
        if payload.startswith("tiktok_"):
            update_user(user_id, source="tiktok")
        elif payload.isdigit():
            ref_id = int(payload)
            if ref_id != user_id and user['ref_by'] is None:
                update_user(user_id, ref_by=ref_id)
                ensure_user(ref_id)


def reward_referrer(user_id, first_name=''):
    user = ensure_user(user_id)
    ref_id = user.get('ref_by')

    # ❌ нет реферера или уже дали награду
    if not ref_id or user.get('reward_given'):
        return

    # ❌ самореферал
    if ref_id == user_id:
        return

    now = time.time()

    # ⏱ защита: пользователь должен прожить минимум 60 сек
    if now - user.get('created_at', now) < 60:
        return

    # ❌ должен быть подписан
    if not check_sub(user_id):
        return

    ref_user = ensure_user(ref_id)

    # 🔁 лимит: максимум 10 рефералов в день
    if now - ref_user.get('last_ref_time', 0) > 86400:
        update_user(ref_id, ref_count_today=0)

    if ref_user.get('ref_count_today', 0) >= 10:
        return

    referrals = ref_user['referrals'] or []

    # ❌ уже есть
    if user_id in referrals:
        return

    # ✅ добавляем
    referrals.append(user_id)

    # 💰 считаем награду
    reward = random.choices(
        [1, 2, 3, 5, 10],
        weights=[50, 25, 15, 8, 2]
    )[0]

    # обновляем бонус реферера
    update_user(ref_id, bonus=ref_user['bonus'] + reward)

    # ✅ если достиг 20 рефералов — даём подписку и кнопку входа
    if len(referrals) >= 20:
        # ✅ Выдаём реферальную подписку
        grant_referral_subscription(ref_id)

        lang = ensure_user(ref_id).get('lang') or 'en'
        if lang == 'ru':
            msg = (
                f"🎉 Ты открыл доступ к приватному каналу!\n\n"
                f"👥 Всего рефералов: {len(referrals)}\n"
                f"💎 Получено бонусов: {reward}\n"
                f"⏰ Подписка на 10 дней активирована!"
            )
            btn_text = "🚀 Войти"
        elif lang == 'es':
            msg = (
                f"🎉 ¡Has desbloqueado acceso al canal privado!\n\n"
                f"👥 Total referidos: {len(referrals)}\n"
                f"💎 Bonos recibidos: {reward}\n"
                f"⏰ ¡Suscripción de 10 días activada!"
            )
            btn_text = "🚀 Entrar"
        else:
            msg = (
                f"🎉 You've unlocked access to the private channel!\n\n"
                f"👥 Total referrals: {len(referrals)}\n"
                f"💎 Bonuses received: {reward}\n"
                f"⏰ 10-day subscription activated!"
            )
            btn_text = "🚀 Enter"

        bot.send_message(
            ref_id,
            msg,
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton(
                    btn_text,
                    url=SECRET_CHANNEL_LINK
                )
            )
        )
    else:
        # 📩 обычное уведомление о новом реферале
        try:
            lang = ensure_user(ref_id).get('lang') or 'en'
            if lang == 'ru':
                msg = (
                    f"🎁 Новый реферал!\n\n"
                    f"👥 Всего: {len(referrals)}\n"
                    f"💎 Получено: +{reward} бонусов\n"
                    f"🎯 До секретного канала: {20 - len(referrals)}"
                )
            elif lang == 'es':
                msg = (
                    f"🎁 ¡Nuevo referido!\n\n"
                    f"👥 Total: {len(referrals)}\n"
                    f"💎 Recibido: +{reward} bonos\n"
                    f"🎯 Para el canal secreto: {20 - len(referrals)}"
                )
            else:
                msg = (
                    f"🎁 New referral!\n\n"
                    f"👥 Total: {len(referrals)}\n"
                    f"💎 Received: +{reward} bonuses\n"
                    f"🎯 To secret channel: {20 - len(referrals)}"
                )
            bot.send_message(ref_id, msg)
        except Exception:
            pass

    # обновляем пользователя
    update_user(
        ref_id,
        referrals=referrals,
        ref_count_today=ref_user.get('ref_count_today', 0) + 1,
        last_ref_time=now
    )
    update_user(user_id, reward_given=True)

#================== автоворонка ================

def run_funnel(user_id, chat_id):
    user = ensure_user(user_id)
    step = user.get('funnel_step', 0)
    lang = user.get('lang') or 'en'

    if step == 0:
        if lang == 'ru':
            msg = (
                "🔥 Добро пожаловать\n\n"
                "Ты пришёл из TikTok — здесь быстрый вход и понятный путь до результата."
            )
        elif lang == 'es':
            msg = (
                "🔥 Bienvenido\n\n"
                "Vienes de TikTok — aquí entrada rápida y camino claro al resultado."
            )
        else:
            msg = (
                "🔥 Welcome\n\n"
                "You came from TikTok — here's fast entry and clear path to results."
            )
        bot.send_message(chat_id, msg)
        update_user(user_id, funnel_step=1)
        threading.Timer(3, run_funnel, args=(user_id, chat_id)).start()
        return

    if step == 1:
        if lang == 'ru':
            msg = (
                "💎 Здесь ты получишь:\n"
                "• доступ к закрытым разделам\n"
                "• быстрый старт без лишних шагов\n"
                "• понятную систему прогресса"
            )
        elif lang == 'es':
            msg = (
                "💎 Aquí obtendrás:\n"
                "• acceso a secciones privadas\n"
                "• inicio rápido sin pasos extra\n"
                "• sistema de progreso claro"
            )
        else:
            msg = (
                "💎 Here you'll get:\n"
                "• access to private sections\n"
                "• fast start without extra steps\n"
                "• clear progress system"
            )
        bot.send_message(chat_id, msg)
        update_user(user_id, funnel_step=2)
        return

    if step == 2:
        if lang == 'ru':
            msg = (
                "👥 Люди обычно заходят через короткий ролик, а дальше добирают интерес уже в боте."
            )
        elif lang == 'es':
            msg = (
                "👥 La gente suele entrar por un video corto, y luego encuentra más interés en el bot."
            )
        else:
            msg = (
                "👥 People usually enter through a short video, then find more interest in the bot."
            )
        bot.send_message(chat_id, msg)
        update_user(user_id, funnel_step=3)
        return

    if step == 3:
        if lang == 'ru':
            msg = (
                "🔒 Выбери удобный путь:\n"
                "• рефералы\n"
                "• покупка доступа"
            )
        elif lang == 'es':
            msg = (
                "🔒 Elige el camino conveniente:\n"
                "• referidos\n"
                "• compra de acceso"
            )
        else:
            msg = (
                "🔒 Choose convenient path:\n"
                "• referrals\n"
                "• purchase access"
            )
        bot.send_message(chat_id, msg)
        push_payment(user_id)
        update_user(user_id, funnel_step=4)
        return

    show_menu(chat_id, user_id)

# ================= HANDLERS =================
@bot.message_handler(commands=['start'])
def start(message):
    """Обработчик /start - ТОЛЬКО отправляет выбор языка"""
    if not is_real_user(message):
        return

    user_id = message.from_user.id

    # Обрабатываем реферальную ссылку (может изменить source на "tiktok")
    handle_ref(message)

    # Обновляем пользователя, чтобы получить актуальный source
    user = ensure_user(user_id)

    # Сохраняем информацию о том, был ли это чистый /start (для ежедневного бонуса)
    args = message.text.split()
    is_clean_start = len(args) == 1  # Только /start без параметров

    # Сохраняем эту информацию в пользовательских данных для использования в on_language_selected
    update_user(user_id, is_clean_start=is_clean_start)

    # ✅ ТОЛЬКО вызываем send_language_selection - вся остальная цепочка запускается из колбэка
    send_language_selection(message.chat.id, user_id)

# ================= НОВЫЙ ОБРАБОТЧИК /start ДЛЯ ПОСЛЕДОВАТЕЛЬНОГО ONBOARDING =================
@bot.message_handler(commands=['start2'])
def start_sequential(message):
    """Новый обработчик для последовательного onboarding"""
    if not is_real_user(message):
        return

    user_id = message.from_user.id
    user = ensure_user(user_id)

    # 1. Process referral which might change the user's source to "tiktok"
    handle_ref(message)

    # 2. ✅ Re-fetch the user to ensure we have the updated "source"
    user = ensure_user(user_id)

    # Проверяем, есть ли параметр (реферальная ссылка)
    args = message.text.split()
    has_param = len(args) > 1

    # Если есть параметр или пользователь новый - запускаем последовательный onboarding
    if has_param or user.get('funnel_step', 0) < FUNNEL_MENU:
        print(f"🚀 Запуск последовательного onboarding для пользователя {user_id}")
        start_sequential_onboarding(user_id, source='referral' if has_param else 'start')
        return

    # Если пользователь уже прошел onboarding - показываем меню
    show_menu(user_id, user_id)

    # 4. ✅ Removed the extra if funnel_step > 0 block that caused duplication
    start_reengage(user_id)


# ================= LANGUAGE SELECTION CALLBACK =================
def on_language_selected(call, lang):
    """Обрабатывает выбор языка и запускает всю цепочку onboarding"""
    user_id = call.from_user.id
    chat_id = call.message.chat.id

    # Проверяем FSM состояние - обрабатываем только если пользователь выбирает язык
    current_state = get_fsm_state(user_id)
    if current_state != FSMState.CHOOSING_LANGUAGE:
        print(f"⚠️ FSM: User {user_id} not in choosing_language state (current: {current_state}), skipping language selection flow")
        return

    print(f"🌐 Пользователь {user_id} выбрал язык: {lang}")

    # 1. Сохраняем язык
    update_user(user_id, lang=lang)
    bot.answer_callback_query(call.id, t(user_id, 'language_saved'))
    clear_fsm_state(user_id)

    # 2. Приветствие
    show_welcome(chat_id, user_id)

    # 3. Ежедневный бонус
    daily_bonus(user_id)

    # 4. Каналы для подписки
    show_sub(chat_id, user_id)

    # 5. Главное меню — сразу после каналов
    show_menu(chat_id, user_id)


@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    if not is_real_user(call):
        return
    user_id = call.from_user.id
    user = ensure_user(user_id)
    chat_id = call.message.chat.id
    mid = call.message.message_id

    # ================= ЯЗЫК - FSM STATE FILTERED =================
    if call.data.startswith('lang_'):
        lang = call.data.split('_')[1]

        # Проверяем FSM состояние
        current_state = get_fsm_state(user_id)
        if current_state == FSMState.CHOOSING_LANGUAGE:
            # Это выбор языка в процессе onboarding - запускаем всю цепочку
            on_language_selected(call, lang)
        else:
            # Это просто смена языка - обновляем и показываем меню
            update_user(user_id, lang=lang)
            bot.answer_callback_query(call.id, t(user_id, 'language_saved'))
            update_user(user_id, subscribed=True)
            reward_referrer(user_id, call.from_user.first_name or '')
            show_menu(chat_id, user_id, mid)

    # Обработка получения ежедневного бонуса
    elif call.data == 'claim_daily_bonus':
        bot.answer_callback_query(call.id)

        # Даем бонус
        daily_bonus(user_id)

        # Переходим к следующему шагу
        current_step = user.get('funnel_step', 0)
        if current_step == FUNNEL_BONUS:
            process_onboarding_step(user_id, FUNNEL_SUBSCRIPTION)

    # Обработка подтверждения подписки
    # ================= СУЩЕСТВУЮЩАЯ ЛОГИКА =================
    # ===== ПОКУПКА =====
    if call.data == 'buy_access':
        bot.answer_callback_query(call.id)

        lang = ensure_user(user_id).get('lang') or 'en'

        if lang == 'ru':
            text = (
                "💳 ДОСТУП В СЕКРЕТНЫЙ КАНАЛ\n\n"
                "⭐ 50 звёзд — 3 дня\n"
                "⭐ 500 звёзд — 30 дней\n"
                "⭐ 1500 звёзд — НАВСЕГДА\n\n"
                "Выбери тариф:"
            )
        elif lang == 'es':
            text = (
                "💳 ACCESO AL CANAL SECRETO\n\n"
                "⭐ 50 estrellas — 3 días\n"
                "⭐ 500 estrellas — 30 días\n"
                "⭐ 1500 estrellas — PARA SIEMPRE\n\n"
                "Elige tu plan:"
            )
        else:
            text = (
                "💳 ACCESS TO SECRET CHANNEL\n\n"
                "⭐ 50 stars — 3 days\n"
                "⭐ 500 stars — 30 days\n"
                "⭐ 1500 stars — FOREVER\n\n"
                "Choose your plan:"
            )

        send_or_edit(chat_id, text, buy_menu(user_id), mid)
        return
    elif call.data == 'lootbox':
        bot.answer_callback_query(call.id)
        user = ensure_user(user_id)

        now = time.time()
        if now - user.get('last_lootbox', 0) > 86400:
            update_user(user_id, lootbox_uses=0)
        if user.get('lootbox_uses', 0) >= 10:
            lang = ensure_user(user_id).get('lang') or 'en'
            if lang == 'ru':
                msg = "🚫 Лимит кейсов на сегодня"
            elif lang == 'es':
                msg = "🚫 Límite de cajas para hoy"
            else:
                msg = "🚫 Daily lootbox limit reached"
            bot.send_message(user_id, msg)
            return
        update_user(user_id, lootbox_uses=user.get('lootbox_uses', 0) + 1)

        # ⛔ анти-спам
        if now - user.get('last_lootbox', 0) < LOOTBOX_COOLDOWN:
            lang = ensure_user(user_id).get('lang') or 'en'
            if lang == 'ru':
                msg = "⏳ Подожди немного"
            elif lang == 'es':
                msg = "⏳ Espera un poco"
            else:
                msg = "⏳ Wait a bit"
            bot.answer_callback_query(call.id, msg, show_alert=True)
            return

        # ❌ не хватает бонусов
        if user['bonus'] < 3:
            lang = ensure_user(user_id).get('lang') or 'en'
            if lang == 'ru':
                msg = "❌ Нужно 3 бонуса"
            elif lang == 'es':
                msg = "❌ Necesitas 3 bonos"
            else:
                msg = "❌ Need 3 bonuses"
            bot.answer_callback_query(call.id, msg, show_alert=True)
            return

        # 🎰 крутим
        lang = ensure_user(user_id).get('lang') or 'en'
        if lang == 'ru':
            reward = random.choices(
                [
                    ("😐 Почти...", 0),
                    ("💎 +1", 1),
                    ("💎 +2", 2),
                    ("💎 +5", 5),
                    ("🔥 JACKPOT +50", 50)
                ],
                weights=[35, 30, 20, 14, 1]
            )[0]
        elif lang == 'es':
            reward = random.choices(
                [
                    ("😐 Casi...", 0),
                    ("💎 +1", 1),
                    ("💎 +2", 2),
                    ("💎 +5", 5),
                    ("🔥 JACKPOT +50", 50)
                ],
                weights=[35, 30, 20, 14, 1]
            )[0]
        else:
            reward = random.choices(
                [
                    ("😐 Almost...", 0),
                    ("💎 +1", 1),
                    ("💎 +2", 2),
                    ("💎 +5", 5),
                    ("🔥 JACKPOT +50", 50)
                ],
                weights=[35, 30, 20, 14, 1]
            )[0]

        text, value = reward

        # 📉 streak
        lose_streak = user.get('lose_streak', 0)

        if value == 0:
            lose_streak += 1
        else:
            lose_streak = 0

        # 💥 защита от невезения
        if lose_streak >= 3:
            text = "💎 +5"
            value = 5
            lose_streak = 0

        # 💰 считаем баланс (ВОТ ГДЕ НУЖЕН new_bonus)
        new_bonus = user['bonus'] - 3 + value

        # 💾 сохраняем
        update_user(
            user_id,
            bonus=new_bonus,
            last_lootbox=now,
            lose_streak=lose_streak
        )

        # 🎬 эффект
        lang = ensure_user(user_id).get('lang') or 'en'
        if lang == 'ru':
            spin_msg = "🎰 Крутим..."
            result_msg = f"🎰 Результат:\n\n{text}"
            secret_msg = (
                "💎 Секрет:\n"
                "Игроки с 3+ прокрутками чаще ловят JACKPOT"
            )
        elif lang == 'es':
            spin_msg = "🎰 Girando..."
            result_msg = f"🎰 Resultado:\n\n{text}"
            secret_msg = (
                "💎 Secreto:\n"
                "Los jugadores con 3+ giros consiguen JACKPOT más seguido"
            )
        else:
            spin_msg = "🎰 Spinning..."
            result_msg = f"🎰 Result:\n\n{text}"
            secret_msg = (
                "💎 Secret:\n"
                "Players with 3+ spins get JACKPOT more often"
            )

        bot.send_message(user_id, spin_msg)
        time.sleep(1.2)

        bot.send_message(user_id, result_msg)
        bot.send_message(user_id, secret_msg)

        # 🏠 Показываем главное меню после кейса
        show_menu(chat_id, user_id)

    elif call.data == 'buy_30':
        bot.answer_callback_query(call.id)
        send_invoice(user_id, 50, "sub_3d")
    elif call.data == 'buy_250':
        bot.answer_callback_query(call.id)
        send_invoice(user_id, 500, "sub_30d")
    elif call.data == 'buy_1000':
        bot.answer_callback_query(call.id)
        send_invoice(user_id, 1500, "sub_forever")
    # ── Реферальная программа ─────────────────────────────
    elif call.data == 'ref_program':
        bot.answer_callback_query(call.id)
        show_ref(chat_id, user_id, mid)

    elif call.data == 'exchange_bonus_week':
        bot.answer_callback_query(call.id)
        user = ensure_user(user_id)

        if user['bonus'] >= 50:
            # Deduct 50 bonuses
            update_user(user_id, bonus=user['bonus'] - 50)

            # Add subscription for 7 days
            add_subscription(user_id, 'bonus_week', 7)

            # Create one-time invite link
            invite_link = create_one_time_invite_link(SECRET_CHANNEL_ID)

            if invite_link:
                bot.send_message(
                    chat_id,
                    f"🎁 Successfully exchanged 50 bonuses for 7 days access!\n\n🔗 Your invite link: {invite_link}",
                    parse_mode='HTML'
                )
            else:
                bot.send_message(
                    chat_id,
                    "❌ Failed to create invite link. Please contact support.",
                    parse_mode='HTML'
                )
        else:
            bot.send_message(
                chat_id,
                f"❌ You need at least 50 bonuses to exchange. You currently have {user['bonus']} bonuses.",
                parse_mode='HTML'
            )

    # ── Главное меню ──────────────────────────────────────
    elif call.data == 'main_menu':
        bot.answer_callback_query(call.id)
        # Удаляем старое сообщение и отправляем новое с фото
        try:
            bot.delete_message(chat_id, mid)
        except Exception:
            pass
        show_menu(chat_id, user_id)

    # ── Смена языка ───────────────────────────────────────
    elif call.data == 'language':
        bot.answer_callback_query(call.id)
        show_lang(chat_id, user_id, mid)
    #---одноразовые ссылкки------
    elif call.data == 'get_chat_link':
        user = ensure_user(user_id)
        now = time.time()

        # ⛔ Проверка cooldown (АНТИ-СПАМ)
        time_passed = now - user['last_link_time']
        if time_passed < LINK_COOLDOWN:
            remaining = int(LINK_COOLDOWN - time_passed)
            lang = ensure_user(user_id).get('lang') or 'en'
            if lang == 'ru':
                msg = f"⏳ Подожди {remaining} сек перед новой ссылкой"
            elif lang == 'es':
                msg = f"⏳ Espera {remaining} seg antes del nuevo enlace"
            else:
                msg = f"⏳ Wait {remaining} sec before new link"
            bot.answer_callback_query(
                call.id,
                msg,
                show_alert=True
            )
            return

        # ✅ обновляем время последней ссылки
        update_user(user_id, last_link_time=now)

        lang = ensure_user(user_id).get('lang') or 'en'
        if lang == 'ru':
            gen_msg = "Генерирую ссылку..."
        elif lang == 'es':
            gen_msg = "Generando enlace..."
        else:
            gen_msg = "Generating link..."
        bot.answer_callback_query(call.id, gen_msg)

        # Находим чат
        chat_info = next((ch for ch in HUB_CHANNELS if ch.get('type') == 'chat'), None)
        if not chat_info:
            lang = ensure_user(user_id).get('lang') or 'en'
            if lang == 'ru':
                msg = "Чат не настроен."
            elif lang == 'es':
                msg = "Chat no configurado."
            else:
                msg = "Chat not configured."
            bot.send_message(chat_id, msg)
            return

        chat_id_for_link = chat_info['chat_id']
        link = create_one_time_invite_link(chat_id_for_link)

        if link:
            lang = ensure_user(user_id).get('lang') or 'en'
            if lang == 'ru':
                link_msg = (
                    f"🔗 Твоя одноразовая ссылка:\n{link}\n\n"
                    "⏱ Действует 24 часа и только на 1 вход"
                )
                success_msg = "✅ Ссылка отправлена в личку"
            elif lang == 'es':
                link_msg = (
                    f"🔗 Tu enlace de un solo uso:\n{link}\n\n"
                    "⏱ Válido por 24 horas y solo para 1 entrada"
                )
                success_msg = "✅ Enlace enviado al privado"
            else:
                link_msg = (
                    f"🔗 Your one-time link:\n{link}\n\n"
                    "⏱ Valid for 24 hours and only for 1 entry"
                )
                success_msg = "✅ Link sent to private chat"

            bot.send_message(user_id, link_msg)

            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton(t(user_id, 'back_btn'), callback_data='main_menu'))

            bot.send_message(chat_id, success_msg, reply_markup=markup)
        else:
            lang = ensure_user(user_id).get('lang') or 'en'
            if lang == 'ru':
                msg = "❌ Ошибка создания ссылки"
            elif lang == 'es':
                msg = "❌ Error al crear enlace"
            else:
                msg = "❌ Error creating link"
            bot.send_message(chat_id, msg)
        
@bot.message_handler(commands=['menu'])
def menu_cmd(message):
    if not is_real_user(message):
        return

    user_id = message.from_user.id
    ensure_user(user_id)

    show_menu(message.chat.id, user_id)


@bot.message_handler(commands=['ref'])
def ref_cmd(message):
    if not is_real_user(message):
        return

    user_id = message.from_user.id
    ensure_user(user_id)

    show_ref(message.chat.id, user_id)


@bot.message_handler(func=lambda m: m.chat.type == 'private')
def protection(message):
    if not is_real_user(message):
        return

    user_id = message.from_user.id
    user = ensure_user(user_id)

    if message.text and message.text.startswith('/'):
        return

    if not user['lang']:
        show_lang(message.chat.id, user_id)
        return

    update_user(user_id, subscribed=True)
    reward_referrer(user_id, message.from_user.first_name or '')
    show_menu(message.chat.id, user_id)
#=======автоодобрение заявок в каналы=======
@bot.chat_join_request_handler()
def handle_all_join_requests(join_request):
    """Обрабатывает все запросы на вступление в каналы"""
    user_id = join_request.from_user.id
    chat_id = join_request.chat.id
    user_name = join_request.from_user.first_name or "Unknown"

    print(f"🔔 ПОЛУЧЕН ЗАПРОС НА ВСТУПЛЕНИЕ:")
    print(f"   Пользователь: {user_name} (ID: {user_id})")
    print(f"   Канал: {chat_id}")
    print(f"   Время: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # ✅ Проверяем, это ли секретный канал
    if chat_id == SECRET_CHANNEL_ID:
        print(f"🔒 Это секретный канал, проверяем доступ...")
        user = get_user(user_id)

        if not user:
            print(f"❌ Пользователь {user_id} не найден в базе")
            bot.decline_chat_join_request(chat_id, user_id)
            return

        # ✅ Используем новую систему подписок
        has_access = has_active_subscription(user_id, SECRET_CHANNEL_ID)

        # Проверяем старые методы для совместимости
        if not has_access:
            ref_count = len(user['referrals'])
            has_access = (
                ref_count >= 20 or
                user.get('paid_forever') == 1 or
                user.get('paid_until', 0) > time.time()
            )

        print(f"   Доступ: {has_access}")

        if has_access:
            bot.approve_chat_join_request(chat_id, user_id)
            print(f"✅ Доступ разрешён для пользователя {user_id}")

            bot.send_message(
                user_id,
                "🔥 Добро пожаловать в SD FETISH GOLD\n\n"
                "Ты получил доступ 👑"
            )
        else:
            bot.decline_chat_join_request(chat_id, user_id)
            print(f"❌ Доступ запрещён для пользователя {user_id}")

            bot.send_message(
                user_id,
                "❌ Доступ закрыт\n\n"
                "Нужно:\n"
                "• 20 рефералов\n"
                "или\n"
                "• купить доступ"
            )
        return

    # ✅ Проверяем, это ли хаб-каналы
    hub_channel_ids = [ch.get('chat_id') for ch in HUB_CHANNELS[:2] if 'chat_id' in ch]
    print(f"📋 Хаб-каналы: {hub_channel_ids}")

    if chat_id in hub_channel_ids:
        print(f"✅ Это хаб-канал, начинаем процесс добавления...")

        try:
            # ✅ Одобряем запрос на вступление
            bot.approve_chat_join_request(chat_id, user_id)
            print(f"✅ Запрос на вступление одобрен для пользователя {user_id}")

            # ✅ Запускаем цепочку onboarding через выбор языка
            send_language_selection(user_id, user_id)
            print(f"✅ Запущена цепочка onboarding для пользователя {user_id}")

        except Exception as e:
            print(f"❌ Ошибка обработки для {chat_id}: {e}")
        return

    print(f"ℹ️ Канал {chat_id} не обрабатывается ботом")
#===================подтверждение оплаты===============
@bot.pre_checkout_query_handler(func=lambda q: True)
def checkout(pre_checkout_query):
    bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
#==================успешная оплата=================
@bot.message_handler(content_types=['successful_payment'])
def got_payment(message):
    user_id = message.from_user.id
    payload = message.successful_payment.invoice_payload
    now = time.time()

    user = ensure_user(user_id)
    lang = user.get('lang') or 'en'

    # ✅ Используем новую систему подписок
    if payload == "sub_3d":
        grant_paid_subscription(user_id, 'short')
        sub_type = 'short'
    elif payload == "sub_30d":
        grant_paid_subscription(user_id, 'monthly')
        sub_type = 'monthly'
    elif payload == "sub_forever":
        # Для навсегда используем старую систему
        update_user(user_id, paid_forever=1)
        sub_type = 'forever'
    else:
        sub_type = 'unknown'

    if lang == 'ru':
        success_msg = "✅ Оплата прошла! Генерирую ссылку для входа..."
        access_msg = "🔓 Доступ открыт!\n\n👇 Нажми на ссылку ниже — она одноразовая и действует 24 часа:"
        btn_text = "🚀 Войти в канал"
        fallback_msg = "🔓 Доступ открыт!\n\nЖми кнопку ниже 👇"
        fallback_btn = "🚀 Войти"
    elif lang == 'es':
        success_msg = "✅ ¡Pago completado! Generando enlace de entrada..."
        access_msg = "🔓 ¡Acceso abierto!\n\n👇 Presiona el enlace de abajo — es de un solo uso y válido por 24 horas:"
        btn_text = "🚀 Entrar al canal"
        fallback_msg = "🔓 ¡Acceso abierto!\n\nPresiona el botón de abajo 👇"
        fallback_btn = "🚀 Entrar"
    else:
        success_msg = "✅ Payment successful! Generating entry link..."
        access_msg = "🔓 Access opened!\n\n👇 Press the link below — it's one-time and valid for 24 hours:"
        btn_text = "🚀 Enter channel"
        fallback_msg = "🔓 Access opened!\n\nPress the button below 👇"
        fallback_btn = "🚀 Enter"

    bot.send_message(user_id, success_msg)

    # ✅ НОВОЕ: создаём одноразовую ссылку и сразу отправляем
    invite_link = create_one_time_invite_link(SECRET_CHANNEL_ID, expire_seconds=86400)

    if invite_link:
        bot.send_message(
            user_id,
            access_msg,
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton(
                    btn_text,
                    url=invite_link  # ← одноразовая ссылка, а не статичная
                )
            )
        )
    else:
        # Если что-то пошло не так — даём статичную ссылку как запасной вариант
        bot.send_message(
            user_id,
            fallback_msg,
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton(
                    fallback_btn,
                    url=SECRET_CHANNEL_LINK
                )
            )
        )

# ================= ОБРАБОТЧИК ФОТО =================
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    """Обработчик для приёма фото и получения file_id"""
    if not is_real_user(message):
        return

    user_id = message.from_user.id
    user = ensure_user(user_id)

    # Проверяем, есть ли фото в сообщении
    if message.photo:
        # Получаем file_id самого большого фото (последний в списке)
        photo = message.photo[-1]
        file_id = photo.file_id

        # Отправляем file_id пользователю
        lang = user.get('lang') or 'en'
        if lang == 'ru':
            msg = f"✅ File ID получен:\n\n`{file_id}`\n\n📝 Скопируй этот ID и вставь в код вместо URL."
        elif lang == 'es':
            msg = f"✅ File ID recibido:\n\n`{file_id}`\n\n📝 Copia este ID y pégalo en el código en lugar del URL."
        else:
            msg = f"✅ File ID received:\n\n`{file_id}`\n\n📝 Copy this ID and paste it in the code instead of URL."

        bot.send_message(user_id, msg, parse_mode='Markdown')

        # Если хочешь автоматически обновить MENU_PHOTO, раскомментируй строки ниже:
        # global MENU_PHOTO
        # MENU_PHOTO = file_id
        # bot.send_message(user_id, "✅ MENU_PHOTO обновлён автоматически!")
    else:
        bot.send_message(user_id, "❌ Фото не найдено в сообщении.")

if __name__ == '__main__':
    print('Бот запущен...')
    print('⚠️  Добавь бота как администратора в оба канала!')
    print(f'⚠️  Загрузи банер и замени MENU_PHOTO на полученный file_id')
    bot.infinity_polling(skip_pending=True, timeout=10, long_polling_timeout=5)
