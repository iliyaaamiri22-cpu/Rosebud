"""
Playwright automation for rosebud.ai signup & Stripe checkout extraction.

Tested Flow (verified working):
  1. Create MailTM temp email
  2. Sign up on rosebud.ai using email magic link (Firebase auth)
  3. Navigate to https://rosebud.ai/profile/my-plan
  4. Click first "Upgrade" button (Indie Dev - $15/month, lowest paid plan)
  5. Capture Stripe checkout URL via request listener
"""
import asyncio
import logging
from playwright.async_api import async_playwright
from tempmail import create_email, wait_for_magic_link

logger = logging.getLogger(__name__)

PLAN_PAGE = "https://rosebud.ai/profile/my-plan"


async def generate_rosebud_checkout() -> dict:
    """
    Main entry point.
    Returns: {"success": True, "email": ..., "checkout_url": ...}
         or: {"success": False, "email": ..., "error": ...}
    """
    # Step 1: Create temp email
    try:
        email, token = await create_email()
    except Exception as e:
        return {"success": False, "email": "N/A", "error": f"Temp email error: {e}"}

    logger.info(f"[AUTO] Email: {email}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )

        try:
            page = await context.new_page()

            # ── Capture Stripe checkout URL ─────────────────────────────────
            # checkout.stripe.com/c/pay/ is the actual checkout page URL
            stripe_checkout_urls: list[str] = []

            def _on_request(req):
                url = req.url
                if "checkout.stripe.com/c/pay/" in url:
                    stripe_checkout_urls.append(url)
                    logger.info(f"[AUTO] Stripe URL captured: {url[:80]}")

            page.on("request", _on_request)

            # ── Step 2: Navigate to rosebud.ai ──────────────────────────────
            logger.info("[AUTO] Loading rosebud.ai")
            await page.goto("https://rosebud.ai", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)

            # ── Step 3: Open Sign In modal ──────────────────────────────────
            logger.info("[AUTO] Opening sign-in modal")
            await page.click("text=SIGN IN", timeout=10000)
            await page.wait_for_timeout(1500)

            # ── Step 4: Accept ToS if present ──────────────────────────────
            checkbox = await page.query_selector("input[type=checkbox]")
            if checkbox:
                await checkbox.check()
                await page.wait_for_timeout(300)

            # ── Step 5: Submit email for magic link ─────────────────────────
            logger.info(f"[AUTO] Submitting email: {email}")
            inp = await page.wait_for_selector("input[type=email]", timeout=10000)
            await inp.fill(email)
            await page.wait_for_timeout(400)
            await page.click("text=Send Email Link", timeout=10000)
            await page.wait_for_timeout(2000)
            logger.info("[AUTO] Magic link requested. Polling inbox…")

            # ── Step 6: Poll MailTM for Firebase magic link ─────────────────
            magic_link = await wait_for_magic_link(token, timeout=120)
            if not magic_link:
                return {
                    "success": False, "email": email,
                    "error": "Magic link not received within 120 s",
                }

            logger.info("[AUTO] Magic link received, completing login…")

            # ── Step 7: Follow magic link → completes Firebase auth ─────────
            await page.goto(magic_link, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(4000)
            logger.info(f"[AUTO] Post-login URL: {page.url}")

            # ── Step 8: Navigate to pricing page ────────────────────────────
            logger.info(f"[AUTO] Going to plan page: {PLAN_PAGE}")
            await page.goto(PLAN_PAGE, wait_until="networkidle", timeout=20000)
            await page.wait_for_timeout(5000)  # allow React to render plan cards
            logger.info(f"[AUTO] Plan page URL: {page.url}")

            # ── Step 9: Find & click first "Upgrade" button ─────────────────
            upgrade_buttons = []
            for btn in await page.query_selector_all("button"):
                try:
                    text = (await btn.inner_text()).strip().lower()
                    if text == "upgrade":
                        upgrade_buttons.append(btn)
                except Exception:
                    pass

            logger.info(f"[AUTO] Found {len(upgrade_buttons)} 'Upgrade' buttons")

            if not upgrade_buttons:
                return {
                    "success": False, "email": email,
                    "error": "No 'Upgrade' button found on pricing page",
                }

            # Click first = Indie Dev ($15/month) – the lowest paid plan
            await upgrade_buttons[0].click(force=True)
            logger.info("[AUTO] Clicked first Upgrade button, waiting for Stripe…")

            # Wait up to 10 seconds for Stripe checkout URL to be captured
            for _ in range(20):
                if stripe_checkout_urls:
                    break
                await asyncio.sleep(0.5)

            # Fallback: check if same-tab navigated to Stripe
            if not stripe_checkout_urls and "checkout.stripe.com" in page.url:
                stripe_checkout_urls.append(page.url)

            if not stripe_checkout_urls:
                return {
                    "success": False, "email": email,
                    "error": "Stripe checkout URL not captured after clicking Upgrade",
                }

            checkout_url = stripe_checkout_urls[0]
            logger.info(f"[AUTO] Done. Checkout URL: {checkout_url[:80]}…")
            return {"success": True, "email": email, "checkout_url": checkout_url}

        except Exception as e:
            logger.exception(f"[AUTO] Unhandled error: {e}")
            return {"success": False, "email": email, "error": str(e)}
        finally:
            await browser.close()
