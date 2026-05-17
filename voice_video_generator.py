"""
voice_video_generator.py — Automated Market Brief Video + Voice

Creates daily market videos using:
  - gTTS (Google Text-to-Speech) — FREE, no API key
  - MoviePy / PIL — video composition
  - Matplotlib — charts
  - Telegram video upload

Video structure (60-90 seconds):
  1. Intro card (3s) — "Today's Market Brief — 15 Apr"
  2. Global markets (10s) — S&P, Gold, Crude with voice
  3. India VIX + Bias (8s)
  4. Top news sentiment (12s) — bullish/bearish headlines
  5. Commodity impact (10s) — which sectors affected
  6. Sector rotation (8s) — overweight/underweight
  7. WOW factors (10s) — HMM regime, FII, dark pool
  8. Signal preview (8s) — "Watch these symbols today"
  9. Disclaimer (5s)

Voice: Indian English accent via gTTS
Charts: Matplotlib with dark theme (professional look)

Install: pip install gtts moviepy pillow matplotlib
"""
from __future__ import annotations
import logging, os, json, tempfile
from datetime import datetime, date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path("daily_videos")
_OUTPUT_DIR.mkdir(exist_ok=True)

# ── Script generator ─────────────────────────────────────────────────

def generate_voice_script(brief_data: dict) -> str:
    """
    Generate natural spoken script for TTS.
    Inspired by NDTV Profit / ET Now anchor style.
    """
    today = date.today().strftime("%A, %d %B %Y")
    global_data = brief_data.get("global", {})
    vix     = float(brief_data.get("india_vix", 0))
    bias    = float(brief_data.get("bias", 0))
    sectors = brief_data.get("top_sectors", [])
    avoid   = brief_data.get("avoid_sectors", [])
    sent    = brief_data.get("sentiment", "NEUTRAL")
    impacts = brief_data.get("commodity_impacts", {})
    wow     = brief_data.get("wow_factors", {})

    def _sp(d, name, unit=""):
        if not d or not d.get("price"):
            return ""
        chg = d.get("chg", 0) or d.get("change_pct", 0)
        direction = "up" if chg > 0 else "down" if chg < 0 else "flat"
        return f"{name} is {direction} {abs(chg):.1f} percent. "

    bias_str = "bullish" if bias > 0.3 else "bearish" if bias < -0.3 else "neutral"
    vix_str  = ("low — ideal for trading" if vix < 15 else
                "moderate — trade with standard sizing" if vix < 20 else
                "elevated — reduce position sizes by 30 percent")

    # Commodity impacts in plain English
    impact_sentences = ""
    for sector, impact in list(impacts.items())[:2]:
        clean = impact.split("—")[-1].strip() if "—" in impact else impact
        clean = clean.replace("🟢", "").replace("🔴", "").strip()
        impact_sentences += f"{sector}: {clean}. "

    # WOW factor commentary
    hmm = wow.get("regime", "TRENDING")
    hmm_str = ("trending — full position sizing" if "TREND" in hmm else
               "choppy — reduce sizing" if "CHOP" in hmm else
               "high noise — avoid new entries" if "NOISE" in hmm else "normal")
    fii_bias = wow.get("fii_bias", "NEUTRAL")

    script = f"""
Good morning traders. Here is your market brief for {today}.

Global markets update.
{_sp(global_data.get('SP500'), "S&P 500")}
{_sp(global_data.get('GOLD'), "Gold")}
{_sp(global_data.get('BRENT'), "Brent crude oil")}
{_sp(global_data.get('USDINR'), "Dollar rupee")}

India market outlook.
India VIX is at {vix:.0f}, which is {vix_str}.
Our global macro model is {bias_str} on Indian markets today.

News sentiment analysis.
After scanning hundreds of headlines, the overall market sentiment is {sent}.
{impact_sentences}

Sector rotation update.
Today we are overweight on {", ".join(sectors[:3]) if sectors else "all sectors"}.
We are reducing exposure to {", ".join(avoid[:2]) if avoid else "no specific sector"}.

WOW factor analysis.
Our HMM market regime detector shows the market is {hmm_str}.
FII positioning is {fii_bias.lower()}.

Today's trading plan.
We will scan 196 symbols using 65 strategies.
Maximum 8 quality-filtered signals will be broadcast.
Risk management rule: never risk more than 1 percent of your capital per trade.

Remember: these are educational signals only.
Always set your stop loss before entering any trade.
Trade safe and good luck.
""".strip()

    return script


def generate_tts_audio(script: str, output_path: str) -> bool:
    """Generate MP3 audio from script using gTTS (free)."""
    try:
        from gtts import gTTS
        tts = gTTS(text=script, lang='en', tld='co.in', slow=False)
        tts.save(output_path)
        logger.info("Audio generated: %s", output_path)
        return True
    except ImportError:
        logger.warning("gTTS not installed: pip install gtts")
        return False
    except Exception as e:
        logger.warning("TTS failed: %s", e)
        return False


def generate_market_chart(brief_data: dict, output_path: str) -> bool:
    """Generate professional dark-theme market chart as PNG."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import numpy as np

        fig, axes = plt.subplots(2, 2, figsize=(16, 9))
        fig.patch.set_facecolor('#0D1117')

        global_data = brief_data.get("global", {})
        commodities = brief_data.get("commodities", {})
        sectors     = brief_data.get("top_sectors", [])
        sentiment   = brief_data.get("sentiment", "NEUTRAL")
        vix         = float(brief_data.get("india_vix", 0))
        bias        = float(brief_data.get("bias", 0))

        # Plot 1: Global markets bar chart
        ax1 = axes[0][0]
        ax1.set_facecolor('#161B22')
        markets = []
        changes = []
        colors  = []
        for key, label in [("SP500","S&P500"),("GOLD","Gold"),
                             ("BRENT","Brent"),("USDINR","USD/INR"),
                             ("USVIX","US VIX")]:
            d = global_data.get(key, {})
            if d and (d.get("price") or d.get("last")):
                chg = float(d.get("chg", 0) or d.get("change_pct", 0))
                markets.append(label)
                changes.append(chg)
                colors.append('#00FF88' if chg > 0 else '#FF4444')
        if markets:
            bars = ax1.barh(markets, changes, color=colors, height=0.6)
            ax1.set_title("Global Markets", color='white', fontsize=12, fontweight='bold')
            ax1.tick_params(colors='white')
            ax1.set_xlabel("% Change", color='#888888')
            ax1.axvline(0, color='#444444', linewidth=0.5)
            for spine in ax1.spines.values():
                spine.set_edgecolor('#333333')
            for bar, val in zip(bars, changes):
                ax1.text(val + 0.02 * (1 if val >= 0 else -1),
                         bar.get_y() + bar.get_height()/2,
                         f"{val:+.2f}%", va='center',
                         color='white', fontsize=9,
                         ha='left' if val >= 0 else 'right')

        # Plot 2: India VIX gauge
        ax2 = axes[0][1]
        ax2.set_facecolor('#161B22')
        vix_color = '#00FF88' if vix < 15 else '#FFA500' if vix < 22 else '#FF4444'
        ax2.set_xlim(0, 50)
        ax2.set_ylim(0, 1)
        ax2.barh([0.5], [vix], height=0.4, color=vix_color, alpha=0.8)
        ax2.axvline(15, color='#00FF88', linewidth=1, linestyle='--', alpha=0.5, label='Low')
        ax2.axvline(22, color='#FFA500', linewidth=1, linestyle='--', alpha=0.5, label='High')
        ax2.set_title(f"India VIX: {vix:.1f}", color='white', fontsize=12, fontweight='bold')
        ax2.set_xlabel("VIX Level", color='#888888')
        ax2.tick_params(colors='white')
        for spine in ax2.spines.values():
            spine.set_edgecolor('#333333')
        vix_label = "LOW — GOOD" if vix < 15 else "MODERATE" if vix < 22 else "HIGH — CAUTION"
        ax2.text(25, 0.5, vix_label, va='center', color=vix_color, fontsize=14, fontweight='bold')

        # Plot 3: Commodities
        ax3 = axes[1][0]
        ax3.set_facecolor('#161B22')
        comm_names, comm_chgs, comm_cols = [], [], []
        for name in ["Gold", "Brent Crude", "Copper", "Natural Gas", "Silver"]:
            d = commodities.get(name, {})
            if d and d.get("price"):
                chg = float(d.get("change_pct", 0))
                comm_names.append(name[:10])
                comm_chgs.append(chg)
                comm_cols.append('#00FF88' if chg > 0 else '#FF4444')
        if comm_names:
            ax3.barh(comm_names, comm_chgs, color=comm_cols, height=0.6)
            ax3.set_title("Commodities", color='white', fontsize=12, fontweight='bold')
            ax3.tick_params(colors='white')
            ax3.set_xlabel("% Change", color='#888888')
            ax3.axvline(0, color='#444444', linewidth=0.5)
            for spine in ax3.spines.values():
                spine.set_edgecolor('#333333')

        # Plot 4: Market bias + sentiment
        ax4 = axes[1][1]
        ax4.set_facecolor('#161B22')
        ax4.set_xlim(-1, 1)
        ax4.set_ylim(0, 1)
        bias_color = '#00FF88' if bias > 0.2 else '#FF4444' if bias < -0.2 else '#FFA500'
        ax4.barh([0.7], [bias], height=0.2, color=bias_color,
                 left=0, align='center')
        ax4.axvline(0, color='white', linewidth=1)
        sent_color = '#00FF88' if sentiment == "BULLISH" else '#FF4444' if sentiment == "BEARISH" else '#FFA500'
        ax4.set_title("Market Bias & Sentiment", color='white', fontsize=12, fontweight='bold')
        ax4.text(0, 0.4, f"Sentiment: {sentiment}", ha='center',
                 color=sent_color, fontsize=14, fontweight='bold')
        ax4.text(bias, 0.85, f"{bias:+.2f}", ha='center',
                 color=bias_color, fontsize=12, fontweight='bold')
        ax4.text(-0.95, 0.7, "BEARISH", color='#FF4444', fontsize=10, va='center')
        ax4.text(0.7, 0.7, "BULLISH", color='#00FF88', fontsize=10, va='center')
        ax4.set_xlabel("Global Macro Score", color='#888888')
        ax4.tick_params(colors='white')
        for spine in ax4.spines.values():
            spine.set_edgecolor('#333333')

        # Title
        today_str = date.today().strftime("%A, %d %B %Y")
        fig.suptitle(f"Market Intelligence Brief — {today_str}",
                     color='white', fontsize=16, fontweight='bold', y=0.98)

        # Footer
        fig.text(0.5, 0.01,
                 "Educational purposes only | Not SEBI registered investment advice",
                 ha='center', color='#666666', fontsize=9)

        plt.tight_layout(rect=[0, 0.03, 1, 0.95])
        plt.savefig(output_path, dpi=150, bbox_inches='tight',
                    facecolor='#0D1117', edgecolor='none')
        plt.close()
        logger.info("Chart generated: %s", output_path)
        return True

    except ImportError as e:
        logger.warning("matplotlib missing: %s | pip install matplotlib", e)
        return False
    except Exception as e:
        logger.warning("Chart failed: %s", e)
        return False


def create_video_from_chart_and_audio(
        chart_path: str, audio_path: str, output_path: str) -> bool:
    """
    Combine chart image + TTS audio into MP4 video.
    Uses moviepy (free). Duration = audio length.
    """
    try:
        from moviepy.editor import ImageClip, AudioFileClip, CompositeVideoClip
        from moviepy.editor import TextClip, concatenate_videoclips

        audio = AudioFileClip(audio_path)
        duration = audio.duration

        # Chart slide
        clip = ImageClip(chart_path, duration=duration)
        clip = clip.set_audio(audio)
        clip = clip.resize(width=1280)

        clip.write_videofile(
            output_path,
            fps=1,
            codec='libx264',
            audio_codec='aac',
            verbose=False,
            logger=None,
        )
        logger.info("Video created: %s (%.0fs)", output_path, duration)
        return True

    except ImportError:
        logger.warning("moviepy not installed: pip install moviepy")
        # Fallback: just save the chart as the deliverable
        return False
    except Exception as e:
        logger.warning("Video creation failed: %s", e)
        return False


def generate_daily_brief_video(brief_data: dict, alerts=None) -> Optional[str]:
    """
    Master function: generate complete market brief video.
    Called at 8:30 AM daily.
    Returns path to video file.
    """
    today = date.today().strftime("%Y%m%d")
    chart_path = str(_OUTPUT_DIR / f"market_chart_{today}.png")
    audio_path = str(_OUTPUT_DIR / f"market_brief_{today}.mp3")
    video_path = str(_OUTPUT_DIR / f"market_brief_{today}.mp4")

    logger.info("Generating daily market brief video...")

    # Step 1: Generate chart
    chart_ok = generate_market_chart(brief_data, chart_path)

    # Step 2: Generate voice
    script = generate_voice_script(brief_data)
    audio_ok = generate_tts_audio(script, audio_path)

    # Step 3: Combine into video
    video_ok = False
    if chart_ok and audio_ok:
        video_ok = create_video_from_chart_and_audio(chart_path, audio_path, video_path)

    # Step 4: Send via Telegram
    if alerts:
        try:
            if video_ok and os.path.exists(video_path):
                # Send video
                caption = (
                    f"🎬 Market Brief | {date.today().strftime('%d %b %Y')}\n"
                    f"Watch for today's trading plan, sentiment & WOW factors\n"
                    f"⚠️ Educational only"
                )
                alerts.send_video(video_path, caption=caption)
                logger.info("Video sent via Telegram")
            elif chart_ok and os.path.exists(chart_path):
                # Send image as fallback
                alerts.send_photo(chart_path,
                    caption=f"📊 Market Brief | {date.today().strftime('%d %b %Y')}")
                logger.info("Chart sent via Telegram (video fallback)")
        except Exception as e:
            logger.warning("Telegram video send: %s", e)

    return video_path if video_ok else chart_path if chart_ok else None


def generate_signal_card_image(signal: dict) -> Optional[str]:
    """
    Generate a professional signal card image for a single trade.
    Sent alongside the text signal on Telegram.
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as patches

        symbol    = signal.get("symbol", "?")
        direction = signal.get("direction", "?")
        price     = float(signal.get("price", 0) or 0)
        target    = float(signal.get("target", 0) or 0)
        sl        = float(signal.get("stop_loss", 0) or 0)
        score     = float(signal.get("score", 0) or 0)
        strategy  = signal.get("strategy", "Confluence")
        regime    = signal.get("regime", "TRENDING")

        fig, ax = plt.subplots(1, 1, figsize=(8, 4))
        fig.patch.set_facecolor('#0D1117')
        ax.set_facecolor('#0D1117')
        ax.set_xlim(0, 10)
        ax.set_ylim(0, 6)
        ax.axis('off')

        # Header background
        bg_color = '#1A4D1A' if direction == "BUY" else '#4D1A1A'
        ax.add_patch(patches.FancyBboxPatch((0, 4.5), 10, 1.5,
            boxstyle="round,pad=0.1", fc=bg_color, ec='none'))

        # Direction icon + symbol
        icon = "▲ BUY" if direction == "BUY" else "▼ SELL"
        ax.text(5, 5.3, f"{icon}  {symbol}", ha='center', va='center',
                color='white', fontsize=20, fontweight='bold')

        # Price levels
        ax.text(1.5, 3.7, "ENTRY", ha='center', color='#888888', fontsize=9)
        ax.text(5.0, 3.7, "TARGET", ha='center', color='#888888', fontsize=9)
        ax.text(8.5, 3.7, "STOP LOSS", ha='center', color='#888888', fontsize=9)

        ax.text(1.5, 3.1, f"₹{price:,.1f}", ha='center', color='white',
                fontsize=14, fontweight='bold')
        ax.text(5.0, 3.1, f"₹{target:,.1f}", ha='center', color='#00FF88',
                fontsize=14, fontweight='bold')
        ax.text(8.5, 3.1, f"₹{sl:,.1f}", ha='center', color='#FF4444',
                fontsize=14, fontweight='bold')

        # Score bar
        ax.add_patch(patches.FancyBboxPatch((0.5, 2.0), 9.0, 0.5,
            boxstyle="round,pad=0.05", fc='#222222', ec='none'))
        bar_w = score / 10 * 9.0
        bar_color = '#00FF88' if score >= 7 else '#FFA500' if score >= 5.5 else '#FF4444'
        ax.add_patch(patches.FancyBboxPatch((0.5, 2.0), bar_w, 0.5,
            boxstyle="round,pad=0.05", fc=bar_color, ec='none', alpha=0.8))
        ax.text(5.0, 2.25, f"Score: {score:.1f}/10", ha='center',
                color='white', fontsize=10, fontweight='bold')

        # Meta info
        rr = abs((target-price)/(price-sl)) if price and sl and sl != price else 0
        target_pct = abs((target-price)/price*100) if price else 0
        sl_pct = abs((price-sl)/price*100) if price else 0

        ax.text(2.5, 1.3, f"R:R = 1:{rr:.1f}", ha='center', color='white', fontsize=10)
        ax.text(5.0, 1.3, f"T: +{target_pct:.1f}%", ha='center', color='#00FF88', fontsize=10)
        ax.text(7.5, 1.3, f"SL: -{sl_pct:.1f}%", ha='center', color='#FF4444', fontsize=10)

        ax.text(5.0, 0.7, f"{strategy}  |  {regime}", ha='center',
                color='#666666', fontsize=9)

        ax.text(5.0, 0.2,
                f"⚠️ Educational only | Risk 1% of YOUR capital | {datetime.now().strftime('%d-%b %H:%M')}",
                ha='center', color='#444444', fontsize=7)

        today = date.today().strftime("%Y%m%d")
        out_path = str(_OUTPUT_DIR / f"signal_{symbol}_{today}_{int(time.time())}.png")
        import time
        plt.savefig(out_path, dpi=120, bbox_inches='tight',
                    facecolor='#0D1117', edgecolor='none')
        plt.close()
        return out_path

    except Exception as e:
        logger.debug("signal_card: %s", e)
        return None
