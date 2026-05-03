"""Playwright automation for rosebud.ai signup & Stripe checkout extraction"""
import asyncio
import re
import logging
from typing import Optional, Tuple
from playwright.async_api import async_playwright, Page, BrowserContext
from tempmail import generate_email, wait_for_magic_link

logger = logging.getLogger(__name__)


async def generate_rosebud_checkout() -> dict:
    """
    Full automation flow:
    1. Generate temp email
    2. Sign up on rosebud.ai via email magic link
    3. Navigate to pricing page
    4. Capture lowest plan Stripe checkout URL
    """
    email = generate_email()
    logger.info(f"[BOT] Starting automation with email: {email}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
        )

        try:
            page = await context.new_page()

            # Step 1: Go to rosebud.ai
            logger.info("[BOT] Navigating to rosebud.ai")
            await page.goto("https://rosebud.ai", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)

            # Step 2: Click SIGN IN
            logger.info("[BOT] Clicking SIGN IN")
            await page.click("text=SIGN IN", timeout=10000)
            await page.wait_for_timeout(1500)

            # Step 3: Accept ToS checkbox
            try:
                checkbox = await page.query_selector("input[type=checkbox]")
                if checkbox:
                    await checkbox.check()
                    await page.wait_for_timeout(500)
            except Exception:
                pass

            # Step 4: Enter temp email
            logger.info(f"[BOT] Entering email: {email}")
            email_input = await page.wait_for_selector("input[type=email]", timeout=10000)
            await email_input.fill(email)
            await page.wait_for_timeout(500)

            # Step 5: Click Send Email Link
            await page.click("text=Send Email Link", timeout=10000)
            logger.info("[BOT] Magic link email sent, waiting for inbox...")
            await page.wait_for_timeout(2000)

            # Step 6: Wait for magic link in 1secmail inbox
            magic_link = await wait_for_magic_link(email, timeout=120)
            if not magic_link:
                return {"success": False, "email": email, "error": "Magic link email not received (timeout 120s)"}

            logger.info("[BOT] Got magic link, navigating...")

            # Step 7: Navigate to magic link to complete login
            await page.goto(magic_link, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(4000)

            # Check if we're logged in
            current_url = page.url
            logger.info(f"[BOT] After magic link, URL: {current_url}")

            # Step 8: Navigate to pricing/my-plan page
            checkout_url = await _find_checkout_url(page, context)

            if not checkout_url:
                return {
                    "success": False,
                    "email": email,
                    "error": "Could not find Stripe checkout URL on pricing page"
                }

            logger.info(f"[BOT] Checkout URL found: {checkout_url[:80]}...")
            return {
                "success": True,
                "email": email,
                "checkout_url": checkout_url
            }

        except Exception as e:
            logger.error(f"[BOT] Automation error: {e}")
            return {"success": False, "email": email, "error": str(e)}
        finally:
            await browser.close()


async def _find_checkout_url(page: Page, context: BrowserContext) -> Optional[str]:
    """Navigate to pricing and capture Stripe checkout URL"""
    
    # Try known pricing page URLs
    pricing_urls = [
        "https://rosebud.ai/my-plan",
        "https://rosebud.ai/settings",
        "https://rosebud.ai/upgrade",
        "https://rosebud.ai/subscribe",
        "https://rosebud.ai/pricing",
    ]

    stripe_url_holder = []

    # Set up request interception to capture Stripe URLs
    async def on_request(request):
        url = request.url
        if "checkout.stripe.com" in url or ("stripe.com" in url and "checkout" in url):
            stripe_url_holder.append(url)

    # Also intercept responses for checkout session URLs
    async def on_response(response):
        url = response.url
        if "rosebud.ai" in url and response.status == 200:
            try:
                ct = response.headers.get("content-type", "")
                if "json" in ct:
                    body = await response.text()
                    # Look for checkout URLs in API responses
                    matches = re.findall(r'https://checkout\.stripe\.com[^"\'\\]+', body)
                    if matches:
                        stripe_url_holder.extend(matches)
            except Exception:
                pass

    page.on("request", on_request)
    page.on("response", on_response)

    for pricing_url in pricing_urls:
        try:
            logger.info(f"[BOT] Trying pricing URL: {pricing_url}")
            await page.goto(pricing_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(3000)

            current = page.url
            logger.info(f"[BOT] Current URL after navigation: {current}")

            # Check if we landed on a 404 or redirect
            if "404" in current or current == "https://rosebud.ai/":
                continue

            # Check for inline stripe URLs in page HTML
            content = await page.content()
            stripe_links = re.findall(r'https://checkout\.stripe\.com[^"\'<\s\\]+', content)
            if stripe_links:
                return stripe_links[0].replace("&amp;", "&")

            # Look for upgrade/subscribe buttons and click them
            checkout_url = await _click_lowest_plan(page, context, stripe_url_holder)
            if checkout_url:
                return checkout_url

            if stripe_url_holder:
                return stripe_url_holder[0]

        except Exception as e:
            logger.warning(f"[BOT] Error on {pricing_url}: {e}")
            continue

    # If we still don't have it, try to find pricing links in the page
    try:
        # Try finding the upgrade link via navigation menu
        nav_links = await page.query_selector_all("a")
        for link in nav_links:
            href = await link.get_attribute("href") or ""
            text = await link.inner_text()
            if any(w in text.lower() for w in ["upgrade", "plan", "pricing", "subscribe"]):
                logger.info(f"[BOT] Found nav link: {text} -> {href}")
                if href.startswith("/"):
                    href = f"https://rosebud.ai{href}"
                if href and "rosebud.ai" in href:
                    await page.goto(href, wait_until="domcontentloaded", timeout=20000)
                    await page.wait_for_timeout(3000)
                    content = await page.content()
                    stripe_links = re.findall(r'https://checkout\.stripe\.com[^"\'<\s\\]+', content)
                    if stripe_links:
                        return stripe_links[0].replace("&amp;", "&")
    except Exception as e:
        logger.warning(f"[BOT] Nav link search error: {e}")

    if stripe_url_holder:
        return stripe_url_holder[0]

    return None


async def _click_lowest_plan(page: Page, context: BrowserContext, stripe_url_holder: list) -> Optional[str]:
    """Find and click the cheapest plan button to capture the Stripe checkout URL"""
    
    try:
        # Wait for content to stabilize
        await page.wait_for_timeout(2000)

        # Look for plan/price elements
        UPGRADE_KEYWORDS = ["upgrade", "subscribe", "get starter", "get basic", "get started", 
                           "starter", "basic", "pro", "annual", "monthly", "choose plan", "select plan"]
        
        # Try buttons first
        buttons = await page.query_selector_all("button")
        for btn in buttons:
            try:
                text = (await btn.inner_text()).lower().strip()
                if any(k in text for k in UPGRADE_KEYWORDS):
                    logger.info(f"[BOT] Found upgrade button: {text}")
                    # Try to capture URL via new page
                    try:
                        async with context.expect_page(timeout=8000) as new_page_info:
                            await btn.click(force=True)
                            new_page = await new_page_info.value
                            await new_page.wait_for_load_state("domcontentloaded", timeout=10000)
                            url = new_page.url
                            if "stripe.com" in url:
                                await new_page.close()
                                return url
                            await new_page.close()
                    except Exception:
                        pass

                    # Same tab navigation check
                    await btn.click(force=True)
                    await page.wait_for_timeout(3000)
                    url = page.url
                    if "stripe.com" in url:
                        return url

                    if stripe_url_holder:
                        return stripe_url_holder[0]
            except Exception:
                continue

        # Try links (anchor tags)
        links = await page.query_selector_all("a")
        for link in links:
            try:
                href = (await link.get_attribute("href")) or ""
                text = (await link.inner_text()).lower().strip()
                if "stripe.com" in href and "checkout" in href:
                    return href.replace("&amp;", "&")
                if any(k in text for k in UPGRADE_KEYWORDS):
                    logger.info(f"[BOT] Found upgrade link: {text}")
                    try:
                        async with context.expect_page(timeout=8000) as new_page_info:
                            await link.click(force=True)
                            new_page = await new_page_info.value
                            await new_page.wait_for_load_state("domcontentloaded", timeout=10000)
                            url = new_page.url
                            if "stripe.com" in url:
                                await new_page.close()
                                return url
                            await new_page.close()
                    except Exception:
                        pass
            except Exception:
                continue

    except Exception as e:
        logger.warning(f"[BOT] Click error: {e}")

    return None
