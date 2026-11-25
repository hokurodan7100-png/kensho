"""24 時間動かすワーカー。

    python worker.py          # ずっと動かす（1 分ごとに仕事がないか確認）
    python worker.py --once   # 1 回だけ実行して終わる（動作確認用）

やること：
1. 一定時間ごと（既定 6 時間）に収集元から懸賞を取り込む
2. 新しい懸賞の応募ページを開いて事前チェックし、「送信待ち」か「手動で応募」に分ける
3. 管理画面で承認された懸賞（送信予定）を送信する
4. 送信待ちの発生・送信の失敗・毎日のまとめを LINE に通知する
"""
import argparse
import json
import os
import sys
import time
import traceback
from datetime import date, datetime, timedelta

import browser_ops
import collector
import db
import notify
from autofill import build_script

DEFAULTS = {
    "collect_hours": "6",     # 取り込みと事前チェックの間隔
    "check_limit": "20",      # 1 回に事前チェックする件数
    "submit_limit": "10",     # 1 回に送信する件数
    "submit_interval": "30",  # 送信と送信の間の秒数（相手のサーバーに負担をかけない）
    "notify_hour": "20",      # 毎日のまとめを送る時刻
    "max_attempts": "2",      # 送信を試す最大回数
}


def setting(name):
    v = db.get_kv("settings").get(name) or DEFAULTS[name]
    return int(v)


def heartbeat(state="待機中"):
    db.set_kv("worker", {"heartbeat": db.now(), "state": state, "pid": str(os.getpid())})


def worker_alive(max_age=180):
    hb = db.get_kv("worker").get("heartbeat")
    if not hb:
        return False
    return (datetime.now() - datetime.fromisoformat(hb)).total_seconds() < max_age


class Browser:
    """必要になったときだけブラウザを起動する（画面は出さない）。"""

    def __init__(self):
        self.pw = self.browser = self.context = None

    def get(self):
        if self.context is None:
            from playwright.sync_api import sync_playwright
            self.pw = sync_playwright().start()
            exe = os.environ.get("KENSHO_BROWSER_PATH")
            if exe:
                self.browser = self.pw.chromium.launch(executable_path=exe)
            else:
                try:
                    self.browser = self.pw.chromium.launch(channel=os.environ.get("KENSHO_BROWSER_CHANNEL", "chrome"))
                except Exception:
                    self.browser = self.pw.chromium.launch()
            self.context = self.browser.new_context(locale="ja-JP", viewport={"width": 1280, "height": 900})
            self.context.set_default_timeout(20000)
        return self.context

    def close(self):
        for obj, fn in ((self.context, "close"), (self.browser, "close"), (self.pw, "stop")):
            try:
                if obj:
                    getattr(obj, fn)()
            except Exception:
                pass
        self.pw = self.browser = self.context = None


def do_collect_and_check(browser, script):
    added, errors = collector.collect_all()
    db.log_event(f"収集：{added} 件を取り込みました")
    for e in errors:
        db.log_event(f"収集エラー：{e}", "error")

    new_pending = []
    for c in db.campaigns_to_check(setting("check_limit")):
        heartbeat(f"事前チェック中：{c['title'][:30]}")
        try:
            status, result, check = browser_ops.precheck(browser.get(), c, script)
        except Exception as e:
            status, result, check = "manual", f"ページを開けませんでした：{str(e).splitlines()[0][:150]}", None
        db.update_campaign(c["id"], status=status, result=result, checked_at=db.now(),
                           check_json=json.dumps(check, ensure_ascii=False) if check else None,
                           form_url=check["form_url"] if check else None)
        if status == "pending":
            new_pending.append(c)
    db.log_event(f"事前チェック：送信待ち {len(new_pending)} 件")
    return new_pending


def do_submit(browser, script):
    done, failed = [], []
    approved = db.campaigns_by_status("approved", setting("submit_limit"))
    for i, c in enumerate(approved):
        if c["deadline"] and c["deadline"] < date.today().isoformat():
            db.update_campaign(c["id"], status="manual", result="締切を過ぎていたため送信しませんでした")
            continue
        if (c["attempts"] or 0) >= setting("max_attempts"):
            db.update_campaign(c["id"], status="manual", result="送信に何度か失敗したため、手動で確認してください")
            failed.append(c)
            continue
        if i:
            time.sleep(setting("submit_interval"))
        heartbeat(f"送信中：{c['title'][:30]}")
        db.update_campaign(c["id"], attempts=(c["attempts"] or 0) + 1)
        try:
            status, result, shot = browser_ops.submit(browser.get(), c, script)
        except Exception as e:
            # 途中で落ちたときは送信できたか分からないので、自動では再送しない
            status, result, shot = "manual", f"送信中にエラーが起きました（送信されたかどうか確認してください）：{str(e).splitlines()[0][:150]}", None
        db.update_campaign(c["id"], status=status, result=result, screenshot=shot)
        db.log_event(f"送信：{c['title'][:40]} → {result}", "info" if status == "applied" else "error")
        (done if status == "applied" else failed).append(c)
    return done, failed


def daily_digest():
    pending = db.campaigns_by_status("pending")
    soon_limit = (date.today() + timedelta(days=3)).isoformat()
    soon = [c for c in db.campaigns_by_status("pending") + db.campaigns_by_status("manual") + db.campaigns_by_status("new")
            if c["deadline"] and date.today().isoformat() <= c["deadline"] <= soon_limit]
    stats = db.stats()
    lines = [f"【懸賞マネージャー】{date.today():%m/%d} のまとめ",
             f"送信待ち（承認してください）：{len(pending)} 件",
             f"手動で応募：{stats.get('manual', 0)} 件",
             f"これまでの応募：{stats.get('applied', 0) + stats.get('won', 0) + stats.get('lost', 0)} 件 / 当選：{stats.get('won', 0)} 件"]
    if soon:
        lines.append("⏰ 締切 3 日以内：")
        lines += [f"・{c['deadline'][5:]} {c['title'][:30]}" for c in soon[:5]]
    return "\n".join(lines)


def safe_notify(text):
    try:
        if notify.send_line(text):
            db.log_event("LINE に通知しました")
    except Exception as e:
        db.log_event(str(e), "error")


def cycle(browser, force=False):
    """1 回分の仕事。"""
    profile = db.get_profile()
    if not profile:
        heartbeat("プロフィール未登録のため待機中")
        return
    script = build_script(profile)
    state = db.get_kv("worker")
    messages = []

    last = state.get("last_collect")
    if force or not last or datetime.now() - datetime.fromisoformat(last) >= timedelta(hours=setting("collect_hours")):
        db.set_kv("worker", {"last_collect": db.now()})
        new_pending = do_collect_and_check(browser, script)
        if new_pending:
            messages.append(f"🆕 送信待ちが {len(new_pending)} 件増えました。管理画面の「承認」から確認してください。\n"
                            + "\n".join(f"・{c['title'][:30]}" for c in new_pending[:5]))

    done, failed = do_submit(browser, script)
    if done or failed:
        msg = f"📮 送信結果：成功 {len(done)} 件"
        if failed:
            msg += f" / 要確認 {len(failed)} 件\n" + "\n".join(f"・{c['title'][:30]}" for c in failed[:5])
        messages.append(msg)

    today = date.today().isoformat()
    if datetime.now().hour >= setting("notify_hour") and state.get("last_digest") != today:
        db.set_kv("worker", {"last_digest": today})
        messages.append(daily_digest())

    if messages:
        safe_notify("\n\n".join(messages))
    browser.close()  # 長時間ブラウザを開きっぱなしにしない


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="1 回だけ実行する（取り込みも必ず行う）")
    ap.add_argument("--force", action="store_true", help="ほかのワーカーが動いていても起動する")
    args = ap.parse_args()

    db.init_db()
    if worker_alive() and not args.force and not args.once:
        print("ワーカーはすでに動いています（二重起動を防ぐため終了します）。", file=sys.stderr)
        return 1
    browser = Browser()
    db.log_event("ワーカーを起動しました")
    print("ワーカーを起動しました。止めるときは Ctrl+C を押してください。", flush=True)
    try:
        while True:
            heartbeat("作業中")
            try:
                cycle(browser, force=args.once)
            except Exception as e:
                db.log_event(f"ワーカーでエラー：{e}", "error")
                traceback.print_exc()
                browser.close()
            heartbeat()
            if args.once:
                return 0
            for _ in range(6):  # 1 分ごとに確認（承認された懸賞をすぐ送るため）
                time.sleep(10)
                heartbeat()
    except KeyboardInterrupt:
        db.log_event("ワーカーを停止しました")
        return 0
    finally:
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
