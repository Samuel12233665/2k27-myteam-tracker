import json
import os
import re
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

RATINGS = [93, 94, 95, 96]
CANDIDATE_URLS = [
    "https://2kauction.com/zh-hant",
    "https://2kauction.com/zh-hant/players"
]

def parse_price_value(raw):
    if not raw:
        return None
    s = raw.strip().replace(",", "")
    m = re.search(r'(\d+(?:\.\d+)?)\s*([kKmM])?', s)
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

def inspect_page_and_extract(page, target_ovr):
    script = """
    (ovr) => {
      const results = [];
      const allEls = Array.from(document.querySelectorAll('a, tr, li, div, article, section'));
      for (const el of allEls) {
        const text = (el.innerText || '').trim();
        if (!text || text.length < 4 || text.length > 500) continue;
        // 避免抓到過大的外層容器
        const childCards = el.querySelectorAll('a, tr, article').length;
        if (childCards > 5) continue;

        const hasExactNumber = new RegExp(`(^|\\s|[^0-9])${ovr}(\\s|[^0-9]|$)`).test(text);
        if (!hasExactNumber) continue;

        // 抓取任何可能是價格的數字或 K/M 數值（排除 93~96 評分本身）
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
          snippet: text.replace(/\\n+/g, ' | ').slice(0, 140),
          href: (el.closest('a') || el.querySelector('a') || {}).href || ''
        });
      }
      return results;
    }
    """
    raw_matches = page.evaluate(script, target_ovr)
    parsed = []
    for item in raw_matches:
        for tok in item.get("price_tokens", []):
            val = parse_price_value(tok)
            if val is not None:
                parsed.append({
                    "player": item.get("player") or "未知球員",
                    "price": val,
                    "snippet": item.get("snippet", ""),
                    "url": item.get("href") or page.url
                })
    parsed.sort(key=lambda x: x["price"])
    return parsed

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
    debug_info = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            locale="zh-HK"
        )

        page = context.new_page()
        active_url = CANDIDATE_URLS[0]
        page_text_preview = ""

        for url in CANDIDATE_URLS:
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(5000)
                for _ in range(4):
                    page.mouse.wheel(0, 1000)
                    page.wait_for_timeout(1000)
                body_txt = (page.inner_text("body") or "").strip()
                if len(body_txt) > 100:
                    active_url = url
                    page_text_preview = body_txt[:400].replace("\n", " | ")
                    break
            except Exception:
                continue

        debug_info["active_url"] = active_url
        debug_info["page_text_preview"] = page_text_preview

        for ovr in RATINGS:
            min_price = None
            lowest_player = "-"
            top3_list = []
            target_url = active_url
            status = "已更新"

            try:
                matches = inspect_page_and_extract(page, ovr)
                debug_info[str(ovr)] = [m["snippet"] for m in matches[:3]]
                if matches:
                    min_price = matches[0]["price"]
                    lowest_player = matches[0]["player"]
                    target_url = matches[0]["url"]
                    top3_list = [f"{m['player']} ({m['price']:,})" for m in matches[:3]]
                    status = "已更新"
                else:
                    status = "未找到符合卡片"
            except Exception:
                status = "解析異常"

            prev_p = old_prices.get(str(ovr))
            daily_diff = (min_price - prev_p) if (min_price and prev_p) else 0

            results.append({
                "ovr": ovr,
                "player": lowest_player,
                "min_price": min_price,
                "top3_summary": top3_list,
                "daily_diff": daily_diff,
                "status": status,
                "url": target_url
            })

        browser.close()

    output = {
        "updated_at": now_str,
        "game": "NBA 2K27 MyTEAM (2kauction.com)",
        "items": results
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    with open("debug.json", "w", encoding="utf-8") as f:
        json.dump(debug_info, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
