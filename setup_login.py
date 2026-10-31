"""Let the user complete official authentication and export a scoped session."""

import argparse
import logging
from pathlib import Path
import webbrowser

from cas_auth import AuthenticationError, load_browser_state, setup_login


SETTINGS_URL = "https://github.com/0Seven-Deadly-Sins/xjtu-sport-bot/settings/secrets/actions"


def show_copy_window(path):
    import tkinter as tk

    root = tk.Tk()
    root.title("XJTU 首次登录配置")
    root.geometry("570x260")
    tk.Label(root, text="学校登录已完成。请把会话保存到 GitHub Actions Secret。", wraplength=520).pack(pady=15)
    tk.Label(root, text="Secret 名称：SPORT_BROWSER_STATE").pack(pady=5)
    status = tk.StringVar(value="复制后保持本窗口打开，直到在 GitHub 点击 Add secret / Update secret。")

    def copy_state():
        state = path.read_text(encoding="utf-8")
        load_browser_state(state)
        root.clipboard_clear()
        root.clipboard_append(state)
        root.update()
        status.set("已复制会话。请在 GitHub 的 Secret 值框中粘贴，不要发到聊天或提交到仓库。")

    tk.Button(root, text="1. 复制学校会话配置", command=copy_state).pack(pady=8)
    tk.Button(root, text="2. 打开 GitHub Secrets 设置页", command=lambda: webbrowser.open(SETTINGS_URL)).pack(pady=8)
    tk.Label(root, textvariable=status, wraplength=520).pack(pady=12)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="在本地浏览器中本人完成登录，再生成 Actions 会话配置。")
    parser.add_argument("--copy-existing", action="store_true", help="复制上次生成的会话，不重新登录")
    parser.add_argument("--browser-channel", choices=["msedge", "chrome"], help="使用已安装的 Edge 或 Chrome，无需下载 Chromium")
    parser.add_argument("--no-copy-ui", action="store_true", help="保存会话后退出，由配置程序直接上传 Secret")
    parser.add_argument("--output", default=str(Path(__file__).with_name(".local") / "auth-state.json"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    path = Path(args.output).resolve()
    try:
        if args.copy_existing:
            load_browser_state(path.read_text(encoding="utf-8"))
        else:
            # Account/password and any MFA response are entered by the user in
            # the official browser page, never in this terminal or a GitHub file.
            setup_login(path, browser_channel=args.browser_channel)
        logging.info("会话已保存在本机受忽略的 .local 目录；请勿上传到代码仓库。")
        if not args.no_copy_ui:
            show_copy_window(path)
        return 0
    except AuthenticationError as error:
        logging.error("%s", error)
    except OSError:
        logging.error("无法读取或保存本地会话文件，请核对路径和权限。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
