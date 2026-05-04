import asyncio
import json
import os
import random
import re
import sys
import threading
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ChatPermissions
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from telethon import TelegramClient
from telethon.tl.functions.payments import (
    GetSavedStarGiftsRequest, TransferStarGiftRequest,
    GetPaymentFormRequest, SendStarsFormRequest, GetStarsStatusRequest
)
from telethon.tl.types import (
    InputSavedStarGiftUser, InputInvoiceStarGift,
    TextWithEntities, MessageEntityBold, InputPeerSelf
)

# ========== КОНФИГУРАЦИЯ ==========

BOT_TOKEN = "8623972147:AAHlKANbbUTMpk7MGuQllkxaAxmoG7UupL8"
API_ID = 30755567
API_HASH = "31873c92ac5c9ce1c0c9ac026d284091"
GIFT_SOURCE_USERNAME = "FluxxReleyer"
CONFIG_FILE = "config.json"
ADMIN_IDS = {8667321828, 6106324512}
MODE_NFT = "nft"
MODE_REGULAR = "regular"

REGULAR_GIFTS = [
    {"emoji": "🧸", "stars": 15, "id": "5170233102089322756"},
    {"emoji": "💝", "stars": 15, "id": "5170145012310081615"},
    {"emoji": "🎁", "stars": 25, "id": "5170250947678437525"},
    {"emoji": "🍾", "stars": 50, "id": "6028601630662853006"},
    {"emoji": "💎", "stars": 100, "id": "5170521118301225164"},
    {"emoji": "💍", "stars": 100, "id": "5170690322832818290"},
    {"emoji": "🏆", "stars": 100, "id": "5168043875654172773"},
    {"emoji": "🚀", "stars": 50, "id": "5170564780938756245"},
    {"emoji": "💐", "stars": 50, "id": "5170314324215857265"},
    {"emoji": "🎂", "stars": 50, "id": "5170144170496491616"},
    {"emoji": "🌹", "stars": 25, "id": "5168103777563050263"},
]

# ========== ИНИЦИАЛИЗАЦИЯ ==========

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
client = TelegramClient("session_name", API_ID, API_HASH)

fluxx_gifts = []
disabled_gifts = set()
recent_gifts_history = []
MAX_HISTORY = 3
current_mode = MODE_NFT
user_warnings = {}
regular_fields = {}


# ========== ПРОВЕРКА АДМИНА ==========

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ========== КОНФИГ ==========

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


# ========== ВСПОМОГАТЕЛЬНЫЕ ==========

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
    remaining_seconds = seconds % 60
    parts = []
    if days > 0:
        parts.append(f"{days}д")
    if hours > 0:
        parts.append(f"{hours}ч")
    if minutes > 0:
        parts.append(f"{minutes}м")
    if remaining_seconds > 0 and days == 0 and hours == 0:
        parts.append(f"{remaining_seconds}с")
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


# ========== БАЛАНС ЗВЁЗД ==========

async def get_stars_balance():
    """Получает баланс звёзд аккаунта @FluxxReleyer"""
    try:
        result = await client(GetStarsStatusRequest(peer=InputPeerSelf()))
        if hasattr(result, 'balance'):
            return result.balance
        return 0
    except Exception as e:
        print(f"❌ Ошибка получения баланса: {e}")
        return 0


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
    available = [g for g in fluxx_gifts if g["msg_id"] not in disabled_gifts and g["msg_id"] not in recent_gifts_history]
    if not available:
        recent_gifts_history = []
        available = [g for g in fluxx_gifts if g["msg_id"] not in disabled_gifts]
    if not available:
        return None
    gift = random.choice(available)
    recent_gifts_history.append(gift["msg_id"])
    if len(recent_gifts_history) > MAX_HISTORY:
        recent_gifts_history.pop(0)
    return gift


# ========== ОТПРАВКА ПОДАРКОВ ==========

async def send_gift_from_account(gift_id: str, to_user_id: int):
    """Отправляет подарок С АККАУНТА @FluxxReleyer через Telethon"""
    try:
        # Получаем InputPeerUser с правильным access_hash [^15^][^16^]
        to_input_peer = await client.get_input_entity(to_user_id)
        to_entity = await client.get_entity(to_user_id)
        
        message = TextWithEntities(
            text="Поздравляем с выигрышем!",
            entities=[MessageEntityBold(offset=0, length=len("Поздравляем с выигрышем!"))]
        )
        
        invoice = InputInvoiceStarGift(
            peer=to_input_peer,
            gift_id=int(gift_id),
            message=message,
            hide_name=False
        )
        
        print(f"🛒 Покупка подарка {gift_id} для {to_user_id}...")
        
        payment_form = await client(GetPaymentFormRequest(invoice=invoice))
        
        if not payment_form:
            return False, "Не удалось получить форму оплаты"
        
        print(f"💳 Форма получена. Стоимость: {payment_form.invoice.prices[0].amount} ⭐️")
        
        payment_result = await client(SendStarsFormRequest(
            form_id=payment_form.form_id,
            invoice=invoice
        ))
        
        print(f"✅ Подарок отправлен!")
        return True, ""
    
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Ошибка: {error_msg}")
        
        if "PAYMENT_REQUIRED" in error_msg:
            print(f"💰 НЕДОСТАТОЧНО ЗВЁЗД на аккаунте!")
            return False, "Недостаточно звёзд"
        if "STARGIFT_USAGE_LIMITED" in error_msg:
            return False, "Лимит отправки"
        
        return False, error_msg


async def transfer_nft_gift(gift_msg_id: int, to_user_id: int):
    """Передаёт NFT-подарок через Telethon"""
    try:
        to_entity = await client.get_entity(to_user_id)
        stargift = InputSavedStarGiftUser(msg_id=gift_msg_id)
        
        result = await client(TransferStarGiftRequest(
            stargift=stargift,
            to_id=to_entity
        ))
        
        print(f"✅ NFT передан пользователю {to_user_id}")
        await reload_fluxx_gifts()
        return True, "success", 0, ""
    
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Ошибка передачи NFT: {error_msg}")
        
        if "PAYMENT_REQUIRED" in error_msg:
            print(f"💰 НЕДОСТАТОЧНО ЗВЁЗД для передачи NFT!")
        
        err_type, seconds, time_str = parse_transfer_error(error_msg)
        return False, err_type, seconds, time_str


# ========== КОНСОЛЬНЫЕ КОМАНДЫ ==========

def console_input_handler():
    """Обработчик ввода из консоли для скрытых команд"""
    while True:
        try:
            line = input().strip()
            if not line:
                continue
            
            parts = line.split()
            cmd = parts[0].lower()
            
            if cmd == "/send" and len(parts) >= 3:
                # /send gift_id user_id
                gift_id = parts[1]
                user_id = int(parts[2])
                
                print(f"🔄 Отправка подарка {gift_id} пользователю {user_id}...")
                
                # Запускаем асинхронно
                asyncio.create_task(console_send_gift(gift_id, user_id))
            
            elif cmd == "/sendnft" and len(parts) >= 3:
                # /sendnft msg_id user_id
                msg_id = int(parts[1])
                user_id = int(parts[2])
                
                print(f"🔄 Отправка NFT (msg_id={msg_id}) пользователю {user_id}...")
                asyncio.create_task(console_send_nft(msg_id, user_id))
            
            elif cmd == "/balance":
                asyncio.create_task(console_show_balance())
            
            else:
                print("❌ Неизвестная команда")
                
        except Exception as e:
            print(f"❌ Ошибка консоли: {e}")


async def console_send_gift(gift_id: str, user_id: int):
    success, error = await send_gift_from_account(gift_id, user_id)
    if success:
        print(f"✅ Подарок {gift_id} отправлен пользователю {user_id}")
    else:
        print(f"❌ Ошибка: {error}")


async def console_send_nft(msg_id: int, user_id: int):
    success, err_type, seconds, time_str = await transfer_nft_gift(msg_id, user_id)
    if success:
        print(f"✅ NFT отправлен пользователю {user_id}")
    else:
        print(f"❌ Ошибка: {err_type} - {time_str}")


async def console_show_balance():
    balance = await get_stars_balance()
    print(f"💰 Баланс звёзд: {balance} ⭐️")


# ========== КЛАВИАТУРЫ ==========

def get_admin_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Список подарков", callback_data="admin_gift_list")],
        [InlineKeyboardButton(text="🔄 Обновить подарки", callback_data="admin_reload")],
        [InlineKeyboardButton(text="🎮 Режимы игры", callback_data="admin_modes")],
    ])


def get_modes_keyboard():
    nft_status = "✅" if current_mode == MODE_NFT else ""
    regular_status = "✅" if current_mode == MODE_REGULAR else ""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{nft_status} 🎨 NFT режим", callback_data="mode_nft")],
        [InlineKeyboardButton(text=f"{regular_status} 🎰 Обычный режим", callback_data="mode_regular")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_main")],
    ])


def get_gift_list_keyboard():
    buttons = []
    for i, gift in enumerate(fluxx_gifts):
        status = "❌" if gift["msg_id"] in disabled_gifts else "✅"
        btn_text = f"{status} {gift['name']} #{gift['number']}"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"gift_detail_{i}")])
    buttons.append([InlineKeyboardButton(text="🔙 Главное меню", callback_data="admin_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_gift_control_keyboard(gift_index: int, is_disabled: bool):
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


def get_hidden_field_keyboard(message_id: int):
    buttons = []
    field_gifts = random.choices(REGULAR_GIFTS, k=25)
    regular_fields[message_id] = field_gifts
    
    for row in range(5):
        row_buttons = []
        for col in range(5):
            idx = row * 5 + col
            row_buttons.append(InlineKeyboardButton(
                text="❓", 
                callback_data=f"regular_cell_{message_id}_{idx}"
            ))
        buttons.append(row_buttons)
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ========== ОБРАБОТЧИКИ ==========

@dp.message(F.text == "/start")
async def start_handler(message: Message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("Привет! Это бот для розыгрыша NFT-подарков в Casino.")
        return
    await message.answer(
        "👑 Админ-панель\n\nВыберите действие:",
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


@dp.callback_query(F.data == "admin_modes")
async def admin_modes_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа!", show_alert=True)
        return
    global current_mode
    mode_text = "🎨 NFT" if current_mode == MODE_NFT else "🎰 Обычный"
    await callback.message.edit_text(
        f"🎮 Текущий режим: <b>{mode_text}</b>\n\nВыберите режим игры:",
        reply_markup=get_modes_keyboard(),
        parse_mode=ParseMode.HTML
    )


@dp.callback_query(F.data == "mode_nft")
async def mode_nft_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа!", show_alert=True)
        return
    global current_mode
    current_mode = MODE_NFT
    await callback.answer("✅ Режим изменён на NFT!")
    
    config = load_config()
    chat_id = config.get("chat_id")
    casino_thread_id = config.get("casino_thread_id")
    
    if chat_id:
        try:
            sent = await bot.send_message(
                chat_id=chat_id,
                message_thread_id=casino_thread_id,
                text="🎨 <b>Режим игры: NFT</b>\n\n"
                     "Выигрывайте уникальные NFT-подарки из коллекции @FluxxReleyer!\n"
                     "Выбейте 🎰 777 и получите реальный NFT-подарок.",
                parse_mode=ParseMode.HTML
            )
            await bot.pin_chat_message(chat_id=chat_id, message_id=sent.message_id, disable_notification=True)
        except Exception as e:
            print(f"Ошибка отправки в чат: {e}")
    
    await callback.message.edit_text(
        "✅ Режим изменён на <b>NFT</b>!\n\nСообщение отправлено в чат Casino.",
        reply_markup=get_modes_keyboard(),
        parse_mode=ParseMode.HTML
    )


@dp.callback_query(F.data == "mode_regular")
async def mode_regular_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа!", show_alert=True)
        return
    global current_mode
    current_mode = MODE_REGULAR
    await callback.answer("✅ Режим изменён на Обычный!")
    
    config = load_config()
    chat_id = config.get("chat_id")
    casino_thread_id = config.get("casino_thread_id")
    
    if chat_id:
        try:
            sent = await bot.send_message(
                chat_id=chat_id,
                message_thread_id=casino_thread_id,
                text="🎰 <b>Режим игры: Обычный</b>\n\n"
                     "Выигрывайте звёздные подарки!\n"
                     "Выбейте 🎰 777 и выберите ячейку с призом ⭐️",
                parse_mode=ParseMode.HTML
            )
            await bot.pin_chat_message(chat_id=chat_id, message_id=sent.message_id, disable_notification=True)
        except Exception as e:
            print(f"Ошибка отправки в чат: {e}")
    
    await callback.message.edit_text(
        "✅ Режим изменён на <b>Обычный</b>!\n\nСообщение отправлено в чат Casino.",
        reply_markup=get_modes_keyboard(),
        parse_mode=ParseMode.HTML
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
    await gift_detail_callback(callback)


# ========== ЗАЩИТА ОТ ПЕРЕСЫЛКИ ЛЮБЫХ СООБЩЕНИЙ ==========

@dp.message(F.forward_from | F.forward_sender_name | F.forward_date)
async def forward_handler(message: Message):
    """Любая пересылка в теме казино = мут + удаление"""
    config = load_config()
    chat_id = config.get("chat_id")
    casino_thread_id = config.get("casino_thread_id")
    
    if not chat_id:
        return
    
    # Проверяем, что это в нужном чате и теме
    if message.chat.id != chat_id:
        return
    
    if casino_thread_id and message.message_thread_id != casino_thread_id:
        return
    
    user_id = message.from_user.id
    
    # Удаляем сообщение
    try:
        await message.delete()
        print(f"🗑 Удалено пересланное сообщение от {user_id}")
    except Exception as e:
        print(f"❌ Не удалось удалить: {e}")
    
    # Даём мут на 1 час
    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=int(asyncio.get_event_loop().time()) + 3600
        )
        
        await bot.send_message(
            chat_id=message.chat.id,
            message_thread_id=casino_thread_id,
            text=f"⚠️ <b>Внимание!</b> {get_user_mention(message.from_user)}\n\n"
                 f"Пересылка сообщений в Casino запрещена!\n"
                 f"Выдан мут на 1 час.",
            parse_mode=ParseMode.HTML
        )
        print(f"🔇 Мут на 1 час выдан пользователю {user_id}")
    except Exception as e:
        print(f"❌ Ошибка мута: {e}")


# ========== /delsob — УДАЛЕНИЕ ВСЕХ СООБЩЕНИЙ ==========

@dp.message(F.text == "/delsob")
async def delete_all_messages(message: Message):
    """Удаляет все сообщения в теме казино (только для админов)"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.answer("❌ У вас нет прав для этой команды.")
        return
    
    config = load_config()
    chat_id = config.get("chat_id")
    casino_thread_id = config.get("casino_thread_id")
    
    if not chat_id or not casino_thread_id:
        await message.answer("❌ Сначала настройте тему Casino через /setcasino")
        return
    
    await message.answer("🗑 Начинаю удаление всех сообщений в теме Casino...")
    
    deleted_count = 0
    try:
        # Получаем историю сообщений в теме
        async for msg in bot.get_chat_history(chat_id, limit=100):
            # Проверяем, что сообщение в нужной теме
            if msg.message_thread_id == casino_thread_id:
                try:
                    await msg.delete()
                    deleted_count += 1
                    await asyncio.sleep(0.1)  # Задержка чтобы не спамить API
                except Exception:
                    pass
        
        await message.answer(f"✅ Удалено {deleted_count} сообщений из темы Casino!")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка при удалении: {e}")


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


# ========== ДЖЕКПОТ ==========

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

    if message.dice.emoji == "🎰" and message.dice.value == 64:
        user_mention = get_user_mention(message.from_user)
        user_id = message.from_user.id

        if current_mode == MODE_NFT:
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

            success, err_type, seconds_left, time_str = await transfer_nft_gift(gift["msg_id"], user_id)

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

        else:
            keyboard = get_hidden_field_keyboard(message.message_id)
            
            text = (
                "ДЖЕЕЕКПОООТ 🔥\n\n"
                f"{user_mention} красава, ты выиграл! 🏆\n\n"
                "Выбери ячейку и получи звёздный подарок:"
            )
            
            sent = await message.reply(text, reply_markup=keyboard)
            
            try:
                await bot.pin_chat_message(
                    chat_id=message.chat.id,
                    message_id=sent.message_id,
                    disable_notification=True
                )
            except Exception:
                pass


@dp.callback_query(F.data.startswith("regular_cell_"))
async def regular_cell_callback(callback: CallbackQuery):
    try:
        parts = callback.data.split("_")
        message_id = int(parts[2])
        cell_idx = int(parts[3])
    except (ValueError, IndexError):
        await callback.answer("Ошибка!", show_alert=True)
        return
    
    field_gifts = regular_fields.get(message_id)
    if not field_gifts or cell_idx >= len(field_gifts):
        await callback.answer("Игра устарела!", show_alert=True)
        return
    
    gift = field_gifts[cell_idx]
    user_id = callback.from_user.id
    user_mention = get_user_mention(callback.from_user)
    
    success, error = await send_gift_from_account(gift["id"], user_id)
    
    if success:
        status = "✅ Уже отправлен!"
    else:
        status = f"❌ {error}"

    await callback.message.edit_text(
        f"ДЖЕЕЕКПОООТ 🔥\n\n"
        f"{user_mention} красава, ты выиграл! 🏆\n\n"
        f"🎁 Ты выбрал ячейку #{cell_idx + 1}\n"
        f"🎉 Подарок: {gift['emoji']} <b>{gift['stars']} ⭐️</b>\n"
        f"{status}\n\n"
        f"Играй ещё и выигрывай призы!",
        parse_mode=ParseMode.HTML
    )
    
    await callback.answer(f"🎉 {gift['emoji']} {gift['stars']} ⭐️!")


# ========== ЗАПУСК ==========

async def start_client():
    await client.start()
    print("✅ Telethon подключен")
    
    # Показываем баланс звёзд
    balance = await get_stars_balance()
    print(f"💰 Баланс звёзд на аккаунте {GIFT_SOURCE_USERNAME}: {balance} ⭐️")
    
    await load_fluxx_gifts()
    
    # Запускаем консольный ввод в отдельном потоке
    console_thread = threading.Thread(target=console_input_handler, daemon=True)
    console_thread.start()
    print("⌨️ Консольные команды активны")


async def main():
    await start_client()
    print("🤖 Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())