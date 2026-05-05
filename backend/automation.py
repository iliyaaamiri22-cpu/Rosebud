"""
Playwright automation for rosebud.ai signup & Stripe checkout extraction.
"""
import os
import asyncio
import logging
import re
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
                _pending_responses: list = []

                def _is_stripe_url(url: str) -> bool:
                    try:
                        parsed = urlparse(url)
                        hostname = parsed.hostname or ""
                        return hostname == "checkout.stripe.com" and ("cs_live_" in url or "cs_test_" in url)
                    except Exception:
                        return False

                def _scan_text_for_stripe(text: str) -> str | None:
                    patterns = [
                        r"https://checkout\.stripe\.com/c/pay/cs_live_[a-zA-Z0-9_]+[^\"'\s<>]*",
                        r"https://checkout\.stripe\.com/c/pay/cs_test_[a-zA-Z0-9_]+[^\"'\s<>]*",
                    ]
                    for pat in patterns:
                        match = re.search(pat, text)
                        if match:
                            return match.group(0)
                    return None

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
                    # Store response object for later body scanning
                    _pending_responses.append(resp)

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

                BUY_KEYWORDS = ["upgrade", "subscribe", "get started", "start free",
                                "start", "buy", "purchase", "choose", "select",
                                "continue", "pay", "pro", "premium", "plan"]

                upgrade_buttons = []

                # ── Deep page analysis: log ALL buttons & links for debugging ──
                all_buttons_text = []
                for btn in await page.query_selector_all("button"):
                    try:
                        txt = (await btn.inner_text()).strip()
                        if txt:
                            all_buttons_text.append(txt)
                    except Exception:
                        pass

                all_links_text = []
                for link in await page.query_selector_all("a"):
                    try:
                        txt = (await link.inner_text()).strip()
                        href = await link.get_attribute("href") or ""
                        if txt:
                            all_links_text.append(f"'{txt}' -> {href}")
                    except Exception:
                        pass

                logger.info(f"[AUTO] ALL buttons on page ({len(all_buttons_text)}): {all_buttons_text}")
                logger.info(f"[AUTO] ALL links on page ({len(all_links_text)}): {all_links_text[:20]}")

                # ── Strategy 1: Exact/partial text match on <button> ──
                for btn in await page.query_selector_all("button"):
                    try:
                        text = (await btn.inner_text()).strip().lower()
                        if any(kw in text for kw in BUY_KEYWORDS):
                            upgrade_buttons.append((btn, text))
                    except Exception:
                        pass
                logger.info(f"[AUTO] Strategy 1 BUTTONS: {len(upgrade_buttons)} -> {[t for _,t in upgrade_buttons]}")

                # ── Strategy 2: Text match on <a> links ──
                if not upgrade_buttons:
                    for link in await page.query_selector_all("a"):
                        try:
                            text = (await link.inner_text()).strip().lower()
                            if any(kw in text for kw in BUY_KEYWORDS):
                                upgrade_buttons.append((link, text))
                        except Exception:
                            pass
                    logger.info(f"[AUTO] Strategy 2 LINKS: {len(upgrade_buttons)} -> {[t for _,t in upgrade_buttons]}")

                # ── Strategy 3: Use Playwright locators (more robust) ──
                if not upgrade_buttons:
                    for kw in BUY_KEYWORDS:
                        try:
                            loc = page.get_by_role("button", name=kw, exact=False)
                            cnt = await loc.count()
                            if cnt > 0:
                                upgrade_buttons.append((loc.first, f"locator:{kw}"))
                                break
                        except Exception:
                            pass
                    logger.info(f"[AUTO] Strategy 3 LOCATOR buttons: {len(upgrade_buttons)}")

                # ── Strategy 4: CSS class-based selectors (common patterns) ──
                if not upgrade_buttons:
                    css_selectors = [
                        "button[class*='upgrade']", "button[class*='subscribe']",
                        "button[class*='plan']", "button[class*='pro']",
                        "button[class*='buy']", "button[class*='pay']",
                        "a[class*='upgrade']", "a[class*='subscribe']",
                        "[data-testid*='upgrade']", "[data-testid*='subscribe']",
                        "[id*='upgrade']", "[id*='subscribe']",
                    ]
                    for sel in css_selectors:
                        try:
                            el = await page.query_selector(sel)
                            if el:
                                txt = (await el.inner_text()).strip() or sel
                                upgrade_buttons.append((el, txt))
                                break
                        except Exception:
                            pass
                    logger.info(f"[AUTO] Strategy 4 CSS selectors: {len(upgrade_buttons)}")

                # ── Strategy 5: Any visible clickable element that looks like CTA ──
                if not upgrade_buttons:
                    all_clickables = await page.query_selector_all("button, a[href], [role='button'], input[type='submit']")
                    for el in all_clickables:
                        try:
                            visible = await el.is_visible()
                            txt = (await el.inner_text()).strip().lower()
                            if visible and len(txt) > 0 and len(txt) < 40:
                                upgrade_buttons.append((el, txt))
                        except Exception:
                            pass
                    logger.info(f"[AUTO] Strategy 5 ALL visible clickables: {len(upgrade_buttons)} -> {[t for _,t in upgrade_buttons[:10]]}")

                # ── Strategy 6: Intercept Stripe checkout URL directly from page scripts ──
                if not upgrade_buttons:
                    try:
                        stripe_url = await page.evaluate(r"""
                            () => {
                                for (const key in window) {
                                    try {
                                        const val = window[key];
                                        if (typeof val === 'string' && val.includes('checkout.stripe.com') && val.includes('cs_live_')) {
                                            return val;
                                        }
                                    } catch(e) {}
                                }
                                const scripts = document.querySelectorAll('script');
                                for (const s of scripts) {
                                    const text = s.textContent || '';
                                    const match = text.match(/(https:\/\/checkout\.stripe\.com\/c\/pay\/cs_live_[^"'\s]+)/);
                                    if (match) return match[1];
                                }
                                return null;
                            }
                        """)
                        if stripe_url and _is_stripe_url(stripe_url):
                            logger.info(f"[AUTO] Strategy 6: Found Stripe URL in page scripts: {stripe_url}")
                            await report("click_upgrade", "done", "Checkout URL found in page data")
                            return {"success": True, "email": email, "checkout_url": stripe_url}
                    except Exception as e:
                        logger.info(f"[AUTO] Strategy 6 failed: {e}")

                # ── Nothing found: debug dump ──
                if not upgrade_buttons:
                    try:
                        await page.screenshot(path="/tmp/rosebud_pricing_debug.png")
                        full_html = await page.content()
                        logger.error(f"[AUTO] DEBUG HTML ({len(full_html)} chars): {full_html[:3000]}")
                    except Exception:
                        pass
                    await report("click_upgrade", "failed", f"No button found. Buttons: {all_buttons_text[:10]}")
                    return {
                        "success": False, "email": email,
                        "error": f"No checkout button found. Available buttons: {all_buttons_text[:5]}",
                    }

                # Click the best candidate
                target, btn_text = upgrade_buttons[0]
                try:
                    if hasattr(target, 'click'):
                        await target.click(force=True)
                    else:
                        await target.click()
                except Exception as e:
                    logger.warning(f"[AUTO] Click failed, trying JS click: {e}")
                    try:
                        await page.evaluate("(el) => el.click()", target)
                    except Exception as e2:
                        logger.error(f"[AUTO] JS click also failed: {e2}")
                        return {"success": False, "email": email, "error": f"Click failed: {e2}"}

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

                # Layer 3: Wait for network idle + scan response bodies
                try:
                    await page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    pass

                # Scan all collected response bodies for Stripe URL
                for resp in _pending_responses:
                    try:
                        body = await resp.text()
                        found = _scan_text_for_stripe(body)
                        if found and found not in stripe_checkout_urls:
                            stripe_checkout_urls.append(found)
                            logger.info(f"[AUTO] Stripe URL captured (response body scan): {found}")
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

                    # Scan page HTML for any embedded Stripe URL
                    try:
                        html = await page.content()
                        found = _scan_text_for_stripe(html)
                        if found and found not in stripe_checkout_urls:
                            stripe_checkout_urls.append(found)
                            logger.info(f"[AUTO] Stripe URL captured (page HTML): {found}")
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

                # Layer 7: Debug — dump ALL request URLs to understand what's happening
                if not stripe_checkout_urls:
                    try:
                        await page.screenshot(path="/tmp/rosebud_stripe_debug.png")
                        logger.error(f"[AUTO] ALL page URLs: {[p.url for p in context.pages]}")
                        logger.error(f"[AUTO] Current page URL: {page.url}")
                        # Try to extract any checkout-like URL from page content
                        html = await page.content()
                        all_urls = re.findall(r'https?://[^"\'\s<>]+', html)
                        stripe_like = [u for u in all_urls if 'stripe.com' in u or 'checkout' in u]
                        logger.error(f"[AUTO] Stripe-like URLs in HTML: {stripe_like[:20]}")
                    except Exception:
                        pass
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
