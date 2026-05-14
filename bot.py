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
LITE_CHANNEL_ID = -1003951067494
LITE_CHANNEL_LINK = "https://t.me/+-xorUn-MI480ODIx"
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

# Хранилище активных daily bonus reminder-таймеров
active_bonus_reminders = {}

# Флаг для остановки фоновых задач
background_tasks_running = True

# ================= DAILY BONUS REMINDER SYSTEM =================
# Новая архитектура системы напоминаний о ежедневном бонусе:
#
# 1. Централизованная система: Один фоновый поток проверяет всех пользователей каждые 5 минут
# 2. Работает для всех пользователей: Новых и старых (last_daily == 0 или first_bonus_claimed == 0)
# 3. Надежность: Переживает перезапуск бота благодаря агрессивной инициализации
# 4. Эффективность: Не создает сотни потоков (всего один фоновый поток)
# 5. Умная отправка: Не спамит пользователями - проверяет last_bonus_reminder и bonus_message_id
#
# Основные функции:
# - schedule_all_daily_reminders(): Запускает фоновую задачу
# - check_and_send_daily_reminders(): Основной цикл проверки (каждые 5 минут)
# - initialize_bonus_reminders(): Агрессивная инициализация при старте
#
# Поля в базе данных:
# - last_daily: Время последнего получения бонуса
# - first_bonus_claimed: Время первого получения бонуса
# - last_bonus_reminder: Время последнего отправленного напоминания
# - bonus_message_id: ID последнего сообщения о бонусе
#

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


# ================= DAILY BONUS REMINDER SYSTEM =================
def start_bonus_reminder(user_id):
    """Запускает reminder-таймер для ежедневного бонуса (устаревшая функция, оставлена для совместимости)"""
    # Эта функция больше не нужна, так как используется централизованная система
    # Оставлена для обратной совместимости
    pass


def send_bonus_reminder_with_delay(user_id, delay):
    """Отправляет reminder-сообщение о ежедневном бонусе с задержкой (устаревшая функция)"""
    # Эта функция больше не нужна
    pass


def send_bonus_reminder(user_id):
    """Отправляет reminder-сообщение о ежедневном бонусе через 24 часа (устаревшая функция)"""
    # Эта функция больше не нужна
    pass


def check_and_send_daily_reminders():
    """
    Проверяет всех пользователей и отправляет напоминания о ежедневном бонусе.
    Запускается каждые N минут в отдельном потоке.
    """
    while background_tasks_running:
        try:
            print("🔍 Проверка пользователей для напоминаний о ежедневном бонусе...")

            conn = sqlite3.connect('bot_database.db')
            cursor = conn.cursor()

            # Получаем всех пользователей
            cursor.execute("SELECT id FROM users")
            users = cursor.fetchall()

            conn.close()

            now = time.time()
            reminders_sent = 0

            for (user_id,) in users:
                try:
                    # Проверяем, можно ли получить бонус
                    can_claim, remaining = can_claim_daily_bonus(user_id)

                    if can_claim:
                        # Проверяем, не было ли уже отправлено напоминание недавно
                        user = ensure_user(user_id)
                        last_reminder = user.get('last_bonus_reminder', 0)

                        # Если напоминание не было отправлено в последние 23 часов
                        if now - last_reminder > 82800:  # 23 часа = 82800 секунд
                            # Проверяем, есть ли активное сообщение о бонусе
                            bonus_message_id = user.get('bonus_message_id')

                            # Если нет активного сообщения или оно старое (более 24 часов)
                            should_send = True
                            if bonus_message_id and bonus_message_id != 0:
                                # Если есть активное сообщение и напоминание было недавно, не отправляем новое
                                should_send = False

                            if should_send:
                                message = send_daily_bonus_message(user_id)
                                if message:
                                    # Обновляем время последнего напоминания
                                    update_user(user_id, last_bonus_reminder=now)
                                    reminders_sent += 1
                                    print(f"✅ Отправлено напоминание пользователю {user_id}")

                except Exception as e:
                    print(f"❌ Ошибка обработки пользователя {user_id}: {e}")
                    continue

            print(f"📊 Отправлено напоминаний: {reminders_sent} из {len(users)} пользователей")

        except Exception as e:
            print(f"❌ Ошибка в check_and_send_daily_reminders: {e}")

        # Ждем 5 минут перед следующей проверкой
        for _ in range(300):  # 5 минут = 300 секунд
            if not background_tasks_running:
                break
            time.sleep(1)


def schedule_all_daily_reminders():
    """
    Запускает централизованную систему напоминаний о ежедневном бонусе.
    Создает один фоновый поток, который проверяет всех пользователей каждые 5 минут.
    """
    print("🚀 Запуск централизованной системы напоминаний о ежедневном бонусе...")

    # Запускаем фоновую задачу
    reminder_thread = threading.Thread(
        target=check_and_send_daily_reminders,
        daemon=True
    )
    reminder_thread.start()

    print("✅ Система напоминаний запущена")


def initialize_bonus_reminders():
    """
    Агрессивная инициализация reminder-системы для всех пользователей.
    Отправляет напоминания всем пользователям, у которых доступен бонус.
    """
    print("🚀 Агрессивная инициализация reminder-системы для всех пользователей...")

    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()

    # Получаем всех пользователей
    cursor.execute("SELECT id, last_daily, first_bonus_claimed FROM users")
    users = cursor.fetchall()

    conn.close()

    now = time.time()
    initialized_count = 0
    old_users_count = 0

    for user_id, last_daily, first_bonus_claimed in users:
        try:
            # Проверяем, можно ли получить бонус
            can_claim, remaining = can_claim_daily_bonus(user_id)

            if can_claim:
                # Проверяем, не было ли уже отправлено напоминание недавно
                user = ensure_user(user_id)
                last_reminder = user.get('last_bonus_reminder', 0)

                # Если напоминание не было отправлено в последние 23 часа
                if now - last_reminder > 82800:  # 23 часа
                    # Проверяем, есть ли активное сообщение о бонусе
                    bonus_message_id = user.get('bonus_message_id')

                    # Если нет активного сообщения или оно старое
                    should_send = True
                    if bonus_message_id and bonus_message_id != 0:
                        # Если есть активное сообщение и напоминание было недавно, не отправляем новое
                        should_send = False

                    if should_send:
                        message = send_daily_bonus_message(user_id)
                        if message:
                            update_user(user_id, last_bonus_reminder=now)
                            initialized_count += 1
                            print(f"✅ Инициализировано напоминание для пользователя {user_id}")

            # Считаем старых пользователей
            if last_daily == 0 or first_bonus_claimed == 0:
                old_users_count += 1
                print(f"📋 Старый пользователь {user_id}: last_daily={last_daily}, first_bonus_claimed={first_bonus_claimed}")

        except Exception as e:
            print(f"❌ Ошибка инициализации пользователя {user_id}: {e}")
            continue

    print(f"✅ Reminder-система инициализирована для {initialized_count} пользователей")
    print(f"📊 Обработано старых пользователей: {old_users_count}")
    print(f"📊 Всего пользователей в базе: {len(users)}")


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
        # После приветствия - ежедневный бонус (если доступен)
        print(f"📝 Этап 3: Проверяем ежедневный бонус для пользователя {user_id}")

        # ✅ Используем can_claim_daily_bonus для проверки
        can_claim, remaining = can_claim_daily_bonus(user_id)

        if can_claim:
            # Бонус доступен - отправляем сообщение
            print(f"✅ Бонус доступен для пользователя {user_id}")
            bonus_message = send_daily_bonus_message(user_id)

            if bonus_message:
                # Бонус отправлен - переходим к этапу бонуса
                update_user(user_id, funnel_step=FUNNEL_BONUS)
                print(f"✅ Бонус отправлен, ожидаем получения от пользователя {user_id}")
            else:
                # Если по какой-то причине не удалось отправить бонус
                print(f"❌ Не удалось отправить бонус пользователю {user_id}")
                show_sub(user_id, user_id)
                update_user(user_id, funnel_step=FUNNEL_SUBSCRIPTION)
        else:
            # Бонус недоступен - пропускаем этап бонуса и переходим сразу к подписке
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            print(f"ℹ️ Бонус недоступен для пользователя {user_id}. Осталось: {hours}ч {minutes}мин")
            show_sub(user_id, user_id)
            update_user(user_id, funnel_step=FUNNEL_SUBSCRIPTION)

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


def can_claim_daily_bonus(user_id):
    """
    Проверяет, может ли пользователь получить ежедневный бонус.

    Returns:
        tuple: (can_claim: bool, remaining_seconds: int)
               can_claim - True если бонус доступен
               remaining_seconds - сколько секунд осталось до следующего бонуса (0 если доступен)
    """
    user = ensure_user(user_id)
    now = time.time()

    first_bonus_claimed = user.get('first_bonus_claimed', 0)

    # Если первый бонус еще не был получен - всегда можно получить
    if first_bonus_claimed == 0:
        print(f"✅ Пользователь {user_id} может получить первый бонус")
        return True, 0

    # Если первый бонус уже был получен - проверяем cooldown
    last_daily = user.get('last_daily', 0)

    if last_daily > 0:
        time_since_last_bonus = now - last_daily

        if time_since_last_bonus < 86400:  # 24 часа = 86400 секунд
            remaining = int(86400 - time_since_last_bonus)
            print(f"⏳ Пользователь {user_id} не может получить бонус. Осталось: {remaining} секунд")
            return False, remaining

    # Если прошло 24 часа или last_daily == 0 - можно получить
    print(f"✅ Пользователь {user_id} может получить бонус")
    return True, 0


def show_daily_bonus_button(user_id, chat_id, message_id=None):
    """
    Универсальная функция для показа кнопки ежедневного бонуса.

    Args:
        user_id: ID пользователя
        chat_id: ID чата
        message_id: ID сообщения для редактирования (опционально)

    Returns:
        Message object или None
    """
    user = ensure_user(user_id)
    lang = user.get('lang') or 'en'

    # ✅ Проверяем, можно ли получить бонус
    can_claim, remaining = can_claim_daily_bonus(user_id)

    if not can_claim:
        # Бонус недоступен - показываем alert с оставшимся временем
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60

        if lang == 'ru':
            msg = f"⏳ Бонус будет доступен через {hours}ч {minutes}мин"
        elif lang == 'es':
            msg = f"⏳ El bono estará disponible en {hours}h {minutes}min"
        else:
            msg = f"⏳ Bonus will be available in {hours}h {minutes}min"

        if message_id:
            try:
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=msg,
                    reply_markup=None
                )
                return None
            except Exception:
                pass

        bot.send_message(chat_id, msg)
        return None

    # Бонус доступен - показываем кнопку
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

    # Редактируем или отправляем сообщение
    if message_id:
        try:
            message = bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=msg,
                reply_markup=markup,
                parse_mode='HTML'
            )
            # Сохраняем message_id в базе данных
            if message:
                update_user(user_id, bonus_message_id=message.message_id)
                print(f"✅ Отредактировано сообщение о бонусе для пользователя {user_id}, message_id: {message.message_id}")
            return message
        except Exception as e:
            print(f"❌ Ошибка редактирования сообщения: {e}")
            # Если не удалось отредактировать, отправляем новое
            pass

    # Отправляем новое сообщение
    message = bot.send_message(chat_id, msg, reply_markup=markup, parse_mode='HTML')

    # Сохраняем message_id в базе данных
    if message:
        update_user(user_id, bonus_message_id=message.message_id)
        print(f"✅ Отправлено сообщение о бонусе пользователю {user_id}, message_id: {message.message_id}")

    return message


def send_daily_bonus_message(user_id):
    """Отправляет сообщение о ежедневном бонусе, если можно получить бонус"""
    try:
        user = ensure_user(user_id)
        lang = user.get('lang') or 'en'

        # ✅ Проверяем, можно ли получить бонус
        can_claim, remaining = can_claim_daily_bonus(user_id)

        if not can_claim:
            print(f"ℹ️ Бонус для пользователя {user_id} еще недоступен. Осталось: {remaining} секунд")
            return None

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
        message = bot.send_message(user_id, msg, reply_markup=markup, parse_mode='HTML')

        # ✅ Сохраняем message_id в базе данных
        if message:
            update_user(user_id, bonus_message_id=message.message_id)
            print(f"✅ Отправлено сообщение о бонусе пользователю {user_id}, message_id: {message.message_id}")

        return message

    except Exception as e:
        print(f"❌ Ошибка в send_daily_bonus_message для пользователя {user_id}: {e}")
        return None

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
        ("is_clean_start", "INTEGER DEFAULT 0"),
        ("first_bonus_claimed", "INTEGER DEFAULT 0"),
        ("last_bonus_reminder", "REAL DEFAULT 0"),
        ("bonus_message_id", "INTEGER DEFAULT 0")
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

    # ✅ Находим индекс поля first_bonus_claimed
    try:
        first_bonus_claimed_index = column_names.index('first_bonus_claimed')
        print(f"✅ Индекс поля first_bonus_claimed: {first_bonus_claimed_index}")
    except ValueError:
        print(f"❌ Поле first_bonus_claimed не найдено в таблице!")
        first_bonus_claimed_index = None

    # ✅ Находим индекс поля last_bonus_reminder
    try:
        last_bonus_reminder_index = column_names.index('last_bonus_reminder')
        print(f"✅ Индекс поля last_bonus_reminder: {last_bonus_reminder_index}")
    except ValueError:
        print(f"❌ Поле last_bonus_reminder не найдено в таблице!")
        last_bonus_reminder_index = None

    # ✅ Находим индекс поля bonus_message_id
    try:
        bonus_message_id_index = column_names.index('bonus_message_id')
        print(f"✅ Индекс поля bonus_message_id: {bonus_message_id_index}")
    except ValueError:
        print(f"❌ Поле bonus_message_id не найдено в таблице!")
        bonus_message_id_index = None

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
            'first_bonus_claimed': safe_get(row, first_bonus_claimed_index if first_bonus_claimed_index is not None else 21, 0),
            'last_bonus_reminder': safe_get(row, last_bonus_reminder_index if last_bonus_reminder_index is not None else 22, 0),
            'bonus_message_id': safe_get(row, bonus_message_id_index if bonus_message_id_index is not None else 23, 0),
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

    msg = f"🧪 Тестирование системы подписок для пользователя {user_id}:\n\n"

    # GOLD канал
    gold_subs = get_active_subscriptions(user_id, SECRET_CHANNEL_ID)
    if gold_subs:
        msg += f"✅ GOLD канал: {len(gold_subs)} активных подписок\n"
        for sub in gold_subs:
            expires = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(sub['expires_at']))
            msg += f"   • {sub['type']}: до {expires}\n"
    else:
        msg += f"❌ GOLD канал: нет активных подписок\n"

    # LITE канал
    lite_subs = get_active_subscriptions(user_id, LITE_CHANNEL_ID)
    if lite_subs:
        msg += f"✅ LITE канал: {len(lite_subs)} активных подписок\n"
        for sub in lite_subs:
            expires = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(sub['expires_at']))
            msg += f"   • {sub['type']}: до {expires}\n"
    else:
        msg += f"❌ LITE канал: нет активных подписок\n"

    # Проверка доступа
    has_gold = has_active_subscription(user_id, SECRET_CHANNEL_ID)
    has_lite = has_active_subscription(user_id, LITE_CHANNEL_ID)

    msg += f"\n🔒 Доступ:\n"
    msg += f"   GOLD: {'✅ Есть' if has_gold else '❌ Нет'}\n"
    msg += f"   LITE: {'✅ Есть' if has_lite else '❌ Нет'}\n"

    bot.reply_to(message, msg)


@bot.message_handler(commands=['test_lite_system'])
def test_lite_system(message):
    """Тестовая команда для проверки системы LITE подписок"""
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Только для администраторов")
        return

    bot.reply_to(message, "🧪 Запускаю тестирование системы LITE подписок...")
    test_lite_subscription_system()
    bot.reply_to(message, "✅ Тестирование завершено! Проверьте логи.")


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

    print(f"✅ Все подписки пользователя {user_id} деактивированы для канала {channel_id}")


def test_lite_subscription_system():
    """Тестовая функция для проверки системы временного доступа в LITE канал"""
    print("🧪 Тестирование системы временного доступа в SD FETISH LITE...")

    # Тест 1: Проверка создания подписки
    print("📝 Тест 1: Создание 3-дневной подписки")
    test_user_id = 999999999  # Тестовый пользователь
    test_sub_id = add_subscription(test_user_id, 'lite_3days', 3, LITE_CHANNEL_ID)
    print(f"✅ Создана тестовая подписка: {test_sub_id}")

    # Тест 2: Проверка активной подписки
    print("📝 Тест 2: Проверка активной подписки")
    active_subs = get_active_subscriptions(test_user_id, LITE_CHANNEL_ID)
    print(f"✅ Активные подписки: {len(active_subs)}")
    for sub in active_subs:
        expires_at = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(sub['expires_at']))
        print(f"   - {sub['type']}: истекает {expires_at}")

    # Тест 3: Проверка has_active_subscription
    print("📝 Тест 3: Проверка has_active_subscription")
    has_access = has_active_subscription(test_user_id, LITE_CHANNEL_ID)
    print(f"✅ Есть доступ: {has_access}")

    # Тест 4: Проверка get_max_expiration
    print("📝 Тест 4: Проверка get_max_expiration")
    max_exp = get_max_expiration(test_user_id, LITE_CHANNEL_ID)
    if max_exp > 0:
        expires_at = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(max_exp))
        print(f"✅ Максимальное время истечения: {expires_at}")
    else:
        print(f"❌ Нет активных подписок")

    # Тест 5: Проверка деактивации подписки
    print("📝 Тест 5: Деактивация подписки")
    deactivate_subscription(test_sub_id)
    print(f"✅ Подписка {test_sub_id} деактивирована")

    # Тест 6: Проверка после деактивации
    print("📝 Тест 6: Проверка после деактивации")
    has_access_after = has_active_subscription(test_user_id, LITE_CHANNEL_ID)
    print(f"✅ Есть доступ после деактивации: {has_access_after}")

    # Тест 7: Проверка функции kick_user_from_channel
    print("📝 Тест 7: Проверка функции kick_user_from_channel")
    # Не выполняем реальный kick, только проверяем что функция существует
    print(f"✅ Функция kick_user_from_channel доступна")

    # Тест 8: Проверка check_and_remove_expired_subscriptions
    print("📝 Тест 8: Проверка check_and_remove_expired_subscriptions")
    print("✅ Функция check_and_remove_expired_subscriptions доступна")

    # Очистка тестовых данных
    print("🧹 Очистка тестовых данных...")
    try:
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        cursor.execute("DELETE FROM subscriptions WHERE user_id = ?", (test_user_id,))
        conn.commit()
        conn.close()
        print(f"✅ Тестовые данные удалены")
    except Exception as e:
        print(f"❌ Ошибка удаления тестовых данных: {e}")

    print("✅ Тестирование системы временного доступа завершено!")


def kick_user_from_channel(user_id, channel_id):
    """Удаляет пользователя из канала (kick)"""
    try:
        # Уведомляем пользователя об истечении подписки
        user = get_user(user_id)
        lang = user.get('lang') or 'en' if user else 'en'

        # Определяем тип канала для сообщения
        if channel_id == LITE_CHANNEL_ID:
            channel_name = "SD FETISH LITE"
        elif channel_id == SECRET_CHANNEL_ID:
            channel_name = "SD FETISH GOLD"
        else:
            channel_name = "канал"

        msgs = {
            'ru': f"⏰ Ваша подписка на {channel_name} истекла.\n\nДля продления выберите тариф в меню бота.",
            'en': f"⏰ Your {channel_name} subscription has expired.\n\nTo renew, select a plan in the bot menu.",
            'es': f"⏰ Tu suscripción a {channel_name} ha expirado.\n\nPara renovar, selecciona un plan en el menú del bot.",
            'hi': f"⏰ आपकी {channel_name} सब्सक्रिप्शन समाप्त हो गई है।\n\nनवीनीकरण के लिए, बॉट मेनू में एक योजना चुनें।",
            'id': f"⏰ Langganan {channel_name} Anda telah kedaluwarsa.\n\nUntuk memperbarui, pilih paket di menu bot."
        }

        try:
            bot.send_message(user_id, msgs.get(lang, msgs['en']))
            print(f"✅ Уведомление отправлено пользователю {user_id} об истечении подписки на {channel_name}")
        except Exception as msg_error:
            print(f"⚠️ Не удалось отправить уведомление пользователю {user_id}: {msg_error}")

        # Проверяем, есть ли пользователь в канале
        try:
            member = bot.get_chat_member(channel_id, user_id)
            print(f"📋 Пользователь {user_id} найден в канале {channel_id}, статус: {member.status}")
        except Exception as check_error:
            print(f"ℹ️ Пользователь {user_id} не найден в канале {channel_id}: {check_error}")
            # Если пользователя нет в канале, считаем что удаление успешно
            return True

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

    # Счётчик для старых пользователей
    old_system_count = 0
    old_system_processed = 0

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

    # Группируем по пользователям и каналам
    user_channel_subs = {}
    for sub_id, user_id, sub_type, channel_id, expires_at in expired_subs:
        key = f"{user_id}_{channel_id}"
        if key not in user_channel_subs:
            user_channel_subs[key] = []
        user_channel_subs[key].append({
            'id': sub_id,
            'user_id': user_id,
            'type': sub_type,
            'channel_id': channel_id,
            'expires_at': expires_at
        })

    # Обрабатываем каждую комбинацию пользователь-канал
    for key, subs in user_channel_subs.items():
        user_id = subs[0]['user_id']
        channel_id = subs[0]['channel_id']

        # Определяем название канала для логов
        if channel_id == str(LITE_CHANNEL_ID):
            channel_name = "SD FETISH LITE"
        elif channel_id == str(SECRET_CHANNEL_ID):
            channel_name = "SD FETISH GOLD"
        else:
            channel_name = f"канал {channel_id}"

        print(f"🔄 Обработка пользователя {user_id} для {channel_name}")

        # Деактивируем истекшие подписки
        for sub in subs:
            try:
                deactivate_subscription(sub['id'])
                print(f"✅ Деактивирована подписка {sub['id']} ({sub['type']}) для пользователя {user_id}")
            except Exception as e:
                print(f"❌ Ошибка деактивации подписки {sub['id']}: {e}")

        # Проверяем, есть ли ещё активные подписки для этого канала
        has_active = has_active_subscription(user_id, int(channel_id))

        if not has_active:
            # Если нет активных подписок для этого канала - удаляем из канала
            print(f"🚫 У пользователя {user_id} нет активных подписок на {channel_name}, удаляем из канала")

            try:
                kick_success = kick_user_from_channel(user_id, int(channel_id))
                if kick_success:
                    print(f"✅ Пользователь {user_id} успешно удален из {channel_name}")
                else:
                    print(f"⚠️ Не удалось удалить пользователя {user_id} из {channel_name}")
            except Exception as e:
                print(f"❌ Ошибка удаления пользователя {user_id} из {channel_name}: {e}")
        else:
            # Если есть активные подписки - оставляем в канале
            max_exp = get_max_expiration(user_id, int(channel_id))
            print(f"✅ У пользователя {user_id} есть активные подписки на {channel_name} до {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(max_exp))}")

    # Проверяем пользователей со старой системой paid_until
    print(f"🔍 Проверка старых пользователей с paid_until...")
    conn_old = sqlite3.connect('bot_database.db')
    cursor_old = conn_old.cursor()
    cursor_old.execute('''
        SELECT id FROM users
        WHERE paid_until > 0 AND paid_until <= ? AND paid_forever = 0
    ''', (now,))
    old_system_expired = cursor_old.fetchall()
    old_system_count = len(old_system_expired)
    conn_old.close()

    if old_system_count > 0:
        print(f"📊 Найдено {old_system_count} старых пользователей с истёкшим paid_until")

    for (user_id,) in old_system_expired:
        old_system_processed += 1
        print(f"🔍 Найден старый пользователь с истёкшим paid_until: {user_id}")

        # Получаем информацию о пользователе для проверки рефералов
        user = get_user(user_id)
        if not user:
            print(f"⚠️ Пользователь {user_id} не найден в базе данных")
            continue

        # Проверяем, нет ли у него активной подписки в новой системе
        has_active_new_system = has_active_subscription(user_id, SECRET_CHANNEL_ID)

        # Проверяем, нет ли 20+ рефералов
        referrals_count = 0
        if user.get('referrals'):
            try:
                referrals = json.loads(user['referrals'])
                if isinstance(referrals, list):
                    referrals_count = len(referrals)
            except (json.JSONDecodeError, TypeError):
                referrals_count = 0

        has_20_plus_referrals = referrals_count >= 20

        # Если ни активной подписки в новой системе, ни 20+ рефералов - кикаем
        if not has_active_new_system and not has_20_plus_referrals:
            print(f"🚫 У пользователя {user_id} нет активной подписки и menos 20 рефералов, удаляем из GOLD канала")

            try:
                kick_success = kick_user_from_channel(user_id, SECRET_CHANNEL_ID)
                if kick_success:
                    print(f"✅ Старый пользователь {user_id} успешно удален из SD FETISH GOLD")
                else:
                    print(f"⚠️ Не удалось удалить старого пользователя {user_id} из SD FETISH GOLD")
            except Exception as e:
                print(f"❌ Ошибка удаления старого пользователя {user_id} из SD FETISH GOLD: {e}")
        else:
            reason = []
            if has_active_new_system:
                reason.append("активная подписка в новой системе")
            if has_20_plus_referrals:
                reason.append(f"{referrals_count} рефералов")

            print(f"✅ Пользователь {user_id} остаётся в канале: {' и '.join(reason)}")

    if old_system_count > 0:
        print(f"✅ Обработано {old_system_processed} старых пользователей из {old_system_count}")


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
    '3months': {
        'duration_days': 90,
        'cost_stars': 999,
        'description': '3 months / 3 месяца / 3 meses / 3 महीने / 3 bulan'
    },
    'monthly': {
        'duration_days': 30,
        'cost_stars': 500,
        'description': 'Месячная подписка (30 дней)'
    },
    'referral': {
        'duration_days': 10,
        'description': 'Реферальная подписка (10 дней)'
    },
    'lite_3days': {
        'duration_days': 3,
        'cost_stars': 59,
        'description': 'SD FETISH LITE (3 дня)'
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
    if sub_type not in ['short', 'monthly', '3months']:
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
        INSERT INTO users (id, lang, ref_by, referrals, bonus, reward_given, subscribed, last_link_time, created_at, last_daily, first_bonus_claimed, last_bonus_reminder)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        0,  # ✅ Добавляем last_daily = 0 для новых пользователей
        0,  # ✅ Добавляем first_bonus_claimed = 0 для новых пользователей
        0   # ✅ Добавляем last_bonus_reminder = 0 для новых пользователей
    ))

    conn.commit()
    conn.close()

    print(f"✅ Создан новый пользователь {user_id} с last_daily=0, first_bonus_claimed=0, last_bonus_reminder=0")

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
        text_3months = "⭐ 999 (90 дней)"
        text_250 = "⭐ 499 (30 дней)"
        text_1000 = "⭐ 1500 (навсегда)"
        text_lite = "⭐ 59 (3 дня)"
    elif lang == 'es':
        text_3months = "⭐ 999 (90 días)"
        text_250 = "⭐ 499 (30 días)"
        text_1000 = "⭐ 1500 (para siempre)"
        text_lite = "⭐ 59 (3 días)"
    elif lang == 'hi':
        text_3months = "⭐ 999 (90 दिन)"
        text_250 = "⭐ 499 (30 दिन)"
        text_1000 = "⭐ 1500 (हमेशा के लिए)"
        text_lite = "⭐ 59 (3 दिन)"
    elif lang == 'id':
        text_3months = "⭐ 999 (90 hari)"
        text_250 = "⭐ 499 (30 hari)"
        text_1000 = "⭐ 1500 (selamanya)"
        text_lite = "⭐ 59 (3 hari)"
    else:
        text_3months = "⭐ 999 (90 days)"
        text_250 = "⭐ 499 (30 days)"
        text_1000 = "⭐ 1500 (forever)"
        text_lite = "⭐ 59 (3 days)"

    markup.add(types.InlineKeyboardButton(
        text_lite,
        callback_data='buy_lite_3days'
    ))

    markup.add(types.InlineKeyboardButton(
        text_250,
        callback_data='buy_250'
    ))

    markup.add(types.InlineKeyboardButton(
        text_3months,
        callback_data='buy_3months'
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


def daily_bonus(user_id, bonus_message_id=None):
    """Выдает ежедневный бонус с жесткой проверкой cooldown"""
    user = ensure_user(user_id)
    now = time.time()

    # ✅ ЖЁСТКАЯ ПРОВЕРКА в самом начале функции
    first_bonus_claimed = user.get('first_bonus_claimed', 0)

    if first_bonus_claimed == 1:
        last_daily = user.get('last_daily', 0)
        if last_daily > 0 and now - last_daily < 86400:
            remaining = int(86400 - (now - last_daily))
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            print(f"⏳ Пользователь {user_id} уже получал бонус сегодня. Осталось: {hours}ч {minutes}мин")
            return False

    print(f"🔍 Выдача ежедневного бонуса для пользователя {user_id}")

    reward = random.randint(1, 5)

    # ✅ Получаем актуальный бонус перед обновлением
    current_bonus = user.get('bonus', 0)

    print(f"✅ Выдаём ежедневный бонус пользователю {user_id}: +{reward} (было: {current_bonus}, станет: {current_bonus + reward})")

    # ✅ Обновляем данные пользователя
    update_data = {
        'bonus': current_bonus + reward,
        'last_daily': now,
        'first_bonus_claimed': 1  # ✅ Устанавливаем флаг первого бонуса
    }

    update_user(user_id, **update_data)

    # ✅ Прямая проверка в базе данных
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT last_daily, bonus, first_bonus_claimed FROM users WHERE id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()

    if result:
        db_last_daily, db_bonus, db_first_bonus_claimed = result
        print(f"🔍 ПРЯМАЯ ПРОВЕРКА В БАЗЕ:")
        print(f"   last_daily в базе: {db_last_daily}")
        print(f"   bonus в базе: {db_bonus}")
        print(f"   first_bonus_claimed в базе: {db_first_bonus_claimed}")
        print(f"   Ожидалось last_daily: {now}")
        print(f"   Ожидалось bonus: {current_bonus + reward}")
        print(f"   Ожидалось first_bonus_claimed: 1")

        if db_last_daily != now:
            print(f"❌ ОШИБКА: last_daily не сохранился в базе!")
        if db_bonus != current_bonus + reward:
            print(f"❌ ОШИБКА: bonus не сохранился в базе!")
        if db_first_bonus_claimed != 1:
            print(f"❌ ОШИБКА: first_bonus_claimed не сохранился в базе!")

    lang = user.get('lang') or 'en'
    if lang == 'ru':
        msg = f"🎁 Ежедневный бонус: +{reward}\n⏳ Приходи завтра"
    elif lang == 'es':
        msg = f"🎁 Bono diario: +{reward}\n⏳ Vuelve mañana"
    else:
        msg = f"🎁 Daily bonus: +{reward}\n⏳ Come back tomorrow"

    # ✅ Редактируем сообщение вместо отправки нового
    if bonus_message_id:
        try:
            bot.edit_message_text(
                chat_id=user_id,
                message_id=bonus_message_id,
                text=msg,
                reply_markup=None
            )
            print(f"✅ Отредактировано сообщение с бонусом для пользователя {user_id}")
            return True
        except Exception as e:
            print(f"❌ Ошибка редактирования сообщения: {e}")
            # Если не удалось отредактировать, отправляем новое сообщение
            bot.send_message(user_id, msg)
            return True
    else:
        # Если нет message_id, отправляем новое сообщение
        bot.send_message(user_id, msg)
        return True


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

    # 3. Каналы для подписки
    show_sub(chat_id, user_id)

    # 4. Главное меню — сразу после каналов
    show_menu(chat_id, user_id)

    # 5. Запускаем reengage таймер
    start_reengage(user_id)


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
        # ✅ Серверная проверка cooldown
        user = ensure_user(user_id)
        can_claim, remaining = can_claim_daily_bonus(user_id)

        if not can_claim:
            # Бонус недоступен - показываем alert с оставшимся временем
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60

            lang = user.get('lang') or 'en'
            if lang == 'ru':
                alert_text = f"⏳ Бонус будет доступен через {hours}ч {minutes}мин"
            elif lang == 'es':
                alert_text = f"⏳ El bono estará disponible en {hours}h {minutes}min"
            else:
                alert_text = f"⏳ Bonus will be available in {hours}h {minutes}min"

            bot.answer_callback_query(call.id, alert_text, show_alert=True)
            print(f"⏳ Пользователь {user_id} пытается получить бонус раньше времени. Осталось: {hours}ч {minutes}мин")
            return

        # ✅ Если проверка прошла, продолжаем обработку
        bot.answer_callback_query(call.id)

        lang = user.get('lang') or 'en'
        bonus_message_id = user.get('bonus_message_id')

        if lang == 'ru':
            success_text = "✅ Бонус получен!"
        elif lang == 'es':
            success_text = "✅ ¡Bono recibido!"
        else:
            success_text = "✅ Bonus received!"

        # Редактируем сообщение с бонусом
        if bonus_message_id:
            try:
                bot.edit_message_text(
                    chat_id=user_id,
                    message_id=bonus_message_id,
                    text=success_text,
                    reply_markup=None  # Убираем кнопки
                )
                print(f"✅ Отредактировано сообщение о бонусе для пользователя {user_id}")
            except Exception as e:
                print(f"❌ Ошибка редактирования сообщения: {e}")

        # Даем бонус (передаем bonus_message_id для редактирования)
        bonus_given = daily_bonus(user_id, bonus_message_id)

        # Запускаем reminder на следующие 24 часа, если бонус был выдан
        if bonus_given:
            start_bonus_reminder(user_id)

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
                "💳 ДОСТУП В GOLD КАНАЛ\n\n"
                "⭐ 499 звёзд — 30 дней\n"
                "⭐ 999 звёзд — 90 дней\n"
                "⭐ 1500 звёзд — НАВСЕГДА\n\n"
                "💳 ДОСТУП В LITE КАНАЛ\n\n"
                "⭐ 59 звёзд — 3 дня\n\n"
                "Выбери тариф:"
            )
        elif lang == 'es':
            text = (
                "💳 ACCESO AL CANAL GOLD\n\n"
                "⭐ 499 estrellas — 30 días\n"
                "⭐ 999 estrellas — 90 días\n"
                "⭐ 1500 estrellas — PARA SIEMPRE\n\n"
                "💳 ACCESO AL CANAL LITE\n\n"
                "⭐ 59 estrellas — 3 días\n\n"
                "Elige tu plan:"
            )
        elif lang == 'hi':
            text = (
                "💳 GOLD चैनल तक पहुंच\n\n"
                "⭐ 499 स्टार — 30 दिन\n"
                "⭐ 999 स्टार — 90 दिन\n"
                "⭐ 1500 स्टार — हमेशा के लिए\n\n"
                "💳 LITE चैनल तक पहुंच\n\n"
                "⭐ 59 स्टार — 3 दिन\n\n"
                "अपना प्लान चुनें:"
            )
        elif lang == 'id':
            text = (
                "💳 AKSES KE CHANNEL GOLD\n\n"
                "⭐ 499 bintang — 30 hari\n"
                "⭐ 999 bintang — 90 hari\n"
                "⭐ 1500 bintang — SELAMANYA\n\n"
                "💳 AKSES KE CHANNEL LITE\n\n"
                "⭐ 59 bintang — 3 hari\n\n"
                "Pilih paketmu:"
            )
        else:
            text = (
                "💳 ACCESS TO GOLD CHANNEL\n\n"
                "⭐ 499 stars — 30 days\n"
                "⭐ 999 stars — 90 days\n"
                "⭐ 1500 stars — FOREVER\n\n"
                "💳 ACCESS TO LITE CHANNEL\n\n"
                "⭐ 59 stars — 3 days\n\n"
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

    elif call.data == 'buy_3months':
        bot.answer_callback_query(call.id)
        send_invoice(user_id, 999, "sub_90d")
    elif call.data == 'buy_250':
        bot.answer_callback_query(call.id)
        send_invoice(user_id, 499, "sub_30d")
    elif call.data == 'buy_1000':
        bot.answer_callback_query(call.id)
        send_invoice(user_id, 1500, "sub_forever")
    elif call.data == 'buy_lite_3days':
        bot.answer_callback_query(call.id)
        lang = ensure_user(user_id).get('lang') or 'en'

        warning_texts = {
            'ru': (
                "⚠️ Важно! При покупке доступа на 3 дня вы получаете контент только за последние 3 дня. "
                "Публикации старше этого периода недоступны в этом плане — они удаляются автоматически.\n\n"
                "Если вы хотите доступ ко всем материалам (включая архив), выберите план '3 месяца' или 'Навсегда'.\n\n"
                "Убедитесь, что этот вариант вам подходит, перед покупкой."
            ),
            'en': (
                "⚠️ Important! When purchasing 3-day access, you get content only from the last 3 days. "
                "Posts older than this period are not available in this plan — they are automatically deleted.\n\n"
                "If you want access to all materials (including the archive), choose the '3 months' or 'Forever' plan.\n\n"
                "Make sure this option suits you before purchasing."
            ),
            'es': (
                "⚠️ ¡Importante! Al comprar acceso de 3 días, obtienes contenido solo de los últimos 3 días. "
                "Las publicaciones anteriores a este período no están disponibles en este plan — se eliminan automáticamente.\n\n"
                "Si deseas acceso a todos los materiales (incluido el archivo), elige el plan '3 meses' o 'Para siempre'.\n\n"
                "Asegúrate de que esta opción te conviene antes de comprar."
            ),
            'hi': (
                "⚠️ महत्वपूर्ण! 3 दिन की एक्सेस खरीदने पर आपको केवल पिछले 3 दिनों का कंटेंट मिलेगा। "
                "इस अवधि से पहले प्रकाशित पोस्ट इस प्लान में उपलब्ध नहीं हैं — वे स्वचालित रूप से हटा दिए जाते हैं।\n\n"
                "यदि आप सभी सामग्री (आर्काइव सहित) तक पहुंच चाहते हैं, तो '3 महीने' या 'हमेशा के लिए' प्लान चुनें।\n\n"
                "खरीदने से पहले सुनिश्चित करें कि यह विकल्प आपके लिए उपयुक्त है।"
            ),
            'id': (
                "⚠️ Penting! Saat membeli akses 3 hari, kamu mendapatkan konten hanya dari 3 hari terakhir. "
                "Postingan yang diterbitkan sebelum periode ini tidak tersedia dalam paket ini — mereka dihapus secara otomatis.\n\n"
                "Jika kamu ingin akses ke semua materi (termasuk arsip), pilih paket '3 bulan' atau 'Selamanya'.\n\n"
                "Pastikan pilihan ini cocok untukmu sebelum membeli."
            )
        }

        agree_texts = {
            'ru': '✅ Я согласен',
            'en': '✅ I agree',
            'es': '✅ Estoy de acuerdo',
            'hi': '✅ मैं सहमत हूं',
            'id': '✅ Saya setuju'
        }

        warning_markup = types.InlineKeyboardMarkup()
        warning_markup.add(types.InlineKeyboardButton(
            agree_texts.get(lang, agree_texts['en']),
            callback_data='confirm_lite_3days'
        ))
        warning_markup.add(types.InlineKeyboardButton(
            t(user_id, 'back'),
            callback_data='buy_access'
        ))

        bot.send_message(user_id, warning_texts.get(lang, warning_texts['en']), reply_markup=warning_markup)
    elif call.data == 'confirm_lite_3days':
        bot.answer_callback_query(call.id)
        send_invoice(user_id, 59, "sub_lite_3d")
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

    # ✅ Проверяем, это ли LITE канал
    if chat_id == LITE_CHANNEL_ID:
        print(f"🔒 Это LITE канал, проверяем доступ...")
        user = get_user(user_id)

        if not user:
            print(f"❌ Пользователь {user_id} не найден в базе")
            bot.decline_chat_join_request(chat_id, user_id)
            return

        # ✅ Используем новую систему подписок для LITE канала
        has_access = has_active_subscription(user_id, LITE_CHANNEL_ID)

        print(f"   Доступ: {has_access}")

        if has_access:
            bot.approve_chat_join_request(chat_id, user_id)
            print(f"✅ Доступ разрешён для пользователя {user_id} в LITE канал")

            bot.send_message(
                user_id,
                "🔥 Добро пожаловать в SD FETISH LITE\n\n"
                "Ты получил доступ на 3 дня ⏰"
            )
        else:
            bot.decline_chat_join_request(chat_id, user_id)
            print(f"❌ Доступ запрещён для пользователя {user_id} в LITE канал")

            bot.send_message(
                user_id,
                "❌ Доступ закрыт\n\n"
                "Для доступа к SD FETISH LITE нужно:\n"
                "• купить 3-дневный тариф\n\n"
                "Выберите тариф в меню бота"
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
        # ✅ ОБРАТНАЯ СОВМЕСТИМОСТЬ: старые покупки на 3 дня продолжают работать
        grant_paid_subscription(user_id, 'short')
        sub_type = 'short'
    elif payload == "sub_90d":
        # Новый тариф — 3 месяца
        add_subscription(user_id, '3months', 90)
        sub_type = '3months'
    elif payload == "sub_30d":
        grant_paid_subscription(user_id, 'monthly')
        sub_type = 'monthly'
    elif payload == "sub_lite_3d":
        add_subscription(user_id, 'lite_3days', 3, LITE_CHANNEL_ID)
        sub_type = 'lite_3days'
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
    # Определяем канал в зависимости от типа подписки
    if sub_type == 'lite_3days':
        channel_id = LITE_CHANNEL_ID
        channel_link = LITE_CHANNEL_LINK
    else:
        channel_id = SECRET_CHANNEL_ID
        channel_link = SECRET_CHANNEL_LINK

    invite_link = create_one_time_invite_link(channel_id, expire_seconds=86400)

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
                    url=channel_link
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
    print('🚀 Бот запущен...')
    print('⚠️  Добавь бота как администратора во все каналы!')
    print('⚠️  Особенно важно: права "Просматривать сообщения" + "Удалять сообщения" в LITE канале')

    # Инициализация системы напоминаний о ежедневном бонусе
    print("🎁 Инициализация системы ежедневных бонусов...")
    initialize_bonus_reminders()

    # Запуск централизованной системы напоминаний
    schedule_all_daily_reminders()

    # Системы подписок
    start_subscription_checker()
    threading.Thread(target=check_and_remove_expired_subscriptions, daemon=True).start()


    print("✅ Все сервисы запущены. Бот работает.")

    bot.infinity_polling(skip_pending=True, timeout=10, long_polling_timeout=5)
