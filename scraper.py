import json
import os
import re
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

TIERS = [
    {"ovr": 93, "code": "GO", "label": "GO (93)"},
    {"ovr": 94, "code": "DM", "label": "DM (94)"},
    {"ovr": 95, "code": "PC", "label": "PC (95)"},
    {"ovr": 96, "code": "MAG", "label": "MAG (96)"},
]

BROWSE_URL = "https://2kauction.com/zh-hant/players"

JS_EXTRACT_TOP_CARD = r"""
(targetOvr) => {
  const cards = Array.from(document.querySelectorAll('a, article, [class*="card"], [class*="player"], tr'));
  for (const el of cards) {
    const text = (el.innerText || '').trim();
    if (!text || text.length < 6 || text.length > 500) continue;
    if (el.querySelectorAll('a, article').length > 2) continue;

    const hasOvr = new RegExp('(^|[^0-9])' + targetOvr + '([^0-9]|$)').test(text);
    if (!hasOvr) continue;

    const buyoutMatch = text.match(/最低一口價[\s\S]{0,35}?(\d+(?:\.\d+)?\s*[kKmM]?|\d{1,3}(?:,\d{3})+)/i);
    const avgMatch = text.match(/平均成交價[\s\S]{0,35}?(\d+(?:\.\d+)?\s*[kKmM]?|\d{1,3}(?:,\d{3})+)/i);
    const anyPriceMatch = text.match(/(\d+(?:\.\d+)?\s*[kKmM]|\d{1,3}(?:,\d{3})+)/);

    const rawPrice = (buyoutMatch && buyoutMatch[1]) || (anyPriceMatch && anyPriceMatch[1]) || '';
    const rawAvg = (avgMatch && avgMatch[1]) || '';

    const lines = text.split('\n').map(s => s.trim()).filter(Boolean);
    let playerName = '';
    for (const line of lines) {
      if (/^\d+$/.test(line)) continue;
      if (/^(平均成交價|最低一口價|查看球員卡|翻轉球員卡|現代|GO|DM|PC|MAG|NBA 2K27)$/i.test(line)) continue;
      if (/^\d+(?:\.\d+)?\s*(k|m|mt)$/i.test(line)) continue;
      if (/^[A-Z]{1,2}(\s*\/\s*[A-Z]{1,2})?$/.test(line)) continue;
      if (line.length >= 2 && line.length <= 40) {
        playerName = line;
        break;
      }
    }

    const linkEl = el.tagName === 'A' ? el : el.querySelector('a');
    const href = (linkEl && linkEl.href) || '';

    if (playerName || rawPrice) {
      return {
        player: playerName || '未知球員',
        raw_buyout: rawPrice,
        raw_avg: rawAvg,
        url: href
      };
    }
  }
  return null;
}
"""


def parse_mt(raw):
    if not raw:
        return None
    s = raw.strip().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:k|m|mt)?", s, re.IGNORECASE)
    if not m:
        return None
    num = float(m.group(1))
    low = s.lower()
    if "m" in low:
        num *= 1_000_000
    elif "k" in low:
        num *= 1_000
    val = int(round(num))
    return val if 100 <= val <= 100_000_000 else None


def switch_to_pc_steam(page):
    selectors = [
        "[title*='PC']",
        "[title*='Steam']",
        "[aria-label*='PC']",
        "[aria-label*='Steam']",
        "button:has-text('Steam')",
        "button:has-text('PC')",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            for i in range(loc.count()):
                el = loc.nth(i)
                if el.is_visible():
                    el.click(timeout=2000)
                    page.wait_for_timeout(1200)
                    return True
        except Exception:
            continue
    return False


def select_price_sort_asc(page):
    try:
        selects = page.locator("select")
        for i in range(selects.count()):
            sel = selects.nth(i)
            if not sel.is_visible():
                continue
            options = sel.locator("option")
            for j in range(options.count()):
                opt_text = (options.nth(j).inner_text() or "").strip()
                opt_val = options.nth(j).get_attribute("value") or ""
                if ("價格" in opt_text and "低到高" in opt_text) or (
                    "最低一口價" in opt_text and "低到高" in opt_text
                ):
                    sel.select_option(value=opt_val if opt_val else opt_text)
                    page.wait_for_timeout(1800)
                    return True
    except Exception:
        pass

    for pattern in ["價格：從低到高", "最低一口價：從低到高", "從低到高"]:
        try:
            loc = page.get_by_text(pattern, exact=False).first
            if loc.is_visible():
                loc.click(timeout=2000)
                page.wait_for_timeout(1500)
                return True
        except Exception:
            continue
    return False


def click_tier_filter(page, code):
    patterns = [
        f"button:has-text('{code}')",
        f"a:has-text('{code}')",
        f"label:has-text('{code}')",
        f"span:has-text('{code}')",
    ]
    for pat in patterns:
        try:
            loc = page.locator(pat)
            for i in range(loc.count()):
                el = loc.nth(i)
                txt = (el.inner_text() or "").strip()
                if re.match(rf"^{re.escape(code)}(\s*\(\d+\))?$", txt, re.IGNORECASE) and el.is_visible():
                    el.click(timeout=2500)
                    page.wait_for_timeout(2000)
                    return True
        except Exception:
            continue
    return False


def extract_top_card(page, ovr):
    info = page.evaluate(JS_EXTRACT_TOP_CARD, ovr)
    if not info:
        return None
    price_val = parse_mt(info.get("raw_buyout"))
    avg_val = parse_mt(info.get("raw_avg"))
    return {
        "player": info.get("player") or "未知球員",
        "min_price": price_val,
        "raw_price_text": info.get("raw_buyout") or "-",
        "avg_price": avg_val,
        "url": info.get("url") or BROWSE_URL,
    }


def extract_detail_trend(context, detail_url):
    if not detail_url or detail_url == BROWSE_URL:
        return ""
    detail_page = context.new_page()
    try:
        detail_page.goto(detail_url, wait_until="domcontentloaded", timeout=25000)
        detail_page.wait_for_timeout(2000)
        body = detail_page.inner_text("body") or ""
        for line in [l.strip() for l in body.splitlines() if l.strip()]:
            if any(k in line for k in ["最低一口價", "平均成交價", "24h", "7d", "%", "▲", "▼", "+", "-"]) and len(line) <= 70:
                if re.search(r"[%▲▼]|[+-]\d+(\.\d+)?", line):
                    return line
        return ""
    except Exception:
        return ""
    finally:
        detail_page.close()


def main():
    hk_tz = timezone(timedelta(hours=8))
    now_str = datetime.now(hk_tz).strftime("%Y-%m-%d %H:%M")

    old_prices = {}
    if os.path.exists("data.json"):
        try:
            with open("data.json", "r", encoding="utf-8") as f:
                old_data = json.load(f)
                for item in old_data.get("items", []):
                    if item.get("min_price") is not None:
                        old_prices[str(item["ovr"])] = item["min_price"]
        except Exception:
            pass

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            locale="zh-HK",
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()

        page_ok = False
        try:
            page.goto(BROWSE_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
            switch_to_pc_steam(page)
            select_price_sort_asc(page)
            page_ok = True
        except Exception:
            page_ok = False

        for tier in TIERS:
            ovr = tier
