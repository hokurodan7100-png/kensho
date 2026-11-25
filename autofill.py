"""応募ページをブラウザで開いて、プロフィールを自動入力する。

    python autofill.py https://example.com/campaign/form

- 画面つきの Chrome/Chromium を起動する（ログイン状態は browser_profile/ に保存）
- 自動入力用のブラウザがすでに開いていれば、そのウィンドウに新しいタブで開く
- ページを読み込むたびに static/fill.js を実行して入力欄を埋める
- 送信はしない。内容を確認して、利用者が自分で送信ボタンを押す
- ブラウザ（2 回目以降はそのタブ）を閉じると終了する
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright

import db

HERE = os.path.dirname(os.path.abspath(__file__))
FILL_JS = os.path.join(HERE, "static", "fill.js")
USER_DATA = os.environ.get("KENSHO_BROWSER_PROFILE") or os.path.join(HERE, "browser_profile")
# 2 回目以降に、開いているブラウザへつなぐためのポート（自分の PC からだけ接続できる）
CDP_PORT = int(os.environ.get("KENSHO_CDP_PORT", 9333))


class AutofillError(Exception):
    """利用者に見せるエラー（管理画面にそのまま表示される）。"""


def build_script(profile):
    with open(FILL_JS, encoding="utf-8") as f:
        return f"window.__KENSHO_PROFILE = {json.dumps(profile, ensure_ascii=False)};\n" + f.read()


def fill_all_frames(page, script):
    for frame in page.frames:
        try:
            frame.evaluate(script)
        except Exception as e:  # 別ドメインの iframe や遷移中のフレームは無視
            print(f"[skip frame] {frame.url}: {e}", file=sys.stderr)


def watch(page, script):
    # ページを読み込むたびに入力（確認画面→戻る、などの遷移にも対応）
    page.on("load", lambda pg: fill_all_frames(pg, script))


def open_and_fill(page, url, script):
    watch(page, script)
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("load", timeout=15000)  # 入力は load イベントで行われる
    except Exception:
        fill_all_frames(page, script)  # load が来ない重いページでは、ここで入力する


def connect_existing(p):
    """すでに開いている自動入力用ブラウザにつなぐ。開いていなければ None。"""
    try:
        return p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}", timeout=3000)
    except Exception:
        return None


def is_profile_locked(err):
    s = str(err)
    return "ProcessSingleton" in s or "already in use" in s or "SingletonLock" in s


def launch_new(p):
    kwargs = dict(
        headless=os.environ.get("KENSHO_HEADLESS") == "1",
        locale="ja-JP",
        no_viewport=True,
        args=["--start-maximized", f"--remote-debugging-port={CDP_PORT}", "--remote-debugging-address=127.0.0.1"],
    )
    exe = os.environ.get("KENSHO_BROWSER_PATH")
    if exe:
        return p.chromium.launch_persistent_context(USER_DATA, executable_path=exe, **kwargs)

    channel = os.environ.get("KENSHO_BROWSER_CHANNEL", "chrome")  # 普段の Chrome を使う
    try:
        return p.chromium.launch_persistent_context(USER_DATA, channel=channel, **kwargs)
    except Exception as e:
        if is_profile_locked(e):
            raise
        chrome_error = e
    # Chrome が見つからないときは Playwright 付属の Chromium を使う
    try:
        return p.chromium.launch_persistent_context(USER_DATA, **kwargs)
    except Exception as e:
        if is_profile_locked(e):
            raise
        raise AutofillError(
            "ブラウザを起動できませんでした。Google Chrome をインストールするか、"
            "「playwright install chromium」を実行してください。"
            f"（Chrome: {str(chrome_error).splitlines()[0]} / Chromium: {str(e).splitlines()[0]}）"
        ) from e


LOCKED_MESSAGE = (
    "自動入力用のブラウザ（browser_profile）を別のブラウザが使用中で、接続もできませんでした。"
    "開いている自動入力用のブラウザのウィンドウをすべて閉じてから、もう一度お試しください。"
    "ウィンドウが見当たらない場合は、タスクマネージャー（Mac はアクティビティモニタ）で Chrome を終了してください。"
)


def main(url):
    db.init_db()
    profile = db.get_profile()
    if not profile:
        raise AutofillError("プロフィールが未登録です。管理画面の「プロフィール」から登録してください。")
    script = build_script(profile)

    with sync_playwright() as p:
        # 1) 自動入力用のブラウザがすでに開いていれば、新しいタブで開く
        browser = connect_existing(p)
        if browser and browser.contexts:
            page = browser.contexts[0].new_page()
            page.bring_to_front()
            open_and_fill(page, url, script)
            print("既に開いている自動入力用ブラウザに、新しいタブで開きました。", flush=True)
            page.wait_for_event("close", timeout=0)  # そのタブを閉じるまで待つ
            return 0

        # 2) 新しく起動する
        try:
            context = launch_new(p)
        except AutofillError:
            raise
        except Exception as e:
            if is_profile_locked(e):
                raise AutofillError(LOCKED_MESSAGE) from e
            raise AutofillError(f"ブラウザを起動できませんでした：{str(e).splitlines()[0]}") from e

        context.on("page", lambda pg: watch(pg, script))
        page = context.pages[0] if context.pages else context.new_page()
        open_and_fill(page, url, script)
        print("ブラウザを起動しました。", flush=True)
        context.wait_for_event("close", timeout=0)  # 利用者がブラウザを閉じるまで待つ
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    try:
        sys.exit(main(sys.argv[1]))
    except AutofillError as e:
        print(f"ERROR: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: 予期しないエラー：{str(e).splitlines()[0] if str(e) else e!r}", file=sys.stderr, flush=True)
        sys.exit(1)
