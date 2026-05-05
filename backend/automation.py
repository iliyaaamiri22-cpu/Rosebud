"""
Playwright automation for rosebud.ai signup & Stripe checkout extraction.
"""
import os
import asyncio
import logging

# Allow Playwright to auto-discover browsers from default install path
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "")
from playwright.async_api import async_playwright
from tempmail import create_email, wait_for_magic_link

logger = logging.getLogger(__name__)

PLAN_PAGE = "https://rosebud.ai/profile/my-plan"


async def generate_rosebud_checkout(progress_callback=None) -> dict:
    """
    Main entry point.
    progress_callback: async callable(step_name, step_num, total_steps, status, detail)
    Returns: {"success": True, "email": ..., "checkout_url": ...}
         or: {"success": False, "email": ..., "error": ...}
    """
    total_steps = 9
    current_step = 0

    async def report(step_name, status="running", detail=""):
        nonlocal current_step
        current_step += 1
        if progress_callback:
            try:
                await progress_callback(step_name, min(current_step, total_steps), total_steps, status, detail)
            except Exception:
                pass

    # Step 1: Create temp email
    await report("create_email", "running", "Creating temporary email...")
    try:
        email, token = await create_email()
    except Exception as e:
        await report("create_email", "failed", str(e))
        return {"success": False, "email": "N/A", "error": f"Temp email error: {e}"}
    await report("create_email", "done", f"Email: {email}")

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

            stripe_checkout_urls: list[str] = []

            def _on_request(req):
                url = req.url
                if "checkout.stripe.com/c/pay/" in url:
                    stripe_checkout_urls.append(url)
                    logger.info(f"[AUTO] Stripe URL captured (request): {url}")

            def _on_response(resp):
                url = resp.url
                if "checkout.stripe.com/c/pay/" in url and url not in stripe_checkout_urls:
                    stripe_checkout_urls.append(url)
                    logger.info(f"[AUTO] Stripe URL captured (response): {url}")

            def _on_popup(popup_page):
                url = popup_page.url
                if "checkout.stripe.com/c/pay/" in url and url not in stripe_checkout_urls:
                    stripe_checkout_urls.append(url)
                    logger.info(f"[AUTO] Stripe URL captured (popup): {url}")

            page.on("request", _on_request)
            page.on("response", _on_response)
            page.on("popup", _on_popup)

            # Step 2: Navigate to rosebud.ai
            await report("navigate", "running", "Loading rosebud.ai...")
            logger.info("[AUTO] Loading rosebud.ai")
            await page.goto("https://rosebud.ai", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            await report("navigate", "done")

            # Step 3: Open Sign In modal
            await report("open_signin", "running", "Opening sign-in modal...")
            logger.info("[AUTO] Opening sign-in modal")
            await page.click("text=SIGN IN", timeout=10000)
            await page.wait_for_timeout(1500)
            await report("open_signin", "done")

            # Step 4: Accept ToS if present
            checkbox = await page.query_selector("input[type=checkbox]")
            if checkbox:
                await checkbox.check()
                await page.wait_for_timeout(300)

            # Step 5: Submit email for magic link
            await report("submit_email", "running", f"Submitting email: {email}...")
            logger.info(f"[AUTO] Submitting email: {email}")
            inp = await page.wait_for_selector("input[type=email]", timeout=10000)
            await inp.fill(email)
            await page.wait_for_timeout(400)
            await page.click("text=Send Email Link", timeout=10000)
            await page.wait_for_timeout(2000)
            await report("submit_email", "done", "Magic link requested")
            logger.info("[AUTO] Magic link requested. Polling inbox…")

            # Step 6: Poll MailTM for Firebase magic link
            await report("wait_email", "running", "Waiting for verification email...")
            magic_link = await wait_for_magic_link(token, timeout=120)
            if not magic_link:
                await report("wait_email", "failed", "Magic link not received within 120s")
                return {
                    "success": False, "email": email,
                    "error": "Magic link not received within 120 s",
                }
            await report("wait_email", "done", "Magic link received!")
            logger.info("[AUTO] Magic link received, completing login…")

            # Step 7: Follow magic link → completes Firebase auth
            await report("login", "running", "Logging in with magic link...")
            await page.goto(magic_link, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(4000)
            logger.info(f"[AUTO] Post-login URL: {page.url}")
            await report("login", "done")

            # Step 8: Navigate to pricing page
            await report("goto_pricing", "running", "Navigating to pricing page...")
            logger.info(f"[AUTO] Going to plan page: {PLAN_PAGE}")
            await page.goto(PLAN_PAGE, wait_until="networkidle", timeout=20000)
            await page.wait_for_timeout(5000)
            logger.info(f"[AUTO] Plan page URL: {page.url}")
            await report("goto_pricing", "done")

            # Step 9: Find & click first "Upgrade" button
            await report("click_upgrade", "running", "Clicking upgrade button...")
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
                await report("click_upgrade", "failed", "No Upgrade button found")
                return {
                    "success": False, "email": email,
                    "error": "No 'Upgrade' button found on pricing page",
                }

            await upgrade_buttons[0].click(force=True)
            logger.info("[AUTO] Clicked first Upgrade button, waiting for Stripe…")

            # Try to detect Stripe checkout
            try:
                await page.wait_for_url(
                    lambda url: "checkout.stripe.com/c/pay/" in url,
                    timeout=15000
                )
                nav_url = page.url
                if nav_url not in stripe_checkout_urls:
                    stripe_checkout_urls.append(nav_url)
                    logger.info(f"[AUTO] Stripe URL captured (navigation): {nav_url}")
            except Exception:
                pass

            if not stripe_checkout_urls:
                try:
                    popup = await page.wait_for_event("popup", timeout=10000)
                    popup_url = popup.url
                    if "checkout.stripe.com/c/pay/" in popup_url and popup_url not in stripe_checkout_urls:
                        stripe_checkout_urls.append(popup_url)
                        logger.info(f"[AUTO] Stripe URL captured (popup): {popup_url}")
                except Exception:
                    pass

            for _ in range(30):
                if stripe_checkout_urls:
                    break
                await asyncio.sleep(0.5)

            if not stripe_checkout_urls and "checkout.stripe.com/c/pay/" in page.url:
                stripe_checkout_urls.append(page.url)
                logger.info(f"[AUTO] Stripe URL captured (last-resort page.url): {page.url}")

            if not stripe_checkout_urls:
                await report("click_upgrade", "failed", "Stripe checkout URL not captured")
                return {
                    "success": False, "email": email,
                    "error": "Stripe checkout URL not captured after clicking Upgrade",
                }

            checkout_url = stripe_checkout_urls[0]
            await report("click_upgrade", "done", f"Checkout URL captured: {checkout_url[:60]}...")
            logger.info(f"[AUTO] Done. Checkout URL: {checkout_url}")
            return {"success": True, "email": email, "checkout_url": checkout_url}

        except Exception as e:
            logger.exception(f"[AUTO] Unhandled error: {e}")
            return {"success": False, "email": email, "error": str(e)}
        finally:
            await browser.close()
