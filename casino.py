import asyncio
import json
import os
import random
import re
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from telethon import TelegramClient
from telethon.tl.functions.payments import GetSavedStarGiftsRequest, TransferStarGiftRequest
from telethon.tl.types import InputSavedStarGiftUser

# ========== КОНФИГУРАЦИЯ ==========

BOT_TOKEN = "8623972147:AAHlKANbbUTMpk7MGuQllkxaAxmoG7UupL8"

# Данные от API Telegram (берутся с my.telegram.org)
API_ID = 30755567
API_HASH = "31873c92ac5c9ce1c0c9ac026d284091"

# Аккаунт, с которого берутся и передаются подарки
GIFT_SOURCE_USERNAME = "FluxxReleyer"

CONFIG_FILE = "config.json"

# ========== ИНИЦИАЛИЗАЦИЯ ==========

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()

# Клиент для работы с аккаунтом @FluxxReleyer
# Сессия сохраняется в session_name.session — НЕ УДАЛЯЙ ЭТОТ ФАЙЛ!
client = TelegramClient("session_name", API_ID, API_HASH)

# Кэш подарков @FluxxReleyer
fluxx_gifts = []

# Отслеживание последних выпавших подарков (чтобы не повторялись)
recent_gifts_history = []
MAX_HISTORY = 3  # Не повторять последние N подарков


# ========== ФУНКЦИИ КОНФИГА ==========

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def get_user_mention(user):
    if user.username:
        return f"@{escape(user.username)}"
    return f'<a href="tg://user?id={user.id}">{escape(user.full_name)}</a>'


def get_nft_link(gift_name, gift_number, slug=None):
    """Формирует ссылку t.me/nft/slug/num"""
    if slug:
        clean_slug = slug.lower().replace(" ", "-").replace("'", "").replace("’", "").replace(".", "")
    else:
        clean_slug = gift_name.lower().replace(" ", "-").replace("'", "").replace("’", "").replace(".", "")
    
    return f"https://t.me/nft/{clean_slug}/{gift_number}"


def get_gift_html(gift_name, gift_number, slug=None):
    """HTML-ссылка на NFT"""
    gift_name_escaped = escape(gift_name)
    gift_url = escape(get_nft_link(gift_name, gift_number, slug), quote=True)
    return f'<a href="{gift_url}">{gift_name_escaped}</a>'


def is_casino_topic(message: Message, chat_id: int, casino_thread_id: int):
    if message.chat.id != chat_id:
        return False
    if message.message_thread_id == casino_thread_id:
        return True
    if message.pinned_message and message.pinned_message.message_thread_id == casino_thread_id:
        return True
    return False


def format_time_left(seconds: int) -> str:
    """Форматирует оставшееся время в дни, часы, минуты"""
    if seconds <= 0:
        return "✅ Готов к передаче"
    
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    
    parts = []
    if days > 0:
        parts.append(f"{days}д")
    if hours > 0:
        parts.append(f"{hours}ч")
    if minutes > 0:
        parts.append(f"{minutes}м")
    
    return "⏳ " + " ".join(parts) if parts else "⏳ <1м"


def parse_transfer_error(error_msg: str):
    """Парсит ошибку передачи и возвращает (тип_ошибки, секунды_до_разблокировки)"""
    match = re.search(r'STARGIFT_TRANSFER_TOO_EARLY_(\d+)', error_msg)
    if match:
        seconds = int(match.group(1))
        return "too_early", seconds, format_time_left(seconds)
    
    if "PAYMENT_REQUIRED" in error_msg:
        return "payment_required", 0, "недостаточно ⭐️"
    
    if "FLOOD" in error_msg:
        return "flood", 0, "слишком много запросов"
    
    return "unknown", 0, "ошибка отправки"


def print_gift_timers():
    """Выводит в консоль таймеры всех подарков"""
    print("\n" + "=" * 60)
    print("📦 ПОДАРКИ @FluxxReleyer:")
    print("-" * 60)
    
    for gift in fluxx_gifts:
        name = gift["name"]
        number = gift["number"]
        timer = gift.get("transfer_timer", "✅ Готов")
        stars = gift.get("transfer_stars", 0)
        
        print(f"  🎁 {name} #{number}")
        print(f"     Стоимость передачи: {stars}⭐️")
        print(f"     Статус: {timer}")
        print()
    
    print("=" * 60 + "\n")


# ========== ПОЛУЧЕНИЕ ПОДАРКОВ С @FluxxReleyer ==========

async def load_fluxx_gifts():
    """
    Загружает подарки из профиля @FluxxReleyer.
    Проверяет каждый подарок на возможность передачи (узнаёт таймер).
    """
    global fluxx_gifts
    
    try:
        entity = await client.get_entity(GIFT_SOURCE_USERNAME)
        print(f"Получен entity: {entity.id}, access_hash: {entity.access_hash}")
        
        result = await client(GetSavedStarGiftsRequest(
            peer=entity,
            offset="",
            limit=100
        ))
        
        gifts = []
        
        if hasattr(result, 'gifts'):
            for gift in result.gifts:
                try:
                    msg_id = None
                    if hasattr(gift, 'msg_id'):
                        msg_id = gift.msg_id
                    elif hasattr(gift, 'message') and hasattr(gift.message, 'id'):
                        msg_id = gift.message.id
                    
                    gift_name = "Unknown Gift"
                    gift_slug = None
                    gift_catalog_id = None
                    
                    if hasattr(gift, 'gift') and gift.gift:
                        gift_obj = gift.gift
                        if hasattr(gift_obj, 'title'):
                            gift_name = gift_obj.title
                        if hasattr(gift_obj, 'slug'):
                            gift_slug = gift_obj.slug
                        if hasattr(gift_obj, 'id'):
                            gift_catalog_id = gift_obj.id
                    
                    # Реальный номер NFT (num для уникальных)
                    gift_number = 0
                    if hasattr(gift, 'num') and gift.num:
                        gift_number = gift.num
                    elif gift_catalog_id:
                        gift_number = gift_catalog_id
                    elif msg_id:
                        gift_number = msg_id
                    
                    transfer_stars = 0
                    if hasattr(gift, 'transfer_stars'):
                        transfer_stars = gift.transfer_stars
                    elif hasattr(gift, 'gift') and hasattr(gift.gift, 'convert_stars'):
                        transfer_stars = gift.gift.convert_stars
                    
                    # Проверяем, можно ли передать (узнаём таймер)
                    transfer_timer = "✅ Готов к передаче"
                    can_transfer = True
                    
                    if msg_id:
                        # Пробуем передать самому себе, чтобы узнать таймер
                        try:
                            stargift = InputSavedStarGiftUser(msg_id=msg_id)
                            await client(TransferStarGiftRequest(
                                stargift=stargift,
                                to_id=entity
                            ))
                        except Exception as e:
                            err_msg = str(e)
                            if "STARGIFT_TRANSFER_TOO_EARLY" in err_msg:
                                _, seconds, time_str = parse_transfer_error(err_msg)
                                transfer_timer = time_str
                                can_transfer = False
                            elif "USER_MUST_BE_DIFFERENT" in err_msg or "SELF_TRANSFER" in err_msg:
                                transfer_timer = "✅ Готов к передаче"
                                can_transfer = True
                            else:
                                transfer_timer = f"❌ {err_msg[:50]}"
                                can_transfer = False
                    
                    if msg_id and gift_number:
                        gifts.append({
                            "msg_id": msg_id,
                            "name": gift_name,
                            "number": gift_number,
                            "slug": gift_slug,
                            "catalog_id": gift_catalog_id,
                            "transfer_stars": transfer_stars,
                            "can_transfer": can_transfer,
                            "transfer_timer": transfer_timer
                        })
                        print(f"✅ {gift_name} #{gift_number} | {transfer_timer}")
                    
                except Exception as e:
                    print(f"❌ Ошибка парсинга подарка: {e}")
                    continue
        
        fluxx_gifts = gifts
        print(f"\n📦 Загружено {len(fluxx_gifts)} подарков")
        
        # Выводим таймеры
        print_gift_timers()
        
        return gifts
    
    except Exception as e:
        print(f"❌ Ошибка загрузки подарков: {e}")
        return []


def get_random_fluxx_gift():
    """
    Возвращает случайный подарок из профиля @FluxxReleyer.
    Не повторяет последние MAX_HISTORY подарков.
    """
    global fluxx_gifts, recent_gifts_history
    
    if not fluxx_gifts:
        return None
    
    # Фильтруем подарки, исключая недавно выпавшие
    available = [g for g in fluxx_gifts if g["msg_id"] not in recent_gifts_history]
    
    # Если все подарки в истории — сбрасываем историю
    if not available:
        recent_gifts_history = []
        available = fluxx_gifts
    
    # Выбираем случайный
    gift = random.choice(available)
    
    # Добавляем в историю
    recent_gifts_history.append(gift["msg_id"])
    if len(recent_gifts_history) > MAX_HISTORY:
        recent_gifts_history.pop(0)
    
    return gift


# ========== ПЕРЕДАЧА ПОДАРКА ==========

async def transfer_gift_to_user(gift_msg_id: int, to_user_id: int):
    """
    Передаёт подарок с @FluxxReleyer указанному пользователю.
    """
    try:
        to_entity = await client.get_entity(to_user_id)
        stargift = InputSavedStarGiftUser(msg_id=gift_msg_id)
        
        result = await client(TransferStarGiftRequest(
            stargift=stargift,
            to_id=to_entity
        ))
        
        print(f"✅ Подарок успешно передан пользователю {to_user_id}")
        return True, "success", 0, ""
    
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Ошибка передачи: {error_msg}")
        
        err_type, seconds, time_str = parse_transfer_error(error_msg)
        return False, err_type, seconds, time_str


# ========== ОБРАБОТЧИКИ КОМАНД ==========

@dp.message(F.text == "/setcasino")
async def set_casino_topic(message: Message):
    if not message.message_thread_id:
        await message.answer("Эту команду нужно отправить внутри темы Casino.")
        return

    config = load_config()
    config["chat_id"] = message.chat.id
    config["casino_thread_id"] = message.message_thread_id
    save_config(config)

    await message.answer(
        "✅ Тема Casino сохранена.\n\n"
        f"chat_id: <code>{message.chat.id}</code>\n"
        f"message_thread_id: <code>{message.message_thread_id}</code>"
    )


@dp.message(F.pinned_message)
async def delete_pin_service_message(message: Message):
    config = load_config()
    chat_id = config.get("chat_id")
    casino_thread_id = config.get("casino_thread_id")

    if not chat_id or not casino_thread_id:
        return

    if not is_casino_topic(message, chat_id, casino_thread_id):
        return

    try:
        await message.delete()
    except Exception:
        pass


@dp.message(F.dice)
async def slot_handler(message: Message):
    config = load_config()

    chat_id = config.get("chat_id")
    casino_thread_id = config.get("casino_thread_id")

    if not chat_id or not casino_thread_id:
        return

    if not is_casino_topic(message, chat_id, casino_thread_id):
        return

    if not message.from_user:
        return

    # 🎰 jackpot / 777
    if message.dice.emoji == "🎰" and message.dice.value == 64:
        user_mention = get_user_mention(message.from_user)
        user_id = message.from_user.id

        # Берём случайный подарок (с защитой от повторов)
        gift = get_random_fluxx_gift()

        if not gift:
            text = (
                "ДЖЕЕЕКПОООТ 🔥\n\n"
                f"{user_mention} красава, ты выиграл NFT-подарок! 🏆\n"
                "(уже отправлен тебе на акк)\n\n"
                "Играй ещё и выигрывай призы до 20.000 ⭐️"
            )
            await message.reply(text)
            return

        # Формируем ссылку на подарок
        gift_html = get_gift_html(gift["name"], gift["number"], gift.get("slug"))

        # Пытаемся передать подарок
        success, err_type, seconds_left, time_str = await transfer_gift_to_user(gift["msg_id"], user_id)

        if success:
            status_text = "(уже отправлен тебе на акк)"
        else:
            if err_type == "too_early":
                status_text = f"(подарок заблокирован — подожди {time_str})"
            elif err_type == "payment_required":
                status_text = "(недостаточно ⭐️ для передачи)"
            elif err_type == "flood":
                status_text = "(слишком много запросов — попробуй позже)"
            else:
                status_text = "(ошибка отправки подарка)"

        text = (
            "ДЖЕЕЕКПОООТ 🔥\n\n"
            f"{user_mention} красава, ты выиграл этот NFT ({gift_html}) {status_text}\n\n"
            "Играй ещё и выигрывай призы до 20.000 ⭐️"
        )

        sent = await message.reply(text)

        try:
            await bot.pin_chat_message(
                chat_id=message.chat.id,
                message_id=sent.message_id,
                disable_notification=True
            )
        except Exception:
            await message.answer(
                "Не смог закрепить сообщение. Дай боту право закреплять сообщения."
            )


# ========== ЗАПУСК ==========

async def start_client():
    """Запускает Telethon клиент из существующей сессии"""
    # Запускаем БЕЗ phone — используем существующую сессию session_name.session
    await client.start()
    print("✅ Telethon клиент подключен по существующей сессии")
    
    # Загружаем подарки с проверкой таймеров
    await load_fluxx_gifts()


async def main():
    await start_client()
    print("🤖 Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())