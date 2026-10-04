import os
import asyncio

from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

PRODUCTS = {
    "leaves": "📦 Коробка листочков",
    "stone": "🪨 Премиум-камень",
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(
            "📦 Коробка листочков",
            callback_data="buy_leaves"
        )],
        [InlineKeyboardButton(
            "🪨 Премиум-камень",
            callback_data="buy_stone"
        )],
    ]

    await update.message.reply_text(
        "🏪 VexMart\n\n"
        "Добро пожаловать в наш дворовый магазин!\n"
        "Выберите товар:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("buy_"):
        product_id = query.data.replace("buy_", "")
        product = PRODUCTS[product_id]

        keyboard = [
            [InlineKeyboardButton(
                "✅ Заказать",
                callback_data=f"confirm_{product_id}"
            )],
            [InlineKeyboardButton(
                "❌ Отмена",
                callback_data="cancel"
            )],
        ]

        await query.edit_message_text(
            f"Вы выбрали:\n\n{product}\n\n"
            "Оформить заказ?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data.startswith("confirm_"):
        product_id = query.data.replace("confirm_", "")
        product = PRODUCTS[product_id]

        user = query.from_user
        username = (
            f"@{user.username}"
            if user.username
            else user.first_name
        )

        if ADMIN_ID:
            keyboard = [
                [InlineKeyboardButton(
                    "✅ Выполнено",
                    callback_data="complete"
                )]
            ]

            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "🔔 НОВЫЙ ЗАКАЗ!\n\n"
                    f"👤 Покупатель: {username}\n"
                    f"📦 Товар: {product}\n\n"
                    "Принеси товар покупателю и нажми "
                    "«Выполнено»."
                ),
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        await query.edit_message_text(
            f"✅ Заказ принят!\n\n"
            f"📦 {product}\n\n"
            "Продавец получил уведомление."
        )

    elif query.data == "complete":
        await query.edit_message_text(
            "✅ Заказ выполнен!\n\n"
            "Товар выдан покупателю."
        )

    elif query.data == "cancel":
        await query.edit_message_text(
            "❌ Заказ отменён."
        )


async def health(request):
    return web.Response(text="VexMartBot is alive! 🏪")


async def main():
    if not TOKEN:
        raise RuntimeError("Не указан BOT_TOKEN")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    web_app = web.Application()
    web_app.router.add_get("/", health)

    runner = web.AppRunner(web_app)
    await runner.setup()

    port = int(os.getenv("PORT", "10000"))

    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    print(f"VexMart запущен на порту {port}!")

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
