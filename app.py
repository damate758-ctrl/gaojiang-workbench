"""稿匠桌面 App 启动器。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8766
URL = f"http://{HOST}:{PORT}"


def service_ready():
    try:
        with urlopen(URL + "/api/health", timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return bool(payload.get("ok"))
    except Exception:
        return False


def start_service():
    from server import Handler
    from http.server import ThreadingHTTPServer

    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def edge_path():
    candidates = [
        Path.home() / "AppData/Local/Microsoft/Edge/Application/msedge.exe",
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    ]
    return next((path for path in candidates if path.exists()), None)


def fallback_window():
    edge = edge_path()
    if not edge:
        raise RuntimeError("没有找到 Microsoft Edge，无法打开稿匠窗口")
    profile = ROOT / ".app-window-profile"
    process = subprocess.Popen([
        str(edge),
        "--app=" + URL,
        "--user-data-dir=" + str(profile),
        "--window-size=1440,920",
        "--no-first-run",
    ])
    process.wait()


def main():
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GaoJiang.Desktop.App")
        except Exception:
            pass
    httpd = None
    if not service_ready():
        httpd = start_service()
        for _ in range(30):
            if service_ready():
                break
            threading.Event().wait(0.2)
    try:
        import webview
        from webview.menu import Menu, MenuAction, MenuSeparator

        window = None
        tray_icon = None
        force_exit = False

        def active_window():
            return window

        def open_path(path: Path):
            target = path.resolve()
            if not target.exists():
                raise FileNotFoundError(f"找不到要打开的位置：{target}")
            os.startfile(str(target))

        def open_page(page: str):
            target = active_window()
            if target is None:
                return
            target.show()
            target.restore()
            target.evaluate_js(
                "(() => { const page = " + json.dumps(page) +
                "; if (typeof setPage === 'function') setPage(page); })()"
            )

        def reload_page():
            target = active_window()
            if target is not None:
                target.load_url(URL)

        def minimize_window():
            target = active_window()
            if target is not None:
                target.minimize()

        def maximize_window():
            target = active_window()
            if target is not None:
                target.maximize()

        def restore_window():
            target = active_window()
            if target is not None:
                target.restore()
                target.show()

        def hide_window(*_args):
            target = active_window()
            if target is not None:
                target.hide()

        def show_window(*_args):
            target = active_window()
            if target is not None:
                target.show()
                target.restore()

        def toggle_fullscreen():
            target = active_window()
            if target is not None:
                target.toggle_fullscreen()

        def show_about():
            if os.name == "nt":
                import ctypes

                ctypes.windll.user32.MessageBoxW(
                    0,
                    "稿匠 · 开源内容创作工具\n\n"
                    "面向今日头条图文创作的本机桌面工作台。\n\n"
                    f"程序目录：{ROOT}",
                    "关于稿匠",
                    0x00000040,
                )

        def quit_app():
            nonlocal force_exit
            force_exit = True
            target = active_window()
            if target is not None:
                target.destroy()

        def start_tray():
            try:
                import pystray
                from PIL import Image

                tray_image = Image.open(ROOT / "icon.png").convert("RGBA")
                tray_image.thumbnail((64, 64), Image.Resampling.LANCZOS)
                tray = pystray.Icon(
                    "gaojiang",
                    tray_image,
                    "稿匠 · 开源内容创作工具",
                    pystray.Menu(
                        pystray.MenuItem("打开稿匠", show_window, default=True),
                        pystray.MenuItem("隐藏窗口", hide_window),
                        pystray.Menu.SEPARATOR,
                        pystray.MenuItem("打开创作工坊", lambda _icon, _item: open_page("studio")),
                        pystray.MenuItem("打开内容发布", lambda _icon, _item: open_page("publish")),
                        pystray.MenuItem(
                            "打开发布草稿目录",
                            lambda _icon, _item: open_path(ROOT / "发布草稿"),
                        ),
                        pystray.Menu.SEPARATOR,
                        pystray.MenuItem("退出稿匠", lambda _icon, _item: quit_app()),
                    ),
                )
                threading.Thread(target=tray.run, name="gaojiang-tray", daemon=True).start()
                return tray
            except Exception as exc:
                print(f"系统托盘初始化失败：{exc}", file=sys.stderr)
                return None

        app_menu = [
            Menu("文件", [
                MenuAction("打开创作工坊", lambda: open_page("studio")),
                MenuAction("打开内容发布", lambda: open_page("publish")),
                MenuSeparator(),
                MenuAction("打开发布草稿目录", lambda: open_path(ROOT / "发布草稿")),
                MenuAction("打开程序目录", lambda: open_path(ROOT)),
                MenuAction("打开使用说明", lambda: open_path(ROOT / "使用说明.md")),
                MenuSeparator(),
                MenuAction("退出稿匠", quit_app),
            ]),
            Menu("视图", [
                MenuAction("返回资源管理", lambda: open_page("resources")),
                MenuAction("打开创作工坊", lambda: open_page("studio")),
                MenuAction("打开内容发布", lambda: open_page("publish")),
                MenuSeparator(),
                MenuAction("刷新页面", reload_page),
                MenuAction("最小化", minimize_window),
                MenuAction("最大化", maximize_window),
                MenuAction("还原窗口", restore_window),
                MenuAction("隐藏到系统托盘", hide_window),
                MenuAction("切换全屏", toggle_fullscreen),
            ]),
            Menu("帮助", [
                MenuAction("查看使用说明", lambda: open_path(ROOT / "使用说明.md")),
                MenuAction("关于稿匠", show_about),
            ]),
        ]

        window_width = 1440
        window_height = 920
        window_x = None
        window_y = None
        screens = getattr(webview, "screens", [])
        if screens:
            frame = screens[0].frame
            window_width = min(window_width, max(900, int(frame.Width) - 64))
            window_height = min(window_height, max(640, int(frame.Height) - 64))
            window_x = int(frame.X) + max(0, (int(frame.Width) - window_width) // 2)
            window_y = int(frame.Y) + max(0, (int(frame.Height) - window_height) // 2)

        window = webview.create_window(
            "稿匠 · 开源内容创作工具",
            URL,
            width=window_width,
            height=window_height,
            x=window_x,
            y=window_y,
            min_size=(min(1080, window_width), min(680, window_height)),
            background_color="#f5f7fb",
            text_select=True,
            menu=app_menu,
        )

        def ask_exit():
            if os.name != "nt":
                return True
            try:
                import ctypes

                MB_YESNO = 0x00000004
                MB_ICONQUESTION = 0x00000020
                IDYES = 6
                answer = ctypes.windll.user32.MessageBoxW(
                    0,
                    "要退出稿匠吗？\n\n"
                    "“是”：完全退出，后台发布任务会一起结束。\n"
                    "“否”：只隐藏到右下角托盘，继续后台运行。\n\n"
                    "隐藏后想彻底退出：右键托盘图标选“退出稿匠”，"
                    "或点窗口菜单 文件 → 退出稿匠。",
                    "稿匠",
                    MB_YESNO | MB_ICONQUESTION,
                )
                return answer == IDYES
            except Exception:
                return True

        def on_window_closing():
            nonlocal force_exit
            if force_exit or tray_icon is None:
                return True
            if ask_exit():
                force_exit = True
                return True
            hide_window()
            return False

        window.events.closing += on_window_closing
        tray_icon = start_tray()
        try:
            icon_path = ROOT / "icon.ico"
            webview.start(debug=False, icon=str(icon_path) if icon_path.exists() else None)
        finally:
            if tray_icon is not None:
                try:
                    tray_icon.stop()
                except Exception:
                    pass
            try:
                window.destroy()
            except Exception:
                pass
    except Exception as first_error:
        try:
            fallback_window()
        except Exception as second_error:
            raise RuntimeError(
                "桌面窗口启动失败。\n"
                f"WebView2：{first_error}\n"
                f"Edge App：{second_error}"
            ) from second_error
    finally:
        if httpd:
            httpd.shutdown()
            httpd.server_close()


def worker_entry():
    from toutiao_worker import main as worker_main
    sys.argv = [sys.argv[0]] + sys.argv[2:]
    worker_main()


if __name__ == "__main__":
    if "--worker" in sys.argv:
        worker_entry()
        raise SystemExit(0)
    if "--image-worker" in sys.argv:
        from image_worker import main as image_main
        image_main()
        raise SystemExit(0)
    main()
