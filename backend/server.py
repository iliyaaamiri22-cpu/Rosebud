from fastapi import FastAPI
from dotenv import load_dotenv
import os
import logging
import asyncio
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes
from automation import generate_rosebud_checkout

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')


def esc(text: str) -> str:
    specials = r'_*[]()~`>#+-=|{}.!'
    for c in specials:
        text = text.replace(c, f'\\{c}')
    return text


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        r"🤖 *Rosebud Checkout Bot*" + "\n\n"
        r"Generate a Stripe checkout link for Rosebud\.ai's lowest plan instantly\!" + "\n\n"
        r"📌 *Commands:*" + "\n"
        r"➤ /gen\_checkout — Auto signup \+ get checkout link" + "\n"
        r"➤ /start — Show this message" + "\n\n"
        r"_Type /gen\_checkout to begin\._"
    )
    await update.message.reply_text(text, parse_mode="MarkdownV2")


async def gen_checkout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        r"⏳ *Starting checkout generation\.\.\.*" + "\n\n"
        r"1️⃣  Creating temp email\.\.\." + "\n"
        r"2️⃣  Signing up on Rosebud\.ai" + "\n"
        r"3️⃣  Waiting for verification email" + "\n"
        r"4️⃣  Extracting checkout link" + "\n\n"
        r"_This may take 60\-120 seconds\._",
        parse_mode="MarkdownV2"
    )

    progress_steps = {
        "create_email": r"1️⃣  Creating temp email...",
        "navigate": r"2️⃣  Loading Rosebud\.ai...",
        "open_signin": r"2️⃣  Opening sign\-in modal...",
        "submit_email": r"2️⃣  Submitting email...",
        "wait_email": r"3️⃣  Waiting for verification email...",
        "login": r"3️⃣  Logging in with magic link...",
        "goto_pricing": r"4️⃣  Navigating to pricing...",
        "click_upgrade": r"4️⃣  Extracting checkout link...",
    }

    async def update_progress(step_name, step_num, total_steps, status, detail):
        step_label = progress_steps.get(step_name, step_name.replace("_", " ").title())
        if status == "done":
            step_label = step_label.replace("...", " ✅")
        elif status == "failed":
            step_label = step_label.replace("...", " ❌")
        else:
            step_label = f"⏳ {step_label}"

        lines = [
            r"⏳ *Processing your request\.\.\.*" + "\n",
            step_label + "\n",
            f"_Step {step_num}/{total_steps} — {esc(detail[:80]) if detail else 'in progress'}_",
        ]
        try:
            await msg.edit_text("".join(lines), parse_mode="MarkdownV2")
        except Exception:
            pass

    try:
        result = await generate_rosebud_checkout(progress_callback=update_progress)

        if result["success"]:
            email = result["email"]
            checkout = result["checkout_url"]

            reply = (
                r"✅ *Checkout Generated Successfully\!*" + "\n\n"
                f"📧 *Email:* `{esc(email)}`" + "\n\n"
                f"🔗 *Checkout Link:*" + "\n"
                f"`{esc(checkout)}`" + "\n\n"
                r"👇 Tap below to open checkout\!"
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Open Checkout", url=checkout)],
                [InlineKeyboardButton("🔄 Generate Another", callback_data="gen_checkout")],
            ])
            await msg.edit_text(reply, parse_mode="MarkdownV2", reply_markup=keyboard)
        else:
            error = result.get("error", "Unknown error")
            email = result.get("email", "N/A")
            await msg.edit_text(
                r"❌ *Failed to generate checkout*" + "\n\n"
                f"📧 Email used: `{esc(email)}`" + "\n"
                f"⚠️ Error: {esc(error)}" + "\n\n"
                r"_Please try again with /gen\_checkout_",
                parse_mode="MarkdownV2"
            )
    except Exception as e:
        logger.error(f"gen_checkout error: {e}")
        await msg.edit_text(
            r"❌ *Error occurred*" + "\n\n"
            f"`{esc(str(e)[:200])}`" + "\n\n"
            r"_Try /gen\_checkout again_",
            parse_mode="MarkdownV2"
        )


telegram_app = None

async def start_telegram_bot():
    global telegram_app
    if not TELEGRAM_TOKEN:
        logger.warning("No TELEGRAM_TOKEN set, bot will not start")
        return
    try:
        telegram_app = Application.builder().token(TELEGRAM_TOKEN).build()
        telegram_app.add_handler(CommandHandler("start", start_cmd))
        telegram_app.add_handler(CommandHandler("gen_checkout", gen_checkout_cmd))
        await telegram_app.initialize()
        await telegram_app.start()
        await telegram_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("Telegram bot started polling")
    except Exception as e:
        logger.error(f"Failed to start Telegram bot: {e}")


async def stop_telegram_bot():
    global telegram_app
    if telegram_app:
        try:
            await telegram_app.updater.stop()
            await telegram_app.stop()
            await telegram_app.shutdown()
        except Exception as e:
            logger.error(f"Error stopping bot: {e}")


from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(start_telegram_bot())
    yield
    await stop_telegram_bot()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    return {"message": "Rosebud Checkout Bot API", "bot_active": telegram_app is not None}
