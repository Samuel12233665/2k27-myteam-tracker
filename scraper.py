import json
import os
import re
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

# 依你截圖的等級對應設定：93=GO, 94=DM, 95=PC, 96=MAG
TIERS = [
    {"ovr": 93, "code": "GO", "label": "GO (93)"},
    {"ovr": 94, "code": "DM", "label": "DM (94)"},
    {"ovr": 95, "code": "PC", "label": "PC (95)"},
    {"ovr": 96, "code": "MAG", "label": "MAG (96)"},
]

BROWSE_URL = "https://2kauction.com/zh-hant/players"


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
    # 嘗試點擊右上角 PC (Steam) 價格切換按鈕
    candidates = [
        "[title*='PC']",
        "[title*='Steam']",
        "[aria-label*='PC']",
        "[aria-label*='Steam']",
        "button:has-text('Steam')",
        "button:has-text('PC')",
    ]
    for sel in candidates:
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
    # 嘗試在右側「排序」下拉選單中選擇「價格：從低到高」或「最低一口價：從低到高」
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

    # 若為自訂下拉元件
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
    # 點擊右側「等級」篩選中的 GO / DM / PC / MAG 按鈕或標籤
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
                # 精準比對開頭為 GO / DM / PC / MAG（如 "GO (132)"）
                if re.match(rf"^{re.escape(code)}(\s*\(\d+\))?$", txt, re.IGNORECASE) and el.is_visible():
                    el.click(timeout=2500)
                    page.wait_for_timeout(2000)
                    return True
        except Exception:
            continue
    return False


def extract_top_card(page, ovr):
    # 抓取目前列表第一張符合 ovr 的球員卡（球員名稱、最低一口價、平均成交價、詳情連結）
    script = """
    (targetOvr) => {
      const cards = Array.from(document.querySelectorAll('a, article, [class*="card"], [class*="player"], tr'));
      for (const el of cards) {
        const text = (el.innerText || '').trim();
        if (!text || text.length < 6 || text.length > 450) continue;
        // 排除大容器
        if (el.querySelectorAll('a, article').length > 2) continue;

        const hasOvr = new RegExp(`(^|[^0-9])${targetOvr}([^0-9]|$)`).test(text);
        if (!hasOvr) continue;

        // 尋找「最低一口價」附近數值
        const buyoutMatch = text.match(/最低一口價[\\s\\S]{0,30}?(\\d+(?:\\.\\d+)?\\s*[kKmM]?|\\d{1,3}(?:,\\d{3})+)/i);
        const avgMatch = text.match(/平均成交價[\\s\\S]{0,30}?(\\d+(?:\\.\\d+)?\\s*[kKmM]?|\\d{1,3}(?:,\\d{3})+)/i);
        const anyPriceMatch = text.match(/(\\d+(?:\\.\\d+)?\\s*[kKmM]|\\d{1,3}(?:,\\d{3})+)/);

        const rawPrice = (buyoutMatch && buyoutMatch[1]) || (anyPriceMatch && anyPriceMatch[1]) || '';
        const rawAvg = (avgMatch && avgMatch[1]) || '';

        // 擷取球員名稱（跳過純數字、評分、MT、最低一口價/平均成交價等標籤）
        const lines = text.split('\\n').map(s => s.trim()).filter(Boolean);
        let playerName = '';
        for (const line of lines) {
          if (/^\\d+$/.test(line)) continue;
          if (/^(平均成交價|最低一口價|查看球員卡|翻轉球員卡|現代|GO|DM|PC|MAG|NBA 2K27)$/i.test(line)) continue;
          if (/^\\d+(?:\\.\\d+)?\\s*(k|m|mt)$/i.test(line)) continue;
          if (/^[A-Z]{1,2}(\\s*\\/\\s*[A-Z]{1,2})?$/.test(line)) continue;
          if (line.length >= 2 && line.length <= 40) {
            playerName = line;
            break;
          }
        }

        const linkEl = el.tagName === 'A' ? el : el.query
