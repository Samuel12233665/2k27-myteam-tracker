import json
import os
import re
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

RATINGS = [93, 94, 95, 96]

# 依據 2kauction 常見 URL 參數結構設定（同時保留頁面互動過濾備援）
BASE_URLS = [
    "https://2kauction.com/zh-hant/players?ovr={ovr}&sort=price_asc",
    "https://2kauction.com/zh-hant?ovr={ovr}&sort=price_asc",
    "https://2kauction.com/zh-hant"
]

def extract_mt_prices(text):
    # 擷取常見 MT 價格格式（例如 12,500 / 12500 / 45.5K / 1.2M）
    prices = []
    # 1. 純數字或千分位數字 (排除 90~99 的評分數字，鎖定 1,000 以上)
    for m in re.findall(r'\b(\d{1,3}(?:,\d{3})+|\d{4,7})\b', text):
        val = int(m.replace(',', ''))
        if 1000 <= val <= 9999999:
            prices.append(val)
    # 2. K / M 簡寫格式 (如 15.5K -> 15500)
    for num, unit in re.findall(r'\b(\d+(?:\.\d+)?)\s*([kKmM])\b', text):
        val = float(num) * (1000 if unit.lower() == 'k' else 1000000)
        if 1000 <= val <= 9999999:
            prices.append(int(val))
    return sorted(set(prices))

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
            locale="zh-Hant"
        )

        for ovr in RATINGS:
            page = context.new_page()
            min_price = None
            sample_prices = []
            target_url = f"https://2kauction.com/zh-hant?ovr={ovr}"
            status = "已更新"

            try:
                loaded = False
                for tpl in BASE_URLS:
                    url = tpl.format(ovr=ovr)
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=35000)
                    page.wait_for_timeout(4500)
                    if resp and resp.status < 400:
                        target_url = url
                        loaded = True
                        break

                if loaded:
                    # 嘗試點擊或輸入評分篩選（若網址參數未自動套用）
                    ovr_btn = page.locator(f"button:has-text('{ovr}'), [data-ovr='{ovr}']").first
                    if ovr_btn.count() > 0 and ovr_btn.is_visible():
                        ovr_btn.click()
                        page.wait_for_timeout(2500)

                    # 嘗試切換「價格由低到高」排序
                    sort_btn = page.locator("text=/價格.*低|Price.*Low|最低價/i").first
                    if sort_btn.count() > 0 and sort_btn.is_visible():
                        sort_btn.click()
                        page.wait_for_timeout(2500)

                    body_text = page.inner_text("body")
                    prices = extract_mt_prices(body_text)
                    if prices:
                        min_price = prices[0]
                        sample_prices = prices[:3]
                    else:
                        status = "暫無該等級掛單或需調整選擇器"
                else:
                    status = "網頁載入失敗"
            except Exception:
                status = "連線逾時"
            finally:
                page.close()

            prev_p = old_prices.get(str(ovr))
            daily_diff = (min_price - prev_p) if (min_price and prev_p) else 0

            results.append({
                "ovr": ovr,
                "tier_name": f"{ovr} OVR 鑽石/粉鑽/銀河級",
                "min_price": min_price,
                "top3_prices": sample_prices,
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

if __name__ == "__main__":
    main()
