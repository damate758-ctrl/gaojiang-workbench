"""Extract article images from JavaScript-rendered Chinese news pages."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


def edge_path():
    candidates = [
        Path.home() / "AppData/Local/Microsoft/Edge/Application/msedge.exe",
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    ]
    return next((path for path in candidates if path.exists()), None)


EXTRACT_SCRIPT = r"""
() => {
  const bad = /(logo|icon|avatar|emoji|qrcode|qr-code|sprite|arrow|footer|header|recommend|related|hot|ranking|sidebar|advert|ad-)/i;
  const good = /(article-content|articlecontent|article-body|detail-content|post-content|content-body|paragraph|multi-text|m-text|editor-content|rich_media_content)/i;
  const items = [];
  for (const img of document.images) {
    const url = img.currentSrc || img.src || '';
    const width = img.naturalWidth || 0;
    const height = img.naturalHeight || 0;
    if (!/^https:\/\//i.test(url) || width < 480 || height < 240) continue;
    const ratio = width / Math.max(height, 1);
    if (ratio < 1.1 || ratio > 3.2 || bad.test(url)) continue;
    let score = 0;
    let blocked = false;
    let p = img.parentElement;
    for (let i = 0; p && i < 7; i++, p = p.parentElement) {
      const marker = `${p.id || ''} ${p.className || ''}`;
      if (bad.test(marker)) blocked = true;
      if (good.test(marker)) score += 8;
    }
    const rect = img.getBoundingClientRect();
    if (rect.top >= -200 && rect.bottom <= innerHeight + 300) score += 2;
    items.push({url, alt: img.alt || '', width, height, score, blocked});
  }
  for (const meta of document.querySelectorAll('meta[property="og:image"],meta[name="twitter:image"]')) {
    const url = meta.content || '';
    if (/^https:\/\//i.test(url) && !bad.test(url) && !items.some(x => x.url === url)) {
      items.push({url, alt: '', width: 0, height: 0, score: 3, blocked: false});
    }
  }
  items.sort((a, b) => (a.blocked - b.blocked) || (b.score - a.score) || (b.width * b.height - a.width * a.height));
  const seen = new Set();
  const eligible = items.filter(x => !x.blocked && !seen.has(x.url) && seen.add(x.url));
  const best = eligible[0]?.score || 0;
  const cutoff = best >= 8 ? 8 : (best >= 3 ? 3 : 1);
  const images = eligible.filter(x => x.score >= cutoff).slice(0, 4);
  const candidates = document.querySelectorAll('article, [id="multi-text"], [class*="article-content"], [class*="articleContent"], [class*="article-body"], [class*="detail-content"], [class*="post-content"], [class*="content-body"]');
  let text = '';
  for (const node of candidates) {
    const value = (node.innerText || '').replace(/\s+/g, ' ').trim();
    if (value.length > text.length && value.length > 120) text = value;
  }
  return {images, text: text.slice(0, 8000)};
}
"""


async def extract_one(context, url):
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
        await page.wait_for_timeout(1800)
        payload = await page.evaluate(EXTRACT_SCRIPT)
        payload["page"] = page.url
        payload["title"] = await page.title()
        return url, payload
    except Exception as exc:
        return url, {"images": [], "text": "", "page": url, "title": "", "error": str(exc)}
    finally:
        await page.close()


async def run(urls):
    from playwright.async_api import async_playwright

    edge = edge_path()
    if not edge:
        return {url: {"images": [], "text": "", "page": url, "error": "Edge not found"} for url in urls}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=str(edge), headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
            viewport={"width": 1365, "height": 900},
        )
        semaphore = asyncio.Semaphore(4)

        async def limited(url):
            async with semaphore:
                return await extract_one(context, url)

        rows = await asyncio.gather(*(limited(url) for url in urls))
        await context.close()
        await browser.close()
    return {original: payload for original, payload in rows}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    data = json.load(sys.stdin)
    urls = [str(url) for url in data.get("urls", []) if str(url).startswith("https://")][:12]
    result = asyncio.run(run(urls))
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
