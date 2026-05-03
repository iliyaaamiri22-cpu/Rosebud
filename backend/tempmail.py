"""
MailTM temp email service
Docs: https://docs.mail.tm
"""
import asyncio
import re
import httpx
import random
import string
from typing import Optional, Tuple

BASE_URL = "https://api.mail.tm"


async def create_email() -> Tuple[str, str]:
    """
    Create a fresh disposable email.
    Returns (email_address, bearer_token)
    Retries up to 3 times on 429 rate limit.
    """
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                # 1. Get available domains
                r = await client.get(f"{BASE_URL}/domains")
                r.raise_for_status()
                domains = [d["domain"] for d in r.json().get("hydra:member", [])]
                if not domains:
                    raise RuntimeError("MailTM: no domains available")

                domain = domains[0]
                username = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
                email = f"{username}@{domain}"
                password = "".join(random.choices(string.ascii_letters + string.digits + "!@#", k=16))

                # 2. Create account
                r2 = await client.post(
                    f"{BASE_URL}/accounts",
                    json={"address": email, "password": password}
                )
                if r2.status_code == 429:
                    wait_s = 30 * (attempt + 1)
                    await asyncio.sleep(wait_s)
                    continue
                if r2.status_code not in (200, 201):
                    raise RuntimeError(
                        f"MailTM: account creation failed {r2.status_code}: {r2.text[:200]}"
                    )

                # 3. Get auth token
                r3 = await client.post(
                    f"{BASE_URL}/token",
                    json={"address": email, "password": password}
                )
                r3.raise_for_status()
                token = r3.json().get("token", "")
                if not token:
                    raise RuntimeError("MailTM: token missing in response")

                return email, token
        except RuntimeError:
            raise
        except Exception as e:
            if attempt == 2:
                raise
            await asyncio.sleep(10)
    raise RuntimeError("MailTM: rate limited after 3 attempts")


async def _get_inbox(token: str) -> list:
    """Return list of message summaries from inbox."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{BASE_URL}/messages",
            headers={"Authorization": f"Bearer {token}"}
        )
        if r.status_code == 200:
            return r.json().get("hydra:member", [])
    return []


async def _get_message(token: str, msg_id: str) -> Optional[dict]:
    """Return full message body."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{BASE_URL}/messages/{msg_id}",
            headers={"Authorization": f"Bearer {token}"}
        )
        if r.status_code == 200:
            return r.json()
    return None


def _extract_magic_link(body: str) -> Optional[str]:
    """
    Extract Firebase magic-link from email body.
    Patterns:
      - https://rosebud.ai/...?oobCode=...
      - https://<app>.firebaseapp.com/__/auth/action?oobCode=...
    """
    patterns = [
        r'https://rosebud\.ai[^\s"\'<>\\]+oobCode[^\s"\'<>\\]+',
        r'https://[^\s"\'<>\\]*firebaseapp\.com[^\s"\'<>\\]+oobCode[^\s"\'<>\\]+',
        r'https://[^\s"\'<>\\]+oobCode=[^\s"\'<>&\\]+(?:&[^\s"\'<>\\]+)*',
        r'href=["\']([^"\']+oobCode[^"\']+)["\']',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, body, re.IGNORECASE)
        if matches:
            link = matches[0]
            if isinstance(link, tuple):
                link = link[0]
            # Decode HTML entities
            link = (
                link.replace("&amp;", "&")
                    .replace("&#38;", "&")
                    .replace("&lt;", "<")
                    .replace("&gt;", ">")
            )
            if "oobCode" in link:
                return link
    return None


async def wait_for_magic_link(token: str, timeout: int = 120) -> Optional[str]:
    """
    Poll MailTM inbox until a Firebase magic-link email arrives.
    Returns the magic-link URL or None on timeout.
    """
    loop = asyncio.get_running_loop()
    seen_ids: set = set()
    start = loop.time()

    while loop.time() - start < timeout:
        messages = await _get_inbox(token)

        for msg in messages:
            msg_id = msg.get("id", "")
            if msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)

            full = await _get_message(token, msg_id)
            if not full:
                continue

            # MailTM: 'html' can be a list of parts, 'text' is a string
            html_raw = full.get("html", "")
            html_body = "".join(html_raw) if isinstance(html_raw, list) else (html_raw or "")
            text_body = full.get("text", "") or ""
            combined = html_body + "\n" + text_body

            link = _extract_magic_link(combined)
            if link:
                return link

        await asyncio.sleep(5)

    return None
