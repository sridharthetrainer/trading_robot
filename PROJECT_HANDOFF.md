# ═══════════════════════════════════════════════════════════════
# TRADING CONTENT AUTOMATION — PROJECT HANDOFF DOCUMENT
# ═══════════════════════════════════════════════════════════════
# 
# Use this document to start a NEW Claude chat for building
# the social media content automation system.
# 
# Copy everything below into a new Claude conversation.
# ═══════════════════════════════════════════════════════════════

## PROJECT OVERVIEW

I have an existing NSE algorithmic trading bot (Python, 219 files, 100K+ lines) that:
- Scans 196 NSE/BSE symbols every 5 minutes using 60+ strategies
- Generates signals with WOW factors (FII, promoter, PCR, sentiment)
- Produces morning market briefs with global data
- Has Angel One broker integration (auto TOTP, no manual login)
- Stores all strategy scores, FII/DII data, VIX in SQLite database
- Runs on Ubuntu Linux home desktop

I want to build a SEPARATE system (not modify the trading bot) that:

### 1. YouTube Auto-Poster
- Generate daily market analysis videos (3-5 minutes)
- Morning brief video: Global markets, VIX, sector analysis, top picks
- Evening recap: What happened, P&L, tomorrow's outlook
- Use TTS (Google/ElevenLabs) + matplotlib charts + stock footage
- Auto-upload to YouTube with SEO titles, descriptions, tags
- Schedule: 8:30 AM morning, 4:30 PM evening, weekdays only

### 2. X (Twitter) Auto-Poster
- Post market signals as tweet threads
- Morning: "🌅 Market Brief" with key levels
- Intraday: Signal alerts with chart images
- Evening: Recap + top/worst performers
- Use tweepy library for posting
- Image generation: matplotlib charts embedded in tweets

### 3. Facebook Page Auto-Poster
- Same content as Twitter but formatted for FB
- Carousel posts for multiple signals
- Video posts (same as YouTube)
- Use facebook-sdk or Graph API

### 4. Telegram Channel (existing, enhance)
- Free channel: delayed signals, morning briefs
- Premium channel: real-time signals with WOW factors
- Subscriber management: trial, paid tiers, expiry

### 5. WhatsApp Business Channel
- Use WhatsApp Business API (or Twilio)
- Same signal format as Telegram
- Paid subscribers only

### 6. Subscription/Monetization
- Free tier: Delayed signals, morning brief (YouTube + Telegram)
- Basic ₹499/mo: Real-time signals (Telegram + WhatsApp)
- Premium ₹999/mo: Full analysis + options + swing picks
- Payment: Razorpay integration
- Trial: 7-day free trial

---

## DATA AVAILABLE FROM TRADING BOT

The trading bot exposes data via SQLite database and JSON files.
The content system reads FROM these — never writes to them.

### Database: trades.db
```sql
-- All strategy scores (recorded every scan cycle)
SELECT * FROM strategy_scores 
-- columns: timestamp, symbol, strategy, score, direction, regime, vix, price, reasons

-- FII/DII daily data
SELECT * FROM fii_dii_data
-- columns: date, fii_buy, fii_sell, fii_net, dii_buy, dii_sell, dii_net,
--          fii_futures_oi, fii_futures_net, vix, nifty_close

-- EOD ML strategy feedback
SELECT * FROM eod_ml_feedback
-- columns: date, strategy, total_signals, avg_score, win_rate, avg_pnl, accuracy

-- All trades (paper + live)
SELECT * FROM trades
-- columns: symbol, strategy, side, qty, entry_price, exit_price,
--          entry_time, exit_time, realized_pnl, status, signal_metadata
```

### JSON Files (updated in real-time)
```
oi_baseline_NIFTY.json      — OI for all strikes
oi_baseline_BANKNIFTY.json  — OI for all strikes
cross_asset_cache.json      — Global market prices
regime_state.json           — Current market regime
signal_log.json             — Last 100 signals with scores
watchlist.json              — User's watchlist
price_alerts.json           — Active price alerts
```

### Key Python Functions to Import
```python
# From the trading bot (read-only access)
from morning_brief import generate_morning_brief, _fetch_global_snapshot
from cross_asset import get_cross_asset_data, get_market_bias
from signal_broadcaster import SignalBroadcaster
from strategy_score_tracker import run_eod_ml_analysis, get_fii_dii_history
from voice_video_generator import generate_market_chart, generate_voice_script
from market_intelligence_hub import get_composite_sentiment
from option_chain_fetcher import OptionChainFetcher
```

---

## SIGNAL FORMAT (WOW-Enhanced)

Each signal from the trading bot looks like:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟢 BUY RELIANCE ⭐⭐⭐
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  📈 SIGNAL SCORE: 8.2/10 → 🔥 HIGH CONVICTION

  ┌─ TRADE SETUP ─────────────────
  │ Entry:     ₹1,315.50
  │ Target:    ₹1,342.00  (+2.0%)
  │ Stop Loss: ₹1,302.00  (-1.0%)
  │ R:R Ratio: 1:2.0  👍
  └─────────────────────────────

  ✨ WOW FACTORS
  │ 🐂 FII BUYING in futures
  │ 👔 Promoter BUYING detected
  │ 🐂 PCR: 1.35
  │ 📊 Sector ENERGY: +1.2%
  │ 🎯 Sentiment: 72/100 (BULLISH)
  │ 🔥 TOP-TIER signal — 8.2/10

  ⚠️ Educational only | Not SEBI registered
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## MORNING BRIEF FORMAT

```
🌍 GLOBAL MACRO UPDATE
🕐 10 May 08:28

  🇺🇸 S&P 500:     5,819 🟢+0.6%
  💵 DXY:          100.5 🔴-0.2%
  🛢️ Brent:        63.8 🔴-1.2%
  🥇 Gold:         3,325 🟢+0.4%
  📊 US VIX:       22.5 🔴-3.1%
  💱 USD/INR:      84.5 🟢+0.1%
  🇮🇳 India VIX:    14.2

  NIFTY Bias: +0.400 → 🟢 BULLISH

🔝 TOP SECTORS: IT, Banking, Auto
⚠️ AVOID: Pharma, Metal
```

---

## EXISTING VIDEO GENERATION CODE

The trading bot already has `voice_video_generator.py` that:
1. Creates matplotlib charts (global markets, VIX gauge, sentiment, commodities)
2. Generates TTS audio using gTTS
3. Combines chart + audio into MP4 using moviepy
4. Sends to Telegram

This code can be REUSED as the base for YouTube videos.
Just need to: make it longer (3-5 min), add transitions, add intro/outro.

---

## TECH STACK RECOMMENDATIONS

### YouTube
- `google-api-python-client` — YouTube Data API v3 (upload, metadata)
- `moviepy` — video composition (already used)
- `matplotlib` — chart generation (already used)
- `gTTS` or `elevenlabs` — text-to-speech
- `Pillow` — thumbnail generation
- Schedule: systemd timer or APScheduler

### X (Twitter)
- `tweepy` v2 — Twitter API v2 (post tweets, threads, images)
- Free tier: 1,500 tweets/month (50/day)
- Basic tier ($100/mo): 3,000 tweets/month
- Image: matplotlib → save as PNG → attach to tweet

### Facebook
- `facebook-sdk` or direct Graph API
- Page access token (never expires with long-lived token)
- Post types: text, image, video, carousel

### WhatsApp Business
- Twilio WhatsApp API ($0.005/message)
- OR WhatsApp Business Cloud API (free first 1,000/month)
- Template messages for signals
- Media messages for charts

### Payments
- Razorpay (₹0 setup, 2% per transaction)
- Subscription plans: ₹499 basic, ₹999 premium
- Webhook for payment confirmation → auto-add to channels

---

## COMPLIANCE REQUIREMENTS

1. Every post MUST include: "Educational purposes only | Not SEBI registered investment adviser"
2. No guaranteed returns language
3. Past performance disclaimer
4. Risk disclosure on every signal
5. No specific "buy/sell" recommendations — frame as "educational analysis"
6. WhatsApp Business requires verified business account

---

## ARCHITECTURE (SEPARATE FROM TRADING BOT)

```
trading_robot/          ← Existing (don't modify)
  trades.db             ← Read by content system
  signal_log.json       ← Read by content system
  cross_asset_cache.json
  
content_automation/     ← NEW project
  main.py               — Scheduler (APScheduler)
  data_reader.py        — Reads from trading_robot/trades.db
  
  youtube/
    video_generator.py  — Create videos from data
    uploader.py         — YouTube API upload
    thumbnail.py        — Generate thumbnails
    
  twitter/
    poster.py           — Post tweets + threads
    chart_gen.py        — Signal charts for tweets
    
  facebook/
    poster.py           — FB page posts
    carousel.py         — Multi-signal carousels
    
  whatsapp/
    sender.py           — WhatsApp Business API
    template.py         — Message templates
    
  telegram/
    enhanced_broadcaster.py — Enhanced signal cards
    subscriber_manager.py   — Trial + paid tiers
    
  subscription/
    razorpay_handler.py — Payment processing
    tier_manager.py     — Free/Basic/Premium logic
    webhook.py          — Payment webhooks
    
  shared/
    chart_styles.py     — Consistent chart styling
    formatters.py       — Platform-specific formatting
    compliance.py       — SEBI disclaimers + risk warnings
    scheduler.py        — Post timing logic
    
  config/
    .env                — API keys (YouTube, Twitter, FB, Razorpay)
    schedule.yaml       — Post schedule by platform
```

---

## DAILY SCHEDULE

```
06:00 AM  — Fetch global data, generate pre-market analysis
08:00 AM  — Generate morning brief video
08:30 AM  — Upload to YouTube + post on all platforms
09:15 AM  — Market opens — start signal monitoring
09:30 AM  — First scan signals → tweet thread + Telegram
10:00 AM  — Intraday signal batch → all platforms
12:00 PM  — Midday update (if significant moves)
03:00 PM  — Pre-close analysis
03:30 PM  — Market close — start EOD processing
04:00 PM  — Generate evening recap video
04:30 PM  — Upload recap to YouTube + all platforms
05:00 PM  — EOD ML analysis → summary post
08:00 PM  — Swing picks for tomorrow → premium channels only
```

---

## API KEYS NEEDED (for new .env)

```
# YouTube
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
YOUTUBE_REFRESH_TOKEN=

# Twitter/X
TWITTER_API_KEY=
TWITTER_API_SECRET=
TWITTER_ACCESS_TOKEN=
TWITTER_ACCESS_TOKEN_SECRET=
TWITTER_BEARER_TOKEN=

# Facebook
FB_PAGE_ID=
FB_PAGE_ACCESS_TOKEN=

# WhatsApp Business
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_ACCESS_TOKEN=
# OR Twilio
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_NUMBER=

# Razorpay
RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=
RAZORPAY_WEBHOOK_SECRET=

# Telegram (reuse from trading bot)
TELEGRAM_BOT_TOKEN=<your_token>
TELEGRAM_FREE_CHANNEL_ID=-1003830079189
TELEGRAM_PREMIUM_CHANNEL_ID=-1003993110321

# Trading bot data path
TRADING_BOT_PATH=/home/sridhar/Desktop/trading_robot
TRADING_DB_PATH=/home/sridhar/Desktop/trading_robot/trades.db
```

---

## GETTING STARTED

1. Create a new folder: `~/Desktop/content_automation/`
2. Copy this document into a new Claude chat
3. Ask Claude to build the system file by file
4. Start with: YouTube video generator + Telegram enhanced signals
5. Then add: Twitter → Facebook → WhatsApp → Razorpay

The trading bot continues running independently.
The content system READS from it but never modifies it.
