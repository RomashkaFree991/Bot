import asyncio
import json
import os
import random
import re
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ChatPermissions
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

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

# Только этот ID имеет доступ к скрытым командам
SUPER_ADMIN_ID = 8667321828

# Режимы игры
MODE_NFT = "nft"
MODE_REGULAR = "regular"

# Подарки для обычного режима
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

# ========== СОСТОЯНИЯ FSM ==========

class SendGiftState(StatesGroup):
    choosing_gift = State()
    entering_user = State()

class SendNftState(StatesGroup):
    choosing_nft = State()
    entering_user = State()

# ========== ИНИЦИАЛИЗАЦИЯ ==========

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=storage)

client = TelegramClient("session_name", API_ID, API_HASH)

fluxx_gifts = []
disabled_gifts = set()
recent_gifts_history = []
MAX_HISTORY = 3
current_mode = MODE_NFT
user_warnings = {}
regular_fields = {}


# ========== ПРОВЕРКА ДОСТУПА ==========

def is_super_admin(user_id: int) -> bool:
    return user_id == SUPER_ADMIN_ID


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
    try:
        result = await client(GetStarsStatusRequest(peer=InputPeerSelf()))
        if hasattr(result, 'balance'):
            return result.balance
        return 0
    except Exception:
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
                except Exception:
                    continue
        
        fluxx_gifts = gifts
        return gifts
    except Exception:
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
    try:
        to_input_peer = await client.get_input_entity(to_user_id)
        
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
        
        payment_form = await client(GetPaymentFormRequest(invoice=invoice))
        
        if not payment_form:
            return False, "Не удалось получить форму оплаты"
        
        await client(SendStarsFormRequest(
            form_id=payment_form.form_id,
            invoice=invoice
        ))
        
        return True, ""
    
    except Exception as e:
        error_msg = str(e)
        if "PAYMENT_REQUIRED" in error_msg:
            return False, "Недостаточно звёзд"
        if "STARGIFT_USAGE_LIMITED" in error_msg:
            return False, "Лимит отправки"
        return False, error_msg


async def transfer_nft_gift(gift_msg_id: int, to_user_id: int):
    try:
        to_entity = await client.get_entity(to_user_id)
        stargift = InputSavedStarGiftUser(msg_id=gift_msg_id)
        
        await client(TransferStarGiftRequest(stargift=stargift, to_id=to_entity))
        
        await reload_fluxx_gifts()
        return True, "success", 0, ""
    
    except Exception as e:
        error_msg = str(e)
        err_type, seconds, time_str = parse_transfer_error(error_msg)
        return False, err_type, seconds, time_str


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


def get_regular_gifts_keyboard():
    """Клавиатура выбора обычного подарка для /send"""
    buttons = []
    for gift in REGULAR_GIFTS:
        buttons.append([InlineKeyboardButton(
            text=f"{gift['emoji']} {gift['stars']} ⭐️",
            callback_data=f"send_regular_{gift['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_send")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_nft_gifts_keyboard():
    """Клавиатура выбора NFT для /sendnft"""
    buttons = []
    for i, gift in enumerate(fluxx_gifts):
        buttons.append([InlineKeyboardButton(
            text=f"{gift['name']} #{gift['number']}",
            callback_data=f"send_nft_{i}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_send")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ========== ОБРАБОТЧИКИ /start ==========

@dp.message(F.text == "/start")
async def start_handler(message: Message):
    user_id = message.from_user.id
    
    if is_super_admin(user_id):
        await message.answer(
            "Выберите действие:",
            reply_markup=get_admin_main_keyboard()
        )
    else:
        await message.answer("Привет! Это бот для розыгрыша NFT-подарков в Casino.")


# ========== СКРЫТЫЕ КОМАНДЫ (только для SUPER_ADMIN_ID) ==========

@dp.message(F.text == "/balance")
async def balance_handler(message: Message):
    if not is_super_admin(message.from_user.id):
        return
    
    balance = await get_stars_balance()
    await message.answer(f"💰 Баланс звёзд: {balance} ⭐️")


@dp.message(F.text == "/send")
async def send_handler(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id):
        return
    
    await state.set_state(SendGiftState.choosing_gift)
    await message.answer(
        "Выберите подарок для отправки:",
        reply_markup=get_regular_gifts_keyboard()
    )


@dp.callback_query(F.data.startswith("send_regular_"))
async def send_regular_choice(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    gift_id = callback.data.split("_")[-1]
    await state.update_data(gift_id=gift_id)
    await state.set_state(SendGiftState.entering_user)
    
    await callback.message.edit_text(
        "Введите ID пользователя или @username:"
    )


@dp.message(SendGiftState.entering_user)
async def send_regular_user(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id):
        await state.clear()
        return
    
    data = await state.get_data()
    gift_id = data.get("gift_id")
    
    user_input = message.text.strip()
    
    # Парсим user_id или username
    try:
        if user_input.startswith("@"):
            user_id = user_input[1:]
        else:
            user_id = int(user_input)
    except ValueError:
        await message.answer("❌ Неверный формат. Введите числовой ID или @username")
        return
    
    # Находим подарок
    gift = None
    for g in REGULAR_GIFTS:
        if g["id"] == gift_id:
            gift = g
            break
    
    if not gift:
        await message.answer("❌ Подарок не найден")
        await state.clear()
        return
    
    # Отправляем
    await message.answer(f"⏳ Отправка {gift['emoji']} {gift['stars']}⭐️...")
    
    success, error = await send_gift_from_account(gift_id, user_id)
    
    if success:
        await message.answer(f"✅ Подарок {gift['emoji']} {gift['stars']}⭐️ отправлен!")
    else:
        await message.answer(f"❌ Ошибка: {error}")
    
    await state.clear()


@dp.message(F.text == "/sendnft")
async def sendnft_handler(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id):
        return
    
    if not fluxx_gifts:
        await message.answer("❌ Список NFT пуст. Сначала обновите через меню.")
        return
    
    await state.set_state(SendNftState.choosing_nft)
    await message.answer(
        "Выберите NFT для отправки:",
        reply_markup=get_nft_gifts_keyboard()
    )


@dp.callback_query(F.data.startswith("send_nft_"))
async def send_nft_choice(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    try:
        index = int(callback.data.split("_")[-1])
        gift = fluxx_gifts[index]
    except (ValueError, IndexError):
        await callback.answer("Ошибка", show_alert=True)
        return
    
    await state.update_data(msg_id=gift["msg_id"], gift_name=gift["name"])
    await state.set_state(SendNftState.entering_user)
    
    await callback.message.edit_text(
        f"Выбрано: {gift['name']} #{gift['number']}\n\n"
        f"Введите ID пользователя или @username:"
    )


@dp.message(SendNftState.entering_user)
async def send_nft_user(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id):
        await state.clear()
        return
    
    data = await state.get_data()
    msg_id = data.get("msg_id")
    gift_name = data.get("gift_name")
    
    user_input = message.text.strip()
    
    try:
        if user_input.startswith("@"):
            user_id = user_input[1:]
        else:
            user_id = int(user_input)
    except ValueError:
        await message.answer("❌ Неверный формат. Введите числовой ID или @username")
        return
    
    await message.answer(f"⏳ Отправка NFT {gift_name}...")
    
    success, err_type, seconds, time_str = await transfer_nft_gift(msg_id, user_id)
    
    if success:
        await message.answer(f"✅ NFT {gift_name} отправлен!")
    else:
        await message.answer(f"❌ Ошибка: {time_str}")
    
    await state.clear()


@dp.callback_query(F.data == "cancel_send")
async def cancel_send(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Отменено")
    await callback.answer()


# ========== ОБЫЧНЫЕ КОЛБЭКИ ==========

@dp.callback_query(F.data == "admin_main")
async def admin_main_callback(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "Выберите действие:",
        reply_markup=get_admin_main_keyboard()
    )


@dp.callback_query(F.data == "admin_modes")
async def admin_modes_callback(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    global current_mode
    mode_text = "🎨 NFT" if current_mode == MODE_NFT else "🎰 Обычный"
    await callback.message.edit_text(
        f"Текущий режим: <b>{mode_text}</b>\n\nВыберите режим:",
        reply_markup=get_modes_keyboard(),
        parse_mode=ParseMode.HTML
    )


@dp.callback_query(F.data == "mode_nft")
async def mode_nft_callback(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    global current_mode
    current_mode = MODE_NFT
    await callback.answer("Режим изменён")
    
    config = load_config()
    chat_id = config.get("chat_id")
    casino_thread_id = config.get("casino_thread_id")
    
    if chat_id:
        try:
            sent = await bot.send_message(
                chat_id=chat_id,
                message_thread_id=casino_thread_id,
                text="🎨 <b>Режим игры: NFT</b>\n\n"
                     "Выигрывайте уникальные NFT-подарки!\n"
                     "Выбейте 🎰 777 и получите реальный NFT-подарок.",
                parse_mode=ParseMode.HTML
            )
            await bot.pin_chat_message(chat_id=chat_id, message_id=sent.message_id, disable_notification=True)
        except Exception:
            pass
    
    await callback.message.edit_text(
        "Режим изменён на <b>NFT</b>",
        reply_markup=get_modes_keyboard(),
        parse_mode=ParseMode.HTML
    )


@dp.callback_query(F.data == "mode_regular")
async def mode_regular_callback(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    global current_mode
    current_mode = MODE_REGULAR
    await callback.answer("Режим изменён")
    
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
        except Exception:
            pass
    
    await callback.message.edit_text(
        "Режим изменён на <b>Обычный</b>",
        reply_markup=get_modes_keyboard(),
        parse_mode=ParseMode.HTML
    )


@dp.callback_query(F.data == "admin_reload")
async def admin_reload_callback(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer("Обновляю...")
    await reload_fluxx_gifts()
    await callback.message.edit_text(
        f"Список обновлён! Загружено: {len(fluxx_gifts)}",
        reply_markup=get_admin_main_keyboard()
    )


@dp.callback_query(F.data == "admin_gift_list")
async def admin_gift_list_callback(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    if not fluxx_gifts:
        await callback.message.edit_text(
            "Подарков не найдено.",
            reply_markup=get_admin_main_keyboard()
        )
        return
    text = "Список подарков:\n\n"
    for gift in fluxx_gifts:
        status = "❌ ВЫКЛ" if gift["msg_id"] in disabled_gifts else "✅ АКТИВЕН"
        link = get_nft_link(gift["name"], gift["number"], gift.get("slug"))
        text += f"• <a href='{link}'>{escape(gift['name'])} #{gift['number']}</a>\n"
        text += f"  {status} | {gift['transfer_timer']}\n\n"
    await callback.message.edit_text(
        text,
        reply_markup=get_gift_list_keyboard(),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )


@dp.callback_query(F.data.startswith("gift_detail_"))
async def gift_detail_callback(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        index = int(callback.data.split("_")[-1])
        gift = fluxx_gifts[index]
    except (ValueError, IndexError):
        await callback.answer("Ошибка", show_alert=True)
        return
    is_disabled = gift["msg_id"] in disabled_gifts
    status = "❌ ВЫКЛ" if is_disabled else "✅ АКТИВЕН"
    link = get_nft_link(gift["name"], gift["number"], gift.get("slug"))
    text = (
        f"🎁 <b>{escape(gift['name'])}</b> #{gift['number']}\n\n"
        f"🔗 <a href='{link}'>Ссылка</a>\n"
        f"📊 {status}\n"
        f"⏳ {gift['transfer_timer']}\n"
        f"💰 {gift['transfer_stars']}⭐️\n"
        f"🆔 <code>{gift['msg_id']}</code>"
    )
    await callback.message.edit_text(
        text,
        reply_markup=get_gift_control_keyboard(index, is_disabled),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )


@dp.callback_query(F.data.startswith("disable_gift_"))
async def disable_gift_callback(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        index = int(callback.data.split("_")[-1])
        gift = fluxx_gifts[index]
    except (ValueError, IndexError):
        await callback.answer("Ошибка", show_alert=True)
        return
    disabled_gifts.add(gift["msg_id"])
    await callback.answer("Убрано")
    await gift_detail_callback(callback)


@dp.callback_query(F.data.startswith("enable_gift_"))
async def enable_gift_callback(callback: CallbackQuery):
    if not is_super_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        index = int(callback.data.split("_")[-1])
        gift = fluxx_gifts[index]
    except (ValueError, IndexError):
        await callback.answer("Ошибка", show_alert=True)
        return
    disabled_gifts.discard(gift["msg_id"])
    await callback.answer("Возвращено")
    await gift_detail_callback(callback)


# ========== ЗАЩИТА ОТ ПЕРЕСЫЛКИ ==========

@dp.message(F.forward_from | F.forward_sender_name | F.forward_date)
async def forward_handler(message: Message):
    config = load_config()
    chat_id = config.get("chat_id")
    casino_thread_id = config.get("casino_thread_id")
    
    if not chat_id:
        return
    
    if message.chat.id != chat_id:
        return
    
    if casino_thread_id and message.message_thread_id != casino_thread_id:
        return
    
    user_id = message.from_user.id
    
    try:
        await message.delete()
    except Exception:
        pass
    
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
            text=f"⚠️ {get_user_mention(message.from_user)}\n\n"
                 f"Пересылка сообщений запрещена!\n"
                 f"Мут на 1 час.",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass


# ========== /setcasino ==========

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
        "Тема сохранена.\n\n"
        f"chat_id: <code>{message.chat.id}</code>\n"
        f"message_thread_id: <code>{message.message_thread_id}</code>"
    )


# ========== /delsob (фикс) ==========

@dp.message(F.text == "/delsob")
async def delete_all_messages(message: Message):
    if not is_super_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    
    config = load_config()
    chat_id = config.get("chat_id")
    casino_thread_id = config.get("casino_thread_id")
    
    if not chat_id or not casino_thread_id:
        await message.answer("Сначала настройте тему через /setcasino")
        return
    
    await message.answer("Начинаю удаление...")
    
    deleted_count = 0
    # Удаляем сообщения от бота в теме (последние 100)
    # Используем Telethon для получения истории
    try:
        async for msg in client.iter_messages(
            entity=chat_id,
            limit=200,
            reply_to=casino_thread_id
        ):
            try:
                await bot.delete_message(chat_id=chat_id, message_id=msg.id)
                deleted_count += 1
                await asyncio.sleep(0.05)
            except Exception:
                pass
        
        await message.answer(f"Удалено {deleted_count} сообщений.")
        
    except Exception as e:
        await message.answer(f"Ошибка: {e}")


# ========== УДАЛЕНИЕ СЕРВИСНЫХ СООБЩЕНИЙ ==========

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
                pass

        else:
            keyboard = get_hidden_field_keyboard(message.message_id)
            
            text = (
                "ДЖЕЕЕКПОООТ 🔥\n\n"
                f"{user_mention} красава, ты выиграл! 🏆\n\n"
                "Выбери ячейку:"
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
        await callback.answer("Ошибка", show_alert=True)
        return
    
    field_gifts = regular_fields.get(message_id)
    if not field_gifts or cell_idx >= len(field_gifts):
        await callback.answer("Игра устарела", show_alert=True)
        return
    
    gift = field_gifts[cell_idx]
    user_id = callback.from_user.id
    user_mention = get_user_mention(callback.from_user)
    
    success, error = await send_gift_from_account(gift["id"], user_id)
    
    if success:
        status = "✅ Отправлен!"
    else:
        status = f"❌ {error}"

    await callback.message.edit_text(
        f"ДЖЕЕЕКПОООТ 🔥\n\n"
        f"{user_mention} красава, ты выиграл! 🏆\n\n"
        f"🎁 Ячейка #{cell_idx + 1}\n"
        f"🎉 {gift['emoji']} <b>{gift['stars']} ⭐️</b>\n"
        f"{status}\n\n"
        f"Играй ещё!",
        parse_mode=ParseMode.HTML
    )
    
    await callback.answer(f"{gift['emoji']} {gift['stars']} ⭐️!")


# ========== ЗАПУСК ==========

async def start_client():
    await client.start()
    balance = await get_stars_balance()
    print(f"💰 Баланс: {balance} ⭐️")
    await load_fluxx_gifts()


async def main():
    await start_client()
    print("🤖 Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())