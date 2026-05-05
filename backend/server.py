from fastapi import FastAPI, APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import asyncio
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# ── WebSocket connections ──
active_websockets: Dict[int, WebSocket] = {}


async def broadcast_progress(chat_id: int, step: str, step_num: int, total: int, status: str, detail: str):
    """Send progress update to connected frontend WebSocket clients."""
    payload = {
        "type": "progress",
        "chat_id": chat_id,
        "step": step,
        "step_num": step_num,
        "total": total,
        "status": status,
        "detail": detail,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    ws = active_websockets.get(chat_id)
    if ws:
        try:
            await ws.send_json(payload)
        except Exception:
            pass


# ── Progress callback for automation ──
async def make_progress_callback(chat_id: int):
    async def cb(step_name, step_num, total_steps, status, detail):
        await broadcast_progress(chat_id, step_name, step_num, total_steps, status, detail)
    return cb


# ── Telegram Bot Handlers ──
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


def esc(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2"""
    specials = r'_*[]()~`>#+-=|{}.!'
    for c in specials:
        text = text.replace(c, f'\\{c}')
    return text


def build_success_message(email: str, checkout_url: str) -> str:
    """Build the final success message with inline open button."""
    return (
        r"✅ *Checkout Generated Successfully\!*" + "\n\n"
        f"📧 *Email:* `{esc(email)}`" + "\n\n"
        f"🔗 *Checkout Link:*" + "\n"
        f"`{esc(checkout_url)}`" + "\n\n"
        r"👇 Tap below to open checkout\!"
    )


async def gen_checkout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    # Send initial live progress message
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

            # Store in MongoDB
            await db.checkouts.insert_one({
                "chat_id": chat_id,
                "email": email,
                "checkout_url": checkout,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "success"
            })

            # Build reply with inline button
            reply = build_success_message(email, checkout)
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


# ── Bot lifecycle ──
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


# ── FastAPI App ──
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(start_telegram_bot())
    yield
    await stop_telegram_bot()
    client.close()


app = FastAPI(lifespan=lifespan)
api_router = APIRouter(prefix="/api")


# ── API Endpoints ──
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


# ── WebSocket Endpoint for Live Progress ──
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    chat_id = None
    try:
        # First message must identify the client
        data = await ws.receive_text()
        try:
            payload = json.loads(data)
            chat_id = payload.get("chat_id")
        except json.JSONDecodeError:
            chat_id = int(data)

        if chat_id:
            active_websockets[chat_id] = ws
            await ws.send_json({"type": "connected", "chat_id": chat_id})
            logger.info(f"WebSocket connected for chat_id: {chat_id}")
        else:
            await ws.send_json({"type": "error", "message": "chat_id required"})
            await ws.close()
            return

        # Keep connection alive
        while True:
            data = await ws.receive_text()
            # Echo heartbeat
            if data == "ping":
                await ws.send_text("pong")

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for chat_id: {chat_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        if chat_id and chat_id in active_websockets:
            del active_websockets[chat_id]
