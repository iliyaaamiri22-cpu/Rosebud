"""1secmail.com temp email service"""
import random
import string
import asyncio
import re
import httpx
from typing import Optional


DOMAINS = ["1secmail.com", "1secmail.org", "1secmail.net", "esiix.com", "wwjmp.com"]
BASE_URL = "https://www.1secmail.com/api/v1/"


def generate_email() -> str:
    username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    domain = random.choice(DOMAINS)
    return f"{username}@{domain}"


async def get_inbox(email: str) -> list:
    login, domain = email.split('@')
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            BASE_URL,
            params={"action": "getMessages", "login": login, "domain": domain}
        )
        if r.status_code == 200:
            return r.json()
    return []


async def get_message(email: str, msg_id: int) -> Optional[dict]:
    login, domain = email.split('@')
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            BASE_URL,
            params={"action": "readMessage", "login": login, "domain": domain, "id": msg_id}
        )
        if r.status_code == 200:
            return r.json()
    return None


async def wait_for_magic_link(email: str, timeout: int = 120) -> Optional[str]:
    """Poll inbox until we find the rosebud.ai magic link email."""
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        messages = await get_inbox(email)
        for msg in messages:
            subject = msg.get("subject", "").lower()
            sender = msg.get("from", "").lower()
            # Rosebud sends Firebase magic link
            if "rosebud" in sender or "firebase" in sender or "sign in" in subject or "magic" in subject or "link" in subject:
                full = await get_message(email, msg["id"])
                if full:
                    # Extract magic link from email body
                    body = full.get("htmlBody", "") or full.get("textBody", "")
                    # Firebase magic link pattern
                    patterns = [
                        r'https://rosebud\.ai[^\s"\'<>]+oobCode[^\s"\'<>]+',
                        r'https://[^\s"\'<>]*firebaseapp[^\s"\'<>]+oobCode[^\s"\'<>]+',
                        r'https://[^\s"\'<>]*googleapis[^\s"\'<>]+',
                        r'href=["\']([^"\']*oobCode[^"\']*)["\']',
                    ]
                    for pattern in patterns:
                        matches = re.findall(pattern, body)
                        if matches:
                            link = matches[0]
                            # Handle href group capture
                            if isinstance(link, tuple):
                                link = link[0]
                            # Clean HTML entities
                            link = link.replace("&amp;", "&")
                            return link
        await asyncio.sleep(5)
    return None
