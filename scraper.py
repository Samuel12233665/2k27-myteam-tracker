import json
import os
import re
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

RATINGS = [93, 94, 95, 96]
TARGET_URLS = [
    "https://2kauction.com/zh-hant/players",
    "https://2kauction.com/zh-hant"
]


def parse_price_value(raw):
    if not raw:
        return None
    s = raw.strip().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*([kKmM])?", s)
    if not m:
        return None
    val = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if unit == "k":
        val *= 1000
    elif unit == "m":
        val *= 1000000
    num = int(val)
    return num if 500 <= num <= 50000000 else None


def extract_cards_for_ovr(page, target_ovr):
    script = """
    (ovr) => {
      const results = [];
      const allEls = Array.from(document.querySelectorAll('a, tr, li, div, article, section'));
      for (const el of allEls) {
        const text = (el.innerText || '').trim();
        if (!text || text.length < 4 || text.length > 500) continue;
        const childCards = el.querySelectorAll('a, tr, article').length;
        if (childCards > 5) continue;

        const hasExactNumber = new RegExp(`(^|\\s|[^0-9])${ovr}(\\s|[^0-9]|$)`).test(text);
        if (!hasExactNumber) continue;

        const priceMatches = Array.from(text.matchAll(/(\\d{1,3}(?:,\\d{3})+|\\d{4,7}|\\d+(?:\\.\\d+)?\\s*[kKmM])/g))
          .map(m => m[0].trim())
          .filter(token => !['93', '94', '95', '96', '2026', '2027'].includes(token));

        if (priceMatches.length === 0) continue;

        const lines = text.split('\\n').map(s => s.trim()).filter(Boolean);
        const playerCandidate = lines.find(l =>
          l.length >= 2 &&
          l.length <= 50 &&
          !/^[0-9,.$kKmM]+$/.test(l) &&
          !/^(MT|OVR|總評|評分|價格|最低價|拍賣|搜尋|篩選)$/i.test(l)
        ) || (lines[0] || '未知球員');

        results.push({
          player: playerCandidate,
          price_tokens: priceMatches,
          href: (el.closest('a') || el.querySelector('a') || {}).href || ''
        });
      }
      return results;
    }
    """
    raw_matches = page.evaluate(script, target_ovr)
    parsed = []
    seen = set()
    for item in raw_matches:
        player = (item.get("player") or "未知球員").strip()
        for tok in item.get("price_tokens", []):
            val = parse_price_value(tok)
            if val is not None:
                key = (player.lower(), val)
                if key in seen:
                    continue
                seen.add(key)
                parsed.append({
                    "player": player,
                    "price": val,
                    "url": item.get("href") or page.url
                })
    parsed.sort(key=lambda x: x["price"])
    return parsed


def try_filter_by_ovr(page, ovr):
    # 嘗試在頁面上點選對應評分的篩選按鈕、分頁或下拉選項
    selectors = [
        f"button:has-text('{ovr}')",
        f"a:has-text('{ovr}')",
        f"[data-ovr='{ovr}']",
        f"[data-rating='{ovr}']",
        f"label:has-text('{ovr}')"
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=2000)
                page.wait_for_timeout(1500)
                return True
        except Exception:
            continue
    return False


def main():
    hk_tz = timezone(timedelta(hours=8))
    now_str = datetime.now(hk_tz).strftime("%Y-%m-%d %H:%M")

    old_prices = {}
    if os.path.exists("data.json"):
        try:
            with open("data.json", "r", encoding="utf-8") as f:
                old_data = json.load(f)
                for item in old_data.get("items", []):
                    if item.get("min_price"):
                        old_prices[str(item["ovr"])] = item["min_price"]
        except Exception:
            pass

    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            locale="zh-HK"
        )

        page = context.new_page()
        active_url = TARGET_URLS[0]
        page_loaded = False

        for url in TARGET_URLS:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(5000)
                for _ in range(10):
                    page.mouse.wheel(0, 1400)
                    page.wait_for_timeout(800)
                body_txt = (page.inner_text("body") or "").strip()
                if len(body_txt) > 100:
                    active_url = url
                    page_loaded = True
                    break
            except Exception:
                continue

        for ovr in RATINGS:
            min_price = None
            lowest_player = "-"
            top3_list = []
            target_url = active_url
            status = "已更新" if page_loaded else "網頁載入失敗"

            if page_loaded:
                try:
                    matches = extract_cards_for_ovr(page, ovr)
                    if not matches:
                        # 若目前畫面沒有該評分，嘗試點選篩選器並重新捲動
                        try_filter_by_ovr(page, ovr)
                        for _ in range(5):
