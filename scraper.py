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

    debug_dump = {
        "url": BROWSE_URL,
        "title": "",
        "body_lines_sample": [],
        "clickable_texts_sample": [],
        "select_options": [],
    }
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            locale="zh-HK",
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()

        try:
            page.goto(BROWSE_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)

            debug_dump["title"] = page.title()
            body_text = page.inner_text("body") or ""
            lines = [l.strip() for l in body_text.splitlines() if l.strip()]
            debug_dump["body_lines_sample"] = lines[:120]

            # 收集頁面上所有 select 選項
            opts = page.evaluate("""
                () => Array.from(document.querySelectorAll('select')).map((s, idx) => ({
                    index: idx,
                    options: Array.from(s.options).map(o => ({ text: o.innerText.trim(), value: o.value }))
                }))
            """)
            debug_dump["select_options"] = opts

            # 收集包含 GO / DM / PC / MAG / 價格 / Steam / 排序 的按鈕或連結文字
            ctrl_texts = page.evaluate("""
                () => {
                  const els = Array.from(document.querySelectorAll('button, a, label, [role="button"]'));
                  return els
                    .map(e => (e.innerText || e.title || e.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' '))
                    .filter(t => t && t.length <= 40 && /(GO|DM|PC|MAG|Steam|價格|排序|最低一口價|瀏覽)/i.test(t))
                    .slice(0, 80);
                }
            """)
            debug_dump["clickable_texts_sample"] = list(dict.fromkeys(ctrl_texts))

        except Exception as e:
            debug_dump["error"] = str(e)

        browser.close()

    for tier in TIERS:
        ovr = tier["ovr"]
        prev_p = old_prices.get(str(ovr))
        results.append({
            "ovr": ovr,
            "tier_code": tier["code"],
            "tier_label": tier["label"],
            "player": "待對齊真實卡片結構",
            "min_price": None,
            "raw_price_text": "-",
            "avg_price": None,
            "trend_note": "",
            "daily_diff": 0 if prev_p is None else 0,
            "status": "已收集頁面結構",
            "url": BROWSE_URL,
        })

    with open("debug.json", "w", encoding="utf-8") as f:
        json.dump(debug_dump, f, ensure_ascii=False, indent=2)

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": now_str,
            "platform": "PC (Steam)",
            "sort": "價格：從低到高",
            "items": results,
        }, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
