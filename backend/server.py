from fastapi import FastAPI, APIRouter
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import asyncio
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Annotated
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from bson import ObjectId
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from automation import generate_rosebud_checkout

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# MongoDB
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')

# --- MongoDB Helpers ---
def _doc_to_checkout(doc: dict) -> dict:
    doc['id'] = str(doc.pop('_id', ''))
    return doc


# --- Telegram Bot Handlers ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *Rosebud Checkout Bot*\n\n"
        "Generate a Stripe checkout link for Rosebud\\.ai's lowest plan instantly\\!\n\n"
        "📌 *Commands:*\n"
        "➤ /gen\\_checkout — Auto signup \\+ get checkout link\n"
        "➤ /start — Show this message\n\n"
        "_Type /gen\\_checkout to begin\\._"
    )
    await update.message.reply_text(text, parse_mode="MarkdownV2")


async def gen_checkout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = await update.message.reply_text(
        "⏳ *Processing your request\\.\\.\\.*\n\n"
        "1\\. Generating temp email ✅\n"
        "2\\. Signing up on Rosebud\\.ai ⏳\n"
        "3\\. Waiting for verification email ⏳\n"
        "4\\. Extracting checkout link ⏳\n\n"
        "_This may take 60\\-120 seconds\\._",
        parse_mode="MarkdownV2"
    )

    try:
        result = await generate_rosebud_checkout()

        if result["success"]:
            email = result["email"]
            checkout = result["checkout_url"]

            # Store in MongoDB
            await db.checkouts.insert_one({
                "chat_id": chat_id,
                "email": email,
                "checkout_url": checkout,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "success"
            })

            # Escape for MarkdownV2
            def esc(text):
                specials = r'_*[]()~`>#+-=|{}.!'
                for c in specials:
                    text = text.replace(c, f'\\{c}')
                return text

            reply = (
                "✅ *Checkout Generated Successfully\\!*\n\n"
                f"📧 *Email:* `{esc(email)}`\n\n"
                f"🔗 *Checkout Link:*\n"
                f"`{esc(checkout)}`\n\n"
                "👆 Copy the link and complete your purchase\\!\n"
                "_Email is temp — link expires soon\\._"
            )
            await msg.edit_text(reply, parse_mode="MarkdownV2")

        else:
            error = result.get("error", "Unknown error")
            email = result.get("email", "N/A")
            def esc(text):
                specials = r'_*[]()~`>#+-=|{}.!'
                for c in specials:
                    text = text.replace(c, f'\\{c}')
                return text
            await msg.edit_text(
                f"❌ *Failed to generate checkout*\n\n"
                f"📧 Email used: `{esc(email)}`\n"
                f"⚠️ Error: {esc(error)}\n\n"
                "_Please try again with /gen\\_checkout_",
                parse_mode="MarkdownV2"
            )

    except Exception as e:
        logger.error(f"gen_checkout error: {e}")
        def esc(text):
            specials = r'_*[]()~`>#+-=|{}.!'
            for c in specials:
                text = text.replace(c, f'\\{c}')
            return text
        await msg.edit_text(
            f"❌ *Error occurred*\n\n`{esc(str(e)[:200])}`\n\n_Try /gen\\_checkout again_",
            parse_mode="MarkdownV2"
        )


# --- Bot lifecycle ---
telegram_app: Optional[Application] = None


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


# --- FastAPI App ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(start_telegram_bot())
    yield
    await stop_telegram_bot()
    client.close()


app = FastAPI(lifespan=lifespan)
api_router = APIRouter(prefix="/api")


# --- API Endpoints ---
@api_router.get("/")
async def root():
    return {"message": "Rosebud Checkout Bot API", "bot_active": telegram_app is not None}


@api_router.get("/status")
async def get_status():
    total = await db.checkouts.count_documents({})
    success = await db.checkouts.count_documents({"status": "success"})
    bot_running = False
    if telegram_app and telegram_app.updater:
        bot_running = telegram_app.updater.running
    return {
        "bot_active": bot_running,
        "total_checkouts": total,
        "successful_checkouts": success
    }


@api_router.get("/checkouts")
async def get_checkouts():
    docs = await db.checkouts.find({}, {"_id": 0}).sort("created_at", -1).limit(20).to_list(20)
    return docs


@api_router.post("/gen_checkout")
async def api_gen_checkout():
    """Trigger checkout generation via HTTP (for testing)"""
    result = await generate_rosebud_checkout()
    if result["success"]:
        await db.checkouts.insert_one({
            "chat_id": None,
            "email": result["email"],
            "checkout_url": result["checkout_url"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "success"
        })
    return result


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)
