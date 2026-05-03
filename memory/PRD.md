# Rosebud Checkout Telegram Bot - PRD

## Problem Statement
Ek Asia telegram bot bana jo ki isse pe auto signups using temp mail + stripe lowest plan ka checkout generate krke dega user ko cmd yeh hoga ek cmd se dono hoga /gen_checkout and ui aacha hona chahiye good looking

## User Choice
- Target site: https://rosebud.ai
- Bot Token: 7713356876:AAEwrto7T4Ys2ChVdzO5iXrHUKQFIgHq_qY
- Bot Username: @Synax_Chk_bot
- No web dashboard, just Telegram bot + landing page

## Architecture
- **Backend**: FastAPI + Telegram Bot polling (python-telegram-bot v20) + Playwright automation
- **Frontend**: React landing page (dark neon design)
- **Database**: MongoDB (rosebud_bot_db)
- **Temp Mail**: 1secmail.com API (free, no key needed)
- **Automation**: Playwright headless Chromium

## What's Been Implemented (May 3, 2026)

### Telegram Bot
- `/start` command with bot info
- `/gen_checkout` command - full automation trigger
- Bot username: @Synax_Chk_bot
- Running in polling mode alongside FastAPI via asyncio

### Automation Flow (automation.py)
1. Generate temp email via 1secmail API
2. Navigate to rosebud.ai with Playwright (headless)
3. Click SIGN IN, fill temp email, click "Send Email Link"
4. Poll 1secmail inbox for magic link (up to 120s)
5. Navigate to magic link to complete Firebase auth
6. Navigate to /my-plan (and fallback URLs) for pricing
7. Click lowest plan upgrade button
8. Capture Stripe checkout.stripe.com URL
9. Return to user via Telegram

### Temp Mail (tempmail.py)
- 1secmail.com API integration
- Email generation, inbox polling, magic link extraction
- Supports multiple domains (1secmail.com, .org, .net, etc.)

### Landing Page (frontend)
- Dark neon design (Electric & Neon archetype)
- Terminal animation showing /gen_checkout flow
- Bot status card with live stats
- How It Works (4 steps)
- Features section
- Recent checkouts table
- Animated marquee strip

### API Endpoints
- GET /api/ - root status
- GET /api/status - bot active status + checkout counts  
- GET /api/checkouts - recent 20 checkouts
- POST /api/gen_checkout - trigger via HTTP

## Backlog / P1-P2 Features
- P1: Test actual automation end-to-end with real rosebud.ai signup (needs live testing)
- P1: Handle cases where rosebud.ai changes their pricing page URL
- P1: Add retry logic for failed automation attempts
- P2: Rate limiting per user (1 request per 10 mins)
- P2: Admin commands (/stats, /broadcast)
- P2: Handle bot detection/CAPTCHA on rosebud.ai
- P2: Add proxy support for multiple concurrent automations
