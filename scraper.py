import json
import os
import re
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

RATINGS = [93, 94, 95, 96]
PLAYERS_URL = "https://2kauction.com/zh-hant/players"

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

def extract_from_dom(page, target_ovr):
    # 在頁面內尋找包含 target_ovr 與價格資訊的卡片/列表元素，並配對球員名稱
    script = """
    (ovr) => {
      const candidates = [];
      const nodes = Array.from(document.querySelectorAll('a, article, tr, li, div'));
      for (const el of nodes) {
        const text = (el.innerText || '').trim();
        if (!text || text.length > 350) continue;
        // 避免抓到整頁大容器
        if (el.querySelectorAll('a, article, tr').length > 4) continue;

        const hasOvr = new RegExp(`(^|[^0-9])${ovr}(\\s*(OVR|總評|等級|評分))?([^0-9]|$)`, 'i').test(text);
        if (!hasOvr) continue;

        // 尋找價格字串（如 12,500 MT、45.5K、1.2M）
        const priceMatch = text.match(/(\\d{1,3}(?:,\\d{3})+|\\d+(?:\\.\\d+)?)\\s*(?:MT|[kKmM])\\b/);
        if (!priceMatch) continue;

        // 擷取非數字、非 MT 價格的文字行作為球員名稱候選
        const lines = text.split('\\n').map(l => l.trim()).filter(l => l.length > 1);
        let playerName = '';
        for (const line of lines) {
          if (/^\\d+$/.test(line)) continue;
          if (/^(OVR|總評|評分|MT|價格|BID|BUYOUT)$/i.test(line)) continue;
          if (/(\\d{1,3}(?:,\\d{3})+|\\d+(?:\\.\\d+)?)\\s*(MT|[kKmM])/i.test(line)) continue;
          if (line.length >= 2 && line.length <= 45) {
            playerName = line;
            break;
          }
        }
        if (!playerName) {
          const link = el.querySelector('a') || (el.tagName === 'A' ? el : null);
          if (link && link.innerText) {
            playerName = link.innerText.trim().split('\\n')[0];
          }
        }

        candidates.push({
          player: playerName || '未知球員',
          raw_price: priceMatch[0],
          href: (el.closest('a') || el.querySelector('a') || {}).href || ''
        });
      }
      return candidates;
    }
    """
    raw_items = page.evaluate(script, target_ovr)
    parsed = []
    for c in raw_items:
        p_val = parse_price_value(c.get("raw_price"))
        if p_val is not None:
            parsed.append({
                "player": c.get("player") or "未知球員",
                "price": p_val,
                "url": c.get("href") or PLAYERS_URL
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
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            locale="zh-HK"
        )

        page = context.new_page()
        page_loaded = False
        try:
            page.goto(PLAYERS_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)
            # 往下捲動以觸發懶載入（Lazy Load）球員卡列表
            for _ in range(5):
                page.mouse.wheel(0, 1200)
                page.wait_for_timeout(1200)
            page_loaded = True
        except Exception:
            page_loaded = False

        for ovr in RATINGS:
            min_price = None
            lowest_player = "-"
            top3_list = []
            target_url = PLAYERS_URL
            status = "已更新" if page_loaded else "網頁載入失敗"

            if page_loaded:
                try:
                    matches = extract_from_dom(page, ovr)
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

if __name__ == "__main__":
    main()
