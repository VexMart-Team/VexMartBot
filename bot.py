import os
import re
import asyncio
import hashlib
import hmac
import secrets
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from aiohttp import web
from supabase import create_client, Client

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# ============================================================
# CONFIG
# ============================================================

VERSION = "0.48.0"

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {
    int(admin_id.strip())
    for admin_id in os.getenv("ADMIN_IDS", "").split(",")
    if admin_id.strip()
}
SUPERADMIN_ID = int(os.getenv("SUPERADMIN_ID", "0") or 0)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is not set")

if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY is not set")

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY,
)

# ============================================================
# DATABASE HELPERS
# ============================================================

def db_select(
    table,
    columns="*",
    filters_dict=None,
    limit=None,
    order_by=None,
    ascending=False,
):
    try:
        query = supabase.table(table).select(columns)

        if filters_dict:
            for key, value in filters_dict.items():
                query = query.eq(key, value)

        if order_by:
            query = query.order(
                order_by,
                desc=not ascending,
            )

        if limit:
            query = query.limit(limit)

        result = query.execute()
        return result.data or []

    except Exception as e:
        print(f"[DB SELECT ERROR] {table}: {e}")
        return []


def db_insert(table, data):
    try:
        result = supabase.table(table).insert(data).execute()
        return result.data or []

    except Exception as e:
        print(f"[DB INSERT ERROR] {table}: {e}")
        return []


def db_update(table, data, filters_dict):
    try:
        query = supabase.table(table).update(data)

        for key, value in filters_dict.items():
            query = query.eq(key, value)

        result = query.execute()
        return result.data or []

    except Exception as e:
        print(f"[DB UPDATE ERROR] {table}: {e}")
        return []


def db_delete(table, filters_dict):
    try:
        query = supabase.table(table).delete()

        for key, value in filters_dict.items():
            query = query.eq(key, value)

        result = query.execute()
        return result.data or []

    except Exception as e:
        print(f"[DB DELETE ERROR] {table}: {e}")
        return []


# ============================================================
# CALLBACK ANSWER
# ============================================================

async def safe_query_answer(
    query,
    text=None,
    show_alert=False,
):
    try:
        await query.answer(
            text=text,
            show_alert=show_alert,
        )

    except Exception as e:
        error_text = str(e).lower()

        if (
            "query is too old" not in error_text
            and "query id is invalid" not in error_text
            and "already answered" not in error_text
        ):
            print(f"[QUERY ANSWER ERROR] {e}")


# ============================================================
# USERS
# ============================================================

def get_user(user_id):
    users = db_select(
        "users",
        filters_dict={"id": user_id},
        limit=1,
    )

    return users[0] if users else None


def ensure_user(tg_user):
    user = get_user(tg_user.id)

    if user:
        updates = {}

        if user.get("username") != tg_user.username:
            updates["username"] = tg_user.username

        if user.get("first_name") != tg_user.first_name:
            updates["first_name"] = tg_user.first_name

        if updates:
            db_update(
                "users",
                updates,
                {"id": tg_user.id},
            )

        return get_user(tg_user.id)

    created = db_insert(
        "users",
        {
            "id": tg_user.id,
            "username": tg_user.username,
            "first_name": tg_user.first_name,
            "role": "buyer",
            "balance": 100,
        },
    )

    return created[0] if created else None


def set_role(user_id, role):
    db_update(
        "users",
        {"role": role},
        {"id": user_id},
    )


def add_balance(user_id, amount):
    user = get_user(user_id)

    if not user:
        return False

    balance = user.get("balance", 0) or 0

    db_update(
        "users",
        {"balance": balance + amount},
        {"id": user_id},
    )

    return True


# ============================================================
# VEXMART 0.44
# ============================================================

XP_DAILY_BONUS = 10
XP_PURCHASE = 50
XP_SALE = 50
XP_REVIEW = 15
XP_STORE_CREATE = 100
XP_TASK = 20
XP_PER_LEVEL = 100

def get_user_xp(user_id):
    user = get_user(user_id)
    return int(user.get("xp", 0) or 0) if user else 0

def get_user_level(user_id):
    return (get_user_xp(user_id) // XP_PER_LEVEL) + 1

def get_level_progress(user_id):
    xp = get_user_xp(user_id)
    return {"level": get_user_level(user_id), "xp": xp, "current": xp % XP_PER_LEVEL, "needed": XP_PER_LEVEL}

async def add_xp(bot, user_id, amount, reason=None):
    if amount <= 0: return False
    user = get_user(user_id)
    if not user: return False
    old_xp = int(user.get("xp", 0) or 0)
    old_level = (old_xp // XP_PER_LEVEL) + 1
    new_xp = old_xp + amount
    new_level = (new_xp // XP_PER_LEVEL) + 1
    db_update("users", {"xp": new_xp}, {"id": user_id})
    if new_level > old_level:
        await notify_user(bot, user_id, f"🆙 Новый уровень!\n\n⭐ Уровень: {new_level}\n✨ XP: {new_xp}")
    return True

def increment_user_counter(user_id, field, amount=1):
    user = get_user(user_id)
    if not user: return False
    value = int(user.get(field, 0) or 0) + amount
    db_update("users", {field: value}, {"id": user_id})
    return True

def get_store_status(store):
    return (store.get("status", "open") or "open") if store else "open"

def store_is_open(store):
    return get_store_status(store) == "open"

def store_status_text(store):
    return "🟢 Открыт" if store_is_open(store) else "🔴 Закрыт"

def get_leaderboard(field, limit=10):
    # Администраторы не участвуют в публичном лидерборде.
    users = [u for u in db_select("users") if not is_admin(int(u.get("id", 0) or 0))]
    users.sort(key=lambda u: int(u.get(field, 0) or 0), reverse=True)
    return users[:limit]

def leaderboard_name(user):
    return str(user.get("first_name") or user.get("username") or user.get("id"))[:30]

async def show_leaderboard(update, context, category="xp"):
    query = update.callback_query
    configs = {
        "xp": ("⭐ Лидерборд по XP", "xp", "XP"),
        "purchases": ("🛒 Лидерборд по покупкам", "purchase_count", "покупок"),
        "sales": ("💰 Лидерборд по продажам", "sales_count", "продаж"),
    }
    title, field, unit = configs.get(category, configs["xp"])
    users = get_leaderboard(field)
    text = f"{title}\n\n"
    if not users: text += "Пока никого нет."
    else:
        for i, user in enumerate(users, 1):
            place = {1:"🥇",2:"🥈",3:"🥉"}.get(i, f"{i}.")
            text += f"{place} {leaderboard_name(user)} — {int(user.get(field,0) or 0)} {unit}\n"
    keyboard = [[InlineKeyboardButton("⭐ XP", callback_data="leaderboard_xp"), InlineKeyboardButton("🛒 Покупки", callback_data="leaderboard_purchases")], [InlineKeyboardButton("💰 Продажи", callback_data="leaderboard_sales")], [InlineKeyboardButton("⬅️ Назад", callback_data="profile")]]
    await query.edit_message_text(text[:4000], reply_markup=InlineKeyboardMarkup(keyboard))
    await safe_query_answer(query)

async def open_store(update, context, store_id):
    query = update.callback_query
    store = get_owned_store(query.from_user.id)
    if not store or store.get("id") != store_id:
        await safe_query_answer(query, "Только владелец может открыть магазин.", show_alert=True); return
    if store_is_open(store):
        await safe_query_answer(query, "Магазин уже открыт."); return
    db_update("stores", {"status":"open"}, {"id":store_id})
    queued = db_select("orders", filters_dict={"store_id":store_id,"status":"queued"})
    if queued:
        db_update("orders", {"status":"pending"}, {"store_id":store_id,"status":"queued"})
        members = get_store_member_ids(store_id)
        for order in queued:
            buyer_id = order.get("buyer_id") or order.get("user_id")
            await notify_user(context.bot, buyer_id, f"🟢 Магазин снова открыт!\n\n📦 Заказ #{order.get('id')} снова передан продавцам.")
            for member_id in members:
                await notify_user(context.bot, member_id, f"🛒 Появился отложенный заказ!\n\n📦 Заказ #{order.get('id')}\nТовар: {order.get('product_name') or 'Товар'}")
    await safe_query_answer(query, f"🟢 Магазин открыт! Заказов выпущено: {len(queued)}", show_alert=True)
    await my_store(update, context)

async def close_store(update, context, store_id):
    query = update.callback_query
    store = get_owned_store(query.from_user.id)
    if not store or store.get("id") != store_id:
        await safe_query_answer(query, "Только владелец может закрыть магазин.", show_alert=True); return
    if not store_is_open(store):
        await safe_query_answer(query, "Магазин уже закрыт."); return
    db_update("stores", {"status":"closed"}, {"id":store_id})
    await safe_query_answer(query, "🔴 Магазин закрыт. Новые заказы будут ждать открытия.", show_alert=True)
    await my_store(update, context)

def task_condition(user_id, task):
    name = str(task.get("task", "")).lower()
    if "посетить vexmart" in name:
        return bool(db_select("bot_visits", filters_dict={"user_id":user_id}, limit=1))
    if "открыть магазин" in name:
        return bool(db_select("stores", filters_dict={"owner_id":user_id}, limit=1))
    if "совершить покупку" in name:
        return bool(db_select("orders", filters_dict={"buyer_id":user_id,"status":"completed"}, limit=1))
    if "оставить отзыв" in name:
        return bool(db_select("product_reviews", filters_dict={"user_id":user_id}, limit=1))
    return False

async def claim_task(update, context, task_id):
    query = update.callback_query; user_id = query.from_user.id
    tasks = db_select("tasks", filters_dict={"id":task_id,"user_id":user_id}, limit=1)
    if not tasks:
        await safe_query_answer(query,"Задание не найдено.",show_alert=True); return
    task = tasks[0]
    if task.get("completed"):
        await safe_query_answer(query,"Задание уже выполнено.",show_alert=True); return
    if not task_condition(user_id, task):
        await safe_query_answer(query,"❌ Условие задания ещё не выполнено.",show_alert=True); return
    db_update("tasks", {"completed":True}, {"id":task_id})
    reward = int(task.get("reward",0) or 0)
    if reward: add_balance(user_id,reward)
    await add_xp(context.bot,user_id,XP_TASK,"task")
    await safe_query_answer(query,f"🎉 Задание выполнено! +{reward} ₽",show_alert=True)
    await show_tasks(update,context)


# ============================================================
# STORE HELPERS
# ============================================================

def get_store(store_id):
    stores = db_select(
        "stores",
        filters_dict={"id": store_id},
        limit=1,
    )

    return stores[0] if stores else None


def get_owned_store(user_id):
    stores = db_select(
        "stores",
        filters_dict={"owner_id": user_id},
        limit=1,
    )

    return stores[0] if stores else None


def get_store_seller_rows(store_id):
    return db_select(
        "store_sellers",
        filters_dict={"store_id": store_id},
    )


def get_store_member_ids(store_id):
    store = get_store(store_id)

    if not store:
        return []

    ids = []

    owner_id = store.get("owner_id")

    if owner_id:
        ids.append(owner_id)

    for row in get_store_seller_rows(store_id):
        user_id = row.get("user_id")

        if user_id and user_id not in ids:
            ids.append(user_id)

    return ids


def is_store_member(user_id, store_id):
    store = get_store(store_id)

    if not store:
        return False

    if store.get("owner_id") == user_id:
        return True

    rows = db_select(
        "store_sellers",
        filters_dict={
            "store_id": store_id,
            "user_id": user_id,
        },
        limit=1,
    )

    return bool(rows)


def get_user_stores(user_id):
    result = []

    owned = db_select(
        "stores",
        filters_dict={"owner_id": user_id},
    )

    for store in owned:
        if store["id"] not in [x["id"] for x in result]:
            result.append(store)

    seller_rows = db_select(
        "store_sellers",
        filters_dict={"user_id": user_id},
    )

    for row in seller_rows:
        store = get_store(row.get("store_id"))

        if store and store["id"] not in [
            x["id"] for x in result
        ]:
            result.append(store)

    return result


def get_user_store(user_id):
    stores = get_user_stores(user_id)
    return stores[0] if stores else None


# ============================================================
# PRODUCT HELPERS
# ============================================================

def get_product(product_id):
    products = db_select(
        "products",
        filters_dict={"id": product_id},
        limit=1,
    )

    return products[0] if products else None


def is_new_product(product):
    created_at = product.get("created_at")

    if not created_at:
        return False

    try:
        created = datetime.fromisoformat(
            str(created_at).replace("Z", "+00:00")
        )

        if created.tzinfo is None:
            created = created.replace(
                tzinfo=timezone.utc
            )

        return (
            datetime.now(timezone.utc) - created
            < timedelta(days=5)
        )

    except Exception:
        return False


def product_title(product):
    title = product.get(
        "name",
        "Без названия",
    )

    if is_new_product(product):
        title = f"🆕 {title}"

    return title



# ============================================================
# VEXMART 0.45 — CATEGORIES / SEARCH / MAP
# ============================================================

def get_categories():
    return db_select(
        "categories",
        order_by="id",
        ascending=True,
    )


def get_category(category_id):
    rows = db_select(
        "categories",
        filters_dict={"id": category_id},
        limit=1,
    )
    return rows[0] if rows else None


def get_product_category(product):
    category_id = product.get("category_id")
    if not category_id:
        return None
    return get_category(category_id)


def category_name(product):
    category = get_product_category(product)
    return category.get("name") if category else None


def _normalize_search_text(value):
    """Нормализует текст для поиска: регистр, ё/е и пунктуацию."""
    text = str(value or "").casefold().replace("ё", "е")
    text = re.sub(r"[^\wа-я0-9]+", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _parse_search_budget(text):
    """Извлекает максимальную цену из запроса."""
    patterns = (
        r"(?:до|<=|не\s+дороже|максимум|max)\s*(\d+)",
        r"(\d+)\s*(?:vxc\b|₽|руб(?:ль|ля|лей)?\b)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                pass
    return None


def product_search_score(product, query_words, query_text=""):
    """Оценивает совпадение товара с текстом запроса."""
    name = _normalize_search_text(product.get("name"))
    description = _normalize_search_text(product.get("description"))
    category = _normalize_search_text(category_name(product))
    normalized_query = _normalize_search_text(query_text)

    score = 0
    if normalized_query and normalized_query == name:
        score += 100
    elif normalized_query and normalized_query in name:
        score += 30

    for word in query_words:
        if word in name.split():
            score += 10
        elif word in name:
            score += 6
        elif word in category:
            score += 4
        elif word in description:
            score += 2

    return score


def smart_search_products(query_text):
    raw_text = str(query_text or "").strip()
    text = _normalize_search_text(raw_text)
    max_price = _parse_search_budget(raw_text)

    # Убираем из поисковых слов фрагмент бюджета и валюту.
    cleaned = re.sub(
        r"(?:до|<=|не\s+дороже|максимум|max)\s*\d+",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b\d+\s*(?:vxc|руб(?:ль|ля|лей)?)\b|₽",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )

    stop_words = {
        "мне", "надо", "нужен", "нужна", "нужно", "хочу",
        "что", "то", "чтобы", "какой", "какая", "какое",
        "какие", "товар", "товары", "купить", "есть",
        "до", "руб", "рублей", "рубля", "рубль", "р", "vxc",
    }
    words = [
        word for word in cleaned.split()
        if len(word) >= 2 and word not in stop_words
        and not word.isdigit()
    ]

    products = db_select("products")
    # Учёт остатков отключён: все товары остаются доступными для поиска.
    available_products = products

    print(
        f"[SMART SEARCH] query={raw_text!r}; "
        f"products={len(products)}; budget={max_price}; words={words}"
    )

    scored = []
    normalized_query = " ".join(words)
    for product in available_products:
        try:
            price = int(product.get("price", 0) or 0)
        except (TypeError, ValueError):
            price = 0

        # Бюджет — настоящий фильтр, а не просто бонус к рейтингу.
        if max_price is not None and price > max_price:
            continue

        if not words and max_price is None:
            continue

        score = product_search_score(
            product,
            words,
            normalized_query,
        )
        if words and score <= 0:
            continue

        scored.append((score, product))

    scored.sort(
        key=lambda item: (
            item[0],
            -int(item[1].get("price", 0) or 0),
        ),
        reverse=True,
    )

    print(f"[SMART SEARCH] matches={len(scored)}")
    return [product for _, product in scored[:20]]


def product_button_text(product):
    category = category_name(product)
    suffix = f" · {category}" if category else ""
    return (
        f"{product_title(product)} — "
        f"{product.get('price', 0)} ₽"
        f"{suffix}"
    )


async def show_categories(update, context):
    query = update.callback_query
    categories = get_categories()

    if not categories:
        await query.edit_message_text(
            "🗂 Категорий пока нет.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="back_menu",
                )]
            ]),
        )
        await safe_query_answer(query)
        return

    buttons = [
        [
            InlineKeyboardButton(
                f"🗂 {category.get('name')}",
                callback_data=f"category_{category.get('id')}",
            )
        ]
        for category in categories
    ]

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="back_menu",
        )
    ])

    await query.edit_message_text(
        "🗂 Категории товаров\n\nВыбери категорию:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    await safe_query_answer(query)


async def show_category_products(update, context, category_id):
    query = update.callback_query
    category = get_category(category_id)

    if not category:
        await safe_query_answer(query, "Категория не найдена.")
        return

    products = db_select(
        "products",
        filters_dict={"category_id": category_id},
    )
    if not products:
        await query.edit_message_text(
            f"🗂 {category.get('name')}\n\n"
            "В этой категории пока нет товаров.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "⬅️ К категориям",
                    callback_data="categories",
                )]
            ]),
        )
        await safe_query_answer(query)
        return

    buttons = [
        [
            InlineKeyboardButton(
                product_button_text(product),
                callback_data=f"product_{product.get('id')}",
            )
        ]
        for product in products
    ]

    buttons.append([
        InlineKeyboardButton(
            "⬅️ К категориям",
            callback_data="categories",
        )
    ])

    await query.edit_message_text(
        f"🗂 {category.get('name')}\n\n"
        "Товары в этой категории:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    await safe_query_answer(query)


async def smart_search_start(update, context):
    query = update.callback_query
    context.user_data["awaiting_smart_search"] = True

    await query.edit_message_text(
        "🧠 Умный поиск\n\n"
        "Напиши, что тебе нужно.\n"
        "Например:\n"
        "«мне надо что-то деревянное до 100 VXC»\n\n"
        "Я попробую найти подходящие товары.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back_menu",
            )]
        ]),
    )
    await safe_query_answer(query)


async def process_smart_search(update, context):
    if not context.user_data.get("awaiting_smart_search"):
        return False

    context.user_data.pop("awaiting_smart_search", None)

    query_text = update.message.text.strip()
    products = smart_search_products(query_text)

    if not products:
        await update.message.reply_text(
            "🔎 Подходящих товаров не нашёл.\n\n"
            "Попробуй изменить описание или указать другой бюджет.",
            reply_markup=buyer_menu(update.effective_user.id),
        )
        return True

    buttons = [
        [
            InlineKeyboardButton(
                product_button_text(product),
                callback_data=f"product_{product.get('id')}",
            )
        ]
        for product in products
    ]

    await update.message.reply_text(
        "🧠 Результаты умного поиска:\n\n"
        f"Запрос: «{query_text}»\n\n"
        "Найденные товары:",
        reply_markup=InlineKeyboardMarkup(
            buttons + [[InlineKeyboardButton(
                "⬅️ В меню",
                callback_data="back_menu",
            )]]
        ),
    )
    return True


async def show_store_map(update, context):
    query = update.callback_query
    stores = db_select("stores")

    stores_with_location = [
        store for store in stores
        if store.get("latitude") is not None
        and store.get("longitude") is not None
    ]

    if not stores_with_location:
        await query.edit_message_text(
            "🗺 Карта магазинов\n\n"
            "Пока ни у одного магазина не указаны координаты.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="back_menu",
                )]
            ]),
        )
        await safe_query_answer(query)
        return

    buttons = []
    for store in stores_with_location:
        lat = float(store["latitude"])
        lon = float(store["longitude"])

        buttons.append([
            InlineKeyboardButton(
                f"📍 {store.get('name', 'Магазин')}",
                url=f"https://yandex.ru/maps/?pt={lon},{lat}&z=16&l=map",
            )
        ])

    await query.edit_message_text(
        "🗺 Магазины на карте\n\n"
        "Нажми на магазин, чтобы открыть его расположение:",
        reply_markup=InlineKeyboardMarkup(
            buttons + [[InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back_menu",
            )]]
        ),
    )
    await safe_query_answer(query)


async def choose_product_category(update, context, product_id):
    query = update.callback_query
    product = get_product(product_id)

    if not product:
        await safe_query_answer(query, "Товар не найден.")
        return

    if not is_store_member(
        query.from_user.id,
        product.get("store_id"),
    ):
        await safe_query_answer(query, "Нет доступа.", show_alert=True)
        return

    categories = get_categories()
    buttons = []

    for category in categories:
        buttons.append([
            InlineKeyboardButton(
                f"🗂 {category.get('name')}",
                callback_data=(
                    f"set_product_category_"
                    f"{product_id}_{category.get('id')}"
                ),
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🚫 Без категории",
            callback_data=f"set_product_category_{product_id}_0",
        )
    ])
    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data=f"edit_product_{product_id}",
        )
    ])

    await query.edit_message_text(
        "🗂 Выбор категории\n\n"
        f"📦 {product.get('name')}\n\n"
        "Выбери категорию:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    await safe_query_answer(query)


async def set_product_category(update, context, product_id, category_id):
    query = update.callback_query
    product = get_product(product_id)

    if not product:
        await safe_query_answer(query, "Товар не найден.")
        return

    if not is_store_member(
        query.from_user.id,
        product.get("store_id"),
    ):
        await safe_query_answer(query, "Нет доступа.", show_alert=True)
        return

    if category_id == 0:
        db_update(
            "products",
            {"category_id": None},
            {"id": product_id},
        )
        message = "🚫 Категория товара сброшена."
    else:
        category = get_category(category_id)
        if not category:
            await safe_query_answer(query, "Категория не найдена.", show_alert=True)
            return

        db_update(
            "products",
            {"category_id": category_id},
            {"id": product_id},
        )
        message = f"🗂 Категория: {category.get('name')}"

    await safe_query_answer(query, message, show_alert=True)
    await edit_product_menu(update, context, product_id)



# ============================================================
# NOTIFICATIONS
# ============================================================

async def notify_user(
    bot,
    user_id,
    text,
    reply_markup=None,
):
    try:
        await bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=reply_markup,
        )

    except Exception as e:
        print(
            f"[NOTIFY ERROR] user={user_id}: {e}"
        )


# ============================================================
# MENUS
# ============================================================

def buyer_menu(user_id=None):
    buttons = [
        [
            InlineKeyboardButton(
                "🏪 Магазины",
                callback_data="stores",
            ),
            InlineKeyboardButton(
                "🛒 Корзина",
                callback_data="cart",
            ),
        ],
        [
            InlineKeyboardButton(
                "📦 Мои заказы",
                callback_data="buyer_orders",
            ),
            InlineKeyboardButton(
                "📋 Задания",
                callback_data="tasks",
            ),
        ],
        [
            InlineKeyboardButton(
                "🎁 Ежедневный бонус",
                callback_data="daily_bonus",
            ),
        ],
        [
            InlineKeyboardButton(
                "🧠 Умный поиск",
                callback_data="smart_search",
            ),
            InlineKeyboardButton(
                "🗂 Категории",
                callback_data="categories",
            ),
        ],
        [
            InlineKeyboardButton(
                "📍 Магазины рядом",
                callback_data="nearby_stores",
            ),
            InlineKeyboardButton(
                "🗺 Карта магазинов",
                callback_data="store_map",
            ),
        ],
        [
            InlineKeyboardButton(
                "👤 Профиль",
                callback_data="profile",
            ),
        ],
        [
            InlineKeyboardButton(
                "💼 Стать продавцом",
                callback_data="become_seller",
            ),
        ],
    ]

    if is_admin(user_id):
        buttons.append(
            [
                InlineKeyboardButton(
                    "🛠 Админ-панель",
                    callback_data="admin",
                )
            ]
        )

    return InlineKeyboardMarkup(buttons)


def seller_menu(user_id):
    buttons = [
        [
            InlineKeyboardButton(
                "🏪 Мой магазин",
                callback_data="my_store",
            ),
        ],
        [
            InlineKeyboardButton(
                "➕ Добавить продавца",
                callback_data="add_seller",
            ),
            InlineKeyboardButton(
                "📨 Запросы",
                callback_data="seller_requests",
            ),
        ],
        [
            InlineKeyboardButton(
                "📦 Мои товары",
                callback_data="my_products",
            ),
            InlineKeyboardButton(
                "🛒 Заказы",
                callback_data="seller_orders",
            ),
        ],
        [
            InlineKeyboardButton(
                "👤 Профиль",
                callback_data="profile",
            ),
        ],
        [
            InlineKeyboardButton(
                "🛍 Стать покупателем",
                callback_data="switch_buyer",
            ),
        ],
    ]

    if is_admin(user_id):
        buttons.append(
            [
                InlineKeyboardButton(
                    "🛠 Админ-панель",
                    callback_data="admin",
                )
            ]
        )

    return InlineKeyboardMarkup(buttons)


# ============================================================
# ACHIEVEMENTS
# ============================================================

def get_achievement(code):
    rows = db_select(
        "achievements",
        filters_dict={"code": code},
        limit=1,
    )

    return rows[0] if rows else None


def has_achievement(user_id, achievement_id):
    rows = db_select(
        "user_achievements",
        filters_dict={
            "user_id": user_id,
            "achievement_id": achievement_id,
        },
        limit=1,
    )

    return bool(rows)


async def award_achievement(
    bot,
    user_id,
    code,
):
    achievement = get_achievement(code)

    if not achievement:
        return False

    achievement_id = achievement["id"]

    if has_achievement(
        user_id,
        achievement_id,
    ):
        return False

    created = db_insert(
        "user_achievements",
        {
            "user_id": user_id,
            "achievement_id": achievement_id,
        },
    )

    if not created:
        return False

    reward = achievement.get("reward", 0) or 0

    if reward:
        add_balance(
            user_id,
            reward,
        )

    await notify_user(
        bot,
        user_id,
        (
            "🏆 Новое достижение!\n\n"
            f"{achievement.get('icon', '🏆')} "
            f"{achievement.get('name')}\n"
            f"{achievement.get('description')}\n\n"
            f"💰 Награда: +{reward} ₽"
        ),
    )

    return True


async def check_basic_achievements(
    bot,
    user_id,
):
    visits = db_select(
        "bot_visits",
        filters_dict={"user_id": user_id},
        limit=1,
    )

    if visits:
        await award_achievement(
            bot,
            user_id,
            "first_visit",
        )

    stores = db_select(
        "stores",
        filters_dict={"owner_id": user_id},
        limit=1,
    )

    if stores:
        await award_achievement(
            bot,
            user_id,
            "first_store",
        )

    user_stores = get_user_stores(user_id)

    if user_stores:
        store_ids = {
            store["id"]
            for store in user_stores
        }

        products = db_select("products")

        if any(
            p.get("store_id") in store_ids
            for p in products
        ):
            await award_achievement(
                bot,
                user_id,
                "first_product",
            )

    orders = db_select(
        "orders",
        filters_dict={"buyer_id": user_id},
        limit=1,
    )

    if orders:
        await award_achievement(
            bot,
            user_id,
            "first_purchase",
        )

    ratings = db_select(
        "store_ratings",
        filters_dict={"user_id": user_id},
        limit=1,
    )

    if ratings:
        await award_achievement(
            bot,
            user_id,
            "first_rating",
        )

    reviews = db_select(
        "product_reviews",
        filters_dict={"user_id": user_id},
        limit=1,
    )

    if reviews:
        await award_achievement(
            bot,
            user_id,
            "first_review",
        )


async def show_achievements(
    update,
    context,
):
    query = update.callback_query
    user_id = query.from_user.id

    achievements = db_select(
        "achievements",
        order_by="id",
        ascending=True,
    )

    earned = db_select(
        "user_achievements",
        filters_dict={"user_id": user_id},
    )

    earned_ids = {
        row.get("achievement_id")
        for row in earned
    }

    text = "🏆 Достижения\n\n"

    for achievement in achievements:
        if achievement["id"] in earned_ids:
            mark = "✅"
        else:
            mark = "🔒"

        text += (
            f"{mark} "
            f"{achievement.get('icon', '🏆')} "
            f"{achievement.get('name')}\n"
            f"{achievement.get('description')}\n"
            f"💰 Награда: "
            f"{achievement.get('reward', 0)} ₽\n\n"
        )

    keyboard = [
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="profile",
            )
        ]
    ]

    await query.edit_message_text(
        text[:4000],
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    await safe_query_answer(query)


# ============================================================
# DAILY BONUS
# ============================================================

def get_daily_bonus(user_id):
    rows = db_select(
        "daily_bonuses",
        filters_dict={"user_id": user_id},
        limit=1,
    )

    return rows[0] if rows else None


async def claim_daily_bonus(
    update,
    context,
):
    query = update.callback_query
    user_id = query.from_user.id

    now = datetime.now(timezone.utc)

    bonus = get_daily_bonus(user_id)

    if not bonus:
        created = db_insert(
            "daily_bonuses",
            {
                "user_id": user_id,
                "last_claimed_at": now.isoformat(),
                "streak": 1,
                "total_claims": 1,
            },
        )

        if not created:
            await safe_query_answer(
                query,
                "❌ Не удалось получить бонус.",
            )
            return

        reward = 20

    else:
        last = bonus.get("last_claimed_at")

        if last:
            try:
                last_dt = datetime.fromisoformat(
                    str(last).replace(
                        "Z",
                        "+00:00",
                    )
                )

                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(
                        tzinfo=timezone.utc
                    )

                difference = now - last_dt

                if difference < timedelta(days=1):
                    remaining = timedelta(days=1) - difference

                    hours = int(
                        remaining.total_seconds()
                        // 3600
                    )

                    minutes = int(
                        (
                            remaining.total_seconds()
                            % 3600
                        ) // 60
                    )

                    await safe_query_answer(
                        query,
                        (
                            "🎁 Бонус уже получен!\n"
                            f"Следующий через "
                            f"{hours} ч. {minutes} мин."
                        ),
                        show_alert=True,
                    )
                    return

                streak = bonus.get(
                    "streak",
                    0,
                ) or 0

                if difference <= timedelta(days=2):
                    streak += 1
                else:
                    streak = 1

            except Exception:
                streak = 1
        else:
            streak = 1

        reward = 20 + min(
            streak * 5,
            100,
        )

        db_update(
            "daily_bonuses",
            {
                "last_claimed_at":
                    now.isoformat(),
                "streak": streak,
                "total_claims":
                    (bonus.get("total_claims", 0) or 0)
                    + 1,
                "updated_at":
                    now.isoformat(),
            },
            {"user_id": user_id},
        )

    add_balance(
        user_id,
        reward,
    )

    await add_xp(
        context.bot,
        user_id,
        XP_DAILY_BONUS,
        "daily_bonus",
    )

    bonus = get_daily_bonus(user_id)

    streak = (
        bonus.get("streak", 1)
        if bonus
        else 1
    )

    if streak >= 7:
        await award_achievement(
            context.bot,
            user_id,
            "daily_7",
        )

    if streak >= 30:
        await award_achievement(
            context.bot,
            user_id,
            "daily_30",
        )

    await safe_query_answer(
        query,
        f"🎁 Получено +{reward} ₽!",
        show_alert=True,
    )

    await query.edit_message_text(
        "🎁 Ежедневный бонус получен!\n\n"
        f"💰 Награда: +{reward} ₽\n"
        f"🔥 Серия: {streak} дн.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "👤 Профиль",
                        callback_data="profile",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="back_menu",
                    )
                ],
            ]
        ),
    )


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if context.args and context.args[0].startswith("auth_"):
        handled = await confirm_telegram_auth_start(
            update, context, context.args[0]
        )
        if handled:
            return

    ensure_user(user)

    db_insert(
        "bot_visits",
        {
            "user_id": user.id,
        },
    )

    await check_basic_achievements(
        context.bot,
        user.id,
    )

    await update.message.reply_text(
        "🛍 Добро пожаловать в VexMart!\n\n"
        f"Версия: {VERSION}",
        reply_markup=buyer_menu(
            user.id
        ),
    )


# ============================================================
# PROFILE
# ============================================================

async def show_profile(
    update,
    context,
):
    query = update.callback_query

    user_id = query.from_user.id
    user = get_user(user_id)

    if not user:
        await safe_query_answer(
            query,
            "Профиль не найден.",
        )
        return

    role = user.get(
        "role",
        "buyer",
    )

    role_text = (
        "🛍 Покупатель"
        if role == "buyer"
        else "💼 Продавец"
    )

    balance = user.get(
        "balance",
        0,
    ) or 0

    bonus = get_daily_bonus(
        user_id
    )

    streak = (
        bonus.get("streak", 0)
        if bonus
        else 0
    )

    earned = db_select(
        "user_achievements",
        filters_dict={
            "user_id": user_id,
        },
    )

    text = (
        "👤 Профиль\n\n"
        f"Имя: "
        f"{user.get('first_name') or '—'}\n"
        f"Username: "
        f"@{user.get('username') or '—'}\n"
        f"ID: {user_id}\n"
        f"Роль: {role_text}\n"
        f"💰 Баланс: {balance} ₽\n\n"
        f"⭐ Уровень: {get_user_level(user_id)}\n"
        f"✨ XP: {user.get('xp', 0) or 0}\n"
        f"🛒 Покупок: {user.get('purchase_count', 0) or 0}\n"
        f"💰 Продаж: {user.get('sales_count', 0) or 0}\n\n"
        f"🔥 Серия бонуса: {streak} дн.\n"
        f"🏆 Достижений: {len(earned)}"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "🎁 Ежедневный бонус",
                callback_data="daily_bonus",
            )
        ],
        [
            InlineKeyboardButton(
                "🏆 Достижения",
                callback_data="achievements",
            ),
            InlineKeyboardButton(
                "📊 Лидерборды",
                callback_data="leaderboard_xp",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back_menu",
            )
        ],
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    await safe_query_answer(query)


# ============================================================
# STORES
# ============================================================

def get_store_rating(store_id):
    ratings = db_select(
        "store_ratings",
        filters_dict={"store_id": store_id},
    )

    if not ratings:
        return None, 0

    total = sum(
        r.get("rating", 0)
        for r in ratings
    )

    return total / len(ratings), len(ratings)


async def show_stores(
    update,
    context,
):
    query = update.callback_query

    stores = db_select(
        "stores",
        order_by="created_at",
        ascending=False,
    )

    if not stores:
        await query.edit_message_text(
            "🏪 Магазинов пока нет.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="back_menu",
                    )
                ]]
            ),
        )

        await safe_query_answer(query)
        return

    buttons = []

    for store in stores:
        rating, count = get_store_rating(
            store["id"]
        )

        rating_text = ""

        if rating is not None:
            rating_text = (
                f" ⭐ {rating:.1f}"
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    f"🏪 {store.get('name', 'Магазин')}"
                    f" {store_status_text(store)}"
                    f"{rating_text}",
                    callback_data=(
                        f"store_{store['id']}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back_menu",
            )
        ]
    )

    await query.edit_message_text(
        "🏪 Магазины:",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    await safe_query_answer(query)


async def show_store(
    update,
    context,
    store_id,
):
    query = update.callback_query

    store = get_store(store_id)

    if not store:
        await safe_query_answer(
            query,
            "Магазин не найден.",
        )
        return

    user_id = query.from_user.id

    products = db_select(
        "products",
        filters_dict={
            "store_id": store_id,
        },
    )

    member = is_store_member(
        user_id,
        store_id,
    )

    seller_rows = get_store_seller_rows(
        store_id
    )

    seller_count = 1 + len(seller_rows)

    owner = get_user(
        store.get("owner_id")
    )

    seller_names = []

    if owner:
        seller_names.append(
            owner.get("first_name")
            or owner.get("username")
            or "Продавец"
        )

    for row in seller_rows:
        seller = get_user(
            row.get("user_id")
        )

        if seller:
            seller_names.append(
                seller.get("first_name")
                or seller.get("username")
                or "Продавец"
            )

    rating, rating_count = get_store_rating(
        store_id
    )

    rating_text = (
        f"⭐ Рейтинг: {rating:.1f}/5 "
        f"({rating_count})"
        if rating is not None
        else "⭐ Рейтинг: пока нет оценок"
    )

    text = (
        f"🏪 {store.get('name', 'Магазин')}\n\n"
        f"{store.get('description') or 'Описание отсутствует.'}\n\n"
        f"{rating_text}\n"
        f"Статус: {store_status_text(store)}\n"
        f"👥 Продавцов: {seller_count}\n"
        f"👤 {', '.join(seller_names) or '—'}"
    )

    if store.get("address"):
        text += (
            f"\n📍 {store['address']}"
        )

    buttons = []

    for product in products:
        buttons.append(
            [
                InlineKeyboardButton(
                    product_button_text(product),
                    callback_data=(
                        f"product_{product['id']}"
                    ),
                )
            ]
        )

    if not products:
        text += "\n\n📦 Товаров пока нет."

    buttons.append(
        [
            InlineKeyboardButton(
                "⭐ Оценить магазин",
                callback_data=(
                    f"rate_store_{store_id}"
                ),
            )
        ]
    )

    if not member:
        buttons.append(
            [
                InlineKeyboardButton(
                    "🙋 Запроситься в продавцы",
                    callback_data=(
                        f"join_store_{store_id}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "⬅️ К магазинам",
                callback_data="stores",
            )
        ]
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    await safe_query_answer(query)


# ============================================================
# STORE RATING
# ============================================================

async def rate_store_menu(
    update,
    context,
    store_id,
):
    query = update.callback_query

    store = get_store(store_id)

    if not store:
        await safe_query_answer(
            query,
            "Магазин не найден.",
        )
        return

    keyboard = []

    row = []

    for rating in range(1, 6):
        row.append(
            InlineKeyboardButton(
                "⭐" * rating,
                callback_data=(
                    f"set_store_rating_"
                    f"{store_id}_{rating}"
                ),
            )
        )

    keyboard.append(row)

    keyboard.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data=(
                    f"store_{store_id}"
                ),
            )
        ]
    )

    await query.edit_message_text(
        f"⭐ Оцени магазин "
        f"«{store.get('name')}»\n\n"
        "Выбери оценку от 1 до 5:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    await safe_query_answer(query)


async def set_store_rating(
    update,
    context,
    store_id,
    rating,
):
    query = update.callback_query
    user_id = query.from_user.id

    store = get_store(store_id)

    if not store:
        await safe_query_answer(
            query,
            "Магазин не найден.",
        )
        return

    existing = db_select(
        "store_ratings",
        filters_dict={
            "store_id": store_id,
            "user_id": user_id,
        },
        limit=1,
    )

    if existing:
        db_update(
            "store_ratings",
            {
                "rating": rating,
                "updated_at":
                    datetime.now(
                        timezone.utc
                    ).isoformat(),
            },
            {
                "id": existing[0]["id"],
            },
        )

        message = "⭐ Оценка магазина обновлена!"

    else:
        created = db_insert(
            "store_ratings",
            {
                "store_id": store_id,
                "user_id": user_id,
                "rating": rating,
            },
        )

        if not created:
            await safe_query_answer(
                query,
                "❌ Не удалось сохранить оценку.",
            )
            return

        message = "⭐ Магазин оценён!"

    await award_achievement(
        context.bot,
        user_id,
        "first_rating",
    )

    await safe_query_answer(
        query,
        message,
    )

    await show_store(
        update,
        context,
        store_id,
    )


# ============================================================
# JOIN STORE
# ============================================================

async def join_store(
    update,
    context,
    store_id,
):
    query = update.callback_query

    user_id = query.from_user.id
    store = get_store(store_id)

    if not store:
        await safe_query_answer(
            query,
            "Магазин не найден.",
        )
        return

    if is_store_member(
        user_id,
        store_id,
    ):
        await safe_query_answer(
            query,
            "Ты уже продавец этого магазина.",
        )
        return

    existing = db_select(
        "store_seller_requests",
        filters_dict={
            "store_id": store_id,
            "sender_id": user_id,
            "status": "pending",
        },
    )

    if existing:
        await safe_query_answer(
            query,
            "Запрос уже отправлен.",
        )
        return

    request = db_insert(
        "store_seller_requests",
        {
            "store_id": store_id,
            "sender_id": user_id,
            "receiver_id":
                store.get("owner_id"),
            "status": "pending",
        },
    )

    if not request:
        await safe_query_answer(
            query,
            "Не удалось отправить запрос.",
        )
        return

    request_id = request[0]["id"]

    buttons = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "✅ Одобрить",
                callback_data=(
                    f"approve_request_{request_id}"
                ),
            ),
            InlineKeyboardButton(
                "❌ Отклонить",
                callback_data=(
                    f"reject_request_{request_id}"
                ),
            ),
        ]]
    )

    await notify_user(
        context.bot,
        store.get("owner_id"),
        (
            "📨 Новый запрос в магазин!\n\n"
            f"🏪 Магазин: {store.get('name')}\n"
            f"👤 Пользователь: "
            f"{query.from_user.first_name or 'Без имени'}\n"
            f"🆔 ID: {user_id}"
        ),
        buttons,
    )

    await safe_query_answer(
        query,
        "Запрос отправлен владельцу магазина!",
    )


# ============================================================
# SELLER REQUESTS
# ============================================================

async def show_seller_requests(
    update,
    context,
):
    query = update.callback_query

    user_id = query.from_user.id

    stores = get_user_stores(user_id)

    if not stores:
        await safe_query_answer(
            query,
            "У тебя нет магазина.",
        )
        return

    store_ids = [
        store["id"]
        for store in stores
    ]

    requests = db_select(
        "store_seller_requests"
    )

    requests = [
        r
        for r in requests
        if (
            r.get("store_id") in store_ids
            and r.get("status") == "pending"
            and r.get("receiver_id") == user_id
        )
    ]

    if not requests:
        await query.edit_message_text(
            "📨 Новых запросов нет.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="seller_menu",
                    )
                ]]
            ),
        )

        await safe_query_answer(query)
        return

    text = "📨 Запросы в магазин:\n\n"
    buttons = []

    for request in requests:
        sender = get_user(
            request.get("sender_id")
        )

        store = get_store(
            request.get("store_id")
        )

        sender_name = (
            sender.get("first_name")
            if sender
            else "Пользователь"
        )

        store_name = (
            store.get("name")
            if store
            else "Магазин"
        )

        text += (
            f"👤 {sender_name}\n"
            f"🏪 {store_name}\n\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    f"👤 {sender_name}",
                    callback_data=(
                        f"request_info_{request['id']}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="seller_menu",
            )
        ]
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    await safe_query_answer(query)


# ============================================================
# APPROVE / REJECT REQUEST
# ============================================================

async def approve_request(
    update,
    context,
    request_id,
):
    query = update.callback_query
    user_id = query.from_user.id

    requests = db_select(
        "store_seller_requests",
        filters_dict={"id": request_id},
        limit=1,
    )

    if not requests:
        await safe_query_answer(
            query,
            "Запрос не найден.",
        )
        return

    request = requests[0]

    if request.get("status") != "pending":
        await safe_query_answer(
            query,
            "Этот запрос уже обработан.",
        )
        return

    if request.get("receiver_id") != user_id:
        await safe_query_answer(
            query,
            "Одобрить запрос может только владелец.",
        )
        return

    store_id = request.get("store_id")

    store = get_store(store_id)

    if not store or store.get("owner_id") != user_id:
        await safe_query_answer(
            query,
            "Только владелец может одобрить запрос.",
        )
        return

    requester_id = request.get(
        "sender_id"
    )

    if not is_store_member(
        requester_id,
        store_id,
    ):
        db_insert(
            "store_sellers",
            {
                "store_id": store_id,
                "user_id": requester_id,
            },
        )

    db_update(
        "store_seller_requests",
        {"status": "accepted"},
        {"id": request_id},
    )

    set_role(
        requester_id,
        "seller",
    )

    await query.edit_message_text(
        "✅ Запрос одобрен.\n\n"
        "Пользователь теперь продавец этого магазина."
    )

    await safe_query_answer(query)

    await notify_user(
        context.bot,
        requester_id,
        (
            "🎉 Твой запрос одобрен!\n\n"
            f"🏪 Теперь ты продавец магазина "
            f"«{store.get('name')}»."
        ),
    )


async def reject_request(
    update,
    context,
    request_id,
):
    query = update.callback_query
    user_id = query.from_user.id

    requests = db_select(
        "store_seller_requests",
        filters_dict={"id": request_id},
        limit=1,
    )

    if not requests:
        await safe_query_answer(
            query,
            "Запрос не найден.",
        )
        return

    request = requests[0]

    if request.get("status") != "pending":
        await safe_query_answer(
            query,
            "Этот запрос уже обработан.",
        )
        return

    if request.get("receiver_id") != user_id:
        await safe_query_answer(
            query,
            "Отклонить запрос может только владелец.",
        )
        return

    store_id = request.get("store_id")
    store = get_store(store_id)

    if not store or store.get("owner_id") != user_id:
        await safe_query_answer(
            query,
            "Нет доступа.",
        )
        return

    requester_id = request.get(
        "sender_id"
    )

    db_update(
        "store_seller_requests",
        {"status": "rejected"},
        {"id": request_id},
    )

    await query.edit_message_text(
        "❌ Запрос отклонён."
    )

    await safe_query_answer(query)

    await notify_user(
        context.bot,
        requester_id,
        (
            "❌ Твой запрос в магазин отклонён.\n\n"
            f"🏪 {store.get('name')}"
        ),
    )


# ============================================================
# ADD SELLER
# ============================================================

async def add_seller(
    update,
    context,
):
    query = update.callback_query

    user_id = query.from_user.id

    store = get_owned_store(user_id)

    if not store:
        await safe_query_answer(
            query,
            "Добавлять продавцов может владелец магазина.",
        )
        return

    context.user_data[
        "awaiting_seller_username"
    ] = True

    await query.edit_message_text(
        "➕ Добавить продавца\n\n"
        "Отправь username друга в Telegram.\n"
        "Например: @username\n\n"
        "Друг должен сначала открыть VexMart "
        "и нажать /start."
    )

    await safe_query_answer(query)


async def process_seller_username(
    update,
    context,
):
    if not context.user_data.get(
        "awaiting_seller_username"
    ):
        return False

    context.user_data[
        "awaiting_seller_username"
    ] = False

    username = update.message.text.strip()

    if username.startswith("@"):
        username = username[1:]

    if not username:
        await update.message.reply_text(
            "❌ Username пустой."
        )
        return True

    current_user_id = (
        update.effective_user.id
    )

    store = get_owned_store(
        current_user_id
    )

    if not store:
        await update.message.reply_text(
            "❌ Только владелец магазина "
            "может добавлять продавцов."
        )
        return True

    users = db_select(
        "users",
        filters_dict={
            "username": username,
        },
        limit=1,
    )

    if not users:
        await update.message.reply_text(
            "❌ Пользователь не найден в VexMart."
        )
        return True

    friend = users[0]
    friend_id = friend["id"]

    if friend_id == current_user_id:
        await update.message.reply_text(
            "😄 Нельзя добавить самого себя."
        )
        return True

    if is_store_member(
        friend_id,
        store["id"],
    ):
        await update.message.reply_text(
            "ℹ️ Этот пользователь уже продавец."
        )
        return True

    existing = db_select(
        "store_seller_requests",
        filters_dict={
            "store_id": store["id"],
            "sender_id": current_user_id,
            "receiver_id": friend_id,
            "status": "pending",
        },
    )

    if existing:
        await update.message.reply_text(
            "📨 Приглашение уже отправлено."
        )
        return True

    created = db_insert(
        "store_seller_requests",
        {
            "store_id": store["id"],
            "sender_id": current_user_id,
            "receiver_id": friend_id,
            "status": "pending",
        },
    )

    if not created:
        await update.message.reply_text(
            "❌ Не удалось создать приглашение."
        )
        return True

    request_id = created[0]["id"]

    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "✅ Принять",
                callback_data=(
                    f"approve_invite_{request_id}"
                ),
            ),
            InlineKeyboardButton(
                "❌ Отклонить",
                callback_data=(
                    f"reject_invite_{request_id}"
                ),
            ),
        ]]
    )

    await notify_user(
        context.bot,
        friend_id,
        (
            "📨 Тебя приглашают стать продавцом!\n\n"
            f"🏪 Магазин: {store.get('name')}\n"
            f"👤 Пригласил: "
            f"{update.effective_user.first_name or 'Продавец'}"
        ),
        keyboard,
    )

    await update.message.reply_text(
        "✅ Приглашение отправлено другу!"
    )

    return True


# ============================================================
# INVITE APPROVAL
# ============================================================

async def approve_invite(
    update,
    context,
    request_id,
):
    query = update.callback_query
    user_id = query.from_user.id

    requests = db_select(
        "store_seller_requests",
        filters_dict={"id": request_id},
        limit=1,
    )

    if not requests:
        await safe_query_answer(
            query,
            "Приглашение не найдено.",
        )
        return

    request = requests[0]

    if request.get("receiver_id") != user_id:
        await safe_query_answer(
            query,
            "Это приглашение не тебе.",
        )
        return

    if request.get("status") != "pending":
        await safe_query_answer(
            query,
            "Приглашение уже обработано.",
        )
        return

    store_id = request["store_id"]

    if not is_store_member(
        user_id,
        store_id,
    ):
        db_insert(
            "store_sellers",
            {
                "store_id": store_id,
                "user_id": user_id,
            },
        )

    db_update(
        "store_seller_requests",
        {"status": "accepted"},
        {"id": request_id},
    )

    set_role(
        user_id,
        "seller",
    )

    store = get_store(store_id)

    await query.edit_message_text(
        "🎉 Ты принял приглашение!\n\n"
        f"Теперь ты продавец магазина "
        f"«{store.get('name') if store else 'Магазин'}»."
    )

    await safe_query_answer(query)


async def reject_invite(
    update,
    context,
    request_id,
):
    query = update.callback_query
    user_id = query.from_user.id

    requests = db_select(
        "store_seller_requests",
        filters_dict={"id": request_id},
        limit=1,
    )

    if not requests:
        await safe_query_answer(
            query,
            "Приглашение не найдено.",
        )
        return

    request = requests[0]

    if request.get("receiver_id") != user_id:
        await safe_query_answer(
            query,
            "Это приглашение не для тебя.",
        )
        return

    if request.get("status") != "pending":
        await safe_query_answer(
            query,
            "Приглашение уже обработано.",
        )
        return

    db_update(
        "store_seller_requests",
        {"status": "rejected"},
        {"id": request_id},
    )

    await query.edit_message_text(
        "❌ Ты отклонил приглашение."
    )

    await safe_query_answer(query)


# ============================================================
# STORE EDIT
# ============================================================

async def edit_store_menu(
    update,
    context,
):
    query = update.callback_query
    user_id = query.from_user.id

    store = get_owned_store(user_id)

    if not store:
        await safe_query_answer(
            query,
            "У тебя нет собственного магазина.",
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "🏪 Название",
                callback_data=(
                    f"edit_store_field_{store['id']}_name"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "📝 Описание",
                callback_data=(
                    f"edit_store_field_{store['id']}_description"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "📍 Адрес",
                callback_data=(
                    f"edit_store_field_{store['id']}_address"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="my_store",
            )
        ],
    ]

    await query.edit_message_text(
        "✏️ Редактирование магазина\n\n"
        f"🏪 {store.get('name')}\n"
        f"📝 {store.get('description') or '—'}\n"
        f"📍 {store.get('address') or '—'}\n\n"
        "Что изменить?",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    await safe_query_answer(query)


async def edit_store_field(
    update,
    context,
    store_id,
    field,
):
    query = update.callback_query

    user_id = query.from_user.id

    store = get_owned_store(user_id)

    if not store or store["id"] != store_id:
        await safe_query_answer(
            query,
            "Только владелец может редактировать магазин.",
        )
        return

    context.user_data[
        "editing_store"
    ] = {
        "store_id": store_id,
        "field": field,
    }

    names = {
        "name": "название",
        "description": "описание",
        "address": "адрес",
    }

    await query.edit_message_text(
        f"✏️ Введи новое {names.get(field, field)}:"
    )

    await safe_query_answer(query)


async def process_store_edit(
    update,
    context,
):
    data = context.user_data.get(
        "editing_store"
    )

    if not data:
        return False

    context.user_data.pop(
        "editing_store",
        None,
    )

    value = update.message.text.strip()

    if not value:
        await update.message.reply_text(
            "❌ Значение не может быть пустым."
        )
        return True

    store_id = data["store_id"]
    field = data["field"]

    store = get_owned_store(
        update.effective_user.id
    )

    if not store or store["id"] != store_id:
        await update.message.reply_text(
            "❌ Нет доступа."
        )
        return True

    db_update(
        "stores",
        {field: value},
        {"id": store_id},
    )

    await update.message.reply_text(
        "✅ Магазин обновлён!",
        reply_markup=seller_menu(
            update.effective_user.id
        ),
    )

    return True


# ============================================================
# STORE DELETE
# ============================================================

async def delete_store_confirm(
    update,
    context,
):
    query = update.callback_query

    store = get_owned_store(
        query.from_user.id
    )

    if not store:
        await safe_query_answer(
            query,
            "У тебя нет собственного магазина.",
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "❌ Да, удалить",
                callback_data=(
                    f"delete_store_yes_{store['id']}"
                ),
            ),
            InlineKeyboardButton(
                "↩️ Отмена",
                callback_data="my_store",
            ),
        ]
    ]

    await query.edit_message_text(
        "⚠️ Удаление магазина\n\n"
        f"Ты действительно хочешь удалить "
        f"«{store.get('name')}»?\n\n"
        "Будут удалены его товары и продавцы.",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    await safe_query_answer(query)


async def delete_store(
    update,
    context,
    store_id,
):
    query = update.callback_query

    store = get_owned_store(
        query.from_user.id
    )

    if not store or store["id"] != store_id:
        await safe_query_answer(
            query,
            "Только владелец может удалить магазин.",
        )
        return

    products = db_select(
        "products",
        filters_dict={
            "store_id": store_id,
        },
    )

    for product in products:
        product_id = product["id"]

        db_delete(
            "cart",
            {"product_id": product_id},
        )

        db_delete(
            "product_reviews",
            {"product_id": product_id},
        )

    db_delete(
        "products",
        {"store_id": store_id},
    )

    db_delete(
        "store_sellers",
        {"store_id": store_id},
    )

    db_delete(
        "store_seller_requests",
        {"store_id": store_id},
    )

    db_delete(
        "store_ratings",
        {"store_id": store_id},
    )

    db_delete(
        "stores",
        {"id": store_id},
    )

    set_role(
        query.from_user.id,
        "buyer",
    )

    await query.edit_message_text(
        "🗑️ Магазин удалён.",
        reply_markup=buyer_menu(
            query.from_user.id
        ),
    )

    await safe_query_answer(query)


# ============================================================
# PRODUCTS
# ============================================================

async def show_product(
    update,
    context,
    product_id,
):
    query = update.callback_query

    product = get_product(product_id)

    if not product:
        await safe_query_answer(
            query,
            "Товар не найден.",
        )
        return

    title = product_title(product)

    reviews = db_select(
        "product_reviews",
        filters_dict={
            "product_id": product_id,
        },
    )

    if reviews:
        average = sum(
            r.get("rating", 0)
            for r in reviews
        ) / len(reviews)

        rating_text = (
            f"⭐ {average:.1f}/5 "
            f"({len(reviews)})"
        )
    else:
        rating_text = (
            "⭐ Пока нет оценок"
        )

    category = category_name(product)
    category_text = f"🗂 Категория: {category}\n" if category else ""

    text = (
        f"📦 {title}\n\n"
        f"{product.get('description') or 'Описание отсутствует.'}\n\n"
        f"💰 Цена: {product.get('price', 0)} ₽\n"
        f"{category_text}"
        f"{rating_text}"
    )

    if product.get("cashback"):
        text += (
            f"\n💸 Кэшбэк: "
            f"{product.get('cashback')} ₽"
        )

    buttons = []

    buttons.append(
        [
            InlineKeyboardButton("🛒 Купить", callback_data=f"buy_{product_id}"),
            InlineKeyboardButton("➕ В корзину", callback_data=f"addcart_{product_id}"),
        ]
    )

    buttons.append(
        [
            InlineKeyboardButton(
                "⭐ Оценить / оставить отзыв",
                callback_data=(
                    f"review_product_{product_id}"
                ),
            )
        ]
    )

    store_id = product.get("store_id")

    if store_id:
        buttons.append(
            [
                InlineKeyboardButton(
                    "🏪 Открыть магазин",
                    callback_data=(
                        f"store_{store_id}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data=(
                    f"store_{store_id}"
                    if store_id
                    else "stores"
                ),
            )
        ]
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    await safe_query_answer(query)


# ============================================================
# PRODUCT REVIEW
# ============================================================

async def review_product_menu(
    update,
    context,
    product_id,
):
    query = update.callback_query

    product = get_product(product_id)

    if not product:
        await safe_query_answer(
            query,
            "Товар не найден.",
        )
        return

    user_id = query.from_user.id

    orders = db_select(
        "orders",
        filters_dict={
            "buyer_id": user_id,
            "product_id": product_id,
            "status": "completed",
        },
    )

    if not orders:
        await safe_query_answer(
            query,
            "Оставить отзыв можно после выполненной покупки.",
            show_alert=True,
        )
        return

    keyboard = []

    row = []

    for rating in range(1, 6):
        row.append(
            InlineKeyboardButton(
                "⭐" * rating,
                callback_data=(
                    f"set_product_rating_"
                    f"{product_id}_{rating}"
                ),
            )
        )

    keyboard.append(row)

    keyboard.append(
        [
            InlineKeyboardButton(
                "💬 Написать отзыв",
                callback_data=(
                    f"write_review_{product_id}"
                ),
            )
        ]
    )

    keyboard.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data=(
                    f"product_{product_id}"
                ),
            )
        ]
    )

    await query.edit_message_text(
        f"⭐ Оценка товара\n\n"
        f"📦 {product.get('name')}\n\n"
        "Выбери оценку:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    await safe_query_answer(query)


async def set_product_rating(
    update,
    context,
    product_id,
    rating,
):
    query = update.callback_query
    user_id = query.from_user.id

    orders = db_select(
        "orders",
        filters_dict={
            "buyer_id": user_id,
            "product_id": product_id,
            "status": "completed",
        },
        limit=1,
    )

    if not orders:
        await safe_query_answer(
            query,
            "Сначала нужно получить товар.",
            show_alert=True,
        )
        return

    existing = db_select(
        "product_reviews",
        filters_dict={
            "product_id": product_id,
            "user_id": user_id,
        },
        limit=1,
    )

    if existing:
        db_update(
            "product_reviews",
            {
                "rating": rating,
                "updated_at":
                    datetime.now(
                        timezone.utc
                    ).isoformat(),
            },
            {
                "id": existing[0]["id"],
            },
        )

        message = "⭐ Оценка обновлена!"

    else:
        db_insert(
            "product_reviews",
            {
                "product_id": product_id,
                "user_id": user_id,
                "order_id": orders[0]["id"],
                "rating": rating,
                "review": None,
            },
        )

        await add_xp(
            context.bot,
            user_id,
            XP_REVIEW,
            "review",
        )

        message = "⭐ Товар оценён!"

    await award_achievement(
        context.bot,
        user_id,
        "first_review",
    )

    await safe_query_answer(
        query,
        message,
    )

    await show_product(
        update,
        context,
        product_id,
    )


async def write_review(
    update,
    context,
    product_id,
):
    query = update.callback_query

    user_id = query.from_user.id

    orders = db_select(
        "orders",
        filters_dict={
            "buyer_id": user_id,
            "product_id": product_id,
            "status": "completed",
        },
        limit=1,
    )

    if not orders:
        await safe_query_answer(
            query,
            "Сначала нужно купить товар.",
            show_alert=True,
        )
        return

    context.user_data[
        "writing_review"
    ] = {
        "product_id": product_id,
        "order_id": orders[0]["id"],
    }

    await query.edit_message_text(
        "💬 Напиши свой отзыв о товаре:"
    )

    await safe_query_answer(query)


async def process_review(
    update,
    context,
):
    data = context.user_data.get(
        "writing_review"
    )

    if not data:
        return False

    context.user_data.pop(
        "writing_review",
        None,
    )

    review_text = update.message.text.strip()

    if not review_text:
        await update.message.reply_text(
            "❌ Отзыв не может быть пустым."
        )
        return True

    user_id = update.effective_user.id

    existing = db_select(
        "product_reviews",
        filters_dict={
            "product_id": data["product_id"],
            "user_id": user_id,
        },
        limit=1,
    )

    if existing:
        db_update(
            "product_reviews",
            {
                "review": review_text,
                "updated_at":
                    datetime.now(
                        timezone.utc
                    ).isoformat(),
            },
            {
                "id": existing[0]["id"],
            },
        )
    else:
        db_insert(
            "product_reviews",
            {
                "product_id":
                    data["product_id"],
                "user_id": user_id,
                "order_id":
                    data["order_id"],
                "rating": 5,
                "review": review_text,
            },
        )

        await add_xp(
            context.bot,
            user_id,
            XP_REVIEW,
            "review",
        )

    await award_achievement(
        context.bot,
        user_id,
        "first_review",
    )

    await update.message.reply_text(
        "💬 Отзыв сохранён! ⭐",
        reply_markup=buyer_menu(
            user_id
        ),
    )

    return True


# ============================================================
# PRODUCT EDIT
# ============================================================

async def edit_product_menu(
    update,
    context,
    product_id,
):
    query = update.callback_query

    product = get_product(product_id)

    if not product:
        await safe_query_answer(
            query,
            "Товар не найден.",
        )
        return

    store_id = product.get("store_id")

    if not is_store_member(
        query.from_user.id,
        store_id,
    ):
        await safe_query_answer(
            query,
            "Нет доступа.",
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "📦 Название",
                callback_data=(
                    f"edit_product_field_"
                    f"{product_id}_name"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "📝 Описание",
                callback_data=(
                    f"edit_product_field_"
                    f"{product_id}_description"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "💰 Цена",
                callback_data=(
                    f"edit_product_field_"
                    f"{product_id}_price"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "💸 Кэшбэк",
                callback_data=(
                    f"edit_product_field_"
                    f"{product_id}_cashback"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "🗂 Категория",
                callback_data=(
                    f"edit_product_category_{product_id}"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="my_products",
            )
        ],
    ]

    await query.edit_message_text(
        "✏️ Редактирование товара\n\n"
        f"📦 {product.get('name')}\n"
        f"💰 {product.get('price', 0)} ₽\n"
        f"💸 Кэшбэк: {product.get('cashback', 0)} ₽\n"
        f"🗂 Категория: {category_name(product) or 'нет'}\n\n"
        "Что изменить?",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    await safe_query_answer(query)


async def edit_product_field(
    update,
    context,
    product_id,
    field,
):
    query = update.callback_query

    product = get_product(product_id)

    if not product:
        await safe_query_answer(
            query,
            "Товар не найден.",
        )
        return

    if not is_store_member(
        query.from_user.id,
        product.get("store_id"),
    ):
        await safe_query_answer(
            query,
            "Нет доступа.",
        )
        return

    context.user_data[
        "editing_product"
    ] = {
        "product_id": product_id,
        "field": field,
    }

    names = {
        "name": "название",
        "description": "описание",
        "price": "цену",
        "cashback": "кэшбэк",
    }

    await query.edit_message_text(
        f"✏️ Введи новое "
        f"{names.get(field, field)}:"
    )

    await safe_query_answer(query)


async def process_product_edit(
    update,
    context,
):
    data = context.user_data.get(
        "editing_product"
    )

    if not data:
        return False

    context.user_data.pop(
        "editing_product",
        None,
    )

    product_id = data["product_id"]
    field = data["field"]

    if field == "stock":
        await update.message.reply_text("ℹ️ Учёт остатков отключён.")
        return True

    product = get_product(product_id)

    if not product:
        await update.message.reply_text(
            "❌ Товар не найден."
        )
        return True

    if not is_store_member(
        update.effective_user.id,
        product.get("store_id"),
    ):
        await update.message.reply_text(
            "❌ Нет доступа."
        )
        return True

    value = update.message.text.strip()

    if field in (
        "price",
        "cashback",
    ):
        try:
            value = int(value)

            if value < 0:
                raise ValueError

        except ValueError:
            await update.message.reply_text(
                "❌ Здесь нужно указать "
                "целое число не меньше 0."
            )
            return True

    if field in (
        "name",
        "description",
    ) and not value:
        await update.message.reply_text(
            "❌ Поле не может быть пустым."
        )
        return True

    db_update(
        "products",
        {field: value},
        {"id": product_id},
    )

    await update.message.reply_text(
        "✅ Товар обновлён!",
        reply_markup=seller_menu(
            update.effective_user.id
        ),
    )

    return True


# ============================================================
# PRODUCT DELETE
# ============================================================

async def delete_product_confirm(
    update,
    context,
    product_id,
):
    query = update.callback_query

    product = get_product(product_id)

    if not product:
        await safe_query_answer(
            query,
            "Товар не найден.",
        )
        return

    if not is_store_member(
        query.from_user.id,
        product.get("store_id"),
    ):
        await safe_query_answer(
            query,
            "Нет доступа.",
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "❌ Да, удалить",
                callback_data=(
                    f"delete_product_yes_{product_id}"
                ),
            ),
            InlineKeyboardButton(
                "↩️ Отмена",
                callback_data="my_products",
            ),
        ]
    ]

    await query.edit_message_text(
        "⚠️ Удаление товара\n\n"
        f"Ты действительно хочешь удалить "
        f"«{product.get('name')}»?",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

    await safe_query_answer(query)


async def delete_product(
    update,
    context,
    product_id,
):
    query = update.callback_query

    product = get_product(product_id)

    if not product:
        await safe_query_answer(
            query,
            "Товар уже удалён.",
        )
        return

    if not is_store_member(
        query.from_user.id,
        product.get("store_id"),
    ):
        await safe_query_answer(
            query,
            "Нет доступа.",
        )
        return

    db_delete(
        "cart",
        {"product_id": product_id},
    )

    db_delete(
        "product_reviews",
        {"product_id": product_id},
    )

    db_delete(
        "products",
        {"id": product_id},
    )

    await query.edit_message_text(
        "🗑️ Товар удалён.",
        reply_markup=seller_menu(
            query.from_user.id
        ),
    )

    await safe_query_answer(query)


# ============================================================
# CART
# ============================================================

async def add_to_cart(
    update,
    context,
    product_id,
):
    query = update.callback_query
    user_id = query.from_user.id

    product = get_product(product_id)

    if not product:
        await safe_query_answer(
            query,
            "Товар не найден.",
        )
        return

    existing = db_select(
        "cart",
        filters_dict={
            "user_id": user_id,
            "product_id": product_id,
        },
        limit=1,
    )

    if existing:
        db_update(
            "cart",
            {
                "quantity":
                    existing[0].get(
                        "quantity",
                        1,
                    ) + 1
            },
            {"id": existing[0]["id"]},
        )

    else:
        db_insert(
            "cart",
            {
                "user_id": user_id,
                "product_id": product_id,
                "quantity": 1,
            },
        )

    await safe_query_answer(
        query,
        "✅ Добавлено в корзину!",
    )


async def show_cart(
    update,
    context,
    acknowledge=True,
):
    query = update.callback_query
    user_id = query.from_user.id

    rows = db_select(
        "cart",
        filters_dict={"user_id": user_id},
    )

    if not rows:
        await query.edit_message_text(
            "🛒 Корзина пуста.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="back_menu",
                    )
                ]]
            ),
        )

        if acknowledge:
            await safe_query_answer(query)

        return

    total = 0
    text = "🛒 Корзина:\n\n"

    for row in rows:
        product = get_product(
            row.get("product_id")
        )

        if not product:
            continue

        quantity = row.get(
            "quantity",
            1,
        )

        price = product.get(
            "price",
            0,
        )

        subtotal = price * quantity
        total += subtotal

        text += (
            f"📦 {product.get('name')}\n"
            f"{price} ₽ × {quantity} = "
            f"{subtotal} ₽\n\n"
        )

    text += f"💰 Итого: {total} ₽"

    buttons = [
        [
            InlineKeyboardButton(
                "💳 Оформить заказ",
                callback_data="checkout_cart",
            )
        ],
        [
            InlineKeyboardButton(
                "🗑 Очистить корзину",
                callback_data="clear_cart",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back_menu",
            )
        ],
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    if acknowledge:
        await safe_query_answer(query)


async def clear_cart(
    update,
    context,
):
    query = update.callback_query

    db_delete(
        "cart",
        {
            "user_id":
                query.from_user.id,
        },
    )

    await safe_query_answer(
        query,
        "🗑 Корзина очищена.",
    )

    await show_cart(
        update,
        context,
        acknowledge=False,
    )


# ============================================================
# BUY PRODUCT
# ============================================================

async def buy_product(
    update,
    context,
    product_id,
):
    query = update.callback_query
    user_id = query.from_user.id

    product = get_product(product_id)

    if not product:
        await safe_query_answer(
            query,
            "Товар не найден.",
        )
        return

    price = product.get(
        "price",
        0,
    )

    user = get_user(user_id)

    if not user:
        await safe_query_answer(
            query,
            "Профиль не найден.",
        )
        return

    balance = user.get(
        "balance",
        0,
    ) or 0

    if balance < price:
        await safe_query_answer(
            query,
            f"Недостаточно денег. "
            f"Баланс: {balance} ₽",
        )
        return

    store_id = product.get(
        "store_id"
    )

    store = (
        get_store(store_id)
        if store_id
        else None
    )

    seller_id = (
        store.get("owner_id")
        if store
        else None
    )

    db_update(
        "users",
        {
            "balance":
                balance - price,
        },
        {"id": user_id},
    )

    order_status = "pending" if store_is_open(store) else "queued"

    created = db_insert(
        "orders",
        {
            "user_id": user_id,
            "product_id": product_id,
            "quantity": 1,
            "total_price": price,
            "status": order_status,
            "store_id": store_id,
            "buyer_id": user_id,
            "seller_id": seller_id,
            "product_name":
                product.get("name"),
            "price": price,
            "cashback":
                product.get(
                    "cashback",
                    0,
                ),
        },
    )

    if not created:
        db_update(
            "users",
            {"balance": balance},
            {"id": user_id},
        )

        await safe_query_answer(
            query,
            "❌ Не удалось оформить заказ.",
        )
        return

    await award_achievement(
        context.bot,
        user_id,
        "first_purchase",
    )

    await safe_query_answer(
        query,
        "✅ Покупка оформлена!",
    )

    await query.edit_message_text(
        "✅ Заказ оформлен!\n\n"
        f"📦 {product.get('name')}\n"
        f"💰 {price} ₽\n\n"
        "Продавец получил уведомление.",
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "📦 Мои заказы",
                    callback_data="buyer_orders",
                )
            ]]
        ),
    )

    if store and seller_id and order_status == "pending":
        await notify_user(
            context.bot,
            seller_id,
            (
                "🛒 Новый заказ!\n\n"
                f"📦 {product.get('name')}\n"
                f"💰 {price} ₽\n"
                f"👤 Покупатель: "
                f"{query.from_user.first_name or 'Покупатель'}"
            ),
        )
    elif store and order_status == "queued":
        await safe_query_answer(
            query,
            "⏳ Магазин закрыт. Заказ сохранён и будет передан продавцам после открытия.",
            show_alert=True,
        )


# ============================================================
# BUYER ORDERS
# ============================================================

async def buyer_orders(
    update,
    context,
):
    query = update.callback_query

    user_id = query.from_user.id

    orders = db_select(
        "orders",
        filters_dict={
            "buyer_id": user_id,
        },
        order_by="created_at",
        ascending=False,
    )

    if not orders:
        await query.edit_message_text(
            "📦 У тебя пока нет заказов.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="back_menu",
                    )
                ]]
            ),
        )

        await safe_query_answer(query)
        return

    status_text = {
        "pending": "⏳ Ожидает",
        "queued": "⏸ В очереди — магазин закрыт",
        "accepted": "✅ Принят",
        "completed": "🎉 Выполнен",
        "cancelled": "❌ Отменён",
    }

    text = "📦 Мои заказы:\n\n"

    for order in orders:
        status = order.get(
            "status",
            "pending",
        )

        text += (
            f"#{order.get('id')} — "
            f"{order.get('product_name') or 'Товар'}\n"
            f"💰 {order.get('total_price', 0)} ₽\n"
            f"{status_text.get(status, status)}\n\n"
        )

    await query.edit_message_text(
        text[:4000],
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="back_menu",
                )
            ]]
        ),
    )

    await safe_query_answer(query)


# ============================================================
# SELLER ORDERS
# ============================================================

async def seller_orders(
    update,
    context,
    acknowledge=True,
):
    query = update.callback_query

    user_id = query.from_user.id

    stores = get_user_stores(user_id)

    store_ids = {
        store["id"]
        for store in stores
    }

    if not store_ids:
        await query.edit_message_text(
            "🏪 У тебя нет магазина.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="seller_menu",
                    )
                ]]
            ),
        )

        if acknowledge:
            await safe_query_answer(query)

        return

    all_orders = db_select(
        "orders",
        order_by="created_at",
        ascending=False,
    )

    orders = [
        order
        for order in all_orders
        if order.get("store_id")
        in store_ids
    ]

    if not orders:
        await query.edit_message_text(
            "🛒 Заказов пока нет.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="seller_menu",
                    )
                ]]
            ),
        )

        if acknowledge:
            await safe_query_answer(query)

        return

    text = "🛒 Заказы магазина:\n\n"
    buttons = []

    for order in orders:
        text += (
            f"#{order.get('id')} — "
            f"{order.get('product_name') or 'Товар'}\n"
            f"💰 {order.get('total_price', 0)} ₽\n"
            f"Статус: {order.get('status')}\n\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    f"📦 Заказ #{order.get('id')}",
                    callback_data=(
                        f"seller_order_{order['id']}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="seller_menu",
            )
        ]
    )

    await query.edit_message_text(
        text[:4000],
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    if acknowledge:
        await safe_query_answer(query)


async def seller_order_details(
    update,
    context,
    order_id,
    acknowledge=True,
):
    query = update.callback_query
    user_id = query.from_user.id

    orders = db_select(
        "orders",
        filters_dict={"id": order_id},
        limit=1,
    )

    if not orders:
        await safe_query_answer(
            query,
            "Заказ не найден.",
        )
        return

    order = orders[0]
    store_id = order.get("store_id")

    if not store_id or not is_store_member(
        user_id,
        store_id,
    ):
        await safe_query_answer(
            query,
            "Нет доступа.",
        )
        return

    buyer = get_user(
        order.get("buyer_id")
        or order.get("user_id")
    )

    buyer_name = (
        buyer.get("first_name")
        if buyer
        else "Покупатель"
    )

    status = order.get(
        "status",
        "pending",
    )

    text = (
        f"📦 Заказ #{order.get('id')}\n\n"
        f"Товар: "
        f"{order.get('product_name') or 'Товар'}\n"
        f"Количество: "
        f"{order.get('quantity', 1)}\n"
        f"Сумма: "
        f"{order.get('total_price', 0)} ₽\n"
        f"Покупатель: {buyer_name}\n"
        f"Статус: {status}"
    )

    buttons = []

    if status == "queued":
        buttons.append(
            [
                InlineKeyboardButton(
                    "⏸ Магазин закрыт — ожидание открытия",
                    callback_data="noop",
                )
            ]
        )

    if status == "pending":
        buttons.append(
            [
                InlineKeyboardButton(
                    "✅ Принять",
                    callback_data=(
                        f"accept_order_{order_id}"
                    ),
                ),
                InlineKeyboardButton(
                    "❌ Отменить",
                    callback_data=(
                        f"cancel_order_{order_id}"
                    ),
                ),
            ]
        )

    if status == "accepted":
        buttons.append(
            [
                InlineKeyboardButton(
                    "🎉 Выполнить",
                    callback_data=(
                        f"complete_order_{order_id}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "⬅️ Заказы",
                callback_data="seller_orders",
            )
        ]
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    if acknowledge:
        await safe_query_answer(query)


async def accept_order(
    update,
    context,
    order_id,
):
    query = update.callback_query
    user_id = query.from_user.id

    orders = db_select(
        "orders",
        filters_dict={"id": order_id},
        limit=1,
    )

    if not orders:
        await safe_query_answer(
            query,
            "Заказ не найден.",
        )
        return

    order = orders[0]

    if not is_store_member(
        user_id,
        order.get("store_id"),
    ):
        await safe_query_answer(
            query,
            "Нет доступа.",
        )
        return

    if order.get("status") != "pending":
        await safe_query_answer(
            query,
            "Заказ уже обработан.",
        )
        return

    db_update(
        "orders",
        {
            "status": "accepted",
            "seller_id": user_id,
        },
        {"id": order_id},
    )

    await safe_query_answer(
        query,
        "✅ Заказ принят!",
    )

    await seller_order_details(
        update,
        context,
        order_id,
        acknowledge=False,
    )

    buyer_id = (
        order.get("buyer_id")
        or order.get("user_id")
    )

    await notify_user(
        context.bot,
        buyer_id,
        (
            "✅ Продавец принял твой заказ!\n\n"
            f"📦 {order.get('product_name') or 'Товар'}"
        ),
    )


async def cancel_order(
    update,
    context,
    order_id,
):
    query = update.callback_query
    user_id = query.from_user.id

    orders = db_select(
        "orders",
        filters_dict={"id": order_id},
        limit=1,
    )

    if not orders:
        await safe_query_answer(
            query,
            "Заказ не найден.",
        )
        return

    order = orders[0]

    if not is_store_member(
        user_id,
        order.get("store_id"),
    ):
        await safe_query_answer(
            query,
            "Нет доступа.",
        )
        return

    if order.get("status") != "pending":
        await safe_query_answer(
            query,
            "Нельзя отменить этот заказ.",
        )
        return

    db_update(
        "orders",
        {
            "status": "cancelled",
            "seller_id": user_id,
        },
        {"id": order_id},
    )

    # Возвращаем деньги покупателю.
    buyer_id = (
        order.get("buyer_id")
        or order.get("user_id")
    )

    buyer = get_user(buyer_id)

    if buyer:
        add_balance(
            buyer_id,
            order.get("total_price", 0) or 0,
        )

    await notify_user(
        context.bot,
        buyer_id,
        (
            "❌ Твой заказ отменён продавцом.\n\n"
            f"📦 {order.get('product_name') or 'Товар'}\n"
            f"💰 Возвращено: "
            f"{order.get('total_price', 0)} ₽"
        ),
    )

    await safe_query_answer(
        query,
        "❌ Заказ отменён.",
    )

    await seller_orders(
        update,
        context,
        acknowledge=False,
    )


async def complete_order(
    update,
    context,
    order_id,
):
    query = update.callback_query
    user_id = query.from_user.id

    orders = db_select(
        "orders",
        filters_dict={"id": order_id},
        limit=1,
    )

    if not orders:
        await safe_query_answer(
            query,
            "Заказ не найден.",
        )
        return

    order = orders[0]

    if not is_store_member(
        user_id,
        order.get("store_id"),
    ):
        await safe_query_answer(
            query,
            "Нет доступа.",
        )
        return

    if order.get("status") != "accepted":
        await safe_query_answer(
            query,
            "Заказ ещё не принят.",
        )
        return

    db_update(
        "orders",
        {"status": "completed"},
        {"id": order_id},
    )

    buyer_id = (
        order.get("buyer_id")
        or order.get("user_id")
    )

    increment_user_counter(buyer_id, "purchase_count", 1)
    await add_xp(context.bot, buyer_id, XP_PURCHASE, "purchase")

    seller_id = order.get("seller_id") or user_id
    increment_user_counter(seller_id, "sales_count", 1)
    await add_xp(context.bot, seller_id, XP_SALE, "sale")

    buyer_id = (
        order.get("buyer_id")
        or order.get("user_id")
    )

    await notify_user(
        context.bot,
        buyer_id,
        (
            "🎉 Твой заказ выполнен!\n\n"
            f"📦 {order.get('product_name') or 'Товар'}"
        ),
    )

    await safe_query_answer(
        query,
        "🎉 Заказ выполнен!",
    )

    await seller_orders(
        update,
        context,
        acknowledge=False,
    )


# ============================================================
# MY STORE
# ============================================================

async def my_store(
    update,
    context,
):
    query = update.callback_query
    user_id = query.from_user.id

    store = get_owned_store(user_id)

    if not store:
        store = get_user_store(user_id)

    if not store:
        await query.edit_message_text(
            "🏪 У тебя пока нет магазина.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="seller_menu",
                    )
                ]]
            ),
        )

        await safe_query_answer(query)
        return

    store_id = store["id"]
    owner = store.get("owner_id") == user_id

    members = get_store_member_ids(
        store_id
    )

    rating, count = get_store_rating(
        store_id
    )

    rating_text = (
        f"⭐ {rating:.1f}/5 ({count})"
        if rating is not None
        else "⭐ Нет оценок"
    )

    text = (
        f"🏪 {store.get('name')}\n\n"
        f"{store.get('description') or 'Описание отсутствует.'}\n\n"
        f"👥 Продавцов: {len(members)}\n"
        f"{rating_text}\n"
        f"Статус: {store_status_text(store)}"
    )

    if store.get("address"):
        text += (
            f"\n📍 {store.get('address')}"
        )

    buttons = [
        [
            InlineKeyboardButton(
                "👥 Продавцы",
                callback_data=(
                    f"store_sellers_{store_id}"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "📦 Товары магазина",
                callback_data=(
                    f"store_{store_id}"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "➕ Добавить продавца",
                callback_data="add_seller",
            )
        ],
        [
            InlineKeyboardButton(
                "📨 Запросы",
                callback_data="seller_requests",
            )
        ],
    ]

    if owner:
        if store_is_open(store):
            buttons.append([
                InlineKeyboardButton(
                    "🔴 Закрыть магазин",
                    callback_data=f"close_store_{store_id}",
                )
            ])
        else:
            buttons.append([
                InlineKeyboardButton(
                    "🟢 Открыть магазин",
                    callback_data=f"open_store_{store_id}",
                )
            ])

        buttons.append(
            [
                InlineKeyboardButton(
                    "✏️ Редактировать магазин",
                    callback_data="edit_store",
                )
            ]
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    "🗑️ Удалить магазин",
                    callback_data="delete_store",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="seller_menu",
            )
        ]
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    await safe_query_answer(query)


async def store_sellers(
    update,
    context,
    store_id,
):
    query = update.callback_query

    store = get_store(store_id)

    if not store:
        await safe_query_answer(
            query,
            "Магазин не найден.",
        )
        return

    if not is_store_member(
        query.from_user.id,
        store_id,
    ):
        await safe_query_answer(
            query,
            "Нет доступа.",
        )
        return

    text = (
        f"👥 Продавцы магазина "
        f"«{store.get('name')}»:\n\n"
    )

    for member_id in get_store_member_ids(
        store_id
    ):
        member = get_user(member_id)

        if not member:
            continue

        name = (
            member.get("first_name")
            or "Без имени"
        )

        username = member.get(
            "username"
        )

        if username:
            name += f" (@{username})"

        if member_id == store.get(
            "owner_id"
        ):
            text += (
                f"👑 {name} — владелец\n"
            )
        else:
            text += (
                f"👤 {name}\n"
            )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="my_store",
                )
            ]]
        ),
    )

    await safe_query_answer(query)


# ============================================================
# STORE STATS
# ============================================================

async def store_stats(
    update,
    context,
):
    query = update.callback_query

    await safe_query_answer(
        query,
        "📊 Статистика магазина пока недоступна.",
    )


# ============================================================
# TASKS
# ============================================================

async def show_tasks(
    update,
    context,
):
    query = update.callback_query
    user_id = query.from_user.id

    tasks = db_select(
        "tasks",
        filters_dict={"user_id": user_id},
    )

    if not tasks:
        default_tasks = [
            ("Посетить VexMart", 15),
            ("Совершить покупку", 50),
            ("Оставить отзыв", 30),
            ("Открыть магазин", 100),
        ]

        for task_name, reward in default_tasks:
            db_insert(
                "tasks",
                {
                    "user_id": user_id,
                    "task": task_name,
                    "reward": reward,
                    "completed": False,
                },
            )

        tasks = db_select(
            "tasks",
            filters_dict={"user_id": user_id},
        )

    # Миграция старого задания 0.43, которое нельзя было проверить
    # по данным сервера. Теперь оно становится проверяемым.
    for task in tasks:
        if str(task.get("task", "")).strip().lower() == "посмотреть товар":
            db_update(
                "tasks",
                {"task": "Совершить покупку"},
                {"id": task.get("id")},
            )
            task["task"] = "Совершить покупку"

    text = "📋 Задания:\n\n"
    buttons = []

    for task in tasks:
        completed = bool(task.get("completed"))
        mark = "✅" if completed else "⬜"
        text += (
            f"{mark} {task.get('task')}\n"
            f"💰 Награда: {task.get('reward', 0)} ₽\n\n"
        )

        if not completed:
            buttons.append([
                InlineKeyboardButton(
                    f"🎁 Получить награду #{task.get('id')}",
                    callback_data=f"claim_task_{task.get('id')}",
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="back_menu",
        )
    ])

    await query.edit_message_text(
        text[:4000],
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    await safe_query_answer(query)


async def create_store_start(update, context):
    query = update.callback_query
    user_id = query.from_user.id
    existing = get_owned_store(user_id)

    if existing:
        set_role(user_id, "seller")
        await query.edit_message_text(
            "🏪 У тебя уже есть магазин.",
            reply_markup=seller_menu(user_id),
        )
        await safe_query_answer(query)
        return

    context.user_data["creating_store"] = True
    await query.edit_message_text(
        "🏪 Создание магазина\n\n"
        "Напиши название магазина:"
    )
    await safe_query_answer(query)


async def process_create_store(update, context):
    if not context.user_data.get("creating_store"):
        return False

    context.user_data.pop("creating_store", None)
    user_id = update.effective_user.id
    name = update.message.text.strip()

    if not name:
        await update.message.reply_text(
            "❌ Название не может быть пустым."
        )
        return True

    created = db_insert(
        "stores",
        {
            "owner_id": user_id,
            "name": name[:100],
            "description": "",
            "status": "open",
        },
    )

    if not created:
        await update.message.reply_text(
            "❌ Не удалось создать магазин."
        )
        return True

    set_role(user_id, "seller")
    await add_xp(
        context.bot,
        user_id,
        XP_STORE_CREATE,
        "store_create",
    )
    await award_achievement(
        context.bot,
        user_id,
        "first_store",
    )

    await update.message.reply_text(
        "🎉 Магазин создан!",
        reply_markup=seller_menu(user_id),
    )
    return True


async def become_seller(
    update,
    context,
):
    query = update.callback_query

    user_id = query.from_user.id

    stores = get_user_stores(user_id)

    if stores:
        set_role(
            user_id,
            "seller",
        )

        await query.edit_message_text(
            "💼 Ты уже являешься продавцом.\n\n"
            "Переключаю тебя в меню продавца.",
            reply_markup=seller_menu(
                user_id
            ),
        )

        await safe_query_answer(query)
        return

    await query.edit_message_text(
        "💼 Чтобы стать продавцом магазина, "
        "открой магазин и отправь запрос "
        "«Запроситься в продавцы».\n\n"
        "После одобрения владельцем магазина "
        "ты автоматически станешь продавцом.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🏪 Магазины",
                        callback_data="stores",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="back_menu",
                    )
                ],
            ]
        ),
    )

    await safe_query_answer(query)


async def switch_buyer(
    update,
    context,
):
    query = update.callback_query

    set_role(
        query.from_user.id,
        "buyer",
    )

    await query.edit_message_text(
        "🛍 Ты переключён в режим покупателя.",
        reply_markup=buyer_menu(
            query.from_user.id
        ),
    )

    await safe_query_answer(query)


async def show_seller_menu(
    update,
    context,
):
    query = update.callback_query

    user_id = query.from_user.id

    user = get_user(user_id)

    if not user:
        user = ensure_user(
            query.from_user
        )

    await query.edit_message_text(
        "💼 Меню продавца",
        reply_markup=seller_menu(
            user_id
        ),
    )

    await safe_query_answer(query)


# ============================================================
# MY PRODUCTS
# ============================================================

async def my_products(
    update,
    context,
):
    query = update.callback_query

    user_id = query.from_user.id

    stores = get_user_stores(user_id)

    if not stores:
        await query.edit_message_text(
            "📦 У тебя нет магазина.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="seller_menu",
                    )
                ]]
            ),
        )

        await safe_query_answer(query)
        return

    store_ids = {
        store["id"]
        for store in stores
    }

    products = db_select("products")

    products = [
        p
        for p in products
        if p.get("store_id")
        in store_ids
    ]

    if not products:
        await query.edit_message_text(
            "📦 Товаров пока нет.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="seller_menu",
                    )
                ]]
            ),
        )

        await safe_query_answer(query)
        return

    text = "📦 Твои товары:\n\n"
    buttons = []

    for product in products:
        text += (
            f"{product_title(product)}\n"
            f"💰 {product.get('price', 0)} ₽\n"
            f"🗂 Категория: {category_name(product) or 'нет'}\n\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    f"✏️ {product.get('name')}",
                    callback_data=(
                        f"edit_product_{product['id']}"
                    ),
                ),
                InlineKeyboardButton(
                    "🗑️",
                    callback_data=(
                        f"delete_product_"
                        f"{product['id']}"
                    ),
                ),
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="seller_menu",
            )
        ]
    )

    await query.edit_message_text(
        text[:4000],
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )

    await safe_query_answer(query)


# ============================================================
# NEARBY STORES
# ============================================================

async def nearby_stores(
    update,
    context,
):
    query = update.callback_query

    await query.edit_message_text(
        "📍 Чтобы найти магазины рядом, "
        "отправь свою геолокацию.",
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="back_menu",
                )
            ]]
        ),
    )

    context.user_data[
        "awaiting_location"
    ] = True

    await safe_query_answer(query)


async def receive_location(
    update,
    context,
):
    if not context.user_data.get(
        "awaiting_location"
    ):
        return

    context.user_data[
        "awaiting_location"
    ] = False

    location = update.message.location

    latitude = location.latitude
    longitude = location.longitude

    stores = db_select("stores")

    nearby = []

    for store in stores:
        lat = store.get("latitude")
        lon = store.get("longitude")

        if lat is None or lon is None:
            continue

        lat_diff = abs(
            float(lat) - latitude
        )

        lon_diff = abs(
            float(lon) - longitude
        )

        distance = (
            lat_diff ** 2
            + lon_diff ** 2
        ) ** 0.5

        nearby.append(
            (
                distance,
                store,
            )
        )

    nearby.sort(
        key=lambda x: x[0]
    )

    nearby = nearby[:10]

    if not nearby:
        await update.message.reply_text(
            "📍 Рядом магазинов не найдено.",
            reply_markup=buyer_menu(
                update.effective_user.id
            ),
        )
        return

    text = "📍 Магазины рядом:\n\n"
    buttons = []

    for _, store in nearby:
        text += (
            f"🏪 {store.get('name')}\n"
            f"📍 {store.get('address') or 'Адрес не указан'}\n\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    f"🏪 {store.get('name')}",
                    callback_data=(
                        f"store_{store['id']}"
                    ),
                )
            ]
        )

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


# ============================================================
# ADMIN
# ============================================================

def is_superadmin(user_id):
    return bool(SUPERADMIN_ID) and user_id == SUPERADMIN_ID


def is_admin(user_id):
    return user_id in ADMIN_IDS or is_superadmin(user_id)


async def salary_command(update, context):
    """Выдача зарплаты в VXC доступна только SUPERADMIN_ID."""
    message = update.effective_message
    actor = update.effective_user

    if not actor or not is_superadmin(actor.id):
        await message.reply_text("⛔ Выдавать зарплату может только главный администратор.")
        return

    if len(context.args) != 2:
        await message.reply_text(
            "Использование:\n"
            "/salary @username сумма\n"
            "или /salary Telegram_ID сумма\n\n"
            "Например: /salary @friend 50"
        )
        return

    target_text = context.args[0].strip()
    try:
        amount = int(context.args[1])
    except (TypeError, ValueError):
        await message.reply_text("❌ Сумма должна быть целым числом VXC.")
        return

    if amount <= 0:
        await message.reply_text("❌ Сумма зарплаты должна быть больше нуля.")
        return
    if amount > 1_000_000:
        await message.reply_text("❌ За одну выдачу можно начислить не больше 1 000 000 VXC.")
        return

    users = db_select("users")
    target = None

    if target_text.startswith("@"):
        username = target_text[1:].casefold()
        target = next(
            (user for user in users
             if str(user.get("username") or "").casefold() == username),
            None,
        )
    else:
        try:
            target_id = int(target_text)
        except ValueError:
            await message.reply_text(
                "❌ Укажи Telegram ID числом или username с символом @."
            )
            return
        target = next(
            (user for user in users if user.get("id") == target_id),
            None,
        )

    if not target:
        await message.reply_text(
            "❌ Пользователь не найден в VexMart. Пусть он сначала запустит бота командой /start."
        )
        return

    if target.get("id") == actor.id:
        await message.reply_text("ℹ️ Зарплату можно выдавать другим пользователям, не самому себе.")
        return

    if not add_balance(target["id"], amount):
        await message.reply_text("⚠️ Не получилось начислить зарплату. Попробуй позже.")
        return

    new_balance = int(target.get("balance", 0) or 0) + amount
    target_name = (
        f"@{target.get('username')}"
        if target.get("username")
        else str(target.get("first_name") or target.get("id"))
    )
    await message.reply_text(
        "✅ Зарплата начислена!\n\n"
        f"👤 Получатель: {target_name}\n"
        f"💵 Начислено: +{amount} VXC\n"
        f"💰 Новый баланс: {new_balance} VXC"
    )


async def salary_menu(update, context):
    query = update.callback_query
    if not is_superadmin(query.from_user.id):
        await safe_query_answer(query, "Нет доступа.", show_alert=True)
        return
    admins = sorted((uid for uid in ADMIN_IDS if uid != SUPERADMIN_ID))
    users = {int(u.get("id")): u for u in db_select("users") if u.get("id") is not None}
    rows = []
    for uid in admins:
        user = users.get(uid, {})
        name = user.get("first_name") or user.get("username") or str(uid)
        rows.append([InlineKeyboardButton(f"👤 {str(name)[:28]} · {uid}", callback_data=f"salary_user_{uid}")])
    rows.append([InlineKeyboardButton("📜 История выплат", callback_data="salary_history")])
    rows.append([InlineKeyboardButton("🗓 Мои планы выплат", callback_data="salary_plans")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin")])
    await query.edit_message_text("💰 Зарплата\n\nВыбери обычного администратора:", reply_markup=InlineKeyboardMarkup(rows))
    await safe_query_answer(query)


async def salary_history(update, context):
    q = update.callback_query
    if not is_superadmin(q.from_user.id):
        await safe_query_answer(q, "Нет доступа.", show_alert=True); return
    rows = db_select("salary_payments", order_by="paid_at", limit=20)
    lines = ["📜 Последние выплаты зарплаты:\n"]
    for r in rows:
        lines.append(f"• {r.get('amount')} VXC → {r.get('recipient_id')} · {str(r.get('paid_at') or '')[:16]}")
    if not rows: lines.append("Выплат пока нет.")
    await q.edit_message_text("\n".join(lines)[:3900], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К зарплате", callback_data="salary_menu")]]))
    await safe_query_answer(q)


async def salary_plans(update, context):
    q = update.callback_query
    if not is_superadmin(q.from_user.id):
        await safe_query_answer(q, "Нет доступа.", show_alert=True); return
    plans = db_select("salary_plans", order_by="created_at", limit=30)
    lines = ["🗓 Планы выплат:\n"]
    buttons = []
    for plan in plans:
        state = "🟢" if plan.get("active") else "⏸"
        lines.append(f"{state} #{plan.get('id')} · {plan.get('recipient_id')} · {plan.get('amount')} VXC · {plan.get('period')}")
        buttons.append([InlineKeyboardButton(f"{state} План #{plan.get('id')} — вкл/выкл", callback_data=f"salary_toggle_{plan.get('id')}"), InlineKeyboardButton("Изменить сумму", callback_data=f"salary_edit_{plan.get('id')}")])
        buttons.append([InlineKeyboardButton(f"❌ Отменить план #{plan.get('id')}", callback_data=f"salary_cancel_{plan.get('id')}")])
    if not plans: lines.append("Планов пока нет.")
    buttons.append([InlineKeyboardButton("⬅️ К зарплате", callback_data="salary_menu")])
    await q.edit_message_text("\n".join(lines)[:3900], reply_markup=InlineKeyboardMarkup(buttons))
    await safe_query_answer(q)


async def salary_toggle(update, context, plan_id):
    q = update.callback_query
    if not is_superadmin(q.from_user.id):
        await safe_query_answer(q, "Нет доступа.", show_alert=True); return
    plans = db_select("salary_plans", filters_dict={"id": plan_id}, limit=1)
    if not plans:
        await safe_query_answer(q, "План не найден.", show_alert=True); return
    db_update("salary_plans", {"active": not bool(plans[0].get("active"))}, {"id": plan_id})
    await salary_plans(update, context)


async def salary_begin_amount(update, context, recipient_id):
    q = update.callback_query
    if not is_superadmin(q.from_user.id) or recipient_id not in ADMIN_IDS or recipient_id == SUPERADMIN_ID:
        await safe_query_answer(q, "Получатель не является обычным администратором.", show_alert=True); return
    context.user_data["salary_draft"] = {"recipient_id": recipient_id}
    await q.edit_message_text("Введи сумму зарплаты в VXC (целое число от 1 до 1 000 000):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="salary_menu")]]))
    await safe_query_answer(q)


async def process_salary_text(update, context):
    draft = context.user_data.get("salary_draft")
    if not draft:
        return False
    if not is_superadmin(update.effective_user.id):
        context.user_data.pop("salary_draft", None); return True
    raw = (update.message.text or "").strip()
    stage = draft.get("stage", "amount")
    if stage == "amount":
        try: amount = int(raw)
        except ValueError:
            await update.message.reply_text("Введи целое число VXC."); return True
        if not 1 <= amount <= 1_000_000:
            await update.message.reply_text("Сумма должна быть от 1 до 1 000 000 VXC."); return True
        draft["amount"] = amount
        draft["stage"] = "mode"
        await update.message.reply_text("Как начислять зарплату?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Один раз", callback_data="salary_once"), InlineKeyboardButton("По расписанию", callback_data="salary_repeat")],[InlineKeyboardButton("Отмена", callback_data="salary_menu")]]))
        return True
    if stage == "schedule":
        period = draft["period"]
        parts = [x.strip() for x in raw.splitlines() if x.strip()]
        try:
            if len(parts) != draft["count"]: raise ValueError
            schedule=[]
            if period == "daily":
                for item in parts:
                    hh, mm = map(int, item.split(":"))
                    if not (0 <= hh <= 23 and 0 <= mm <= 59): raise ValueError
                    schedule.append({"hour":hh,"minute":mm})
            elif period == "weekly":
                days={"пн":1,"понедельник":1,"вт":2,"вторник":2,"ср":3,"среда":3,"чт":4,"четверг":4,"пт":5,"пятница":5,"сб":6,"суббота":6,"вс":7,"воскресенье":7}
                for item in parts:
                    day, clock = item.split()
                    weekday = days.get(day.casefold())
                    if weekday is None and day.isdigit() and 1 <= int(day) <= 7: weekday=int(day)
                    hh,mm=map(int,clock.split(":"))
                    if weekday is None or not (0<=hh<=23 and 0<=mm<=59): raise ValueError
                    schedule.append({"weekday":weekday,"hour":hh,"minute":mm})
            else:
                for item in parts:
                    day=int(item)
                    if not 1 <= day <= 31: raise ValueError
                    schedule.append({"day":day})
        except Exception:
            example = "09:00\\n18:30" if period=="daily" else ("пн 09:00\\nпт 18:00" if period=="weekly" else "1\\n15")
            await update.message.reply_text(f"Формат не подходит. Отправь ровно {draft['count']} строк(и). Пример:\n{example}")
            return True
        now = datetime.now(timezone.utc)
        first = next_salary_due(period, schedule, now)
        record={"recipient_id":draft["recipient_id"],"created_by":update.effective_user.id,"amount":draft["amount"],"period":period,"schedule":schedule,"active":True,"next_run_at":first.isoformat()}
        inserted=db_insert("salary_plans",record)
        if not inserted:
            await update.message.reply_text("Не удалось сохранить план. Проверь, что SQL-миграция 0.48.0 выполнена в Supabase.")
        else:
            await update.message.reply_text(f"✅ План зарплаты создан: {draft['amount']} VXC за каждое начисление. Первое — {first.astimezone(ZoneInfo('Asia/Omsk')).strftime('%d.%m.%Y %H:%M Омск')}.")
        context.user_data.pop("salary_draft",None)
        return True
    return False


def next_salary_due(period, schedule, after):
    from datetime import time as dtime
    local_tz = ZoneInfo("Asia/Omsk")
    after = after.astimezone(local_tz)
    candidates=[]
    if period == "daily":
        for item in schedule:
            candidate=after.replace(hour=item["hour"],minute=item["minute"],second=0,microsecond=0)
            if candidate <= after: candidate += timedelta(days=1)
            candidates.append(candidate.astimezone(timezone.utc))
    elif period == "weekly":
        for item in schedule:
            delta=(item["weekday"]-after.isoweekday())%7
            candidate=(after+timedelta(days=delta)).replace(hour=item["hour"],minute=item["minute"],second=0,microsecond=0)
            if candidate <= after: candidate += timedelta(days=7)
            candidates.append(candidate.astimezone(timezone.utc))
    else:
        for item in schedule:
            day=item["day"]
            year,month=after.year,after.month
            for _ in range(24):
                import calendar
                last=calendar.monthrange(year,month)[1]
                if day <= last:
                    candidate=after.replace(year=year,month=month,day=day,hour=0,minute=0,second=0,microsecond=0)
                    if candidate > after: candidates.append(candidate.astimezone(timezone.utc))
                    break
                month += 1
                if month == 13: month=1; year += 1
    if not candidates: raise ValueError("No future salary date")
    return min(candidates)


async def salary_scheduler(application):
    while True:
        try:
            now=datetime.now(timezone.utc)
            due=db_select("salary_plans", filters_dict={"active":True}, limit=200)
            for plan in due:
                run_at=_parse_datetime(plan.get("next_run_at"))
                if not run_at or run_at > now: continue
                next_run=next_salary_due(plan["period"],plan["schedule"],run_at+timedelta(seconds=1))
                try:
                    result=supabase.rpc("process_salary_payout",{"p_plan_id":plan["id"],"p_due_at":run_at.isoformat(),"p_next_run_at":next_run.isoformat()}).execute()
                    if result.data:
                        await notify_user(application.bot,int(plan["recipient_id"]),f"💰 Зарплата начислена автоматически: +{plan['amount']} VXC.")
                        await notify_user(application.bot,SUPERADMIN_ID,f"✅ Зарплата {plan['amount']} VXC начислена администратору {plan['recipient_id']}.")
                except Exception as e: print(f"[SALARY SCHEDULER ERROR] plan={plan.get('id')}: {e}")
        except Exception as e: print(f"[SALARY LOOP ERROR] {e}")
        await asyncio.sleep(30)


async def start_salary_scheduler(application):
    application.bot_data["salary_task"] = asyncio.create_task(salary_scheduler(application))


async def stop_salary_scheduler(application):
    task=application.bot_data.get("salary_task")
    if task:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass


async def admin_panel(
    update,
    context,
):
    query = update.callback_query

    if not is_admin(query.from_user.id):
        await safe_query_answer(
            query,
            "Нет доступа.",
        )
        return

    users = db_select("users")
    stores = db_select("stores")
    products = db_select("products")
    orders = db_select("orders")
    categories = db_select("categories")

    open_stores = sum(
        1 for store in stores
        if store_is_open(store)
    )
    balance_total = sum(
        int(user.get("balance", 0) or 0)
        for user in users
    )

    status_counts = {}
    for order in orders:
        status = str(order.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    status_text = ", ".join(
        f"{status}: {count}"
        for status, count in sorted(status_counts.items())
    ) or "нет заказов"

    buttons = [
        [
            InlineKeyboardButton(
                "📊 Статистика",
                callback_data="admin_stats",
            )
        ],
        [
            InlineKeyboardButton(
                "👥 Пользователи",
                callback_data="admin_users",
            ),
            InlineKeyboardButton(
                "🏪 Магазины",
                callback_data="admin_stores",
            ),
        ],
        [
            InlineKeyboardButton(
                "📦 Товары",
                callback_data="admin_products",
            ),
            InlineKeyboardButton(
                "🛒 Заказы",
                callback_data="admin_orders",
            ),
        ],
        [
            InlineKeyboardButton(
                "🗂 Категории",
                callback_data="admin_categories",
            )
        ],
        *([[InlineKeyboardButton("💰 Зарплата", callback_data="salary_menu")]] if is_superadmin(query.from_user.id) else []),
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back_menu",
            )
        ],
    ]

    text = (
        "🛠 Админ-панель\n\n"
        f"VexMart {VERSION}\n\n"
        f"👥 Пользователей: {len(users)}\n"
        f"🏪 Магазинов: {len(stores)} "
        f"(🟢 {open_stores})\n"
        f"📦 Товаров: {len(products)}\n"
        f"🗂 Категорий: {len(categories)}\n"
        f"🛒 Заказов: {len(orders)}\n"
        f"💰 Баланс пользователей: {balance_total} VXC\n\n"
        f"📋 Статусы заказов: {status_text}"
    )

    await query.edit_message_text(
        text[:4000],
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    await safe_query_answer(query)


async def admin_stats(update, context):
    query = update.callback_query

    if not is_admin(query.from_user.id):
        await safe_query_answer(query, "Нет доступа.")
        return

    users = db_select("users")
    stores = db_select("stores")
    products = db_select("products")
    orders = db_select("orders")
    reviews = db_select("product_reviews")
    ratings = db_select("store_ratings")
    visits = db_select("bot_visits")

    completed = sum(
        1 for order in orders
        if order.get("status") == "completed"
    )
    pending = sum(
        1 for order in orders
        if order.get("status") in {"pending", "queued"}
    )
    total_sales = sum(
        int(order.get("total_price", 0) or 0)
        for order in orders
        if order.get("status") == "completed"
    )

    text = (
        "📊 Техническая статистика VexMart\n\n"
        f"👥 Пользователей: {len(users)}\n"
        f"🏪 Магазинов: {len(stores)}\n"
        f"📦 Товаров: {len(products)}\n"
        f"🛒 Заказов: {len(orders)}\n"
        f"✅ Завершённых: {completed}\n"
        f"⏳ Ожидающих: {pending}\n"
        f"💰 Сумма завершённых продаж: {total_sales} ₽\n"
        f"⭐ Отзывов: {len(reviews)}\n"
        f"⭐ Оценок магазинов: {len(ratings)}\n"
        f"👀 Посещений бота: {len(visits)}"
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="admin",
            )]
        ]),
    )
    await safe_query_answer(query)


async def admin_categories(update, context):
    query = update.callback_query

    if not is_admin(query.from_user.id):
        await safe_query_answer(query, "Нет доступа.")
        return

    categories = get_categories()
    products = db_select("products")

    lines = ["🗂 Категории\n"]
    for category in categories:
        count = sum(
            1 for product in products
            if product.get("category_id") == category.get("id")
        )
        lines.append(
            f"#{category.get('id')} "
            f"{category.get('name')} — {count} товаров"
        )

    if not categories:
        lines.append("Категорий пока нет.")

    await query.edit_message_text(
        "\n".join(lines)[:4000],
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="admin",
            )]
        ]),
    )
    await safe_query_answer(query)



async def admin_users(
    update,
    context,
):
    query = update.callback_query

    if not is_admin(
        query.from_user.id
    ):
        await safe_query_answer(
            query,
            "Нет доступа.",
        )
        return

    users = db_select(
        "users",
        order_by="id",
        ascending=False,
        limit=30,
    )

    text = "👥 Пользователи:\n\n"

    for user in users:
        text += (
            f"🆔 {user.get('id')}\n"
            f"👤 {user.get('first_name') or '—'}\n"
            f"@{user.get('username') or '—'}\n"
            f"Роль: {user.get('role')}\n"
            f"💰 {user.get('balance', 0)} ₽\n\n"
        )

    await query.edit_message_text(
        text[:4000],
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="admin",
                )
            ]]
        ),
    )

    await safe_query_answer(query)


async def admin_stores(
    update,
    context,
):
    query = update.callback_query

    if not is_admin(
        query.from_user.id
    ):
        await safe_query_answer(
            query,
            "Нет доступа.",
        )
        return

    stores = db_select(
        "stores",
        order_by="created_at",
        ascending=False,
    )

    text = "🏪 Магазины:\n\n"

    for store in stores:
        members = get_store_member_ids(
            store["id"]
        )

        text += (
            f"#{store['id']} "
            f"{store.get('name')}\n"
            f"👑 Владелец: "
            f"{store.get('owner_id')}\n"
            f"👥 Продавцов: "
            f"{len(members)}\n\n"
        )

    await query.edit_message_text(
        text[:4000],
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="admin",
                )
            ]]
        ),
    )

    await safe_query_answer(query)


async def admin_products(
    update,
    context,
):
    query = update.callback_query

    if not is_admin(
        query.from_user.id
    ):
        await safe_query_answer(
            query,
            "Нет доступа.",
        )
        return

    products = db_select(
        "products",
        order_by="id",
        ascending=False,
        limit=50,
    )

    text = "📦 Товары:\n\n"

    for product in products:
        text += (
            f"#{product.get('id')} "
            f"{product_title(product)}\n"
            f"💰 {product.get('price', 0)} ₽\n"
            f"🏪 {product.get('store_id') or '—'}\n\n"
        )

    await query.edit_message_text(
        text[:4000],
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="admin",
                )
            ]]
        ),
    )

    await safe_query_answer(query)


async def admin_orders(
    update,
    context,
):
    query = update.callback_query

    if not is_admin(
        query.from_user.id
    ):
        await safe_query_answer(
            query,
            "Нет доступа.",
        )
        return

    orders = db_select(
        "orders",
        order_by="created_at",
        ascending=False,
        limit=50,
    )

    text = "🛒 Заказы:\n\n"

    for order in orders:
        text += (
            f"#{order.get('id')}\n"
            f"📦 {order.get('product_name') or 'Товар'}\n"
            f"💰 {order.get('total_price', 0)} ₽\n"
            f"👤 Покупатель: "
            f"{order.get('buyer_id') or order.get('user_id')}\n"
            f"💼 Продавец: "
            f"{order.get('seller_id') or '—'}\n"
            f"Статус: "
            f"{order.get('status')}\n\n"
        )

    await query.edit_message_text(
        text[:4000],
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="admin",
                )
            ]]
        ),
    )

    await safe_query_answer(query)


# ============================================================
# BACK TO MENU
# ============================================================

async def back_menu(
    update,
    context,
):
    query = update.callback_query

    user = get_user(
        query.from_user.id
    )

    if not user:
        ensure_user(
            query.from_user
        )

        user = get_user(
            query.from_user.id
        )

    if (
        user
        and user.get("role") == "seller"
    ):
        await query.edit_message_text(
            "💼 Меню продавца",
            reply_markup=seller_menu(
                query.from_user.id
            ),
        )

    else:
        await query.edit_message_text(
            "🛍 Главное меню",
            reply_markup=buyer_menu(
                query.from_user.id
            ),
        )

    await safe_query_answer(query)


# ============================================================
# CALLBACK ROUTER
# ============================================================

async def callback_router(
    update,
    context,
):
    query = update.callback_query
    data = query.data or ""

    try:

        # ----------------------------
        # DAILY BONUS
        # ----------------------------

        if data == "daily_bonus":
            await claim_daily_bonus(
                update,
                context,
            )
            return

        if data == "achievements":
            await show_achievements(
                update,
                context,
            )
            return

        if data == "categories":
            await show_categories(update, context)
            return

        if data.startswith("category_"):
            await show_category_products(
                update,
                context,
                int(data.split("_")[-1]),
            )
            return

        if data == "smart_search":
            await smart_search_start(update, context)
            return

        if data == "store_map":
            await show_store_map(update, context)
            return

        if data == "edit_product_category":
            await safe_query_answer(query)
            return

        if data.startswith("edit_product_category_"):
            await choose_product_category(
                update,
                context,
                int(data.split("_")[-1]),
            )
            return

        if data.startswith("set_product_category_"):
            parts = data.split("_")
            await set_product_category(
                update,
                context,
                int(parts[3]),
                int(parts[4]),
            )
            return

        if data == "create_store":
            await create_store_start(update, context)
            return

        if data.startswith("leaderboard_"):
            await show_leaderboard(
                update,
                context,
                data[len("leaderboard_"):],
            )
            return

        if data.startswith("claim_task_"):
            await claim_task(
                update,
                context,
                int(data.split("_")[-1]),
            )
            return

        if data.startswith("open_store_"):
            await open_store(
                update,
                context,
                int(data.split("_")[-1]),
            )
            return

        if data.startswith("close_store_"):
            await close_store(
                update,
                context,
                int(data.split("_")[-1]),
            )
            return

        if data == "noop":
            await safe_query_answer(query)
            return

        # ----------------------------
        # STORE EDIT / DELETE
        # ----------------------------

        if data == "edit_store":
            await edit_store_menu(
                update,
                context,
            )
            return

        if data == "delete_store":
            await delete_store_confirm(
                update,
                context,
            )
            return

        if data.startswith(
            "delete_store_yes_"
        ):
            store_id = int(
                data.split("_")[-1]
            )

            await delete_store(
                update,
                context,
                store_id,
            )
            return

        if data.startswith(
            "edit_store_field_"
        ):
            parts = data.split("_")
            store_id = int(parts[3])
            field = parts[4]

            await edit_store_field(
                update,
                context,
                store_id,
                field,
            )
            return

        # ----------------------------
        # PRODUCT EDIT / DELETE
        # ----------------------------

        if data.startswith(
            "edit_product_field_"
        ):
            parts = data.split("_")
            product_id = int(parts[3])
            field = parts[4]

            await edit_product_field(
                update,
                context,
                product_id,
                field,
            )
            return

        if data.startswith(
            "edit_product_"
        ):
            product_id = int(
                data.split("_")[-1]
            )

            await edit_product_menu(
                update,
                context,
                product_id,
            )
            return

        if data.startswith(
            "delete_product_"
        ):
            product_id = int(
                data.split("_")[-1]
            )

            await delete_product_confirm(
                update,
                context,
                product_id,
            )
            return

        if data.startswith(
            "delete_product_yes_"
        ):
            product_id = int(
                data.split("_")[-1]
            )

            await delete_product(
                update,
                context,
                product_id,
            )
            return

        # ----------------------------
        # STORE RATINGS
        # ----------------------------

        if data.startswith(
            "rate_store_"
        ):
            store_id = int(
                data.split("_")[-1]
            )

            await rate_store_menu(
                update,
                context,
                store_id,
            )
            return

        if data.startswith(
            "set_store_rating_"
        ):
            parts = data.split("_")
            store_id = int(parts[3])
            rating = int(parts[4])

            await set_store_rating(
                update,
                context,
                store_id,
                rating,
            )
            return

        # ----------------------------
        # PRODUCT REVIEWS
        # ----------------------------

        if data.startswith(
            "review_product_"
        ):
            product_id = int(
                data.split("_")[-1]
            )

            await review_product_menu(
                update,
                context,
                product_id,
            )
            return

        if data.startswith(
            "set_product_rating_"
        ):
            parts = data.split("_")
            product_id = int(parts[3])
            rating = int(parts[4])

            await set_product_rating(
                update,
                context,
                product_id,
                rating,
            )
            return

        if data.startswith(
            "write_review_"
        ):
            product_id = int(
                data.split("_")[-1]
            )

            await write_review(
                update,
                context,
                product_id,
            )
            return

        # ----------------------------
        # REQUESTS
        # ----------------------------

        if data.startswith(
            "approve_request_"
        ):
            request_id = int(
                data.split("_")[-1]
            )

            await approve_request(
                update,
                context,
                request_id,
            )
            return

        if data.startswith(
            "reject_request_"
        ):
            request_id = int(
                data.split("_")[-1]
            )

            await reject_request(
                update,
                context,
                request_id,
            )
            return

        if data.startswith(
            "approve_invite_"
        ):
            request_id = int(
                data.split("_")[-1]
            )

            await approve_invite(
                update,
                context,
                request_id,
            )
            return

        if data.startswith(
            "reject_invite_"
        ):
            request_id = int(
                data.split("_")[-1]
            )

            await reject_invite(
                update,
                context,
                request_id,
            )
            return

        # ----------------------------
        # JOIN STORE
        # ----------------------------

        if data.startswith(
            "join_store_"
        ):
            store_id = int(
                data.split("_")[-1]
            )

            await join_store(
                update,
                context,
                store_id,
            )
            return

        # ----------------------------
        # SELLER ORDERS
        # ----------------------------

        if data.startswith(
            "seller_order_"
        ):
            order_id = int(
                data.split("_")[-1]
            )

            await seller_order_details(
                update,
                context,
                order_id,
            )
            return

        if data.startswith(
            "accept_order_"
        ):
            order_id = int(
                data.split("_")[-1]
            )

            await accept_order(
                update,
                context,
                order_id,
            )
            return

        if data.startswith(
            "cancel_order_"
        ):
            order_id = int(
                data.split("_")[-1]
            )

            await cancel_order(
                update,
                context,
                order_id,
            )
            return

        if data.startswith(
            "complete_order_"
        ):
            order_id = int(
                data.split("_")[-1]
            )

            await complete_order(
                update,
                context,
                order_id,
            )
            return

        # ----------------------------
        # STORE SELLERS
        # ----------------------------

        if data.startswith(
            "store_sellers_"
        ):
            store_id = int(
                data.split("_")[-1]
            )

            await store_sellers(
                update,
                context,
                store_id,
            )
            return

        # ----------------------------
        # PRODUCTS
        # ----------------------------

        if data.startswith(
            "addcart_"
        ):
            product_id = int(
                data.split("_")[-1]
            )

            await add_to_cart(
                update,
                context,
                product_id,
            )
            return

        if data.startswith(
            "buy_"
        ):
            product_id = int(
                data.split("_")[-1]
            )

            await buy_product(
                update,
                context,
                product_id,
            )
            return

        if data.startswith(
            "product_"
        ):
            product_id = int(
                data.split("_")[-1]
            )

            await show_product(
                update,
                context,
                product_id,
            )
            return

        # ----------------------------
        # STORES
        # ----------------------------

        if data.startswith(
            "store_"
        ):
            suffix = data[
                len("store_"):
            ]

            if not suffix.isdigit():
                await safe_query_answer(
                    query,
                    "⚠️ Старая кнопка больше не действует.",
                )
                return

            store_id = int(suffix)

            await show_store(
                update,
                context,
                store_id,
            )
            return

        # ----------------------------
        # ADMIN
        # ----------------------------

        if data == "admin":
            await admin_panel(
                update,
                context,
            )
            return
        if data == "salary_menu":
            await salary_menu(update, context); return
        if data == "salary_history":
            await salary_history(update, context); return
        if data == "salary_plans":
            await salary_plans(update, context); return
        if data.startswith("salary_user_"):
            await salary_begin_amount(update, context, int(data.rsplit("_",1)[1])); return
        if data.startswith("salary_toggle_"):
            await salary_toggle(update, context, int(data.rsplit("_",1)[1])); return
        if data.startswith("salary_edit_"):
            if not is_superadmin(query.from_user.id):
                await safe_query_answer(query,"Нет доступа.",show_alert=True); return
            plan_id=int(data.rsplit("_",1)[1])
            if not db_select("salary_plans",filters_dict={"id":plan_id},limit=1):
                await safe_query_answer(query,"План не найден.",show_alert=True); return
            context.user_data["salary_edit_plan_id"]=plan_id
            await query.edit_message_text(f"Введи новую сумму VXC для плана #{plan_id} (1–1 000 000):")
            await safe_query_answer(query); return
        if data.startswith("salary_cancel_"):
            if not is_superadmin(query.from_user.id):
                await safe_query_answer(query,"Нет доступа.",show_alert=True); return
            plan_id=int(data.rsplit("_",1)[1])
            db_delete("salary_plans",{"id":plan_id})
            await salary_plans(update,context); return
        if data in {"salary_once", "salary_repeat"}:
            if not is_superadmin(query.from_user.id):
                await safe_query_answer(query, "Нет доступа.", show_alert=True); return
            draft=context.user_data.get("salary_draft")
            if not draft:
                await safe_query_answer(query, "Сессия истекла. Начни заново.", show_alert=True); return
            if data == "salary_once":
                target_id=int(draft["recipient_id"]); amount=int(draft["amount"])
                target=get_user(target_id)
                if not target or not add_balance(target_id,amount):
                    await safe_query_answer(query,"Не удалось начислить зарплату.",show_alert=True); return
                db_insert("salary_payments",{"recipient_id":target_id,"amount":amount,"created_by":query.from_user.id,"payment_type":"manual"})
                context.user_data.pop("salary_draft",None)
                await query.edit_message_text(f"✅ Начислено {amount} VXC администратору {target_id}. Баланс не списывался ни у кого.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К зарплате",callback_data="salary_menu")]]))
                await notify_user(context.bot,target_id,f"💰 Тебе начислена зарплата: +{amount} VXC.")
                await notify_user(context.bot,SUPERADMIN_ID,f"✅ Ты начислил зарплату {amount} VXC администратору {target_id}.")
                await safe_query_answer(query); return
            draft["stage"]="period"
            await query.edit_message_text("Выбери период:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Ежедневно",callback_data="salary_period_daily")],[InlineKeyboardButton("Еженедельно",callback_data="salary_period_weekly")],[InlineKeyboardButton("Ежемесячно",callback_data="salary_period_monthly")]]))
            await safe_query_answer(query); return
        if data.startswith("salary_period_"):
            if not is_superadmin(query.from_user.id):
                await safe_query_answer(query,"Нет доступа.",show_alert=True); return
            draft=context.user_data.get("salary_draft")
            if not draft:
                await safe_query_answer(query,"Сессия истекла.",show_alert=True); return
            period=data.rsplit("_",1)[1]; draft["period"]=period; draft["stage"]="count"
            await query.edit_message_text("Сколько выплат в каждом периоде? Введи число от 1 до 10.")
            await safe_query_answer(query); return

        if data == "admin_stats":
            await admin_stats(update, context)
            return

        if data == "admin_categories":
            await admin_categories(update, context)
            return

        if data == "admin_users":
            await admin_users(
                update,
                context,
            )
            return

        if data == "admin_stores":
            await admin_stores(
                update,
                context,
            )
            return

        if data == "admin_products":
            await admin_products(
                update,
                context,
            )
            return

        if data == "admin_orders":
            await admin_orders(
                update,
                context,
            )
            return

        # ----------------------------
        # MENUS
        # ----------------------------

        if data == "back_menu":
            await back_menu(
                update,
                context,
            )
            return

        if data == "seller_menu":
            await show_seller_menu(
                update,
                context,
            )
            return

        if data == "stores":
            await show_stores(
                update,
                context,
            )
            return

        if data == "nearby_stores":
            await nearby_stores(
                update,
                context,
            )
            return

        if data == "profile":
            await show_profile(
                update,
                context,
            )
            return

        if data == "cart":
            await show_cart(
                update,
                context,
            )
            return

        if data == "clear_cart":
            await clear_cart(
                update,
                context,
            )
            return

        if data == "buyer_orders":
            await buyer_orders(
                update,
                context,
            )
            return

        if data == "seller_orders":
            await seller_orders(
                update,
                context,
            )
            return

        if data == "tasks":
            await show_tasks(
                update,
                context,
            )
            return

        if data == "my_store":
            await my_store(
                update,
                context,
            )
            return

        if data == "my_products":
            await my_products(
                update,
                context,
            )
            return

        if data == "add_seller":
            await add_seller(
                update,
                context,
            )
            return

        if data == "seller_requests":
            await show_seller_requests(
                update,
                context,
            )
            return

        if data == "become_seller":
            await become_seller(
                update,
                context,
            )
            return

        if data == "switch_buyer":
            await switch_buyer(
                update,
                context,
            )
            return

        # ----------------------------
        # OLD STORE STATS
        # ----------------------------

        if data == "store_stats":
            await store_stats(
                update,
                context,
            )
            return

        # ----------------------------
        # UNKNOWN
        # ----------------------------

        await safe_query_answer(
            query,
            "Неизвестная команда.",
        )

    except Exception as e:
        print(
            f"[CALLBACK ERROR] {data}: {e}"
        )

        await safe_query_answer(
            query,
            "⚠️ Произошла ошибка.",
        )


# ============================================================
# TEXT HANDLER
# ============================================================

async def text_handler(
    update,
    context,
):
    edit_plan_id=context.user_data.get("salary_edit_plan_id")
    if edit_plan_id is not None:
        if not is_superadmin(update.effective_user.id):
            context.user_data.pop("salary_edit_plan_id",None); return
        try: new_amount=int((update.message.text or "").strip())
        except ValueError: new_amount=0
        if not 1 <= new_amount <= 1_000_000:
            await update.message.reply_text("Введи целое число от 1 до 1 000 000 VXC."); return
        db_update("salary_plans",{"amount":new_amount},{"id":edit_plan_id})
        context.user_data.pop("salary_edit_plan_id",None)
        await update.message.reply_text(f"✅ Сумма выплат в плане #{edit_plan_id} изменена на {new_amount} VXC.")
        return
    if await process_salary_text(update, context):
        return
    draft=context.user_data.get("salary_draft")
    if draft and draft.get("stage") == "count":
        try: count=int((update.message.text or "").strip())
        except ValueError: count=0
        if not 1 <= count <= 10:
            await update.message.reply_text("Введи число от 1 до 10."); return
        draft["count"]=count; draft["stage"]="schedule"
        period=draft["period"]
        example="09:00\n18:30" if period=="daily" else ("пн 09:00\nпт 18:00" if period=="weekly" else "1\n15")
        description="Время HH:MM, каждая выплата отдельной строкой." if period=="daily" else ("День недели и время (пн/вт/ср/чт/пт/сб/вс HH:MM)." if period=="weekly" else "Только число месяца 1–31, без времени. Несуществующий день месяца пропускается.")
        await update.message.reply_text(f"Теперь отправь {count} строк(и). {description}\nПример:\n{example}")
        return
    if await process_smart_search(update, context):
        return

    if await process_create_store(
        update,
        context,
    ):
        return

    if await process_seller_username(
        update,
        context,
    ):
        return

    if await process_store_edit(
        update,
        context,
    ):
        return

    if await process_product_edit(
        update,
        context,
    ):
        return

    if await process_review(
        update,
        context,
    ):
        return

    if context.user_data.get(
        "awaiting_location"
    ):
        return

    await update.message.reply_text(
        "Используй кнопки меню 👇",
        reply_markup=buyer_menu(
            update.effective_user.id
        ),
    )


# ============================================================
# LOCATION HANDLER
# ============================================================

async def location_handler(
    update,
    context,
):
    await receive_location(
        update,
        context,
    )


# ============================================================
# AUTO-REFRESH / TELEGRAM KEEP-ALIVE
# ============================================================

async def telegram_keep_alive(application):
    """
    Периодически обращаемся к Telegram API, чтобы поддерживать
    активную сессию бота и не допускать ситуации, когда после
    длительного простоя inline-кнопки начинают вести себя так,
    будто бот больше не отвечает.

    Интервал специально меньше 30 минут.
    """
    while True:
        try:
            await application.bot.get_me()
            print("[KEEP-ALIVE] Telegram connection refreshed")
        except Exception as e:
            print(f"[KEEP-ALIVE ERROR] {e}")

        await asyncio.sleep(5 * 60)


async def start_keep_alive(application):
    task = asyncio.create_task(
        telegram_keep_alive(application)
    )
    application.bot_data["keep_alive_task"] = task


async def stop_keep_alive(application):
    task = application.bot_data.get("keep_alive_task")

    if task:
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass


# ============================================================
# APP AUTHENTICATION
# ============================================================

AUTH_SESSION_DAYS = 30
AUTH_CHALLENGE_MINUTES = 5
AUTH_RATE_WINDOW_SECONDS = 600
AUTH_RATE_MAX_ATTEMPTS = 10
_auth_attempts = defaultdict(deque)


def _utc_now():
    return datetime.now(timezone.utc)


def _iso_utc(value):
    return value.isoformat()


def _parse_datetime(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result
    except (TypeError, ValueError):
        return None


def _sha256(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_password(password, salt=None):
    salt_bytes = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt_bytes,
        n=16384,
        r=8,
        p=1,
        dklen=64,
    )
    return f"scrypt$16384$8$1${salt_bytes.hex()}${digest.hex()}"


def _verify_password(password, stored):
    try:
        algorithm, n, r, p_value, salt_hex, digest_hex = stored.split("$", 5)
        if algorithm != "scrypt":
            return False
        calculated = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p_value),
            dklen=len(bytes.fromhex(digest_hex)),
        )
        return hmac.compare_digest(calculated.hex(), digest_hex)
    except (ValueError, TypeError, MemoryError):
        return False


def _normalise_username(username):
    return str(username or "").strip().casefold()


def _valid_username(username):
    if not isinstance(username, str):
        return False
    username = username.strip()
    return (
        3 <= len(username) <= 24
        and all(ch.isalnum() or ch in "_.-" for ch in username)
    )


def _auth_rate_limited(request, action):
    now = _utc_now().timestamp()
    ip = request.remote or "unknown"
    key = f"{ip}:{action}"
    attempts = _auth_attempts[key]
    while attempts and now - attempts[0] > AUTH_RATE_WINDOW_SECONDS:
        attempts.popleft()
    if len(attempts) >= AUTH_RATE_MAX_ATTEMPTS:
        return True
    attempts.append(now)
    return False


def _bearer_token(request):
    value = request.headers.get("Authorization", "")
    if not value.lower().startswith("bearer "):
        return None
    token = value[7:].strip()
    return token if 20 <= len(token) <= 256 else None


def _get_account_for_token(token):
    sessions = db_select(
        "app_sessions",
        filters_dict={"token_hash": _sha256(token)},
        limit=1,
    )
    if not sessions:
        return None
    session = sessions[0]
    expires = _parse_datetime(session.get("expires_at"))
    if session.get("revoked_at") or not expires or expires <= _utc_now():
        return None
    accounts = db_select(
        "app_accounts",
        filters_dict={"id": session.get("account_id")},
        limit=1,
    )
    return accounts[0] if accounts else None


def _create_app_session(account_id):
    token = secrets.token_urlsafe(40)
    created = db_insert("app_sessions", {
        "account_id": account_id,
        "token_hash": _sha256(token),
        "expires_at": _iso_utc(_utc_now() + timedelta(days=AUTH_SESSION_DAYS)),
    })
    return token if created else None


async def _read_json_object(request):
    try:
        value = await request.json()
    except Exception:
        return None
    return value if isinstance(value, dict) else None


async def api_auth_register(request):
    if _auth_rate_limited(request, "register"):
        return web.json_response(
            {"ok": False, "error": "too_many_attempts"},
            status=429,
        )

    data = await _read_json_object(request)
    if not data:
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)

    username = str(data.get("username") or "").strip()
    password = data.get("password")
    if not _valid_username(username):
        return web.json_response({
            "ok": False,
            "error": "invalid_username",
            "message": "Логин должен содержать 3–24 буквы, цифры, точку, дефис или подчёркивание.",
        }, status=400)
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        return web.json_response({
            "ok": False,
            "error": "invalid_password",
            "message": "Пароль должен содержать от 8 до 128 символов.",
        }, status=400)

    normalized = _normalise_username(username)
    existing = await asyncio.to_thread(
        db_select, "app_accounts", "id", {"username_normalized": normalized}, 1
    )
    if existing:
        return web.json_response({"ok": False, "error": "username_taken"}, status=409)

    created = await asyncio.to_thread(
        db_insert,
        "app_accounts",
        {
            "username": username,
            "username_normalized": normalized,
            "password_hash": _hash_password(password),
        },
    )
    if not created:
        # A unique constraint also protects against simultaneous registrations.
        return web.json_response({"ok": False, "error": "registration_failed"}, status=409)

    account = created[0]
    token = await asyncio.to_thread(_create_app_session, account["id"])
    if not token:
        return web.json_response({"ok": False, "error": "session_creation_failed"}, status=500)

    return web.json_response({
        "ok": True,
        "token": token,
        "expires_in": AUTH_SESSION_DAYS * 24 * 60 * 60,
        "account": {
            "id": account["id"],
            "username": account["username"],
            "telegram_linked": bool(account.get("telegram_user_id")),
        },
    }, status=201)


async def api_auth_login(request):
    if _auth_rate_limited(request, "login"):
        return web.json_response(
            {"ok": False, "error": "too_many_attempts"},
            status=429,
        )

    data = await _read_json_object(request)
    if not data:
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
    username = data.get("username")
    password = data.get("password")
    if not isinstance(username, str) or not isinstance(password, str):
        return web.json_response({"ok": False, "error": "invalid_credentials"}, status=401)

    accounts = await asyncio.to_thread(
        db_select,
        "app_accounts",
        "id,username,username_normalized,password_hash,telegram_user_id",
        {"username_normalized": _normalise_username(username)},
        1,
    )
    account = accounts[0] if accounts else None
    stored_hash = account.get("password_hash") if account else None
    if not stored_hash or not _verify_password(password, stored_hash):
        return web.json_response({"ok": False, "error": "invalid_credentials"}, status=401)

    token = await asyncio.to_thread(_create_app_session, account["id"])
    if not token:
        return web.json_response({"ok": False, "error": "session_creation_failed"}, status=500)
    return web.json_response({
        "ok": True,
        "token": token,
        "expires_in": AUTH_SESSION_DAYS * 24 * 60 * 60,
        "account": {
            "id": account["id"],
            "username": account["username"],
            "telegram_linked": bool(account.get("telegram_user_id")),
        },
    })


async def api_auth_me(request):
    token = _bearer_token(request)
    account = await asyncio.to_thread(_get_account_for_token, token) if token else None
    if not account:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    return web.json_response({
        "ok": True,
        "account": {
            "id": account["id"],
            "username": account["username"],
            "telegram_linked": bool(account.get("telegram_user_id")),
        },
    })


async def api_auth_logout(request):
    token = _bearer_token(request)
    if not token:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    sessions = await asyncio.to_thread(
        db_select, "app_sessions", filters_dict={"token_hash": _sha256(token)}, limit=1
    )
    if not sessions:
        return web.json_response({"ok": True})
    await asyncio.to_thread(
        db_update,
        "app_sessions",
        {"revoked_at": _iso_utc(_utc_now())},
        {"id": sessions[0]["id"]},
    )
    return web.json_response({"ok": True})


async def api_auth_telegram_start(request):
    if _auth_rate_limited(request, "telegram_start"):
        return web.json_response({"ok": False, "error": "too_many_attempts"}, status=429)

    application = request.app.get("telegram_application")
    if not application:
        return web.json_response({"ok": False, "error": "telegram_unavailable"}, status=503)
    try:
        bot_info = await application.bot.get_me()
    except Exception as exc:
        print(f"[AUTH TELEGRAM BOT INFO ERROR] {exc}")
        return web.json_response({"ok": False, "error": "telegram_unavailable"}, status=503)

    challenge = secrets.token_urlsafe(24)
    expires_at = _iso_utc(_utc_now() + timedelta(minutes=AUTH_CHALLENGE_MINUTES))
    created = await asyncio.to_thread(
        db_insert,
        "telegram_login_challenges",
        {
            "challenge_hash": _sha256(challenge),
            "expires_at": expires_at,
        },
    )
    if not created:
        return web.json_response({"ok": False, "error": "challenge_creation_failed"}, status=500)

    return web.json_response({
        "ok": True,
        "bot_link": f"https://t.me/{bot_info.username}?start=auth_{challenge}",
        "expires_in": AUTH_CHALLENGE_MINUTES * 60,
        "message": "Открой ссылку и подтверди вход в Telegram-боте, затем проверь статус.",
    })


async def api_auth_telegram_status(request):
    challenge = request.query.get("challenge", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", challenge):
        return web.json_response({"ok": False, "error": "invalid_challenge"}, status=400)

    rows = await asyncio.to_thread(
        db_select,
        "telegram_login_challenges",
        filters_dict={"challenge_hash": _sha256(challenge)},
        limit=1,
    )
    if not rows:
        return web.json_response({"ok": False, "error": "challenge_not_found"}, status=404)
    row = rows[0]
    if row.get("consumed_at"):
        return web.json_response({"ok": False, "error": "challenge_already_used"}, status=410)
    expires = _parse_datetime(row.get("expires_at"))
    if not expires or expires <= _utc_now():
        return web.json_response({"ok": False, "error": "challenge_expired"}, status=410)
    if not row.get("confirmed_at") or not row.get("telegram_user_id"):
        return web.json_response({"ok": True, "confirmed": False})

    telegram_id = int(row["telegram_user_id"])
    accounts = await asyncio.to_thread(
        db_select,
        "app_accounts",
        filters_dict={"telegram_user_id": telegram_id},
        limit=1,
    )
    account = accounts[0] if accounts else None

    if not account:
        # Telegram-only account; keep Telegram bot's existing users table and balances intact.
        created = await asyncio.to_thread(
            db_insert,
            "app_accounts",
            {
                "username": f"tg_{telegram_id}",
                "username_normalized": f"tg_{telegram_id}",
                "password_hash": None,
                "telegram_user_id": telegram_id,
            },
        )
        if not created:
            # If two polls raced, re-read the account before returning an error.
            accounts = await asyncio.to_thread(
                db_select,
                "app_accounts",
                filters_dict={"telegram_user_id": telegram_id},
                limit=1,
            )
            account = accounts[0] if accounts else None
        else:
            account = created[0]

    if not account:
        return web.json_response({"ok": False, "error": "telegram_account_failed"}, status=500)

    token = await asyncio.to_thread(_create_app_session, account["id"])
    if not token:
        return web.json_response({"ok": False, "error": "session_creation_failed"}, status=500)

    await asyncio.to_thread(
        db_update,
        "telegram_login_challenges",
        {"consumed_at": _iso_utc(_utc_now())},
        {"id": row["id"]},
    )
    return web.json_response({
        "ok": True,
        "confirmed": True,
        "token": token,
        "expires_in": AUTH_SESSION_DAYS * 24 * 60 * 60,
        "account": {
            "id": account["id"],
            "username": account["username"],
            "telegram_linked": True,
        },
    })


async def confirm_telegram_auth_start(update, context, payload):
    """Confirm a one-time app login challenge from a Telegram deep link."""
    challenge = payload[len("auth_"):]
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", challenge):
        return False

    rows = await asyncio.to_thread(
        db_select,
        "telegram_login_challenges",
        filters_dict={"challenge_hash": _sha256(challenge)},
        limit=1,
    )
    if not rows:
        await update.message.reply_text("❌ Ссылка входа не найдена или уже недействительна.")
        return True

    row = rows[0]
    expires = _parse_datetime(row.get("expires_at"))
    if row.get("consumed_at") or not expires or expires <= _utc_now():
        await update.message.reply_text("⌛ Ссылка входа истекла. Вернись в приложение и запроси новую.")
        return True

    # Telegram supplies the identity; never trust an ID sent by the app.
    telegram_user = update.effective_user
    await asyncio.to_thread(ensure_user, telegram_user)
    await asyncio.to_thread(
        db_update,
        "telegram_login_challenges",
        {
            "telegram_user_id": telegram_user.id,
            "confirmed_at": _iso_utc(_utc_now()),
        },
        {"id": row["id"]},
    )
    await update.message.reply_text(
        "✅ Вход в VexMart подтверждён!\n\n"
        "Вернись в приложение — оно завершит вход автоматически."
    )
    return True


# ============================================================
# WEB SERVER + READ-ONLY CATALOG API
# ============================================================

async def health(request):
    return web.Response(
        text=f"VexMart {VERSION} is alive!"
    )


@web.middleware
async def api_cors_middleware(request, handler):
    """Allow the read-only catalog API to be called from Expo web preview."""
    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        response = await handler(request)

    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


async def api_stores(request):
    """Return public store-card fields only; do not expose exact location data."""
    stores = await asyncio.to_thread(
        db_select,
        "stores",
        "id,name,description,status,created_at",
        None,
        100,
        "created_at",
        False,
    )
    return web.json_response({
        "ok": True,
        "count": len(stores),
        "stores": stores,
    })


async def api_store_products(request):
    try:
        store_id = int(request.match_info["store_id"])
        if store_id < 1:
            raise ValueError
    except (TypeError, ValueError):
        return web.json_response(
            {"ok": False, "error": "invalid_store_id"},
            status=400,
        )

    stores = await asyncio.to_thread(
        db_select,
        "stores",
        "id,name,status",
        {"id": store_id},
        1,
    )
    if not stores:
        return web.json_response(
            {"ok": False, "error": "store_not_found"},
            status=404,
        )

    products = await asyncio.to_thread(
        db_select,
        "products",
        "id,name,description,price,store_id,cashback,created_at,category_id",
        {"store_id": store_id},
        100,
        "created_at",
        False,
    )
    return web.json_response({
        "ok": True,
        "store": stores[0],
        "count": len(products),
        "products": products,
    })


async def api_options(request):
    return web.Response(status=204)


async def start_web_server(
    application,
):
    port = int(
        os.getenv(
            "PORT",
            "10000",
        )
    )

    app = web.Application(middlewares=[api_cors_middleware])
    app["telegram_application"] = application

    app.router.add_get(
        "/",
        health,
    )

    app.router.add_get(
        "/health",
        health,
    )
    app.router.add_get("/api/stores", api_stores)

    # Authentication API. Secrets stay on the server; only token hashes are stored.
    app.router.add_post("/api/auth/register", api_auth_register)
    app.router.add_post("/api/auth/login", api_auth_login)
    app.router.add_get("/api/auth/me", api_auth_me)
    app.router.add_post("/api/auth/logout", api_auth_logout)
    app.router.add_post("/api/auth/telegram/start", api_auth_telegram_start)
    app.router.add_get("/api/auth/telegram/status", api_auth_telegram_status)
    app.router.add_get(
        "/api/stores/{store_id}/products",
        api_store_products,
    )
    app.router.add_route("OPTIONS", "/{tail:.*}", api_options)

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port,
    )

    await site.start()

    application.bot_data[
        "web_runner"
    ] = runner

    print(
        f"[WEB] Health server started "
        f"on port {port}"
    )

    await start_keep_alive(application)
    await start_salary_scheduler(application)


async def stop_web_server(
    application,
):
    runner = application.bot_data.get(
        "web_runner"
    )

    if runner:
        try:
            await runner.cleanup()

        except Exception as e:
            print(
                f"[WEB CLEANUP ERROR] {e}"
            )

    await stop_keep_alive(application)
    await stop_salary_scheduler(application)


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update,
    context,
):
    print(
        "[BOT ERROR]",
        context.error,
    )


# ============================================================
# MAIN
# ============================================================

def main():
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(
            start_web_server
        )
        .post_shutdown(
            stop_web_server
        )
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "salary",
            salary_command,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_router,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.LOCATION,
            location_handler,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            text_handler,
        )
    )

    application.add_error_handler(
        error_handler
    )

    print(
        f"🚀 VexMart {VERSION} starting..."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
