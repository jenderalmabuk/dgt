#!/usr/bin/env python3
"""Nexus Crypto Viral & News Sentiment Radar.

Monitors high-impact crypto news sources (Binance Listings, Cointelegraph,
CoinDesk, Decrypt), extracts entities & sentiment using local 9router LLM,
maintains real-time sentiment state for trading bots, and sends Telegram alerts.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [sentiment_radar] %(message)s",
)
logger = logging.getLogger("sentiment_radar")

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent
RUNTIME_DIR = BASE_DIR / "runtime" / "sentiment"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = RUNTIME_DIR / "latest_sentiment.json"
SEEN_FILE = RUNTIME_DIR / "seen_articles.json"
HISTORY_FILE = RUNTIME_DIR / "sentiment_history.jsonl"

# Configuration
ROUTER_BASE = os.getenv("NINE_ROUTER_BASE", "http://localhost:20128/v1").rstrip("/")
# If running on host, replace host.docker.internal with localhost
if "host.docker.internal" in ROUTER_BASE and not os.path.exists("/.dockerenv"):
    ROUTER_BASE = ROUTER_BASE.replace("host.docker.internal", "localhost")

ROUTER_KEY = os.getenv("NINE_ROUTER_API_KEY", "sk-0b1153f1a8ae386c-rvqg8m-99aa5f44")
MODEL = os.getenv("ADVERSARIAL_MODEL", "Free-Tiers")

TELEGRAM_TOKEN = (
    os.getenv("FUSIONNEW_TELEGRAM_BOT_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("FUSION_TELEGRAM_TOKEN")
)
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "578305627")


def send_telegram(text: str) -> None:
    """Send formatted Telegram notification."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": int(TELEGRAM_CHAT_ID),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        data = urllib.parse.urlencode(payload).encode()
        req = urllib.request.Request(url, data=data, timeout=8)
        with urllib.request.urlopen(req) as resp:
            pass
    except Exception as e:
        logger.debug(f"Failed to send Telegram alert: {e}")


def load_json(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return default
    return default


def save_json(path: Path, data: Any) -> None:
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.rename(path)
    except Exception as e:
        logger.error(f"Failed to save {path}: {e}")


# --- Ingestion Feed Collectors ---


def fetch_binance_listings() -> List[Dict[str, Any]]:
    """Fetch recent new listings and asset additions from Binance."""
    articles = []
    url = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageSize=10&pageNo=1"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.loads(resp.read().decode())
        catalogs = res.get("data", {}).get("catalogs", [])
        for cat in catalogs:
            for art in cat.get("articles", []):
                articles.append({
                    "id": f"binance_{art.get('id')}",
                    "title": art.get("title", ""),
                    "source": "Binance Listing",
                    "url": f"https://www.binance.com/en/support/announcement/{art.get('code')}",
                    "timestamp": art.get("releaseDate", int(time.time() * 1000)) / 1000.0,
                    "is_listing": True,
                })
    except Exception as e:
        logger.debug(f"Failed to fetch Binance listings: {e}")
    return articles


def fetch_rss_feed(feed_url: str, source_name: str) -> List[Dict[str, Any]]:
    """Generic XML RSS feed parser."""
    articles = []
    try:
        req = urllib.request.Request(feed_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            tree = ET.fromstring(resp.read())
        for item in tree.findall(".//item")[:15]:
            title_elem = item.find("title")
            guid_node = item.find("guid")
            link_node = item.find("link")
            guid_elem = guid_node if guid_node is not None else link_node
            link_elem = link_node
            title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""
            link = link_elem.text.strip() if link_elem is not None and link_elem.text else ""
            guid = guid_elem.text.strip() if guid_elem is not None and guid_elem.text else link
            if title:
                articles.append({
                    "id": f"{source_name}_{guid}",
                    "title": title,
                    "source": source_name,
                    "url": link,
                    "timestamp": time.time(),
                    "is_listing": "listing" in title.lower() or "list" in title.lower(),
                })
    except Exception as e:
        logger.debug(f"Failed to fetch {source_name} RSS: {e}")
    return articles


def fetch_telegram_radar_feed() -> List[Dict[str, Any]]:
    """Fetch intelligence posts from curated Telegram channels (CEX listing, Whale, News)."""
    items = []
    feed_paths = [
        Path("/opt/signalcopyreal/runtime/state/radar_telegram_feed.jsonl"),
        RUNTIME_DIR / "radar_telegram_feed.jsonl",
    ]
    for p in feed_paths:
        if not p.exists():
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                lines = f.readlines()[-60:]
            for line in lines:
                if not line.strip():
                    continue
                obj = json.loads(line)
                txt = obj.get("text", "").strip()
                if len(txt) < 15:
                    continue
                # Unique ID based on hash of text & channel
                post_hash = hashlib.md5(f"{obj.get('source_id')}_{txt[:80]}".encode()).hexdigest()[:12]
                channel_name = obj.get("source_name") or f"TG {obj.get('source_id')}"
                items.append({
                    "id": f"tg_{post_hash}",
                    "title": txt[:250].replace("\n", " "),
                    "source": f"Telegram: {channel_name}",
                    "url": f"https://t.me/c/{str(obj.get('source_id')).replace('-100', '')}",
                    "timestamp": obj.get("timestamp", time.time()),
                    "is_listing": any(k in txt.lower() for k in ["listing", "list", "listed", "binance", "bybit", "coinbase", "hyperliquid"]),
                })
        except Exception as e:
            logger.debug(f"Failed to read telegram radar feed from {p}: {e}")
    return items


def poll_all_sources() -> List[Dict[str, Any]]:
    """Gather headlines across all configured feeds."""
    all_news = []
    all_news.extend(fetch_binance_listings())
    all_news.extend(fetch_telegram_radar_feed())
    all_news.extend(fetch_rss_feed("https://cointelegraph.com/rss", "Cointelegraph"))
    all_news.extend(fetch_rss_feed("https://www.coindesk.com/arc/outboundfeeds/rss", "CoinDesk"))
    all_news.extend(fetch_rss_feed("https://decrypt.co/feed", "Decrypt"))
    all_news.extend(fetch_rss_feed("https://news.google.com/rss/search?q=Binance+listing+when:1d&hl=en-US&gl=US&ceid=US:en", "GoogleNews Listing"))
    all_news.extend(fetch_rss_feed("https://news.google.com/rss/search?q=crypto+exploit+OR+hack+when:1d&hl=en-US&gl=US&ceid=US:en", "GoogleNews Security"))
    return all_news


# --- LLM Entity & Sentiment Analyzer ---


def analyze_sentiment_batch(news_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Analyze news headlines in batches using local 9router LLM."""
    if not news_items or not ROUTER_KEY:
        return []

    titles_payload = [
        {"idx": i, "title": item["title"], "source": item["source"]}
        for i, item in enumerate(news_items)
    ]

    prompt = f"""Anda analis sentimen kripto presisi tinggi. Analisis daftar berita berikut dalam format JSON Array.
Daftar Berita:
{json.dumps(titles_payload, ensure_ascii=False)}

Tugas:
1. Identifikasi simbol koin/ticker kripto (misal BTC, ETH, SOL, SUI, DOGE, dll). Jika tidak ada koin spesifik atau murni makro/fiat, gunakan 'MACRO'.
2. Tentukan sentimen: BULLISH, BEARISH, atau NEUTRAL.
3. Berikan skor sentimen antara -1.0 (sangat bearish/hack) s/d +1.0 (sangat bullish/listing).
4. Tentukan category: LISTING, PARTNERSHIP, REGULATION, HACK_EXPLOIT, MACRO, TECH, atau GENERAL.
5. Rangkum inti berita dalam 1 kalimat Bahasa Indonesia.

Format respons WAJIB hanya JSON Array valid tanpa teks pembuka/penutup:
[
  {{
    "idx": 0,
    "symbol": "BTC",
    "sentiment": "BULLISH",
    "score": 0.8,
    "category": "LISTING",
    "summary": "Ringkasan singkat berita..."
  }}
]
"""
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a professional crypto sentiment classification engine. Respond with JSON array only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }).encode()

    try:
        req = urllib.request.Request(
            f"{ROUTER_BASE}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {ROUTER_KEY}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace").strip()

        if raw.startswith("data:"):
            raw = raw.split("data:", 1)[1].strip()

        data, _ = json.JSONDecoder().raw_decode(raw)
        content = data["choices"][0]["message"]["content"].strip()

        # Extract JSON Array block if surrounded by markdown code blocks
        if "```json" in content:
            content = content.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in content:
            content = content.split("```", 1)[1].split("```", 1)[0].strip()

        parsed = json.loads(content)
        results = []
        for res in parsed:
            idx = res.get("idx")
            if idx is not None and 0 <= idx < len(news_items):
                item = dict(news_items[idx])
                item.update({
                    "symbol": str(res.get("symbol", "UNKNOWN")).upper().replace("$", ""),
                    "sentiment": res.get("sentiment", "NEUTRAL"),
                    "score": float(res.get("score", 0.0)),
                    "category": res.get("category", "GENERAL"),
                    "summary": res.get("summary", ""),
                    "analyzed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                })
                results.append(item)
        return results
    except Exception as e:
        logger.error(f"Error in LLM sentiment analysis: {e}")
        return []


class SentimentRadar:
    def __init__(self):
        self.seen_ids = set(load_json(SEEN_FILE, []))
        self.latest_sentiment = load_json(STATE_FILE, {})

    def run_cycle(self) -> None:
        """Run single ingestion and analysis cycle."""
        logger.info("Scanning crypto news feeds & announcements...")
        all_news = poll_all_sources()
        new_items = [n for n in all_news if n["id"] not in self.seen_ids]

        if not new_items:
            logger.info("No new news items detected.")
            return

        if not self.seen_ids and len(new_items) > 6:
            logger.info(f"Initial run: caching {len(new_items)} historical items, analyzing top 5 latest...")
            for n in new_items:
                self.seen_ids.add(n["id"])
            items_to_analyze = new_items[:5]
        else:
            items_to_analyze = new_items[:15]

        logger.info(f"Processing {len(items_to_analyze)} headlines with NLP...")
        analyzed_items = []
        batch_size = 4
        for i in range(0, len(items_to_analyze), batch_size):
            batch = items_to_analyze[i:i + batch_size]
            results = analyze_sentiment_batch(batch)
            analyzed_items.extend(results)

        # Update persistent state
        for item in analyzed_items:
            self.seen_ids.add(item["id"])
            sym = item.get("symbol")
            if sym and sym not in ("UNKNOWN", "NONE", "CRYPTO", "MACRO"):
                # Normalize symbol (e.g. BTC -> BTCUSDT)
                clean_sym = sym if sym.endswith("USDT") else f"{sym}USDT"
                self.latest_sentiment[clean_sym] = {
                    "symbol": clean_sym,
                    "sentiment": item["sentiment"],
                    "score": item["score"],
                    "category": item["category"],
                    "summary": item["summary"],
                    "headline": item["title"],
                    "source": item["source"],
                    "url": item.get("url", ""),
                    "timestamp": item.get("analyzed_at"),
                }

                # High-impact alert trigger (Listing or strong sentiment |score| >= 0.7)
                if item["category"] == "LISTING" or abs(item["score"]) >= 0.7:
                    icon = "🚀" if item["score"] > 0 else "🚨"
                    sent_icon = "🟢" if item["sentiment"] == "BULLISH" else ("🔴" if item["sentiment"] == "BEARISH" else "⚪")
                    send_telegram(
                        f"{icon} <b>[Sentiment Radar Alert]</b>\n"
                        f"<b>Pair:</b> #{clean_sym}\n"
                        f"<b>Category:</b> {item['category']}\n"
                        f"<b>Sentiment:</b> {sent_icon} {item['sentiment']} ({item['score']:+.2f})\n"
                        f"<b>Source:</b> {item['source']}\n"
                        f"<b>Detail:</b> {item['summary']}\n"
                        f"<a href=\"{item['url']}\">Baca Sumber Berita</a>"
                    )

            # Append to history file
            try:
                with open(HISTORY_FILE, "a") as f:
                    f.write(json.dumps(item, default=str) + "\n")
            except Exception:
                pass

        # Save states
        save_json(SEEN_FILE, list(self.seen_ids)[-1000:])  # retain last 1000 seen
        save_json(STATE_FILE, self.latest_sentiment)
        logger.info(f"Updated sentiment state for {len(self.latest_sentiment)} tracked pairs.")


# --- Public Helper for Trading Bots ---


def get_symbol_sentiment(symbol: str, max_age_hours: int = 12) -> Optional[Dict[str, Any]]:
    """Helper for DGT / Nexus trading bots to check sentiment confluence."""
    clean = symbol.upper()
    if not clean.endswith("USDT"):
        clean += "USDT"
    data = load_json(STATE_FILE, {})
    entry = data.get(clean)
    if not entry:
        return None
    try:
        t_str = entry.get("timestamp")
        if t_str:
            t = dt.datetime.fromisoformat(t_str)
            age = (dt.datetime.now(dt.timezone.utc) - t).total_seconds()
            if age > max_age_hours * 3600:
                return None
    except Exception:
        pass
    return entry


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nexus Crypto Sentiment & Viral Radar")
    parser.add_argument("--daemon", action="store_true", help="Run continuously in background")
    parser.add_argument("--interval", type=int, default=180, help="Polling interval in seconds (default: 180s)")
    args = parser.parse_args()

    radar = SentimentRadar()
    if args.daemon:
        logger.info(f"Starting Sentiment Radar Daemon (poll interval: {args.interval}s)...")
        while True:
            try:
                radar.run_cycle()
            except Exception as e:
                logger.error(f"Error in radar cycle: {e}", exc_info=True)
            time.sleep(args.interval)
    else:
        logger.info("Running single Sentiment Radar scan cycle...")
        radar.run_cycle()
