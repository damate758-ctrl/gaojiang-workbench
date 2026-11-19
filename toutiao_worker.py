"""Edge automation for Toutiao review staging and direct publishing."""

from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DATA_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ROOT
PROFILE = DATA_ROOT / ".toutiao-profile"
STATUS = DATA_ROOT / ".publish-status.json"
WORKER_PID = DATA_ROOT / ".publish-worker.pid"
EDITOR_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"
LOGIN_URL = "https://mp.toutiao.com/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)


class LoginRequired(RuntimeError):
    """The persistent Toutiao profile has no valid login session."""


def write_status(state: str, message: str, **extra):
    payload = {"state": state, "message": message, **extra}
    STATUS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def find_edge():
    candidates = [
        Path.home() / "AppData/Local/Microsoft/Edge/Application/msedge.exe",
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    ]
    return next((path for path in candidates if path.exists()), None)


def live_pages(context):
    return [page for page in context.pages if not page.is_closed()]


def page_score(page):
    try:
        url = page.url.casefold()
    except Exception:
        return -1
    if "mp.toutiao.com" in url:
        return 4
    if url.startswith("https://"):
        return 2
    if url in {"", "about:blank", "edge://newtab/"}:
        return 0
    return 1


def choose_page(context):
    """Reuse the real Toutiao tab instead of activating a trailing about:blank tab."""
    pages = live_pages(context)
    if not pages:
        return context.new_page()
    page = max(pages, key=page_score)
    for other in pages:
        if other is page:
            continue
        try:
            if other.url in {"", "about:blank", "edge://newtab/"}:
                other.close()
        except Exception:
            continue
    try:
        page.bring_to_front()
    except Exception:
        pass
    return page


def navigate(context, url):
    page = choose_page(context)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except Exception as first_error:
        try:
            page.goto(url, wait_until="commit", timeout=30_000)
            page.wait_for_timeout(1800)
        except Exception:
            raise RuntimeError(f"头条页面打开失败：{first_error}") from first_error
    try:
        page.bring_to_front()
    except Exception:
        pass
    return page


TITLE_SELECTORS = [
    "textarea[placeholder*='请输入文章标题']",
    "input[placeholder*='请输入文章标题']",
    "textarea[placeholder*='标题']",
    "input[placeholder*='标题']",
    "[class*='title'] textarea",
]


def find_editor_control(context):
    pages = sorted(live_pages(context), key=page_score, reverse=True)
    for page in pages:
        for selector in TITLE_SELECTORS:
            locator = page.locator(selector)
            try:
                if locator.count() and locator.first.is_visible(timeout=500):
                    return page, locator.first
            except Exception:
                continue
    return None, None


def wait_for_login_or_editor(context, timeout_seconds=300):
    for _ in range(timeout_seconds // 2):
        page, title = find_editor_control(context)
        if title is not None:
            try:
                page.bring_to_front()
            except Exception:
                pass
            return page, title
        choose_page(context).wait_for_timeout(1500)
    return choose_page(context), None


def choose_editor(page):
    candidates = page.locator("div[contenteditable='true']")
    best, best_area = None, 0
    for index in range(min(candidates.count(), 16)):
        locator = candidates.nth(index)
        try:
            if not locator.is_visible():
                continue
            box = locator.bounding_box()
            if not box:
                continue
            area = box.get("width", 0) * box.get("height", 0)
            if area > best_area:
                best, best_area = locator, area
        except Exception:
            continue
    return best


def comparable_heading(text):
    return re.sub(r"[\W_]+", "", str(text or ""), flags=re.UNICODE).casefold()


def strip_duplicate_heading(content, titles):
    text = str(content or "").lstrip()
    lines = text.splitlines()
    if not lines:
        return ""
    if isinstance(titles, str):
        titles = [titles]
    aliases = {
        comparable_heading(title)
        for title in titles
        if comparable_heading(title)
    }
    def is_heading(value):
        candidate = comparable_heading(value)
        if candidate in aliases:
            return True
        return any(
            len(alias) >= 10
            and candidate.startswith(alias)
            and 0 < len(candidate) - len(alias) <= 40
            for alias in aliases
        )
    removed = 0
    while lines:
        while lines and not lines[0].strip():
            lines.pop(0)
        if not lines or removed >= 3 or not is_heading(lines[0]):
            break
        lines.pop(0)
        removed += 1
    if removed:
        return "\n".join(lines).lstrip()
    return text


def normalize_toutiao_title(value, max_length=30):
    title = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(title) <= max_length:
        return title
    shortened = title[:max_length].rstrip()
    punctuation = "，。！？；：、,.!?;:"
    cut = max(shortened.rfind(mark) for mark in punctuation)
    if cut >= 10:
        shortened = shortened[:cut]
    shortened = shortened.rstrip("，。！？；：、,.!?;: ")
    return shortened or title[:max_length].rstrip()


def download_images(article, folder):
    paths = []
    requested = max(0, min(5, int(article.get("imageCount", 3) or 0)))
    for index, image in enumerate(article.get("images", [])[:requested], start=1):
        url = image.get("url", "")
        if not url.startswith("https://"):
            continue
        try:
            request = Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*",
            })
            with urlopen(request, timeout=20) as response:
                content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if not content_type.startswith("image/"):
                    continue
                data = response.read(12_000_001)
                if len(data) > 12_000_000:
                    continue
            suffix = mimetypes.guess_extension(content_type) or ".jpg"
            if suffix == ".jpe":
                suffix = ".jpg"
            target = folder / ("image-" + str(index) + suffix)
            target.write_bytes(data)
            paths.append(target)
        except Exception:
            continue
    return paths


def fill_editor_for_review(context, article, folder):
    page = navigate(context, EDITOR_URL)
    write_status("login", "正在等待头条登录和编辑器载入。请在 Edge 窗口内完成登录。")
    page, title = wait_for_login_or_editor(context)
    if title is None:
        write_status(
            "login_required",
            "已打开头条创作者后台。请完成登录并进入图文发布页，浏览器会继续保留。",
            manual=True,
        )
        page.wait_for_timeout(1_200_000)
        return

    try:
        title.fill(article["title"])
    except Exception:
        title.click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(article["title"])

    editor = choose_editor(page)
    if editor is None:
        write_status("editor_not_found", "已填入标题，但未识别正文编辑区。请手动粘贴正文。", manual=True)
        page.wait_for_timeout(1_200_000)
        return

    uploaded_api = []
    image_payloads = image_upload_payloads(article)
    if image_payloads:
        try:
            uploaded_api = upload_images_to_toutiao(page, image_payloads)
        except Exception:
            uploaded_api = []

    safe_content = build_article_html(
        article["content"],
        uploaded_api,
        article.get("title", ""),
        article.get("originalTitle", ""),
    )
    try:
        editor.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        editor.evaluate(
            """(element, markup) => {
              element.focus();
              document.execCommand('insertHTML', false, markup);
              element.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText'}));
            }""",
            safe_content,
        )
    except Exception:
        editor.click()
        page.keyboard.insert_text(article["content"])

    uploaded = len(uploaded_api)
    page.wait_for_timeout(3500)
    write_status(
        "ready_for_review",
        "标题、正文" + (("和 " + str(uploaded) + " 张配图") if uploaded else "") +
        "已填入。请在头条窗口检查排版，确认后发布。",
        manual=True,
        draft=str(folder),
        uploadedImages=uploaded,
    )
    page.wait_for_timeout(3_600_000)


def api_result(response):
    text = response.text()
    try:
        data = json.loads(text)
    except Exception:
        data = {"raw": text[:500]}
    return {"status": response.status, "data": data}


def validate_login_and_get_media_id(page):
    result = page.evaluate(
        """
        async () => {
          const url = 'https://mp.toutiao.com/mp/agw/article/new?article_type=0&format=json&compat=1&column_no=';
          const response = await fetch(url, {
            credentials: 'include',
            headers: {Accept: 'application/json, text/plain, */*'}
          });
          const text = await response.text();
          let data;
          try { data = JSON.parse(text); } catch { data = {raw: text.slice(0, 500)}; }
          return {status: response.status, data};
        }
        """
    )
    data = result.get("data") or {}
    if int(data.get("code", -1)) != 0:
        raise LoginRequired(
            "头条号登录态失效：" + json.dumps(
                {"code": data.get("code"), "message": data.get("message")},
                ensure_ascii=False,
            )
        )
    media_id = str((data.get("data") or {}).get("media_id", ""))
    if not media_id:
        raise RuntimeError("头条登录校验成功，但没有返回媒体 ID")
    return media_id


def image_upload_payloads(article):
    requested = max(0, min(5, int(article.get("imageCount", 3) or 0)))
    payloads = []
    total = 0
    for index, image in enumerate(article.get("images", [])[:requested]):
        url = str(image.get("url", "")).strip()
        if not url.startswith("https://"):
            continue
        try:
            request = Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*",
            })
            with urlopen(request, timeout=25) as response:
                mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if not mime.startswith("image/"):
                    continue
                data = response.read(10_000_001)
            if not data or len(data) > 10_000_000:
                continue
            total += len(data)
            if total > 28_000_000:
                break
            extension = (mimetypes.guess_extension(mime) or ".jpg").lstrip(".")
            payloads.append({
                "name": f"image-{index + 1}.{extension}",
                "mime": mime,
                "data": base64.b64encode(data).decode("ascii"),
                "page": str(image.get("page", ""))[:2000],
                "isCover": index == 0,
            })
        except Exception:
            continue
    return payloads


def upload_images_to_toutiao(page, images):
    if not images:
        return []
    results = page.evaluate(
        """
        async ({images}) => {
          const uploadUrl = 'https://mp.toutiao.com/spice/image?upload_source=20020003&aid=1231&device_platform=web';
          const coverUrl = uploadUrl + '&need_cover_url=1';
          const output = [];
          for (const item of images) {
            try {
              const binary = atob(item.data);
              const bytes = new Uint8Array(binary.length);
              for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
              const form = new FormData();
              form.append('image', new Blob([bytes], {type: item.mime}), item.name);
              const response = await fetch(uploadUrl, {
                method: 'POST',
                credentials: 'include',
                headers: {Accept: 'application/json, text/plain, */*'},
                body: form
              });
              const text = await response.text();
              let uploaded;
              try { uploaded = JSON.parse(text); } catch { uploaded = {raw: text.slice(0, 500)}; }
              if (Number(uploaded.code) !== 0 || !uploaded.data?.image_uri || !uploaded.data?.image_url) {
                output.push({ok: false, error: uploaded.message || '图片上传没有返回有效资源', data: uploaded});
                continue;
              }
              let cover = null;
              if (item.isCover) {
                const coverForm = new FormData();
                coverForm.append('imageUrl', uploaded.data.image_url);
                const coverResponse = await fetch(coverUrl, {
                  method: 'POST',
                  credentials: 'include',
                  headers: {Accept: 'application/json, text/plain, */*'},
                  body: coverForm
                });
                const coverText = await coverResponse.text();
                try { cover = JSON.parse(coverText); } catch { cover = {raw: coverText.slice(0, 500)}; }
                if (Number(cover.code) !== 0 || !cover.data?.image_uri) {
                  output.push({ok: false, error: cover.message || '封面生成没有返回有效资源', data: cover});
                  continue;
                }
              }
              output.push({
                ok: true,
                bodyUri: uploaded.data.image_uri,
                bodyUrl: uploaded.data.image_url,
                width: uploaded.data.image_width || 800,
                height: uploaded.data.image_height || 600,
                mime: uploaded.data.image_mime_type || item.mime,
                coverUri: cover?.data?.image_uri || '',
                coverUrl: cover?.data?.cover_url || cover?.data?.image_url || '',
                coverWidth: cover?.data?.image_width || uploaded.data.image_width || 800,
                coverHeight: cover?.data?.image_height || uploaded.data.image_height || 600
              });
            } catch (error) {
              output.push({ok: false, error: String(error && error.message || error)});
            }
          }
          return output;
        }
        """,
        {"images": images},
    )
    failed = next((item for item in results if not item.get("ok")), None)
    if failed:
        raise RuntimeError("头条图片处理失败：" + str(failed.get("error", "未知错误")))
    if results and not results[0].get("coverUri"):
        raise RuntimeError("头条未能生成文章封面")
    return results


def build_article_html(content, uploaded_images, title="", original_title=""):
    body = strip_duplicate_heading(content, [title, original_title])
    parts = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
    if not parts:
        raise RuntimeError("正文为空，无法发布")
    slots = {}
    for index, image in enumerate(uploaded_images):
        position = max(0, (index + 1) * len(parts) // (len(uploaded_images) + 1))
        slots.setdefault(position, []).append(image)
    output = []
    for index, part in enumerate(parts):
        output.append("<p>" + html.escape(part).replace("\n", "<br>") + "</p>")
        for image in slots.get(index, []):
            output.append(
                '<img src="' + html.escape(str(image["bodyUrl"]), quote=True) + '" '
                'web_uri="' + html.escape(str(image["bodyUri"]), quote=True) + '" '
                'img_width="' + str(int(image.get("width", 800))) + '" '
                'img_height="' + str(int(image.get("height", 600))) + '" '
                'image_type="1" mime_type="' + html.escape(str(image.get("mime", "image/jpeg")), quote=True) + '">'
            )
    return "".join(output)


def publish_body(article, media_id, safe_content, cover, word_count):
    declaration_map = {
        "无需声明": 0,
        "引用站内": 1,
        "自行拍摄": 2,
        "AI生成": 3,
        "取材网络": 4,
        "个人观点，仅供参考": 5,
        "虚构演绎，故事经历": 6,
        "投资观点，仅供参考": 7,
        "健康医疗分享，仅供参考": 8,
    }
    declaration = str(article.get("declaration", "取材网络"))
    declaration_type = declaration_map.get(declaration, 4)
    extra = {
        "content_source": 100000000402,
        "content_word_cnt": word_count,
        "is_multi_title": 0,
        "sub_titles": [],
        "gd_ext": {
            "entrance": "",
            "from_page": "publisher_mp",
            "enter_from": "PC",
            "device_platform": "mp",
            "is_message": 0,
        },
        "tuwen_wtt_trans_flag": "2" if word_count >= 800 else ("1" if word_count <= 100 else "0"),
        "info_source": {
            "source_type": declaration_type,
            "source_author_uid": "",
            "time_format": "",
            "position": {},
        },
    }
    covers = []
    if cover:
        covers.append({
            "id": "",
            "url": cover["coverUrl"],
            "uri": cover["coverUri"],
            "ic_uri": "",
            "thumb_width": cover["coverWidth"],
            "thumb_height": cover["coverHeight"],
            "extra": {"from_content_uri": "", "from_content": "0"},
        })
    return {
        "source": "29",
        "extra": json.dumps(extra, ensure_ascii=False, separators=(",", ":")),
        "title": article["title"],
        "content": safe_content,
        "save": "1",
        "timer_status": "0",
        "timer_time": "",
        "entrance": "main",
        "search_creation_info": json.dumps({"searchTopOne": 0, "abstract": "", "clue_id": ""}, separators=(",", ":")),
        "title_id": f"{int(time.time() * 1000)}_{media_id}",
        "mp_editor_stat": "{}",
        "educluecard": "",
        "draft_form_data": json.dumps({"coverType": 2}, separators=(",", ":")),
        "pgc_feed_covers": json.dumps(covers, ensure_ascii=False, separators=(",", ":")),
        "article_ad_type": "3",
        "claim_exclusive": "0",
        "is_fans_article": "0",
        "govern_forward": "0",
        "praise": "0",
        "disable_praise": "0",
        "tree_plan_article": "0",
        "star_order_id": "",
        "star_order_name": "",
        "customer_nick_name": "",
        "activity_tag": "0",
        "trends_writing_tag": "0",
        "is_refute_rumor": "0",
        "ic_uri_list": "",
        "appid_list": "",
        "stock_ids": "",
        "concern_list": "",
        "pgc_id": "",
    }


def publish_directly(context, article, folder):
    page = navigate(context, EDITOR_URL)
    if "auth/page/login" in page.url or "sso.toutiao.com" in page.url:
        raise LoginRequired("头条号尚未登录，请先在账号中心完成登录")
    write_status("publishing", "正在验证头条登录状态和上传配图。")

    media_id = validate_login_and_get_media_id(page)
    images = image_upload_payloads(article)
    uploaded = upload_images_to_toutiao(page, images)
    safe_content = build_article_html(
        article["content"],
        uploaded,
        article.get("title", ""),
        article.get("originalTitle", ""),
    )
    word_count = len(re.sub(r"<[^>]+>", "", safe_content))
    cover = uploaded[0] if uploaded else None
    body = publish_body(article, media_id, safe_content, cover, word_count)

    anti = page.evaluate(
        """
        async () => {
          const response = await fetch('https://mp.toutiao.com/tt-anti-token', {
            credentials: 'include',
            headers: {Accept: 'application/json, text/plain, */*'}
          });
          const text = await response.text();
          try { return JSON.parse(text); } catch { return {raw: text.slice(0, 500)}; }
        }
        """
    )
    token = str((anti.get("data") or {}).get("token", ""))
    if int(anti.get("errno", -1)) != 0 or not token:
        raise RuntimeError("无法获取头条提交校验，请重新登录头条号")

    publish_url = (
        "https://mp.toutiao.com/mp/agw/article/publish?"
        + urlencode({"source": "mp", "type": "article", "aid": "1231", "mp_publish_ab_val": "0"})
    )
    result = page.evaluate(
        """
        async ({url, body, token}) => {
          const response = await fetch(url, {
            method: 'POST',
            credentials: 'include',
            headers: {
              Accept: 'application/json, text/plain, */*',
              'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
              'tt-anti-token': token
            },
            body
          });
          const text = await response.text();
          try { return {status: response.status, data: JSON.parse(text)}; }
          catch { return {status: response.status, data: {raw: text.slice(0, 800)}}; }
        }
        """,
        {"url": publish_url, "body": urlencode(body), "token": token},
    )
    data = result.get("data") or {}
    if int(data.get("code", -1)) != 0:
        raise RuntimeError(
            "今日头条发布失败：" + json.dumps(
                {"code": data.get("code"), "message": data.get("message") or data.get("raw")},
                ensure_ascii=False,
            )
        )
    article_id = str((data.get("data") or {}).get("pgc_id", ""))
    if not article_id or article_id == "0":
        raise RuntimeError("头条没有返回有效文章 ID，请到创作者后台确认结果")
    write_status(
        "published",
        f"发布成功，文章 ID：{article_id}。" + (
            f"标题、{len(uploaded)} 张配图和封面已提交。"
            if uploaded else "标题和正文已提交，本次未使用配图。"
        ),
        manual=False,
        articleId=article_id,
        draft=str(folder),
        uploadedImages=len(uploaded),
    )
def launch_context(playwright, edge, headless=False):
    last_error = None
    for attempt in range(4):
        try:
            return playwright.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE),
                executable_path=str(edge),
                headless=headless,
                no_viewport=True,
                accept_downloads=True,
                args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            )
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 + attempt)
    raise RuntimeError(
        "Edge 发布档案仍被旧窗口占用。请关闭头条发布窗口后重试。"
    ) from last_error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("login", "open", "publish"), required=True)
    parser.add_argument("--draft")
    args = parser.parse_args()
    PROFILE.mkdir(parents=True, exist_ok=True)
    WORKER_PID.write_text(str(__import__("os").getpid()), encoding="ascii")

    try:
        from playwright.sync_api import sync_playwright

        edge = find_edge()
        if not edge:
            write_status("error", "没有找到 Microsoft Edge。")
            return
        write_status(
            "opening",
            "正在后台连接今日头条。" if args.mode == "publish" else "正在打开本机头条窗口。",
        )
        with sync_playwright() as playwright:
            context = launch_context(playwright, edge, headless=args.mode == "publish")
            try:
                if args.mode == "login":
                    page = navigate(context, LOGIN_URL)
                    write_status("login_open", "登录窗口已打开。请在 Edge 内登录，登录状态保存在本机独立档案。")
                    page.wait_for_timeout(1_800_000)
                    return
                if not args.draft:
                    write_status("error", "没有找到待发布草稿。")
                    return
                folder = Path(args.draft)
                article = json.loads((folder / "article.json").read_text(encoding="utf-8"))
                article["title"] = normalize_toutiao_title(article.get("title", ""))
                if len(article["title"]) < 2:
                    write_status("error", "发布标题须在 2 至 30 个字之间", draft=str(folder))
                    return
                if args.mode == "open":
                    fill_editor_for_review(context, article, folder)
                else:
                    try:
                        publish_directly(context, article, folder)
                    except LoginRequired as exc:
                        write_status(
                            "login_required",
                            str(exc),
                            manual=True,
                            draft=str(folder),
                        )
            finally:
                try:
                    context.close()
                except Exception:
                    pass
    except Exception as exc:
        write_status("error", "发布窗口异常：" + str(exc), draft=args.draft or "")
    finally:
        try:
            WORKER_PID.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
