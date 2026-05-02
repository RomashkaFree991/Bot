import asyncio
import json
import os
import random
import re
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
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

# Админы бота
ADMIN_IDS = {8667321828, 6106324512}

# ========== ИНИЦИАЛИЗАЦИЯ ==========

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()

# Клиент для работы с аккаунтом @FluxxReleyer
client = TelegramClient("session_name", API_ID, API_HASH)

# Кэш подарков @FluxxReleyer
fluxx_gifts = []

# Список отключённых подарков (msg_id) — их не выдают в джекпоте
disabled_gifts = set()

# Отслеживание последних выпавших подарков
recent_gifts_history = []
MAX_HISTORY = 3


# ========== ПРОВЕРКА АДМИНА ==========

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


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
    if slug:
        clean_slug = slug.lower().replace(" ", "-").replace("'", "").replace("’", "").replace(".", "")
    else:
        clean_slug = gift_name.lower().replace(" ", "-").replace("'", "").replace("’", "").replace(".", "")
    return f"https://t.me/nft/{clean_slug}/{gift_number}"


def get_gift_html(gift_name, gift_number, slug=None):
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
    if seconds <= 0:
        return "✅ Готов"
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
    match = re.search(r'STARGIFT_TRANSFER_TOO_EARLY_(\d+)', error_msg)
    if match:
        seconds = int(match.group(1))
        return "too_early", seconds, format_time_left(seconds)
    if "PAYMENT_REQUIRED" in error_msg:
        return "payment_required", 0, "недостаточно ⭐️"
    if "FLOOD" in error_msg:
        return "flood", 0, "слишком много запросов"
    return "unknown", 0, "ошибка"


# ========== ЗАГРУЗКА ПОДАРКОВ ==========

async def load_fluxx_gifts():
    global fluxx_gifts
    try:
        entity = await client.get_entity(GIFT_SOURCE_USERNAME)
        result = await client(GetSavedStarGiftsRequest(peer=entity, offset="", limit=100))
        
        gifts = []
        if hasattr(result, 'gifts'):
            for gift in result.gifts:
                try:
                    msg_id = None
                    if hasattr(gift, 'msg_id'):
                        msg_id = gift.msg_id
                    elif hasattr(gift, 'message') and hasattr(gift.message, 'id'):
                        msg_id = gift.message.id
                    
                    gift_name = "Unknown"
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
                    
                    transfer_timer = "✅ Готов"
                    can_transfer = True
                    
                    if msg_id:
                        try:
                            stargift = InputSavedStarGiftUser(msg_id=msg_id)
                            await client(TransferStarGiftRequest(stargift=stargift, to_id=entity))
                        except Exception as e:
                            err_msg = str(e)
                            if "STARGIFT_TRANSFER_TOO_EARLY" in err_msg:
                                _, seconds, time_str = parse_transfer_error(err_msg)
                                transfer_timer = time_str
                                can_transfer = False
                            elif "USER_MUST_BE_DIFFERENT" in err_msg or "SELF_TRANSFER" in err_msg:
                                transfer_timer = "✅ Готов"
                                can_transfer = True
                            else:
                                transfer_timer = f"❌ {err_msg[:30]}"
                                can_transfer = False
                    
                    if msg_id and gift_number:
                        gifts.append({
                            "msg_id": msg_id,
                            "name": gift_name,
                            "number": gift_number,
                            "slug": gift_slug,
                            "transfer_stars": transfer_stars,
                            "can_transfer": can_transfer,
                            "transfer_timer": transfer_timer
                        })
                except Exception as e:
                    print(f"Ошибка парсинга: {e}")
                    continue
        
        fluxx_gifts = gifts
        print(f"Загружено {len(fluxx_gifts)} подарков")
        return gifts
    except Exception as e:
        print(f"Ошибка загрузки: {e}")
        return []


async def reload_fluxx_gifts():
    global recent_gifts_history
    recent_gifts_history = []
    await load_fluxx_gifts()


def get_random_fluxx_gift():
    global fluxx_gifts, recent_gifts_history, disabled_gifts
    
    if not fluxx_gifts:
        return None
    
    # Фильтруем: только активные (не в disabled) и не недавние
    available = [
        g for g in fluxx_gifts 
        if g["msg_id"] not in disabled_gifts and g["msg_id"] not in recent_gifts_history
    ]
    
    if not available:
        # Если все в истории — сбрасываем историю, но disabled оставляем
        recent_gifts_history = []
        available = [g for g in fluxx_gifts if g["msg_id"] not in disabled_gifts]
    
    if not available:
        return None
    
    gift = random.choice(available)
    recent_gifts_history.append(gift["msg_id"])
    if len(recent_gifts_history) > MAX_HISTORY:
        recent_gifts_history.pop(0)
    
    return gift


# ========== ПЕРЕДАЧА ПОДАРКА ==========

async def transfer_gift_to_user(gift_msg_id: int, to_user_id: int):
    try:
        to_entity = await client.get_entity(to_user_id)
        stargift = InputSavedStarGiftUser(msg_id=gift_msg_id)
        result = await client(TransferStarGiftRequest(stargift=stargift, to_id=to_entity))
        await reload_fluxx_gifts()
        return True, "success", 0, ""
    except Exception as e:
        error_msg = str(e)
        err_type, seconds, time_str = parse_transfer_error(error_msg)
        return False, err_type, seconds, time_str


# ========== АДМИН-КЛАВИАТУРЫ ==========

def get_admin_main_keyboard():
    """Главное меню админа"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Список подарков", callback_data="admin_gift_list")],
        [InlineKeyboardButton(text="🔄 Обновить подарки", callback_data="admin_reload")],
    ])


def get_gift_control_keyboard(gift_index: int, is_disabled: bool):
    """Клавиатура управления подарком"""
    if is_disabled:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Вернуть в розыгрыш", callback_data=f"enable_gift_{gift_index}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_gift_list")]
        ])
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Убрать из розыгрыша", callback_data=f"disable_gift_{gift_index}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_gift_list")]
        ])


def get_gift_list_keyboard():
    """Клавиатура списка подарков"""
    buttons = []
    for i, gift in enumerate(fluxx_gifts):
        status = "❌" if gift["msg_id"] in disabled_gifts else "✅"
        btn_text = f"{status} {gift['name']} #{gift['number']}"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"gift_detail_{i}")])
    
    buttons.append([InlineKeyboardButton(text="🔙 Главное меню", callback_data="admin_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ========== ОБРАБОТЧИКИ КОМАНД ==========

@dp.message(F.text == "/start")
async def start_handler(message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.answer("Привет! Это бот для розыгрыша NFT-подарков в Casino.")
        return
    
    # Админское меню
    await message.answer(
        "👑 Админ-панель\n\n"
        "Выберите действие:",
        reply_markup=get_admin_main_keyboard()
    )


@dp.callback_query(F.data == "admin_main")
async def admin_main_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа!", show_alert=True)
        return
    
    await callback.message.edit_text(
        "👑 Админ-панель\n\nВыберите действие:",
        reply_markup=get_admin_main_keyboard()
    )


@dp.callback_query(F.data == "admin_reload")
async def admin_reload_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа!", show_alert=True)
        return
    
    await callback.answer("Обновляю...")
    await reload_fluxx_gifts()
    
    await callback.message.edit_text(
        f"✅ Список подарков обновлён!\nЗагружено: {len(fluxx_gifts)} подарков",
        reply_markup=get_admin_main_keyboard()
    )


@dp.callback_query(F.data == "admin_gift_list")
async def admin_gift_list_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа!", show_alert=True)
        return
    
    if not fluxx_gifts:
        await callback.message.edit_text(
            "📦 Подарков не найдено.\nСначала обновите список.",
            reply_markup=get_admin_main_keyboard()
        )
        return
    
    text = "📦 Список подарков @FluxxReleyer:\n\n"
    for gift in fluxx_gifts:
        status = "❌ ВЫКЛ" if gift["msg_id"] in disabled_gifts else "✅ АКТИВЕН"
        link = get_nft_link(gift["name"], gift["number"], gift.get("slug"))
        text += f"• <a href='{link}'>{escape(gift['name'])} #{gift['number']}</a>\n"
        text += f"  Статус: {status} | {gift['transfer_timer']}\n\n"
    
    await callback.message.edit_text(
        text,
        reply_markup=get_gift_list_keyboard(),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )


@dp.callback_query(F.data.startswith("gift_detail_"))
async def gift_detail_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа!", show_alert=True)
        return
    
    try:
        index = int(callback.data.split("_")[-1])
        gift = fluxx_gifts[index]
    except (ValueError, IndexError):
        await callback.answer("Ошибка!", show_alert=True)
        return
    
    is_disabled = gift["msg_id"] in disabled_gifts
    status = "❌ ВЫКЛЮЧЕН" if is_disabled else "✅ АКТИВЕН"
    
    link = get_nft_link(gift["name"], gift["number"], gift.get("slug"))
    
    text = (
        f"🎁 <b>{escape(gift['name'])}</b> #{gift['number']}\n\n"
        f"🔗 <a href='{link}'>Ссылка на NFT</a>\n"
        f"📊 Статус: {status}\n"
        f"⏳ Таймер: {gift['transfer_timer']}\n"
        f"💰 Стоимость передачи: {gift['transfer_stars']}⭐️\n"
        f"🆔 msg_id: <code>{gift['msg_id']}</code>"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_gift_control_keyboard(index, is_disabled),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )


@dp.callback_query(F.data.startswith("disable_gift_"))
async def disable_gift_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа!", show_alert=True)
        return
    
    try:
        index = int(callback.data.split("_")[-1])
        gift = fluxx_gifts[index]
    except (ValueError, IndexError):
        await callback.answer("Ошибка!", show_alert=True)
        return
    
    disabled_gifts.add(gift["msg_id"])
    await callback.answer(f"Подарок {gift['name']} убран из розыгрыша!")
    
    # Обновляем детальную страницу
    await gift_detail_callback(callback)


@dp.callback_query(F.data.startswith("enable_gift_"))
async def enable_gift_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа!", show_alert=True)
        return
    
    try:
        index = int(callback.data.split("_")[-1])
        gift = fluxx_gifts[index]
    except (ValueError, IndexError):
        await callback.answer("Ошибка!", show_alert=True)
        return
    
    disabled_gifts.discard(gift["msg_id"])
    await callback.answer(f"Подарок {gift['name']} возвращён в розыгрыш!")
    
    # Обновляем детальную страницу
    await gift_detail_callback(callback)


# ========== ОБЫЧНЫЕ КОМАНДЫ ==========

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

        gift_html = get_gift_html(gift["name"], gift["number"], gift.get("slug"))

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
    await client.start()
    print("✅ Telethon подключен")
    await load_fluxx_gifts()


async def main():
    await start_client()
    print("🤖 Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())