# =========================================================
# VexMart Bot
# VERSION: 0.42.0 Beta
# Admin UI + Geolocation + New Products + Multi Sellers
# =========================================================

import os
import math
from datetime import datetime, timezone, timedelta

from aiohttp import web, ClientSession
from supabase import create_client

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# CONFIG
# =========================================================

VERSION = "0.42.0 Beta"

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not TOKEN:
    raise RuntimeError("Не указан BOT_TOKEN")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "Не указаны SUPABASE_URL или SUPABASE_KEY"
    )

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)


# =========================================================
# DATABASE HELPERS
# =========================================================

def db_select(
    table,
    columns="*",
    filters_dict=None,
    limit=None,
    order_by=None,
    descending=False
):
    query = supabase.table(table).select(columns)

    if filters_dict:
        for key, value in filters_dict.items():
            query = query.eq(key, value)

    if order_by:
        query = query.order(
            order_by,
            desc=descending
        )

    if limit:
        query = query.limit(limit)

    return query.execute()


def db_insert(table, data):
    return supabase.table(table).insert(data).execute()


def db_update(table, data, filters_dict):
    query = supabase.table(table).update(data)

    for key, value in filters_dict.items():
        query = query.eq(key, value)

    return query.execute()


def get_user(user_id):
    result = db_select(
        "users",
        filters_dict={"id": user_id},
        limit=1
    )

    return result.data[0] if result.data else None


def get_store(store_id):
    if not store_id:
        return None

    result = db_select(
        "stores",
        filters_dict={"id": store_id},
        limit=1
    )

    return result.data[0] if result.data else None


def get_user_store(user_id):
    # Сначала ищем собственный магазин
    result = db_select(
        "stores",
        filters_dict={"owner_id": user_id},
        limit=1
    )

    if result.data:
        return result.data[0]

    # Если собственного нет — ищем магазин,
    # где пользователь является дополнительным продавцом
    seller_links = db_select(
        "store_sellers",
        filters_dict={"user_id": user_id},
        limit=1
    )

    if seller_links.data:
        return get_store(
            seller_links.data[0]["store_id"]
        )

    return None


def get_product(product_id):
    result = db_select(
        "products",
        filters_dict={"id": product_id},
        limit=1
    )

    return result.data[0] if result.data else None


# =========================================================
# MULTI-SELLER HELPERS
# =========================================================

def is_store_seller(user_id, store_id):
    store = get_store(store_id)

    if not store:
        return False

    if store.get("owner_id") == user_id:
        return True

    result = db_select(
        "store_sellers",
        filters_dict={
            "store_id": store_id,
            "user_id": user_id
        },
        limit=1
    )

    return bool(result.data)


def get_store_sellers(store_id):
    result = db_select(
        "store_sellers",
        filters_dict={"store_id": store_id},
        order_by="id"
    )

    sellers = []

    for link in result.data or []:
        user = get_user(link["user_id"])

        if user:
            sellers.append(user)

    return sellers


def get_store_member_ids(store_id):
    store = get_store(store_id)

    if not store:
        return []

    member_ids = []

    owner_id = store.get("owner_id")

    if owner_id:
        member_ids.append(owner_id)

    result = db_select(
        "store_sellers",
        filters_dict={"store_id": store_id}
    )

    for row in result.data or []:
        user_id = row.get("user_id")

        if user_id and user_id not in member_ids:
            member_ids.append(user_id)

    return member_ids


def get_seller_store_ids(user_id):
    store_ids = []

    # Собственный магазин
    own_stores = db_select(
        "stores",
        filters_dict={"owner_id": user_id}
    )

    for store in own_stores.data or []:
        if store["id"] not in store_ids:
            store_ids.append(store["id"])

    # Дополнительные магазины
    seller_links = db_select(
        "store_sellers",
        filters_dict={"user_id": user_id}
    )

    for link in seller_links.data or []:
        store_id = link.get("store_id")

        if store_id and store_id not in store_ids:
            store_ids.append(store_id)

    return store_ids


def get_pending_requests_for_user(user_id):
    result = db_select(
        "store_seller_requests",
        filters_dict={
            "receiver_id": user_id,
            "status": "pending"
        },
        order_by="id",
        descending=True
    )

    return result.data or []


def has_pending_request(
    store_id,
    sender_id,
    receiver_id
):
    result = db_select(
        "store_seller_requests",
        filters_dict={
            "store_id": store_id,
            "sender_id": sender_id,
            "receiver_id": receiver_id,
            "status": "pending"
        },
        limit=1
    )

    return bool(result.data)


# =========================================================
# NEW PRODUCT
# =========================================================

NEW_PRODUCT_DAYS = 5


def is_new_product(product):
    created_at = product.get("created_at")

    if not created_at:
        return False

    try:
        if isinstance(created_at, datetime):
            created = created_at

            if created.tzinfo is None:
                created = created.replace(
                    tzinfo=timezone.utc
                )
        else:
            created = datetime.fromisoformat(
                str(created_at).replace(
                    "Z",
                    "+00:00"
                )
            )

        now = datetime.now(timezone.utc)

        age = now - created

        return age < timedelta(
            days=NEW_PRODUCT_DAYS
        )

    except Exception as e:
        print("New product date error:", e)
        return False


def new_badge(product):
    if is_new_product(product):
        return "🆕 "

    return ""


# =========================================================
# DISTANCE
# =========================================================

def calculate_distance(
    lat1,
    lon1,
    lat2,
    lon2
):
    earth_radius = 6371000

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)

    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad)
        * math.cos(lat2_rad)
        * math.sin(delta_lon / 2) ** 2
    )

    return earth_radius * (
        2 * math.atan2(
            math.sqrt(a),
            math.sqrt(1 - a)
        )
    )


def format_distance(distance):
    if distance < 1000:
        return f"{int(distance)} м"

    km = distance / 1000

    if km < 10:
        return f"{km:.1f} км"

    return f"{int(km)} км"


# =========================================================
# REVERSE GEOCODING
# =========================================================

async def reverse_geocode(
    latitude,
    longitude
):
    url = (
        "https://nominatim.openstreetmap.org/reverse"
        f"?lat={latitude}"
        f"&lon={longitude}"
        "&format=json"
        "&zoom=18"
        "&addressdetails=1"
    )

    headers = {
        "User-Agent": f"VexMartBot/{VERSION}"
    }

    try:
        async with ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                timeout=10
            ) as response:

                if response.status != 200:
                    print(
                        "Nominatim HTTP status:",
                        response.status
                    )
                    return None

                data = await response.json()

                return data.get("display_name")

    except Exception as e:
        print(
            "Reverse geocode error:",
            e
        )

        return None


# =========================================================
# KEYBOARDS
# =========================================================

def buyer_menu(user_id=None):
    buttons = [
        [
            InlineKeyboardButton(
                "🏪 Магазины",
                callback_data="stores"
            ),
            InlineKeyboardButton(
                "📦 Товары",
                callback_data="products"
            )
        ],
        [
            InlineKeyboardButton(
                "👤 Профиль",
                callback_data="profile"
            ),
            InlineKeyboardButton(
                "🎯 Задания",
                callback_data="tasks"
            )
        ],
        [
            InlineKeyboardButton(
                "🛒 Мои заказы",
                callback_data="buyer_orders"
            )
        ],
        [
            InlineKeyboardButton(
                "🏪 Стать продавцом",
                callback_data="switch_seller"
            )
        ]
    ]

    if user_id == ADMIN_ID:
        buttons.append([
            InlineKeyboardButton(
                "🛠 Админ-панель",
                callback_data="admin"
            )
        ])

    return InlineKeyboardMarkup(buttons)


def seller_menu(user_id=None):
    buttons = [
        [
            InlineKeyboardButton(
                "🏪 Мой магазин",
                callback_data="my_store"
            )
        ],
        [
            InlineKeyboardButton(
                "📦 Мои товары",
                callback_data="products"
            ),
            InlineKeyboardButton(
                "🛒 Заказы",
                callback_data="seller_orders"
            )
        ],
        [
            InlineKeyboardButton(
                "👥 Запросы продавцов",
                callback_data="seller_requests"
            )
        ],
        [
            InlineKeyboardButton(
                "👤 Профиль",
                callback_data="profile"
            )
        ],
        [
            InlineKeyboardButton(
                "🛒 Режим покупателя",
                callback_data="switch_buyer"
            )
        ]
    ]

    if user_id == ADMIN_ID:
        buttons.append([
            InlineKeyboardButton(
                "🛠 Админ-панель",
                callback_data="admin"
            )
        ])

    return InlineKeyboardMarkup(buttons)


def back_keyboard(callback="buyer_menu"):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data=callback
            )
        ]
    ])


def location_keyboard():
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton(
                    "📍 Отправить геолокацию",
                    request_location=True
                )
            ],
            [
                KeyboardButton("❌ Отмена")
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def confirm_location_keyboard(
    confirm_callback,
    retry_callback
):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Да, сохранить",
                callback_data=confirm_callback
            )
        ],
        [
            InlineKeyboardButton(
                "🔄 Отправить другую",
                callback_data=retry_callback
            )
        ]
    ])


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update,
    context
):
    print(
        "Exception while handling update:",
        context.error
    )


# =========================================================
# STATISTICS
# =========================================================

async def track_visit(user_id):
    try:
        db_insert(
            "bot_visits",
            {"user_id": user_id}
        )
    except Exception as e:
        print(
            "Visit stats error:",
            e
        )


async def track_store_view(
    user_id,
    store_id
):
    try:
        db_insert(
            "store_views",
            {
                "user_id": user_id,
                "store_id": store_id
            }
        )
    except Exception as e:
        print(
            "Store view stats error:",
            e
        )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user

    if not user:
        return

    user_id = user.id

    existing = get_user(user_id)

    try:
        if existing:
            db_update(
                "users",
                {
                    "username": user.username,
                    "first_name": user.first_name
                },
                {"id": user_id}
            )
        else:
            db_insert(
                "users",
                {
                    "id": user_id,
                    "username": user.username,
                    "first_name": user.first_name,
                    "role": "buyer",
                    "balance": 100
                }
            )

    except Exception as e:
        print(
            "User save error:",
            e
        )

    await track_visit(user_id)

    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        "🛒 Добро пожаловать в VexMart!\n"
        "💰 Здесь всё покупается за VexCoin.\n\n"
        f"⚙️ Версия: {VERSION}",
        reply_markup=buyer_menu(user_id)
    )


# =========================================================
# PROFILE
# =========================================================

async def show_profile(query):
    user_id = query.from_user.id
    user = get_user(user_id)

    if not user:
        await query.answer(
            "❌ Профиль не найден.",
            show_alert=True
        )
        return

    role = user.get(
        "role",
        "buyer"
    )

    role_text = (
        "🏪 Продавец"
        if role == "seller"
        else "🛒 Покупатель"
    )

    balance = user.get(
        "balance",
        0
    )

    pending_requests = len(
        get_pending_requests_for_user(
            user_id
        )
    )

    text = (
        "👤 Профиль\n\n"
        f"🆔 ID: {user_id}\n"
        f"👤 Имя: "
        f"{user.get('first_name') or 'Не указано'}\n"
        f"💰 Баланс: {balance} VexCoin\n"
        f"🎭 Роль: {role_text}"
    )

    if pending_requests:
        text += (
            f"\n\n📩 Новых запросов: "
            f"{pending_requests}"
        )

    await query.edit_message_text(
        text,
        reply_markup=back_keyboard(
            "seller_menu"
            if role == "seller"
            else "buyer_menu"
        )
    )


# =========================================================
# STORES
# =========================================================

async def show_stores(query):
    result = db_select(
        "stores",
        order_by="id"
    )

    stores = result.data or []

    if not stores:
        await query.edit_message_text(
            "🏪 Магазинов пока нет.",
            reply_markup=back_keyboard()
        )
        return

    buttons = []

    for store in stores[:50]:
        buttons.append([
            InlineKeyboardButton(
                f"🏪 {store['name']}",
                callback_data=f"store_{store['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "📍 Найти магазины рядом",
            callback_data="nearby_request"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "🔎 Поиск магазина",
            callback_data="search_stores"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="buyer_menu"
        )
    ])

    await query.edit_message_text(
        "🏪 Магазины VexMart:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def show_store(
    query,
    store_id
):
    user_id = query.from_user.id

    store = get_store(store_id)

    if not store:
        await query.answer(
            "❌ Магазин не найден.",
            show_alert=True
        )
        return

    await track_store_view(
        user_id,
        store_id
    )

    result = db_select(
        "products",
        filters_dict={
            "store_id": store_id
        },
        order_by="id"
    )

    products = result.data or []

    text = (
        f"🏪 {store['name']}\n\n"
        f"📝 "
        f"{store.get('description') or 'Без описания'}\n"
    )

    if store.get("address"):
        text += (
            f"\n📍 {store['address']}\n"
        )

    # Продавцы
    members = get_store_member_ids(
        store_id
    )

    if members:
        text += (
            f"\n👥 Продавцов: {len(members)}"
        )

    buttons = []

    if products:
        text += "\n\n📦 Товары:\n"

        for product in products[:15]:
            badge = new_badge(product)

            text += (
                f"\n• {badge}"
                f"{product['name']}"
                f" — {product['price']} V₽"
            )

            buttons.append([
                InlineKeyboardButton(
                    f"{badge}📦 {product['name']}",
                    callback_data=(
                        f"product_{product['id']}"
                    )
                )
            ])

    else:
        text += (
            "\n\n📦 Товаров пока нет."
        )

    # Кнопка запроса в продавцы
    if (
        not is_store_seller(
            user_id,
            store_id
        )
        and store.get("owner_id") != user_id
    ):
        buttons.append([
            InlineKeyboardButton(
                "🙋 Запроситься в продавцы",
                callback_data=(
                    f"request_store_{store_id}"
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="stores"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================================================
# BUYER REQUEST TO STORE
# =========================================================

async def request_store_seller(
    query,
    store_id,
    context
):
    user_id = query.from_user.id

    store = get_store(store_id)

    if not store:
        await query.answer(
            "❌ Магазин не найден.",
            show_alert=True
        )
        return

    owner_id = store.get(
        "owner_id"
    )

    if owner_id == user_id:
        await query.answer(
            "Ты уже владелец этого магазина.",
            show_alert=True
        )
        return

    if is_store_seller(
        user_id,
        store_id
    ):
        await query.answer(
            "Ты уже продавец этого магазина.",
            show_alert=True
        )
        return

    if has_pending_request(
        store_id,
        user_id,
        owner_id
    ):
        await query.answer(
            "📩 Ты уже отправил запрос.",
            show_alert=True
        )
        return

    try:
        result = db_insert(
            "store_seller_requests",
            {
                "store_id": store_id,
                "sender_id": user_id,
                "receiver_id": owner_id,
                "status": "pending"
            }
        )

        if not result.data:
            raise RuntimeError(
                "Request was not created"
            )

    except Exception as e:
        print(
            "Store seller request error:",
            e
        )

        await query.answer(
            "❌ Не удалось отправить запрос.",
            show_alert=True
        )
        return

    sender = get_user(user_id)

    sender_name = (
        sender.get("first_name")
        if sender
        else "Пользователь"
    )

    await query.answer(
        "📩 Запрос отправлен владельцу!",
        show_alert=True
    )

    try:
        await context.bot.send_message(
            chat_id=owner_id,
            text=(
                "📩 Новый запрос в магазин!\n\n"
                f"👤 {sender_name} хочет стать "
                f"продавцом магазина "
                f"«{store['name']}».\n\n"
                "Одобрить запрос?"
            ),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ Одобрить",
                        callback_data=(
                            f"approve_request_"
                            f"{result.data[0]['id']}"
                        )
                    ),
                    InlineKeyboardButton(
                        "❌ Отклонить",
                        callback_data=(
                            f"reject_request_"
                            f"{result.data[0]['id']}"
                        )
                    )
                ]
            ])
        )

    except Exception as e:
        print(
            "Request notification error:",
            e
        )

    await show_store(
        query,
        store_id
    )


# =========================================================
# SELLER REQUESTS
# =========================================================

async def seller_requests(query):
    user_id = query.from_user.id

    store_ids = get_seller_store_ids(
        user_id
    )

    if not store_ids:
        await query.edit_message_text(
            "👥 У тебя пока нет магазина.",
            reply_markup=back_keyboard(
                "seller_menu"
            )
        )
        return

    requests = get_pending_requests_for_user(
        user_id
    )

    # Показываем только запросы магазинов,
    # где пользователь является продавцом
    filtered = []

    for request in requests:
        if request.get("store_id") in store_ids:
            filtered.append(request)

    if not filtered:
        await query.edit_message_text(
            "👥 Новых запросов нет.",
            reply_markup=back_keyboard(
                "seller_menu"
            )
        )
        return

    text = "👥 Запросы продавцов\n\n"

    buttons = []

    for request in filtered[:20]:
        store = get_store(
            request.get("store_id")
        )

        sender = get_user(
            request.get("sender_id")
        )

        store_name = (
            store.get("name")
            if store
            else "Магазин"
        )

        sender_name = (
            sender.get("first_name")
            if sender
            else "Пользователь"
        )

        sender_is_member = is_store_seller(
            request.get("sender_id"),
            request.get("store_id")
        )

        if sender_is_member:
            text += (
                f"📩 Приглашение для "
                f"{sender_name}\n"
            )
        else:
            text += (
                f"🙋 Запрос от "
                f"{sender_name}\n"
            )

        text += (
            f"🏪 {store_name}\n\n"
        )

        buttons.append([
            InlineKeyboardButton(
                f"👤 {sender_name} • {store_name}",
                callback_data=(
                    f"request_info_{request['id']}"
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="seller_menu"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def request_info(
    query,
    request_id
):
    user_id = query.from_user.id

    result = db_select(
        "store_seller_requests",
        filters_dict={
            "id": request_id
        },
        limit=1
    )

    if not result.data:
        await query.answer(
            "❌ Запрос не найден.",
            show_alert=True
        )
        return

    request = result.data[0]

    if request.get("receiver_id") != user_id:
        await query.answer(
            "⛔ Это не твой запрос.",
            show_alert=True
        )
        return

    store = get_store(
        request.get("store_id")
    )

    sender = get_user(
        request.get("sender_id")
    )

    if not store:
        await query.answer(
            "❌ Магазин не найден.",
            show_alert=True
        )
        return

    sender_name = (
        sender.get("first_name")
        if sender
        else "Пользователь"
    )

    sender_is_member = is_store_seller(
        request.get("sender_id"),
        request.get("store_id")
    )

    if sender_is_member:
        title = "📩 Приглашение стать продавцом"
    else:
        title = "🙋 Запрос стать продавцом"

    text = (
        f"{title}\n\n"
        f"👤 Пользователь: {sender_name}\n"
        f"🏪 Магазин: {store['name']}\n\n"
        "Что сделать?"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Одобрить",
                callback_data=(
                    f"approve_request_{request_id}"
                )
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Отклонить",
                callback_data=(
                    f"reject_request_{request_id}"
                )
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="seller_requests"
            )
        ]
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard
    )


async def approve_request(
    query,
    request_id,
    context
):
    user_id = query.from_user.id

    result = db_select(
        "store_seller_requests",
        filters_dict={
            "id": request_id,
            "status": "pending"
        },
        limit=1
    )

    if not result.data:
        await query.answer(
            "❌ Запрос уже обработан.",
            show_alert=True
        )
        return

    request = result.data[0]

    store_id = request["store_id"]
    sender_id = request["sender_id"]

    if not is_store_seller(
        user_id,
        store_id
    ):
        await query.answer(
            "⛔ Ты не продавец этого магазина.",
            show_alert=True
        )
        return

    if is_store_seller(
        sender_id,
        store_id
    ):
        db_update(
            "store_seller_requests",
            {"status": "accepted"},
            {"id": request_id}
        )

        await query.answer(
            "Пользователь уже продавец.",
            show_alert=True
        )
        return

    try:
        db_insert(
            "store_sellers",
            {
                "store_id": store_id,
                "user_id": sender_id
            }
        )

        db_update(
            "store_seller_requests",
            {"status": "accepted"},
            {"id": request_id}
        )

        # Становится продавцом VexMart
        db_update(
            "users",
            {"role": "seller"},
            {"id": sender_id}
        )

    except Exception as e:
        print(
            "Approve seller request error:",
            e
        )

        await query.answer(
            "❌ Не удалось одобрить запрос.",
            show_alert=True
        )
        return

    store = get_store(store_id)

    store_name = (
        store["name"]
        if store
        else "магазина"
    )

    await query.answer(
        "✅ Пользователь добавлен!",
        show_alert=True
    )

    try:
        await context.bot.send_message(
            chat_id=sender_id,
            text=(
                "🎉 Твой запрос одобрен!\n\n"
                f"🏪 Теперь ты продавец магазина "
                f"«{store_name}»."
            )
        )
    except Exception as e:
        print(
            "Approval notification error:",
            e
        )

    await seller_requests(
        query
    )


async def reject_request(
    query,
    request_id,
    context
):
    user_id = query.from_user.id

    result = db_select(
        "store_seller_requests",
        filters_dict={
            "id": request_id,
            "status": "pending"
        },
        limit=1
    )

    if not result.data:
        await query.answer(
            "❌ Запрос уже обработан.",
            show_alert=True
        )
        return

    request = result.data[0]

    store_id = request["store_id"]
    sender_id = request["sender_id"]

    if not is_store_seller(
        user_id,
        store_id
    ):
        await query.answer(
            "⛔ Нет доступа.",
            show_alert=True
        )
        return

    try:
        db_update(
            "store_seller_requests",
            {"status": "rejected"},
            {"id": request_id}
        )

    except Exception as e:
        print(
            "Reject seller request error:",
            e
        )

        await query.answer(
            "❌ Не удалось отклонить запрос.",
            show_alert=True
        )
        return

    store = get_store(store_id)

    store_name = (
        store["name"]
        if store
        else "магазина"
    )

    await query.answer(
        "❌ Запрос отклонён.",
        show_alert=True
    )

    try:
        await context.bot.send_message(
            chat_id=sender_id,
            text=(
                "❌ Твой запрос отклонён.\n\n"
                f"🏪 Магазин: {store_name}"
            )
        )
    except Exception as e:
        print(
            "Reject notification error:",
            e
        )

    await seller_requests(
        query
    )


# =========================================================
# SELLER INVITE
# =========================================================

async def add_seller(
    query,
    context
):
    user_id = query.from_user.id

    store = get_user_store(
        user_id
    )

    if not store:
        await query.answer(
            "❌ Магазин не найден.",
            show_alert=True
        )
        return

    if not is_store_seller(
        user_id,
        store["id"]
    ):
        await query.answer(
            "⛔ Ты не продавец этого магазина.",
            show_alert=True
        )
        return

    context.user_data.clear()

    context.user_data.update({
        "action": "invite_seller",
        "invite_store_id": store["id"]
    })

    await query.edit_message_text(
        "👥 Добавить продавца\n\n"
        "Напиши @username друга.\n\n"
        "Друг должен хотя бы один раз "
        "запустить VexMart."
    )


async def process_seller_invite(
    update,
    context,
    username
):
    user_id = update.effective_user.id

    store_id = context.user_data.get(
        "invite_store_id"
    )

    if not store_id:
        context.user_data.clear()

        await update.message.reply_text(
            "❌ Магазин не найден.",
            reply_markup=seller_menu(user_id)
        )
        return

    username = username.strip()

    if username.startswith("@"):
        username = username[1:]

    if not username:
        await update.message.reply_text(
            "❌ Укажи username."
        )
        return

    result = db_select(
        "users",
        filters_dict={
            "username": username
        },
        limit=1
    )

    if not result.data:
        await update.message.reply_text(
            "❌ Пользователь не найден.\n\n"
            "Он должен сначала запустить VexMart "
            "командой /start."
        )
        return

    target = result.data[0]

    target_id = target["id"]

    if target_id == user_id:
        await update.message.reply_text(
            "😄 Себя пригласить нельзя."
        )
        return

    if is_store_seller(
        target_id,
        store_id
    ):
        await update.message.reply_text(
            "👥 Этот пользователь уже "
            "продавец магазина."
        )

        context.user_data.clear()

        await update.message.reply_text(
            "Главное меню:",
            reply_markup=seller_menu(user_id)
        )
        return

    store = get_store(store_id)

    if not store:
        context.user_data.clear()

        await update.message.reply_text(
            "❌ Магазин не найден.",
            reply_markup=seller_menu(user_id)
        )
        return

    owner_id = store["owner_id"]

    if has_pending_request(
        store_id,
        user_id,
        target_id
    ):
        await update.message.reply_text(
            "📩 Ты уже отправил этому "
            "пользователю приглашение."
        )
        return

    # Защита от ситуации, когда владелец
    # уже отправлял обратный запрос
    if has_pending_request(
        store_id,
        target_id,
        user_id
    ):
        await update.message.reply_text(
            "📩 У вас уже есть ожидающий запрос."
        )
        return

    try:
        result = db_insert(
            "store_seller_requests",
            {
                "store_id": store_id,
                "sender_id": user_id,
                "receiver_id": target_id,
                "status": "pending"
            }
        )

        if not result.data:
            raise RuntimeError(
                "Invite was not created"
            )

    except Exception as e:
        print(
            "Seller invite error:",
            e
        )

        await update.message.reply_text(
            "❌ Не удалось отправить приглашение."
        )
        return

    context.user_data.clear()

    await update.message.reply_text(
        "📩 Приглашение отправлено!",
        reply_markup=ReplyKeyboardRemove()
    )

    await update.message.reply_text(
        "Главное меню:",
        reply_markup=seller_menu(user_id)
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                "📩 Тебя приглашают стать "
                "продавцом!\n\n"
                f"🏪 Магазин: {store['name']}\n\n"
                "Если ты согласишься, ты станешь "
                "продавцом этого магазина."
            ),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ Стать продавцом",
                        callback_data=(
                            f"approve_request_"
                            f"{result.data[0]['id']}"
                        )
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Отказаться",
                        callback_data=(
                            f"reject_request_"
                            f"{result.data[0]['id']}"
                        )
                    )
                ]
            ])
        )

    except Exception as e:
        print(
            "Invite notification error:",
            e
        )


# =========================================================
# NEARBY STORES
# =========================================================

async def request_nearby_location(
    query,
    context
):
    context.user_data.clear()

    context.user_data[
        "action"
    ] = "nearby_stores"

    await query.edit_message_text(
        "📍 Поиск магазинов рядом\n\n"
        "Отправь свою геолокацию.\n"
        "Она используется только для расчёта "
        "расстояния и не сохраняется."
    )

    await query.message.reply_text(
        "📍 Нажми кнопку ниже:",
        reply_markup=location_keyboard()
    )


async def show_nearby_stores(
    update,
    context,
    latitude,
    longitude
):
    stores_result = db_select(
        "stores"
    )

    stores = stores_result.data or []

    nearby = []

    for store in stores:
        store_lat = store.get(
            "latitude"
        )

        store_lon = store.get(
            "longitude"
        )

        if (
            store_lat is None
            or store_lon is None
        ):
            continue

        distance = calculate_distance(
            latitude,
            longitude,
            store_lat,
            store_lon
        )

        nearby.append(
            (
                distance,
                store
            )
        )

    nearby.sort(
        key=lambda item: item[0]
    )

    context.user_data.clear()

    if not nearby:
        await update.message.reply_text(
            "📍 Пока нет магазинов с указанным "
            "местоположением.",
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_text(
            "Главное меню:",
            reply_markup=buyer_menu(
                update.effective_user.id
            )
        )

        return

    buttons = []

    for distance, store in nearby[:10]:
        buttons.append([
            InlineKeyboardButton(
                f"🏪 {store['name']} • "
                f"{format_distance(distance)}",
                callback_data=(
                    f"store_{store['id']}"
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="stores"
        )
    ])

    await update.message.reply_text(
        "📍 Ближайшие магазины:",
        reply_markup=ReplyKeyboardRemove()
    )

    await update.message.reply_text(
        "Выбери магазин:",
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


# =========================================================
# SEARCH
# =========================================================

async def search_stores(
    query,
    search_text
):
    result = db_select(
        "stores"
    )

    stores = result.data or []

    search_text = (
        search_text.lower().strip()
    )

    matches = [
        store
        for store in stores
        if search_text in (
            store.get("name") or ""
        ).lower()
    ]

    if not matches:
        await query.edit_message_text(
            "🔎 Ничего не найдено.",
            reply_markup=back_keyboard(
                "stores"
            )
        )
        return

    buttons = []

    for store in matches[:10]:
        buttons.append([
            InlineKeyboardButton(
                f"🏪 {store['name']}",
                callback_data=(
                    f"store_{store['id']}"
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="stores"
        )
    ])

    await query.edit_message_text(
        "🔎 Результаты поиска:",
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


async def search_products(
    query,
    search_text
):
    result = db_select(
        "products"
    )

    products = result.data or []

    search_text = (
        search_text.lower().strip()
    )

    matches = [
        product
        for product in products
        if search_text in (
            product.get("name") or ""
        ).lower()
    ]

    if not matches:
        await query.edit_message_text(
            "🔎 Товаров не найдено.",
            reply_markup=back_keyboard(
                "products"
            )
        )
        return

    buttons = []

    for product in matches[:20]:
        badge = new_badge(product)

        buttons.append([
            InlineKeyboardButton(
                f"{badge}📦 "
                f"{product['name']} — "
                f"{product['price']} V₽",
                callback_data=(
                    f"product_{product['id']}"
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="products"
        )
    ])

    await query.edit_message_text(
        "🔎 Результаты поиска:",
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


# =========================================================
# PRODUCTS
# =========================================================

async def show_products(query):
    result = db_select(
        "products",
        order_by="id",
        descending=True
    )

    products = result.data or []

    if not products:
        await query.edit_message_text(
            "📦 Товаров пока нет.",
            reply_markup=back_keyboard()
        )
        return

    buttons = []

    for product in products[:50]:
        badge = new_badge(product)

        buttons.append([
            InlineKeyboardButton(
                f"{badge}🛒 "
                f"{product['name']} — "
                f"{product['price']} V₽",
                callback_data=(
                    f"product_{product['id']}"
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔎 Поиск товара",
            callback_data="search_products"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="buyer_menu"
        )
    ])

    await query.edit_message_text(
        "📦 Товары VexMart:",
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


async def show_product(
    query,
    product_id
):
    product = get_product(
        product_id
    )

    if not product:
        await query.answer(
            "❌ Товар не найден.",
            show_alert=True
        )
        return

    store = None

    if product.get("store_id"):
        store = get_store(
            product["store_id"]
        )

    badge = new_badge(product)

    text = (
        f"{badge}📦 "
        f"{product['name']}\n\n"
        f"📝 "
        f"{product.get('description') or 'Без описания'}\n\n"
        f"💰 Цена: "
        f"{product['price']} V₽\n"
        f"📦 Остаток: "
        f"{product.get('stock', 0)}\n"
        f"💸 Кэшбэк: "
        f"{product.get('cashback', 5)}%\n"
    )

    if is_new_product(product):
        text += (
            "\n🆕 Новинка!"
            f"\nДействует первые "
            f"{NEW_PRODUCT_DAYS} дней после добавления."
        )

    if store:
        text += (
            f"\n\n🏪 Магазин: "
            f"{store['name']}"
        )

        if store.get("address"):
            text += (
                f"\n📍 {store['address']}"
            )

    keyboard = [
        [
            InlineKeyboardButton(
                "🛒 Купить",
                callback_data=(
                    f"buy_{product_id}"
                )
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="products"
            )
        ]
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def buy_product(
    query,
    product_id
):
    user_id = query.from_user.id

    product = get_product(
        product_id
    )

    if not product:
        await query.answer(
            "❌ Товар не найден.",
            show_alert=True
        )
        return

    stock = product.get(
        "stock",
        0
    )

    if stock <= 0:
        await query.answer(
            "❌ Товар закончился.",
            show_alert=True
        )
        return

    user = get_user(
        user_id
    )

    if not user:
        await query.answer(
            "❌ Пользователь не найден.",
            show_alert=True
        )
        return

    price = product["price"]

    balance = user.get(
        "balance",
        0
    )

    if balance < price:
        await query.answer(
            "❌ Недостаточно VexCoin.",
            show_alert=True
        )
        return

    seller_id = None

    store_id = product.get(
        "store_id"
    )

    if store_id:
        store = get_store(
            store_id
        )

        if store:
            seller_id = store.get(
                "owner_id"
            )

    cashback_percent = (
        product.get(
            "cashback",
            5
        ) or 0
    )

    cashback = int(
        price
        * cashback_percent
        / 100
    )

    new_balance = (
        balance
        - price
        + cashback
    )

    try:
        db_update(
            "users",
            {
                "balance": new_balance
            },
            {
                "id": user_id
            }
        )

        db_update(
            "products",
            {
                "stock": stock - 1
            },
            {
                "id": product_id
            }
        )

        db_insert(
            "orders",
            {
                "user_id": user_id,
                "product_id": product_id,
                "quantity": 1,
                "total_price": price,
                "status": "pending",
                "buyer_id": user_id,
                "seller_id": seller_id,
                "product_name": product["name"],
                "price": price,
                "cashback": cashback,
                "store_id": store_id
            }
        )

    except Exception as e:
        print(
            "Buy product error:",
            e
        )

        await query.answer(
            "❌ Ошибка при оформлении покупки.",
            show_alert=True
        )
        return

    await query.edit_message_text(
        "✅ Покупка успешно оформлена!\n\n"
        f"📦 {product['name']}\n"
        f"💰 Потрачено: {price} V₽\n"
        f"💸 Кэшбэк: +{cashback} V₽\n\n"
        f"💳 Баланс: "
        f"{new_balance} VexCoin",
        reply_markup=back_keyboard()
    )


# =========================================================
# TASKS
# =========================================================

async def tasks_menu(query):
    user_id = query.from_user.id

    result = db_select(
        "tasks",
        filters_dict={
            "user_id": user_id
        }
    )

    tasks = result.data or []

    if not tasks:
        default_tasks = [
            {
                "user_id": user_id,
                "task": "Зайти в VexMart",
                "reward": 10,
                "completed": False
            },
            {
                "user_id": user_id,
                "task": "Посмотреть магазин",
                "reward": 15,
                "completed": False
            }
        ]

        for task in default_tasks:
            try:
                db_insert(
                    "tasks",
                    task
                )
            except Exception as e:
                print(
                    "Task insert error:",
                    e
                )

        result = db_select(
            "tasks",
            filters_dict={
                "user_id": user_id
            }
        )

        tasks = result.data or []

    text = "🎯 Задания\n\n"

    buttons = []

    for task in tasks:
        if task["completed"]:
            text += (
                f"✅ {task['task']} — "
                f"+{task['reward']} VexCoin\n"
            )

        else:
            text += (
                f"🔲 {task['task']} — "
                f"+{task['reward']} VexCoin\n"
            )

            buttons.append([
                InlineKeyboardButton(
                    f"▶️ Выполнить: "
                    f"{task['task']}",
                    callback_data=(
                        f"task_{task['task']}"
                    )
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="buyer_menu"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


async def complete_task(
    query,
    task_name
):
    user_id = query.from_user.id

    result = db_select(
        "tasks",
        filters_dict={
            "user_id": user_id,
            "task": task_name
        },
        limit=1
    )

    if not result.data:
        await query.answer(
            "❌ Задание не найдено.",
            show_alert=True
        )
        return

    task = result.data[0]

    if task["completed"]:
        await query.answer(
            "Это задание уже выполнено.",
            show_alert=True
        )
        return

    user = get_user(
        user_id
    )

    if not user:
        await query.answer(
            "❌ Пользователь не найден.",
            show_alert=True
        )
        return

    new_balance = (
        user.get(
            "balance",
            0
        )
        + task["reward"]
    )

    try:
        db_update(
            "users",
            {
                "balance": new_balance
            },
            {
                "id": user_id
            }
        )

        db_update(
            "tasks",
            {
                "completed": True
            },
            {
                "user_id": user_id,
                "task": task_name
            }
        )

    except Exception as e:
        print(
            "Complete task error:",
            e
        )

        await query.answer(
            "❌ Ошибка выполнения задания.",
            show_alert=True
        )
        return

    await query.answer(
        f"🎉 +{task['reward']} VexCoin!",
        show_alert=True
    )

    await tasks_menu(
        query
    )


# =========================================================
# STORE MANAGEMENT
# =========================================================

async def my_store(query):
    user_id = query.from_user.id

    store = get_user_store(
        user_id
    )

    if not store:
        await query.edit_message_text(
            "🏪 У тебя пока нет магазина.\n\n"
            "Создай его и начни продавать товары!",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "➕ Создать магазин",
                        callback_data="create_store"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="seller_menu"
                    )
                ]
            ])
        )
        return

    is_owner = (
        store.get("owner_id")
        == user_id
    )

    members = get_store_member_ids(
        store["id"]
    )

    text = (
        f"🏪 {store['name']}\n\n"
        f"📝 "
        f"{store.get('description') or 'Без описания'}\n"
    )

    if store.get("address"):
        text += (
            f"\n📍 {store['address']}\n"
        )
    else:
        text += (
            "\n📍 Местоположение ещё "
            "не указано\n"
        )

    text += (
        f"\n👥 Продавцов: {len(members)}"
    )

    buttons = [
        [
            InlineKeyboardButton(
                "📊 Статистика",
                callback_data="store_stats"
            )
        ],
        [
            InlineKeyboardButton(
                "👥 Добавить продавца",
                callback_data="add_seller"
            )
        ],
        [
            InlineKeyboardButton(
                "👥 Запросы продавцов",
                callback_data="seller_requests"
            )
        ]
    ]

    if is_owner:
        buttons.append([
            InlineKeyboardButton(
                "📍 Указать местоположение",
                callback_data="set_store_location"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="seller_menu"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


async def create_store(
    query,
    context
):
    context.user_data.clear()

    context.user_data[
        "action"
    ] = "create_store_name"

    await query.edit_message_text(
        "🏪 Создание магазина\n\n"
        "Напиши название магазина:"
    )


async def store_stats(query):
    user_id = query.from_user.id

    store = get_user_store(
        user_id
    )

    if not store:
        await query.answer(
            "❌ Магазин не найден.",
            show_alert=True
        )
        return

    if not is_store_seller(
        user_id,
        store["id"]
    ):
        await query.answer(
            "⛔ Нет доступа.",
            show_alert=True
        )
        return

    products = db_select(
        "products",
        filters_dict={
            "store_id": store["id"]
        }
    ).data or []

    orders = db_select(
        "orders",
        filters_dict={
            "store_id": store["id"]
        }
    ).data or []

    total_sales = sum(
        order.get(
            "total_price",
            0
        )
        for order in orders
    )

    sellers = get_store_member_ids(
        store["id"]
    )

    text = (
        "📊 Статистика магазина\n\n"
        f"🏪 {store['name']}\n\n"
        f"👥 Продавцов: {len(sellers)}\n"
        f"📦 Товаров: {len(products)}\n"
        f"🛒 Заказов: {len(orders)}\n"
        f"💰 Продаж: {total_sales} V₽\n"
    )

    if store.get("address"):
        text += (
            f"\n📍 {store['address']}"
        )

    await query.edit_message_text(
        text,
        reply_markup=back_keyboard(
            "my_store"
        )
    )


# =========================================================
# STORE LOCATION
# =========================================================

async def set_store_location(
    query,
    context
):
    user_id = query.from_user.id

    store = get_user_store(
        user_id
    )

    if not store:
        await query.answer(
            "❌ У тебя нет магазина.",
            show_alert=True
        )
        return

    if store.get("owner_id") != user_id:
        await query.answer(
            "⛔ Только владелец магазина "
            "может менять его местоположение.",
            show_alert=True
        )
        return

    context.user_data.clear()

    context.user_data.update({
        "action": "update_store_location",
        "update_store_id": store["id"]
    })

    await query.edit_message_text(
        "📍 Указать местоположение магазина\n\n"
        "Отправь геолокацию магазина.\n\n"
        "Я определю адрес и покажу его "
        "для подтверждения."
    )

    await query.message.reply_text(
        "📍 Нажми кнопку ниже:",
        reply_markup=location_keyboard()
    )


async def confirm_update_store_location(
    query,
    context
):
    user_id = query.from_user.id

    store_id = context.user_data.get(
        "update_store_id"
    )

    latitude = context.user_data.get(
        "update_store_latitude"
    )

    longitude = context.user_data.get(
        "update_store_longitude"
    )

    address = context.user_data.get(
        "update_store_address"
    )

    if (
        not store_id
        or latitude is None
        or longitude is None
        or not address
    ):
        context.user_data.clear()

        await query.edit_message_text(
            "❌ Данные местоположения "
            "потерялись.",
            reply_markup=seller_menu(
                user_id
            )
        )
        return

    store = get_store(
        store_id
    )

    if (
        not store
        or store["owner_id"] != user_id
    ):
        context.user_data.clear()

        await query.edit_message_text(
            "❌ Нет доступа.",
            reply_markup=seller_menu(
                user_id
            )
        )
        return

    try:
        db_update(
            "stores",
            {
                "latitude": latitude,
                "longitude": longitude,
                "address": address
            },
            {
                "id": store_id,
                "owner_id": user_id
            }
        )

    except Exception as e:
        print(
            "Update store location error:",
            e
        )

        await query.edit_message_text(
            "❌ Не удалось сохранить "
            "местоположение."
        )
        return

    context.user_data.clear()

    await query.edit_message_text(
        "✅ Местоположение магазина "
        "обновлено!\n\n"
        f"📍 {address}",
        reply_markup=seller_menu(
            user_id
        )
    )


async def retry_update_store_location(
    query,
    context
):
    store_id = context.user_data.get(
        "update_store_id"
    )

    context.user_data.clear()

    context.user_data.update({
        "action": "update_store_location",
        "update_store_id": store_id
    })

    await query.edit_message_text(
        "🔄 Хорошо!\n\n"
        "Отправь геолокацию магазина ещё раз."
    )

    await query.message.reply_text(
        "📍 Нажми кнопку:",
        reply_markup=location_keyboard()
    )


# =========================================================
# ORDERS
# =========================================================

async def buyer_orders(query):
    user_id = query.from_user.id

    orders = db_select(
        "orders",
        filters_dict={
            "buyer_id": user_id
        },
        order_by="id",
        descending=True
    ).data or []

    if not orders:
        await query.edit_message_text(
            "🛒 У тебя пока нет заказов.",
            reply_markup=back_keyboard()
        )
        return

    text = "🛒 Мои заказы\n\n"

    for order in orders:
        status = order.get(
            "status",
            "pending"
        )

        status_text = {
            "pending": "⏳ Ожидает",
            "accepted": "📦 Принят",
            "completed": "✅ Выполнен",
            "done": "✅ Выполнен"
        }.get(
            status,
            status
        )

        text += (
            f"#{order['id']} — "
            f"{order.get('product_name', 'Товар')}\n"
            f"💰 {order.get('total_price', 0)} V₽\n"
            f"{status_text}\n\n"
        )

    await query.edit_message_text(
        text,
        reply_markup=back_keyboard()
    )


async def seller_orders(query):
    user_id = query.from_user.id

    store_ids = get_seller_store_ids(
        user_id
    )

    if not store_ids:
        await query.edit_message_text(
            "📦 У тебя пока нет магазина.",
            reply_markup=back_keyboard(
                "seller_menu"
            )
        )
        return

    all_orders = db_select(
        "orders",
        order_by="id",
        descending=True,
        limit=1000
    ).data or []

    orders = [
        order
        for order in all_orders
        if order.get("store_id") in store_ids
    ]

    if not orders:
        await query.edit_message_text(
            "📦 Заказов пока нет.",
            reply_markup=back_keyboard(
                "seller_menu"
            )
        )
        return

    text = "📦 Заказы покупателей\n\n"

    buttons = []

    for order in orders:
        status = order.get(
            "status",
            "pending"
        )

        status_text = {
            "pending": "⏳ Ожидает",
            "accepted": "📦 Принят",
            "completed": "✅ Выполнен",
            "done": "✅ Выполнен"
        }.get(
            status,
            status
        )

        text += (
            f"#{order['id']} — "
            f"{order.get('product_name', 'Товар')}\n"
            f"💰 {order.get('total_price', 0)} V₽\n"
            f"{status_text}\n\n"
        )

        if status == "pending":
            buttons.append([
                InlineKeyboardButton(
                    f"✅ Принять #{order['id']}",
                    callback_data=(
                        f"accept_order_{order['id']}"
                    )
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="seller_menu"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        )
    )


async def accept_order(
    query,
    order_id
):
    user_id = query.from_user.id

    result = db_select(
        "orders",
        filters_dict={
            "id": order_id
        },
        limit=1
    )

    if not result.data:
        await query.answer(
            "❌ Заказ не найден.",
            show_alert=True
        )
        return

    order = result.data[0]

    store_id = order.get(
        "store_id"
    )

    if not store_id:
        await query.answer(
            "❌ У заказа нет магазина.",
            show_alert=True
        )
        return

    if not is_store_seller(
        user_id,
        store_id
    ):
        await query.answer(
            "⛔ Ты не продавец этого магазина.",
            show_alert=True
        )
        return

    if order.get("status") != "pending":
        await query.answer(
            "Этот заказ уже обработан.",
            show_alert=True
        )
        return

    try:
        db_update(
            "orders",
            {
                "status": "accepted",
                "seller_id": user_id
            },
            {
                "id": order_id,
                "status": "pending"
            }
        )

    except Exception as e:
        print(
            "Accept order error:",
            e
        )

        await query.answer(
            "❌ Не удалось принять заказ.",
            show_alert=True
        )
        return

    await query.answer(
        "✅ Заказ принят!",
        show_alert=True
    )

    await seller_orders(
        query
    )


# =========================================================
# TEXT / LOCATION
# =========================================================

async def text_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if (
        not update.message
        or not update.message.text
    ):
        return

    text = update.message.text.strip()

    user_id = update.effective_user.id

    # CANCEL
    if text == "❌ Отмена":
        context.user_data.clear()

        await update.message.reply_text(
            "❌ Отменено.",
            reply_markup=ReplyKeyboardRemove()
        )

        user = get_user(
            user_id
        )

        role = (
            user.get("role")
            if user
            else "buyer"
        )

        await update.message.reply_text(
            "Главное меню:",
            reply_markup=(
                seller_menu(user_id)
                if role == "seller"
                else buyer_menu(user_id)
            )
        )

        return

    # SELLER INVITE
    if context.user_data.get(
        "action"
    ) == "invite_seller":

        await process_seller_invite(
            update,
            context,
            text
        )

        return

    # SEARCH STORES
    if context.user_data.get(
        "searching_stores"
    ):
        context.user_data[
            "searching_stores"
        ] = False

        result = db_select(
            "stores"
        )

        stores = result.data or []

        matches = [
            store
            for store in stores
            if text.lower() in (
                store.get("name") or ""
            ).lower()
        ]

        if not matches:
            await update.message.reply_text(
                "🔎 Ничего не найдено.",
                reply_markup=buyer_menu(
                    user_id
                )
            )
            return

        keyboard = []

        for store in matches[:20]:
            keyboard.append([
                InlineKeyboardButton(
                    f"🏪 {store['name']}",
                    callback_data=(
                        f"store_{store['id']}"
                    )
                )
            ])

        keyboard.append([
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="stores"
            )
        ])

        await update.message.reply_text(
            "🔎 Результаты поиска:",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

        return

    # SEARCH PRODUCTS
    if context.user_data.get(
        "searching_products"
    ):
        context.user_data[
            "searching_products"
        ] = False

        result = db_select(
            "products"
        )

        products = result.data or []

        matches = [
            product
            for product in products
            if text.lower() in (
                product.get("name") or ""
            ).lower()
        ]

        if not matches:
            await update.message.reply_text(
                "🔎 Товаров не найдено.",
                reply_markup=buyer_menu(
                    user_id
                )
            )
            return

        keyboard = []

        for product in matches[:20]:
            badge = new_badge(
                product
            )

            keyboard.append([
                InlineKeyboardButton(
                    f"{badge}📦 "
                    f"{product['name']} — "
                    f"{product['price']} V₽",
                    callback_data=(
                        f"product_{product['id']}"
                    )
                )
            ])

        keyboard.append([
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="products"
            )
        ])

        await update.message.reply_text(
            "🔎 Результаты поиска:",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

        return

    # CREATE STORE NAME
    if context.user_data.get(
        "action"
    ) == "create_store_name":

        context.user_data[
            "new_store_name"
        ] = text

        context.user_data[
            "action"
        ] = "create_store_description"

        await update.message.reply_text(
            "📝 Теперь напиши описание магазина."
        )

        return

    # CREATE STORE DESCRIPTION
    if context.user_data.get(
        "action"
    ) == "create_store_description":

        name = context.user_data.get(
            "new_store_name"
        )

        if not name:
            context.user_data.clear()

            await update.message.reply_text(
                "❌ Название магазина "
                "потерялось.",
                reply_markup=buyer_menu(
                    user_id
                )
            )

            return

        try:
            result = db_insert(
                "stores",
                {
                    "owner_id": user_id,
                    "name": name,
                    "description": text
                }
            )

            if not result.data:
                raise RuntimeError(
                    "Store was not created"
                )

            store_id = result.data[0]["id"]

        except Exception as e:
            print(
                "Create store error:",
                e
            )

            context.user_data.clear()

            await update.message.reply_text(
                "❌ Не удалось создать магазин."
            )

            return

        context.user_data.clear()

        context.user_data.update({
            "action": "update_store_location",
            "update_store_id": store_id
        })

        await update.message.reply_text(
            "🏪 Магазин создан!\n\n"
            "Теперь укажи его "
            "местоположение.",
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_text(
            "📍 Нажми кнопку ниже:",
            reply_markup=location_keyboard()
        )

        return

    # DELIVERY ADDRESS
    if context.user_data.get(
        "waiting_delivery_address"
    ):

        order_id = context.user_data.get(
            "delivery_order_id"
        )

        if order_id:
            try:
                db_update(
                    "orders",
                    {
                        "delivery_address": text
                    },
                    {
                        "id": order_id
                    }
                )

            except Exception as e:
                print(
                    "Delivery address error:",
                    e
                )

        context.user_data.clear()

        await update.message.reply_text(
            "📍 Адрес доставки сохранён.",
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_text(
            "Главное меню:",
            reply_markup=buyer_menu(
                user_id
            )
        )

        return

    await update.message.reply_text(
        "🤔 Я не понял это сообщение.\n\n"
        "Используй кнопки меню.",
        reply_markup=buyer_menu(
            user_id
        )
    )


async def location_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    location = update.message.location

    latitude = location.latitude
    longitude = location.longitude

    action = context.user_data.get(
        "action"
    )

    # NEARBY STORES
    if action == "nearby_stores":
        await show_nearby_stores(
            update,
            context,
            latitude,
            longitude
        )
        return

    # UPDATE STORE LOCATION
    if action == "update_store_location":

        store_id = context.user_data.get(
            "update_store_id"
        )

        if not store_id:
            await update.message.reply_text(
                "❌ Магазин не найден.",
                reply_markup=ReplyKeyboardRemove()
            )
            return

        address = await reverse_geocode(
            latitude,
            longitude
        )

        if not address:
            address = (
                f"{latitude:.6f}, "
                f"{longitude:.6f}"
            )

        context.user_data.update({
            "update_store_latitude": latitude,
            "update_store_longitude": longitude,
            "update_store_address": address
        })

        await update.message.reply_text(
            "📍 Местоположение получено!\n\n"
            f"🏠 Адрес:\n{address}\n\n"
            "Сохранить это местоположение?",
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_text(
            "Выбери действие:",
            reply_markup=confirm_location_keyboard(
                "confirm_update_store_location",
                "retry_update_store_location"
            )
        )

        return

    await update.message.reply_text(
        "📍 Геолокация получена, "
        "но сейчас она не требуется.",
        reply_markup=ReplyKeyboardRemove()
    )


# =========================================================
# ROLE SWITCH
# =========================================================

async def switch_role(
    query,
    role
):
    user_id = query.from_user.id

    try:
        db_update(
            "users",
            {
                "role": role
            },
            {
                "id": user_id
            }
        )

    except Exception as e:
        print(
            "Switch role error:",
            e
        )

        await query.answer(
            "❌ Не удалось сменить режим.",
            show_alert=True
        )

        return

    if role == "seller":
        await query.edit_message_text(
            "🏪 Ты перешёл в режим продавца.",
            reply_markup=seller_menu(
                user_id
            )
        )

    else:
        await query.edit_message_text(
            "🛒 Ты перешёл в режим покупателя.",
            reply_markup=buyer_menu(
                user_id
            )
        )


# =========================================================
# ADMIN UI
# =========================================================

ADMIN_PAGE_SIZE = 5


def admin_role_text(role):
    return {
        "seller": "🏪 Продавец",
        "buyer": "🛒 Покупатель"
    }.get(
        role,
        "❔ Не выбрана"
    )


def admin_status_text(status):
    return {
        "pending": "⏳ Ожидает",
        "accepted": "📦 Принят",
        "completed": "✅ Выполнен",
        "done": "✅ Выполнен",
        "cancelled": "❌ Отменён"
    }.get(
        status,
        status or "❔ Неизвестен"
    )


def admin_user_name(user):
    if not user:
        return "Неизвестный пользователь"

    first_name = (
        user.get("first_name")
        or "Без имени"
    )

    username = user.get(
        "username"
    )

    if username:
        return (
            f"{first_name} "
            f"(@{username})"
        )

    return first_name


def admin_format_date(value):
    if not value:
        return "Не указано"

    return str(value).replace(
        "T",
        " "
    )[:16]


async def admin_panel(query):
    user_id = query.from_user.id

    if user_id != ADMIN_ID:
        await query.answer(
            "⛔ Нет доступа.",
            show_alert=True
        )
        return

    users = db_select(
        "users"
    ).data or []

    stores = db_select(
        "stores"
    ).data or []

    products = db_select(
        "products"
    ).data or []

    orders = db_select(
        "orders"
    ).data or []

    text = (
        "🛠 Админ-панель\n\n"
        f"👤 Пользователей: {len(users)}\n"
        f"🏪 Магазинов: {len(stores)}\n"
        f"📦 Товаров: {len(products)}\n"
        f"🛒 Заказов: {len(orders)}\n"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "👤 Пользователи",
                callback_data="admin_users"
            )
        ],
        [
            InlineKeyboardButton(
                "🏪 Магазины",
                callback_data="admin_stores"
            )
        ],
        [
            InlineKeyboardButton(
                "📦 Товары",
                callback_data="admin_products"
            )
        ],
        [
            InlineKeyboardButton(
                "🛒 Заказы",
                callback_data="admin_orders"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="buyer_menu"
            )
        ]
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def admin_list(
    query,
    table,
    title,
    page=0
):
    if query.from_user.id != ADMIN_ID:
        await query.answer(
            "⛔ Нет доступа.",
            show_alert=True
        )
        return

    rows = db_select(
        table,
        order_by="id",
        descending=True,
        limit=1000
    ).data or []

    total = len(rows)

    total_pages = max(
        1,
        math.ceil(
            total / ADMIN_PAGE_SIZE
        )
    )

    page = max(
        0,
        min(
            page,
            total_pages - 1
        )
    )

    start = (
        page
        * ADMIN_PAGE_SIZE
    )

    end = (
        start
        + ADMIN_PAGE_SIZE
    )

    page_rows = rows[start:end]

    text = (
        f"{title}\n\n"
        f"📄 Страница "
        f"{page + 1} "
        f"из {total_pages}\n"
        f"Всего: {total}\n"
    )

    keyboard = []

    if not page_rows:
        text += "\nПусто."

    for row in page_rows:

        if table == "users":

            name = (
                row.get("first_name")
                or row.get("username")
                or str(row.get("id"))
            )

            balance = row.get(
                "balance",
                0
            )

            button_text = (
                f"👤 {name} • "
                f"{balance} VexCoin"
            )

            callback = (
                f"admin_user_{row['id']}"
            )

        elif table == "stores":

            name = (
                row.get("name")
                or "Без названия"
            )

            button_text = (
                f"🏪 {name}"
            )

            callback = (
                f"admin_store_{row['id']}"
            )

        elif table == "products":

            name = (
                row.get("name")
                or "Без названия"
            )

            price = row.get(
                "price",
                0
            )

            badge = new_badge(
                row
            )

            button_text = (
                f"{badge}📦 {name} • "
                f"{price} V₽"
            )

            callback = (
                f"admin_product_{row['id']}"
            )

        elif table == "orders":

            product_name = (
                row.get("product_name")
                or f"Товар #{row.get('product_id')}"
            )

            button_text = (
                f"🛒 #{row['id']} • "
                f"{product_name}"
            )

            callback = (
                f"admin_order_{row['id']}"
            )

        else:
            continue

        keyboard.append([
            InlineKeyboardButton(
                button_text[:60],
                callback_data=callback
            )
        ])

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️",
                callback_data=(
                    f"admin_{table}_"
                    f"{page - 1}"
                )
            )
        )

    if page < total_pages - 1:
        navigation.append(
            InlineKeyboardButton(
                "➡️",
                callback_data=(
                    f"admin_{table}_"
                    f"{page + 1}"
                )
            )
        )

    if navigation:
        keyboard.append(
            navigation
        )

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ В админ-панель",
            callback_data="admin"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def admin_user_details(
    query,
    user_id
):
    if query.from_user.id != ADMIN_ID:
        return

    user = get_user(
        user_id
    )

    if not user:
        await query.edit_message_text(
            "❌ Пользователь не найден.",
            reply_markup=back_keyboard(
                "admin_users_0"
            )
        )
        return

    stores = db_select(
        "stores",
        filters_dict={
            "owner_id": user_id
        },
        limit=100
    ).data or []

    seller_links = db_select(
        "store_sellers",
        filters_dict={
            "user_id": user_id
        },
        limit=100
    ).data or []

    buyer_orders = db_select(
        "orders",
        filters_dict={
            "buyer_id": user_id
        },
        limit=1000
    ).data or []

    seller_orders_list = db_select(
        "orders",
        filters_dict={
            "seller_id": user_id
        },
        limit=1000
    ).data or []

    username = user.get(
        "username"
    )

    text = (
        "👤 Пользователь\n\n"
        f"🆔 ID: {user.get('id')}\n"
        f"👤 Имя: "
        f"{user.get('first_name') or 'Не указано'}\n"
        f"🔗 Username: "
        f"@{username if username else 'нет'}\n"
        f"🎭 Роль: "
        f"{admin_role_text(user.get('role'))}\n"
        f"💰 Баланс: "
        f"{user.get('balance', 0)} VexCoin\n\n"
        f"🏪 Собственных магазинов: "
        f"{len(stores)}\n"
        f"👥 Магазинов как продавец: "
        f"{len(seller_links)}\n"
        f"🛒 Покупок: "
        f"{len(buyer_orders)}\n"
        f"📦 Продаж: "
        f"{len(seller_orders_list)}"
    )

    keyboard = []

    for store in stores[:5]:
        keyboard.append([
            InlineKeyboardButton(
                f"🏪 "
                f"{store.get('name', 'Магазин')}",
                callback_data=(
                    f"admin_store_"
                    f"{store['id']}"
                )
            )
        ])

    for link in seller_links[:5]:
        store = get_store(
            link.get("store_id")
        )

        if store:
            keyboard.append([
                InlineKeyboardButton(
                    f"👥 "
                    f"{store.get('name', 'Магазин')}",
                    callback_data=(
                        f"admin_store_"
                        f"{store['id']}"
                    )
                )
            ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ К пользователям",
            callback_data="admin_users_0"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def admin_store_details(
    query,
    store_id
):
    if query.from_user.id != ADMIN_ID:
        return

    store = get_store(
        store_id
    )

    if not store:
        await query.edit_message_text(
            "❌ Магазин не найден.",
            reply_markup=back_keyboard(
                "admin_stores_0"
            )
        )
        return

    owner = get_user(
        store.get("owner_id")
    )

    products = db_select(
        "products",
        filters_dict={
            "store_id": store_id
        },
        limit=1000
    ).data or []

    orders = db_select(
        "orders",
        filters_dict={
            "store_id": store_id
        },
        limit=1000
    ).data or []

    sellers = get_store_sellers(
        store_id
    )

    address = (
        store.get("address")
        or "Не указано"
    )

    text = (
        "🏪 Магазин\n\n"
        f"📛 Название: "
        f"{store.get('name')}\n"
        f"📝 Описание: "
        f"{store.get('description') or 'Нет описания'}\n\n"
        f"👤 Владелец: "
        f"{admin_user_name(owner)}\n"
        f"🆔 ID владельца: "
        f"{store.get('owner_id')}\n\n"
        f"👥 Дополнительных продавцов: "
        f"{len(sellers)}\n"
        f"📦 Товаров: "
        f"{len(products)}\n"
        f"🛒 Заказов: "
        f"{len(orders)}\n"
        f"📍 Адрес: "
        f"{address}\n\n"
        f"📅 Создан: "
        f"{admin_format_date(store.get('created_at'))}"
    )

    keyboard = []

    for seller in sellers[:5]:
        keyboard.append([
            InlineKeyboardButton(
                f"👤 "
                f"{admin_user_name(seller)}",
                callback_data=(
                    f"admin_user_"
                    f"{seller['id']}"
                )
            )
        ])

    for product in products[:5]:
        badge = new_badge(
            product
        )

        keyboard.append([
            InlineKeyboardButton(
                f"{badge}📦 "
                f"{product.get('name', 'Товар')}",
                callback_data=(
                    f"admin_product_"
                    f"{product['id']}"
                )
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ К магазинам",
            callback_data="admin_stores_0"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def admin_product_details(
    query,
    product_id
):
    if query.from_user.id != ADMIN_ID:
        return

    product = get_product(
        product_id
    )

    if not product:
        await query.edit_message_text(
            "❌ Товар не найден.",
            reply_markup=back_keyboard(
                "admin_products_0"
            )
        )
        return

    store = get_store(
        product.get("store_id")
    )

    badge = new_badge(
        product
    )

    text = (
        f"{badge}📦 Товар\n\n"
        f"📛 Название: "
        f"{product.get('name')}\n"
        f"📝 Описание: "
        f"{product.get('description') or 'Нет описания'}\n\n"
        f"💰 Цена: "
        f"{product.get('price', 0)} V₽\n"
        f"📦 Остаток: "
        f"{product.get('stock', 0)}\n"
        f"💸 Кэшбэк: "
        f"{product.get('cashback', 0)}%\n"
        f"🆕 Новинка: "
        f"{'Да' if is_new_product(product) else 'Нет'}\n"
        f"🏪 Магазин: "
        f"{store.get('name') if store else 'Не указан'}\n"
        f"🆔 ID товара: "
        f"{product.get('id')}\n"
        f"📅 Создан: "
        f"{admin_format_date(product.get('created_at'))}"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "🏪 Открыть магазин",
                callback_data=(
                    f"admin_store_{store['id']}"
                    if store
                    else "admin_stores_0"
                )
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ К товарам",
                callback_data="admin_products_0"
            )
        ]
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def admin_order_details(
    query,
    order_id
):
    if query.from_user.id != ADMIN_ID:
        return

    rows = db_select(
        "orders",
        filters_dict={
            "id": order_id
        },
        limit=1
    ).data or []

    if not rows:
        await query.edit_message_text(
            "❌ Заказ не найден.",
            reply_markup=back_keyboard(
                "admin_orders_0"
            )
        )
        return

    order = rows[0]

    buyer_id = (
        order.get("buyer_id")
        or order.get("user_id")
    )

    seller_id = order.get(
        "seller_id"
    )

    buyer = (
        get_user(buyer_id)
        if buyer_id
        else None
    )

    seller = (
        get_user(seller_id)
        if seller_id
        else None
    )

    store = get_store(
        order.get("store_id")
    )

    product_name = (
        order.get("product_name")
        or f"Товар #{order.get('product_id')}"
    )

    status = admin_status_text(
        order.get("status")
    )

    delivery = order.get(
        "delivery_address"
    )

    text = (
        "🛒 Заказ\n\n"
        f"🆔 Номер: #{order.get('id')}\n"
        f"📦 Товар: {product_name}\n"
        f"🔢 Количество: "
        f"{order.get('quantity', 1)}\n\n"
        f"💰 Цена: "
        f"{order.get('price', 0)} V₽\n"
        f"💵 Сумма: "
        f"{order.get('total_price', 0)} V₽\n"
        f"💸 Кэшбэк: "
        f"{order.get('cashback', 0)} V₽\n\n"
        f"👤 Покупатель: "
        f"{admin_user_name(buyer)}\n"
        f"🏪 Продавец: "
        f"{admin_user_name(seller)}\n"
        f"🏪 Магазин: "
        f"{store.get('name') if store else 'Не указан'}\n\n"
        f"📊 Статус: {status}\n"
        f"📅 Создан: "
        f"{admin_format_date(order.get('created_at'))}"
    )

    if delivery:
        text += (
            f"\n📍 Доставка: {delivery}"
        )

    keyboard = []

    if buyer:
        keyboard.append([
            InlineKeyboardButton(
                "👤 Покупатель",
                callback_data=(
                    f"admin_user_"
                    f"{buyer['id']}"
                )
            )
        ])

    if seller:
        keyboard.append([
            InlineKeyboardButton(
                "👤 Продавец",
                callback_data=(
                    f"admin_user_"
                    f"{seller['id']}"
                )
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ К заказам",
            callback_data="admin_orders_0"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================================================
# CALLBACK ROUTER
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    try:
        await query.answer()

        data = query.data

        user_id = query.from_user.id

        # =================================================
        # MAIN MENUS
        # =================================================

        if data == "buyer_menu":
            await query.edit_message_text(
                "🛒 Меню покупателя",
                reply_markup=buyer_menu(
                    user_id
                )
            )
            return

        if data == "seller_menu":
            await query.edit_message_text(
                "🏪 Меню продавца",
                reply_markup=seller_menu(
                    user_id
                )
            )
            return

        # =================================================
        # ADMIN
        # =================================================

        if data == "admin":
            await admin_panel(
                query
            )
            return

        if data == "admin_users":
            await admin_list(
                query,
                "users",
                "👤 Пользователи",
                0
            )
            return

        if data == "admin_stores":
            await admin_list(
                query,
                "stores",
                "🏪 Магазины",
                0
            )
            return

        if data == "admin_products":
            await admin_list(
                query,
                "products",
                "📦 Товары",
                0
            )
            return

        if data == "admin_orders":
            await admin_list(
                query,
                "orders",
                "🛒 Заказы",
                0
            )
            return

        # ADMIN PAGINATION

        if data.startswith(
            "admin_users_"
        ):
            page = int(
                data.rsplit(
                    "_",
                    1
                )[1]
            )

            await admin_list(
                query,
                "users",
                "👤 Пользователи",
                page
            )
            return

        if data.startswith(
            "admin_stores_"
        ):
            page = int(
                data.rsplit(
                    "_",
                    1
                )[1]
            )

            await admin_list(
                query,
                "stores",
                "🏪 Магазины",
                page
            )
            return

        if data.startswith(
            "admin_products_"
        ):
            page = int(
                data.rsplit(
                    "_",
                    1
                )[1]
            )

            await admin_list(
                query,
                "products",
                "📦 Товары",
                page
            )
            return

        if data.startswith(
            "admin_orders_"
        ):
            page = int(
                data.rsplit(
                    "_",
                    1
                )[1]
            )

            await admin_list(
                query,
                "orders",
                "🛒 Заказы",
                page
            )
            return

        # ADMIN DETAILS

        if data.startswith(
            "admin_user_"
        ):
            target_user_id = int(
                data.rsplit(
                    "_",
                    1
                )[1]
            )

            await admin_user_details(
                query,
                target_user_id
            )
            return

        if data.startswith(
            "admin_store_"
        ):
            store_id = int(
                data.rsplit(
                    "_",
                    1
                )[1]
            )

            await admin_store_details(
                query,
                store_id
            )
            return

        if data.startswith(
            "admin_product_"
        ):
            product_id = int(
                data.rsplit(
                    "_",
                    1
                )[1]
            )

            await admin_product_details(
                query,
                product_id
            )
            return

        if data.startswith(
            "admin_order_"
        ):
            order_id = int(
                data.rsplit(
                    "_",
                    1
                )[1]
            )

            await admin_order_details(
                query,
                order_id
            )
            return

        # =================================================
        # PROFILE
        # =================================================

        if data == "profile":
            await show_profile(
                query
            )
            return

        # =================================================
        # STORES
        # =================================================

        if data == "stores":
            await show_stores(
                query
            )
            return

        if data in (
            "nearby",
            "nearby_request"
        ):
            await request_nearby_location(
                query,
                context
            )
            return

        if data == "search_stores":
            context.user_data.clear()

            context.user_data[
                "searching_stores"
            ] = True

            await query.edit_message_text(
                "🔎 Напиши название магазина:"
            )

            return

        if data.startswith(
            "request_store_"
        ):
            store_id = int(
                data.split("_")[-1]
            )

            await request_store_seller(
                query,
                store_id,
                context
            )
            return

        if data.startswith(
            "store_"
        ):
            store_id = int(
                data.split("_")[1]
            )

            await show_store(
                query,
                store_id
            )
            return

        # =================================================
        # PRODUCTS
        # =================================================

        if data == "products":
            await show_products(
                query
            )
            return

        if data == "search_products":
            context.user_data.clear()

            context.user_data[
                "searching_products"
            ] = True

            await query.edit_message_text(
                "🔎 Напиши название товара:"
            )

            return

        if data.startswith(
            "product_"
        ):
            product_id = int(
                data.split("_")[1]
            )

            await show_product(
                query,
                product_id
            )
            return

        # =================================================
        # BUY
        # =================================================

        if data.startswith(
            "buy_"
        ):
            product_id = int(
                data.split("_")[1]
            )

            await buy_product(
                query,
                product_id
            )
            return

        # =================================================
        # TASKS
        # =================================================

        if data == "tasks":
            await tasks_menu(
                query
            )
            return

        if data.startswith(
            "task_"
        ):
            task_name = data[
                len("task_"):
            ]

            await complete_task(
                query,
                task_name
            )
            return

        # =================================================
        # ORDERS
        # =================================================

        if data in (
            "orders",
            "buyer_orders"
        ):
            await buyer_orders(
                query
            )
            return

        if data == "seller_orders":
            await seller_orders(
                query
            )
            return

        if data.startswith(
            "accept_order_"
        ):
            order_id = int(
                data.split("_")[2]
            )

            await accept_order(
                query,
                order_id
            )
            return

        # =================================================
        # STORE MANAGEMENT
        # =================================================

        if data == "my_store":
            await my_store(
                query
            )
            return

        if data == "create_store":
            await create_store(
                query,
                context
            )
            return

        if data == "store_stats":
            await store_stats(
                query
            )
            return

        if data == "add_seller":
            await add_seller(
                query,
                context
            )
            return

        if data == "seller_requests":
            await seller_requests(
                query
            )
            return

        if data.startswith(
            "request_info_"
        ):
            request_id = int(
                data.split("_")[-1]
            )

            await request_info(
                query,
                request_id
            )
            return

        if data.startswith(
            "approve_request_"
        ):
            request_id = int(
                data.split("_")[-1]
            )

            await approve_request(
                query,
                request_id,
                context
            )
            return

        if data.startswith(
            "reject_request_"
        ):
            request_id = int(
                data.split("_")[-1]
            )

            await reject_request(
                query,
                request_id,
                context
            )
            return

        if data in (
            "store_location",
            "set_store_location"
        ):
            await set_store_location(
                query,
                context
            )
            return

        if data == "confirm_update_store_location":
            await confirm_update_store_location(
                query,
                context
            )
            return

        if data == "retry_update_store_location":
            await retry_update_store_location(
                query,
                context
            )
            return

        # =================================================
        # ROLE
        # =================================================

        if data == "switch_buyer":
            await switch_role(
                query,
                "buyer"
            )
            return

        if data == "switch_seller":
            await switch_role(
                query,
                "seller"
            )
            return

        # =================================================
        # CANCEL
        # =================================================

        if data == "cancel_location":
            context.user_data.clear()

            await query.edit_message_text(
                "❌ Отменено.",
                reply_markup=buyer_menu(
                    user_id
                )
            )
            return

        # =================================================
        # BACK
        # =================================================

        if data == "back":
            await query.edit_message_text(
                "🛒 Меню покупателя",
                reply_markup=buyer_menu(
                    user_id
                )
            )
            return

        await query.edit_message_text(
            "❌ Неизвестная команда."
        )

    except Exception as e:
        print(
            "Callback handler error:",
            e
        )

        try:
            await query.answer(
                "❌ Произошла ошибка.",
                show_alert=True
            )
        except Exception:
            pass


# =========================================================
# WEB SERVER
# =========================================================

async def health(request):
    return web.Response(
        text=(
            f"VexMart {VERSION} "
            "is running!"
        )
    )


async def start_web_server(
    application
):
    app = web.Application()

    app.router.add_get(
        "/",
        health
    )

    app.router.add_get(
        "/health",
        health
    )

    runner = web.AppRunner(
        app
    )

    await runner.setup()

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    application.bot_data[
        "web_runner"
    ] = runner

    print(
        f"🌐 Web server started "
        f"on port {port}"
    )


async def stop_web_server(
    application
):
    runner = application.bot_data.get(
        "web_runner"
    )

    if runner:
        await runner.cleanup()


# =========================================================
# POST INIT
# =========================================================

async def post_init(
    application: Application
):
    await start_web_server(
        application
    )

    print(
        "🌐 Web server initialized"
    )


async def post_shutdown(
    application: Application
):
    await stop_web_server(
        application
    )

    print(
        "🌐 Web server stopped"
    )


# =========================================================
# MAIN
# =========================================================

def main():
    print(
        f"🚀 Starting VexMart {VERSION}"
    )

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        MessageHandler(
            filters.LOCATION,
            location_message
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            text_message
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    print(
        "🤖 Telegram bot starting..."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
