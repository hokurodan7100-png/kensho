import os
import sys
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))

PROFILE = {
    "last_name": "山田", "first_name": "太郎", "last_kana": "ヤマダ", "first_kana": "タロウ",
    "email": "taro@example.com", "phone": "090-1234-5678", "zip": "123-4567", "prefecture": "東京都",
    "city": "千代田区千代田", "address1": "1-1", "address2": "テストマンション101",
    "birth_year": "1990", "birth_month": "2", "birth_day": "1", "gender": "男性", "job": "会社員",
}


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    return db


# ---------- 締切日の推定 ----------
@pytest.mark.parametrize("text, expected", [
    ("締切：2026年12月31日まで", "2026-12-31"),
    ("応募期間 2026/11/01〜2026/12/15", "2026-12-15"),
    ("応募期間：11/1〜12/20", "2026-12-20"),
    ("1月15日(木)23:59まで", "2027-01-15"),          # 年をまたぐ
    ("11月20日締切", "2026-11-20"),                   # 少し前の日付は今年のまま
    ("豪華賞品が当たる！", None),
    ("2026年2月30日まで", None),                      # 存在しない日付
])
def test_parse_deadline(text, expected):
    from collector import parse_deadline
    assert parse_deadline(text, today=date(2026, 11, 25)) == expected


# ---------- フィード ----------
RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>Amazonギフト券1万円が当たる</title><link>https://example.com/a</link>
<description>&lt;p&gt;Web応募。締切：12月10日&lt;/p&gt;</description></item>
<item><title>ハガキで応募 お米</title><link>https://example.com/b</link><description>締切 12/31</description></item>
<item><title>リンクなし</title></item>
</channel></rss>""".encode()

ATOM = """<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>家電プレゼント</title><link rel="alternate" href="https://example.com/c"/>
<summary>応募期間 2026/12/01〜2026/12/24</summary></entry></feed>""".encode()


def test_parse_feed_rss_and_atom():
    from collector import parse_feed
    rss = parse_feed(RSS)
    assert [i["url"] for i in rss] == ["https://example.com/a", "https://example.com/b"]
    assert "<p>" not in rss[0]["summary"]
    atom = parse_feed(ATOM)
    assert atom[0]["url"] == "https://example.com/c"


def test_collect_filters_and_dedupes(fresh_db):
    import collector
    db = fresh_db
    db.add_feed("テスト", "https://feed.example/rss")
    db.add_feed("壊れた", "https://broken.example/rss")
    db.set_kv("filter", {"include": "", "exclude": "ハガキ"})

    def fetcher(url):
        if "broken" in url:
            raise OSError("timeout")
        return RSS

    added, errors = collector.collect_all(fetcher)
    assert added == 1 and len(errors) == 1
    c = db.list_campaigns()[0]
    assert c["title"].startswith("Amazon") and c["deadline"].endswith("-12-10") and c["source"] == "テスト"
    assert collector.collect_all(fetcher)[0] == 0  # 2 回目は重複しない


# ---------- Web 画面 ----------
def _token(client):
    client.get("/")
    with client.session_transaction() as s:
        return s["csrf"]


def test_web_flow(fresh_db, monkeypatch):
    import app as appmod
    launched = []
    class FakeProc:
        def __init__(self, args, **kw):
            launched.append(args)
        def wait(self, timeout=None):
            raise appmod.subprocess.TimeoutExpired(args, timeout)
    args = None
    monkeypatch.setattr(appmod.subprocess, "Popen", FakeProc)
    client = appmod.app.test_client()
    tok = _token(client)

    # CSRF トークンなしの POST は拒否
    assert client.post("/campaign/new", data={"url": "https://x.example"}).status_code == 400

    r = client.post("/campaign/new", data={"_csrf": tok, "url": "https://x.example/form", "title": "テスト懸賞",
                                           "deadline": date.today().isoformat()}, follow_redirects=True)
    assert "追加しました" in r.text and "まだ応募していない" in r.text
    cid = fresh_db.list_campaigns()[0]["id"]

    # プロフィール未登録だと自動入力できない
    client.post(f"/campaign/{cid}/autofill", data={"_csrf": tok})
    assert launched == []
    client.post("/profile", data={"_csrf": tok, **PROFILE})
    assert fresh_db.get_profile()["email"] == "taro@example.com"
    client.post(f"/campaign/{cid}/autofill", data={"_csrf": tok})
    assert launched and launched[0][-1] == "https://x.example/form"

    client.post(f"/campaign/{cid}/status/applied", data={"_csrf": tok})
    c = fresh_db.get_campaign(cid)
    assert c["status"] == "applied" and c["applied_at"]

    assert client.post("/campaign/new", data={"_csrf": tok, "url": "javascript:alert(1)"}).status_code == 200
    assert len(fresh_db.list_campaigns()) == 1


# ---------- フォーム自動入力（実ブラウザ） ----------
@pytest.fixture(scope="module")
def browser():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        exe = os.environ.get("KENSHO_TEST_CHROMIUM")
        b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        yield b
        b.close()


def _fill(browser, html):
    from autofill import build_script
    page = browser.new_page()
    page.set_content(f"<html><body>{html}</body></html>")
    result = page.evaluate(build_script(PROFILE) + "\n;window.__KENSHO_RESULT")
    vals = page.evaluate("""() => Object.fromEntries(Array.from(document.querySelectorAll('input,select,textarea'))
        .map(e => [e.name, e.type === 'radio' || e.type === 'checkbox' ? (e.checked ? e.value : null) : e.value])
        .filter(([k, v]) => !(v === null && document.querySelector(`[name="${k}"]:checked`))))""")
    page.close()
    return result, vals


def test_fill_table_form(browser):
    from forms import TABLE_FORM
    result, v = _fill(browser, TABLE_FORM)
    assert (v["n1"], v["n2"]) == ("山田", "太郎")
    assert (v["k1"], v["k2"]) == ("ヤマダ", "タロウ")
    assert v["mail"] == v["mail2"] == "taro@example.com"
    assert (v["z1"], v["z2"]) == ("123", "4567")
    assert v["pref"] == "13"
    assert (v["addr_city"], v["addr_street"], v["addr_bld"]) == ("千代田区千代田", "1-1", "テストマンション101")
    assert (v["t1"], v["t2"], v["t3"]) == ("090", "1234", "5678")
    assert v["sex"] == "1"
    assert (v["by"], v["bm"], v["bd"]) == ("1990", "2", "01")
    assert v["opinion"] == ""        # 自由記述は空のまま（黄色で表示）
    assert v["agree"] is None        # 規約同意は自分でチェック
    assert result["unknown"] == 1


def test_fill_label_form(browser):
    from forms import LABEL_FORM
    _, v = _fill(browser, LABEL_FORM)
    assert (v["sei"], v["mei"]) == ("山田", "太郎")
    assert (v["sei_kana"], v["mei_kana"]) == ("やまだ", "たろう")
    assert v["tel"] == "09012345678" and v["zipcode"] == "1234567"
    assert v["address"] == "東京都千代田区千代田1-1テストマンション101"
    assert v["age"].isdigit()
    assert v["captcha"] == ""        # CAPTCHA には触れない


def test_fill_autocomplete_form(browser):
    from forms import AUTOCOMPLETE_FORM
    _, v = _fill(browser, AUTOCOMPLETE_FORM)
    assert (v["x1"], v["x2"], v["x3"], v["x4"], v["x5"]) == ("山田", "太郎", "taro@example.com", "123-4567", "東京都")
    assert v["x6"] == "1990-02-01"
    assert v["x7"] == ""
