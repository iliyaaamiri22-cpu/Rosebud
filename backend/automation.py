"""
Playwright automation for rosebud.ai signup & Stripe checkout extraction.
"""
import os
import asyncio
import logging
from urllib.parse import urlparse

# Allow Playwright to auto-discover browsers from default install path
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "")
from playwright.async_api import async_playwright
from tempmail import create_email, wait_for_magic_link

logger = logging.getLogger(__name__)

PLAN_PAGE = "https://rosebud.ai/profile/my-plan"

# Global semaphore to limit concurrent browser instances (memory safety)
_BROWSER_SEMAPHORE = asyncio.Semaphore(3)


def _is_stripe_url(url: str) -> bool:
    """Check if URL is a real Stripe checkout URL by examining HOSTNAME (not just any substring)."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        return hostname == "checkout.stripe.com" and ("cs_live_" in url or "cs_test_" in url)
    except Exception:
        return False


async def generate_rosebud_checkout(progress_callback=None) -> dict:
    """
    Main entry point.
    progress_callback: async callable(step_name, step_num, total_steps, status, detail)
    Returns: {"success": True, "email": ..., "checkout_url": ...}
         or: {"success": False, "email": ..., "error": ...}
    """
    async with _BROWSER_SEMAPHORE:
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
                    if _is_stripe_url(url):
                        stripe_checkout_urls.append(url)
                        logger.info(f"[AUTO] Stripe URL captured (request): {url}")

                def _on_response(resp):
                    url = resp.url
                    if _is_stripe_url(url) and url not in stripe_checkout_urls:
                        stripe_checkout_urls.append(url)
                        logger.info(f"[AUTO] Stripe URL captured (response): {url}")

                def _on_popup(popup_page):
                    url = popup_page.url
                    if _is_stripe_url(url) and url not in stripe_checkout_urls:
                        stripe_checkout_urls.append(url)
                        logger.info(f"[AUTO] Stripe URL captured (popup): {url}")

                def _on_framenavigated(frame):
                    url = frame.url
                    if _is_stripe_url(url) and url not in stripe_checkout_urls:
                        stripe_checkout_urls.append(url)
                        logger.info(f"[AUTO] Stripe URL captured (frame nav): {url}")

                page.on("request", _on_request)
                page.on("response", _on_response)
                page.on("popup", _on_popup)
                page.on("framenavigated", _on_framenavigated)

                # Step 2: Navigate to rosebud.ai
                await report("navigate", "running", "Loading rosebud.ai...")
                logger.info("[AUTO] Loading rosebud.ai")
                await page.goto("https://rosebud.ai", wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(1500)
                await report("navigate", "done")

                # Step 3: Open Sign In modal
                await report("open_signin", "running", "Opening sign-in modal...")
                logger.info("[AUTO] Opening sign-in modal")
                await page.click("text=SIGN IN", timeout=10000)
                await page.wait_for_timeout(800)
                await report("open_signin", "done")

                # Step 4: Accept ToS if present
                checkbox = await page.query_selector("input[type=checkbox]")
                if checkbox:
                    await checkbox.check()
                    await page.wait_for_timeout(200)

                # Step 5: Submit email for magic link
                await report("submit_email", "running", f"Submitting email: {email}...")
                logger.info(f"[AUTO] Submitting email: {email}")
                inp = await page.wait_for_selector("input[type=email]", timeout=10000)
                await inp.fill(email)
                await page.wait_for_timeout(200)
                await page.click("text=Send Email Link", timeout=10000)
                await page.wait_for_timeout(1000)
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
                await page.wait_for_timeout(2500)
                logger.info(f"[AUTO] Post-login URL: {page.url}")
                await report("login", "done")

                # Step 8: Navigate to pricing page
                await report("goto_pricing", "running", "Navigating to pricing page...")
                logger.info(f"[AUTO] Going to plan page: {PLAN_PAGE}")
                await page.goto(PLAN_PAGE, wait_until="networkidle", timeout=20000)
                await page.wait_for_timeout(3500)
                logger.info(f"[AUTO] Plan page URL: {page.url}")
                await report("goto_pricing", "done")

                # Step 9: Find & click first Upgrade / Subscribe / Get Started button
                await report("click_upgrade", "running", "Finding upgrade button...")

                # Multiple possible button texts Rosebud might use
                BUY_KEYWORDS = ["upgrade", "subscribe", "get started", "start free", "start", "buy", "purchase", "choose", "select"]

                # ── Strategy 1: Search buttons by text ──
                upgrade_buttons = []
                for btn in await page.query_selector_all("button"):
                    try:
                        text = (await btn.inner_text()).strip().lower()
                        if any(kw in text for kw in BUY_KEYWORDS):
                            upgrade_buttons.append((btn, text))
                    except Exception:
                        pass

                logger.info(f"[AUTO] Found {len(upgrade_buttons)} matching BUTTONS: {[t for _,t in upgrade_buttons]}")

                # ── Strategy 2: If no buttons, search <a> links ──
                if not upgrade_buttons:
                    for link in await page.query_selector_all("a"):
                        try:
                            text = (await link.inner_text()).strip().lower()
                            if any(kw in text for kw in BUY_KEYWORDS):
                                upgrade_buttons.append((link, text))
                        except Exception:
                            pass
                    logger.info(f"[AUTO] Found {len(upgrade_buttons)} matching LINKS: {[t for _,t in upgrade_buttons]}")

                # ── Strategy 3: Still nothing? Search page HTML for Stripe payment intent ──
                if not upgrade_buttons:
                    page_html = await page.content()
                    page_lower = page_html.lower()
                    if "checkout" in page_lower or "stripe" in page_lower:
                        # Try to find any clickable element near pricing
                        for el in await page.query_selector_all("[role='button'], .btn, .button, [class*='upgrade'], [class*='subscribe'], [class*='pricing']"):
                            try:
                                if await el.is_visible():
                                    upgrade_buttons.append((el, "fallback-clickable"))
                            except Exception:
                                pass
                        logger.info(f"[AUTO] Found {len(upgrade_buttons)} fallback clickable elements")

                if not upgrade_buttons:
                    # Save page HTML + screenshot for debugging
                    try:
                        await page.screenshot(path="/tmp/rosebud_pricing_debug.png")
                        html_snippet = (await page.content())[:2000]
                        logger.error(f"[AUTO] No button found. HTML snippet: {html_snippet}")
                    except Exception:
                        pass
                    await report("click_upgrade", "failed", "No Upgrade button found")
                    return {
                        "success": False, "email": email,
                        "error": "No 'Upgrade' button found on pricing page",
                    }

                target, btn_text = upgrade_buttons[0]
                await target.click(force=True)
                logger.info(f"[AUTO] Clicked '{btn_text}' button, waiting for Stripe…")

                # ── MULTI-LAYER STRIPE CHECKOUT CAPTURE ──

                # Layer 1: Wait for same-tab navigation
                try:
                    await page.wait_for_url(
                        lambda url: _is_stripe_url(url),
                        timeout=20000
                    )
                    nav_url = page.url
                    if nav_url and _is_stripe_url(nav_url) and nav_url not in stripe_checkout_urls:
                        stripe_checkout_urls.append(nav_url)
                        logger.info(f"[AUTO] Stripe URL captured (navigation): {nav_url}")
                except Exception:
                    pass

                # Layer 2: Wait for popup
                if not stripe_checkout_urls:
                    try:
                        popup = await page.wait_for_event("popup", timeout=15000)
                        popup_url = popup.url
                        if _is_stripe_url(popup_url) and popup_url not in stripe_checkout_urls:
                            stripe_checkout_urls.append(popup_url)
                            logger.info(f"[AUTO] Stripe URL captured (popup): {popup_url}")
                    except Exception:
                        pass

                # Layer 3: Wait for network idle then aggressive polling
                try:
                    await page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    pass

                # Layer 4: Poll page.url, window.location.href, all pages, all frames
                for i in range(40):  # 20 seconds total
                    if stripe_checkout_urls:
                        break

                    # Check main page URL
                    current_url = page.url
                    if _is_stripe_url(current_url) and current_url not in stripe_checkout_urls:
                        stripe_checkout_urls.append(current_url)
                        logger.info(f"[AUTO] Stripe URL captured (poll page.url): {current_url}")
                        break

                    # Check window.location.href (includes hash fragment)
                    try:
                        loc_href = await page.evaluate("window.location.href")
                        if _is_stripe_url(loc_href) and loc_href not in stripe_checkout_urls:
                            stripe_checkout_urls.append(loc_href)
                            logger.info(f"[AUTO] Stripe URL captured (window.location): {loc_href}")
                            break
                    except Exception:
                        pass

                    # Check all pages in browser context
                    try:
                        all_pages = context.pages
                        for p in all_pages:
                            p_url = p.url
                            if _is_stripe_url(p_url) and p_url not in stripe_checkout_urls:
                                stripe_checkout_urls.append(p_url)
                                logger.info(f"[AUTO] Stripe URL captured (other page): {p_url}")
                                break
                    except Exception:
                        pass

                    # Check all frames
                    try:
                        for frame in page.frames:
                            f_url = frame.url
                            if _is_stripe_url(f_url) and f_url not in stripe_checkout_urls:
                                stripe_checkout_urls.append(f_url)
                                logger.info(f"[AUTO] Stripe URL captured (frame): {f_url}")
                                break
                    except Exception:
                        pass

                    await asyncio.sleep(0.5)

                # Layer 5: If still nothing, try checking if any page navigated to stripe
                if not stripe_checkout_urls:
                    try:
                        all_pages = context.pages
                        for p in all_pages:
                            p_url = p.url
                            if _is_stripe_url(p_url) and p_url not in stripe_checkout_urls:
                                stripe_checkout_urls.append(p_url)
                                logger.info(f"[AUTO] Stripe URL captured (final page scan): {p_url}")
                    except Exception:
                        pass

                # Layer 6: Check if main page URL has stripe (last resort)
                if not stripe_checkout_urls:
                    final_url = page.url
                    if _is_stripe_url(final_url):
                        stripe_checkout_urls.append(final_url)
                        logger.info(f"[AUTO] Stripe URL captured (final page.url): {final_url}")

                if not stripe_checkout_urls:
                    await report("click_upgrade", "failed", "Stripe checkout URL not captured")
                    return {
                        "success": False, "email": email,
                        "error": "Stripe checkout URL not captured after clicking Upgrade",
                    }

                # Return the longest URL (most complete, likely has all params + hash)
                logger.info(f"[AUTO] All captured Stripe URLs ({len(stripe_checkout_urls)}): {stripe_checkout_urls}")
                checkout_url = max(stripe_checkout_urls, key=len)
                await report("click_upgrade", "done", f"Checkout URL captured: {checkout_url[:60]}...")
                logger.info(f"[AUTO] Done. Checkout URL: {checkout_url}")
                return {"success": True, "email": email, "checkout_url": checkout_url}

            except Exception as e:
                logger.exception(f"[AUTO] Unhandled error: {e}")
                return {"success": False, "email": email, "error": str(e)}
            finally:
                await browser.close()


async def generate_rosebud_checkouts(count: int = 1, progress_callback=None) -> list[dict]:
    """
    Generate multiple Stripe checkout links in PARALLEL (workers).
    Max 3 browsers run simultaneously (memory safety via semaphore).
    Returns a list of result dicts.
    """
    tasks = [generate_rosebud_checkout(progress_callback) for _ in range(count)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    processed = []
    for r in results:
        if isinstance(r, Exception):
            processed.append({"success": False, "email": "N/A", "error": str(r)})
        else:
            processed.append(r)
    return processed
