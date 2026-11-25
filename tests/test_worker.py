"""ワーカー（事前チェック→承認→送信→通知）の通しテスト。ローカルの模擬懸賞サイトを使う。"""
import functools
import http.server
import json
import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")

from test_all import PROFILE  # noqa: E402

pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def site():
    visited = []

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            visited.append(self.path)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=SITE))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", visited
    httpd.shutdown()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import browser_ops
    import db
    import notify
    import worker
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "w.db"))
    monkeypatch.setattr(browser_ops, "SHOT_DIR", str(tmp_path / "shots"))
    if os.environ.get("KENSHO_TEST_CHROMIUM"):
        monkeypatch.setenv("KENSHO_BROWSER_PATH", os.environ["KENSHO_TEST_CHROMIUM"])
    db.init_db()
    db.set_kv("profile", PROFILE)
    db.set_kv("settings", {"submit_interval": "0"})
    sent = []
    monkeypatch.setattr(notify, "send_line", lambda text: sent.append(text) or True)
    return db, worker, sent, tmp_path


def _add(db, base, page, title):
    db.add_campaign(title=title, url=f"{base}/{page}", deadline="2099-12-31")
    return [c for c in db.list_campaigns() if c["title"] == title][0]["id"]


def test_full_flow(site, env):
    base, visited = site
    db, worker, sent, tmp = env
    ok = _add(db, base, "article.html", "家電キャンペーン")
    cap = _add(db, base, "captcha.html", "画像認証あり")
    req = _add(db, base, "required.html", "アンケート必須")
    nolink = _add(db, base, "nolink.html", "ハガキ応募")
    stuck = _add(db, base, "stuck.html", "画面が進まない")

    browser = worker.Browser()
    worker.cycle(browser, force=True)

    # --- 事前チェック ---
    c = db.get_campaign(ok)
    assert c["status"] == "pending", c["result"]
    chk = json.loads(c["check_json"])
    assert chk["form_url"].endswith("/form.html")              # 記事ページから応募フォームを辿った
    assert {"label": "メールアドレス", "value": "taro@example.com"} in chk["fields"]
    assert [u["label"] for u in chk["unknownFields"]] == ["ご感想"]  # 任意の自由記述は空欄で OK
    agree = [cb for cb in chk["checkboxes"] if cb["agree"]]
    assert len(agree) == 1 and agree[0]["required"]
    assert chk["submit_label"] == "確認画面へ"

    assert db.get_campaign(cap)["status"] == "manual" and "画像認証" in db.get_campaign(cap)["result"]
    assert db.get_campaign(req)["status"] == "manual" and "きっかけ：" not in db.get_campaign(req)["result"] and "知ったきっかけ" in db.get_campaign(req)["result"] and "（" not in db.get_campaign(req)["result"].split("：")[-1]
    assert db.get_campaign(nolink)["status"] == "manual" and "見つかりません" in db.get_campaign(nolink)["result"]
    assert db.get_campaign(stuck)["status"] == "pending"
    assert any("送信待ちが 2 件" in m for m in sent)

    # --- 承認していないものは送信されない ---
    assert not any("done.html" in p for p in visited)

    # --- 承認 → 送信 ---
    db.update_campaign(ok, status="approved")
    db.update_campaign(stuck, status="approved")
    sent.clear()
    worker.cycle(browser)

    c = db.get_campaign(ok)
    assert c["status"] == "applied", c["result"]
    assert c["applied_at"] and os.path.exists(os.path.join(tmp, "shots", c["screenshot"]))
    confirm = [p for p in visited if p.startswith("/confirm.html?")]
    assert confirm and "agree=on" in confirm[-1] and "news=" not in confirm[-1]  # 同意だけチェック
    assert sum(p.startswith("/done.html") for p in visited) == 1                # 1 回だけ送信

    s = db.get_campaign(stuck)
    assert s["status"] == "manual" and "入力画面のまま" in s["result"]
    assert sum(p.startswith("/stuck.html?") for p in visited) == 1              # 二重に押さない
    assert any("成功 1 件 / 要確認 1 件" in m for m in sent)

    # --- 次のサイクルでは何も再送しない ---
    n = len(visited)
    worker.cycle(browser)
    assert not any(p.startswith(("/done.html", "/stuck.html?")) for p in visited[n:])


def test_page_changed_after_approval(site, env):
    base, _ = site
    db, worker, sent, _ = env
    cid = _add(db, base, "form.html", "変更テスト")
    browser = worker.Browser()
    worker.cycle(browser, force=True)
    assert db.get_campaign(cid)["status"] == "pending"
    # 承認後にプロフィールが変わった（＝承認時と入力内容が違う）
    db.set_kv("profile", {**PROFILE, "email": "other@example.com"})
    db.update_campaign(cid, status="approved")
    worker.cycle(browser)
    c = db.get_campaign(cid)
    assert c["status"] == "manual" and "変わった" in c["result"]


def test_expired_not_sent(site, env):
    base, _ = site
    db, worker, _, _ = env
    cid = _add(db, base, "form.html", "締切済み")
    db.update_campaign(cid, status="approved", deadline="2000-01-01", check_json="{}")
    worker.do_submit(worker.Browser(), "")
    assert db.get_campaign(cid)["status"] == "manual"


def test_line_payload(env, monkeypatch):
    import importlib
    import notify
    importlib.reload(notify)  # send_line の差し替えを戻す
    db = env[0]
    assert notify.send_line("x") is False  # 未設定なら送らない
    db.set_kv("settings", {"line_token": "TOKEN", "line_user_id": "U123"})
    captured = {}

    class Res:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return b"{}"

    def opener(req, timeout):
        captured.update(url=req.full_url, auth=req.get_header("Authorization"), body=json.loads(req.data))
        return Res()

    assert notify.send_line("テスト", opener=opener)
    assert captured["url"] == "https://api.line.me/v2/bot/message/push"
    assert captured["auth"] == "Bearer TOKEN"
    assert captured["body"] == {"to": "U123", "messages": [{"type": "text", "text": "テスト"}]}


def test_review_and_settings_pages(env):
    import app as appmod
    db = env[0]
    db.add_campaign(title="承認テスト", url="https://example.com/f")
    cid = db.list_campaigns()[0]["id"]
    db.update_campaign(cid, status="pending", check_json=json.dumps({
        "form_url": "https://example.com/f", "fields": [{"label": "メール", "value": "a@example.com"}],
        "unknownFields": [], "checkboxes": [{"index": 0, "label": "規約に同意", "required": True, "agree": True, "checked": False}],
        "submit_label": "応募する"}))
    client = appmod.app.test_client()
    r = client.get("/review")
    assert "a@example.com" in r.text and "チェックを入れます" in r.text
    with client.session_transaction() as s:
        tok = s["csrf"]
    # 承認画面以外から approved にはできない
    client.post(f"/campaign/{cid}/status/approved", data={"_csrf": tok})
    assert db.get_campaign(cid)["status"] == "pending"
    client.post("/review/approve", data={"_csrf": tok, "ids": [str(cid)]})
    assert db.get_campaign(cid)["status"] == "approved"
    client.post(f"/campaign/{cid}/unapprove", data={"_csrf": tok})
    assert db.get_campaign(cid)["status"] == "pending"

    client.post("/settings", data={"_csrf": tok, "line_token": "SECRET", "line_user_id": "U1", "collect_hours": "3"})
    assert db.get_kv("settings")["line_token"] == "SECRET"
    r = client.get("/settings")
    assert "SECRET" not in r.text  # トークンは画面に出さない
    client.post("/settings", data={"_csrf": tok, "line_token": "", "line_user_id": "U1"})
    assert db.get_kv("settings")["line_token"] == "SECRET"  # 空欄なら変えない
