"""ワーカーが使うブラウザ操作：応募ページの事前チェックと、承認済みの懸賞の送信。

安全のための約束：
- 画像認証（CAPTCHA）・ログインが必要なページは自動で扱わず「手動で応募」にする
- 承認したときと入力内容が変わっていたら送信しない
- 1 つの懸賞に送信ボタンを押すのは最大 3 回（入力画面→確認画面→完了）。同じ入力画面が
  残っているときは二重送信を避けるため、それ以上押さない
"""
import json
import os
import re
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SHOT_DIR = os.path.join(HERE, "screenshots")

FORM_LINK_RE = re.compile(r"応募する|応募はこちら|応募フォーム|今すぐ応募|エントリー|キャンペーン(ページ|サイト)|応募ページ|詳細・応募|公式サイト")
DONE_RE = re.compile(r"応募(が|を)?(完了|受け付け|受付)|送信(が|を)?完了|ありがとうございま|受け付けました|受付けました|thank\s*you", re.I)

# 送信ボタンを探す（data-kensho-submit を付けて文言を返す）
FIND_SUBMIT_JS = r"""
() => {
  const OK = /送信|応募|確認|次へ|エントリー|この内容で|submit|send|entry|confirm|apply|next/i;
  const NG = /戻る|修正|訂正|クリア|リセット|ログイン|検索|キャンセル|削除|back|reset|clear|login|search|cancel/i;
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  document.querySelectorAll("[data-kensho-submit]").forEach((e) => e.removeAttribute("data-kensho-submit"));
  const text = (el) => (el.innerText || el.value || el.alt || el.getAttribute("aria-label") || el.title || "").trim();
  const cands = Array.from(document.querySelectorAll("button, input[type=submit], input[type=image], input[type=button], a[role=button]"))
    .filter((el) => visible(el) && !el.disabled && OK.test(text(el)) && !NG.test(text(el)));
  // 入力欄のあるフォームの中のボタンを優先する
  cands.sort((a, b) => (b.form ? 1 : 0) - (a.form ? 1 : 0));
  if (!cands.length) return null;
  cands[0].setAttribute("data-kensho-submit", "1");
  return text(cands[0]).slice(0, 30) || "（文字なしのボタン）";
}
"""

EDITABLE_COUNT_JS = r"""
() => Array.from(document.querySelectorAll("input, select, textarea")).filter((el) =>
  !el.disabled && !el.readOnly && !/^(hidden|submit|button|reset|image|checkbox|radio)$/.test(el.type) &&
  !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)).length
"""


def _wait(page, ms=1500):
    try:
        page.wait_for_load_state("load", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(ms)


def run_fill(page, script):
    """全フレームで入力し、最も多く入力できたフレームとその結果を返す。"""
    best = (None, {"filled": 0, "unknown": 0, "fields": [], "unknownFields": [], "checkboxes": [],
                   "captcha": False, "login": False})
    for frame in page.frames:
        try:
            res = frame.evaluate("window.__KENSHO_NOBANNER = true;\n" + script + "\n;window.__KENSHO_RESULT")
        except Exception:
            continue
        if res and (best[0] is None or res["filled"] > best[1]["filled"]):
            best = (frame, res)
        if res and (res.get("captcha") or res.get("login")):  # どのフレームにあっても記録する
            best[1]["captcha"] = best[1]["captcha"] or res.get("captcha")
            best[1]["login"] = best[1]["login"] or res.get("login")
    return best


def find_form_link(page):
    """記事ページなどから、応募ページへのリンクを探す。"""
    links = page.evaluate(
        """() => Array.from(document.querySelectorAll('a[href]')).map(a => ({
              href: a.href, text: (a.innerText || (a.querySelector('img') || {}).alt || '').trim().slice(0, 60)}))"""
    )
    for ln in links:
        if ln["href"].startswith(("http://", "https://")) and FORM_LINK_RE.search(ln["text"]) and ln["href"] != page.url:
            return ln["href"]
    return None


def precheck(context, campaign, script):
    """応募ページを開いて入力を試し、(status, result, check) を返す。

    status は "pending"（承認すれば自動送信できる）か "manual"（手動で応募）。
    """
    page = context.new_page()
    try:
        url = campaign["form_url"] or campaign["url"]
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        _wait(page)
        frame, res = run_fill(page, script)
        hops = 0
        while res["filled"] == 0 and hops < 2:  # フォームがなければ「応募はこちら」などを辿る
            nxt = find_form_link(page)
            if not nxt:
                break
            page.goto(nxt, timeout=30000, wait_until="domcontentloaded")
            _wait(page)
            frame, res = run_fill(page, script)
            hops += 1

        reasons = []
        if res["filled"] == 0:
            reasons.append("応募フォームが見つかりません（URL を応募フォームのページに直すと再チェックできます）")
        if res["captcha"]:
            reasons.append("画像認証（CAPTCHA）があります")
        if res["login"]:
            reasons.append("ログインが必要です")
        req_unknown = [u["label"] or "（名前のない欄）" for u in res["unknownFields"] if u["required"]]
        if req_unknown:
            reasons.append("自動で入力できない必須項目：" + "、".join(req_unknown[:5]))
        req_cb = [c["label"] for c in res["checkboxes"] if c["required"] and not c["agree"]]
        if req_cb:
            reasons.append("同意以外の必須チェック項目：" + "、".join(req_cb[:3]))
        submit_label = frame.evaluate(FIND_SUBMIT_JS) if frame and not reasons else None
        if not reasons and not submit_label:
            reasons.append("送信ボタンが見つかりません")

        check = {
            "form_url": page.url,
            "fields": res["fields"],
            "unknownFields": res["unknownFields"],
            "checkboxes": res["checkboxes"],
            "submit_label": submit_label,
        }
        if reasons:
            return "manual", " / ".join(reasons), check
        return "pending", f"自動入力 {res['filled']} 項目・送信ボタン「{submit_label}」", check
    finally:
        page.close()


def _shot(page, cid):
    os.makedirs(SHOT_DIR, exist_ok=True)
    name = f"{cid}_{datetime.now():%Y%m%d_%H%M%S}.png"
    try:
        page.screenshot(path=os.path.join(SHOT_DIR, name), full_page=True)
        return name
    except Exception:
        return None


def _has_body(frame):
    try:
        return frame.evaluate("!!document.body")
    except Exception:
        return False


def _same_fields(a, b):
    key = lambda fs: sorted((f["label"], f["value"]) for f in fs)
    return key(a) == key(b)


def submit(context, campaign, script):
    """承認済みの懸賞を送信して (status, result, screenshot) を返す。"""
    approved = json.loads(campaign["check_json"] or "{}")
    page = context.new_page()
    try:
        page.goto(approved.get("form_url") or campaign["url"], timeout=30000, wait_until="domcontentloaded")
        _wait(page)
        frame, res = run_fill(page, script)
        if res["captcha"] or res["login"]:
            return "manual", "画像認証またはログインが表示されたため送信しませんでした", _shot(page, campaign["id"])
        if not frame or not _same_fields(res["fields"], approved.get("fields", [])):
            return "manual", "承認したときと入力内容が変わったため送信しませんでした（ページが変わった可能性があります）", _shot(page, campaign["id"])

        # 承認画面に表示した、規約同意と必須のチェックボックスにチェックを入れる
        for cb in approved.get("checkboxes", []):
            if cb["agree"] or cb["required"]:
                loc = frame.locator(f"[data-kensho-cb='{cb['index']}']")
                if loc.count() != 1:
                    return "manual", f"チェック項目「{cb['label']}」が見つからないため送信しませんでした", _shot(page, campaign["id"])
                loc.check()

        for step in range(3):
            label = frame.evaluate(FIND_SUBMIT_JS)
            if not label:
                break
            frame.locator("[data-kensho-submit='1']").first.click()
            _wait(page, 2500)
            if frame.is_detached():  # iframe 内のフォームが画面ごと切り替わった
                frame = page.main_frame
            body = " ".join(f.inner_text("body") for f in page.frames if not f.is_detached() and _has_body(f))
            if DONE_RE.search(body):
                return "applied", f"自動送信しました（{step + 1} 回目のボタンで完了画面を確認）", _shot(page, campaign["id"])
            if page.evaluate("document.querySelector(\"iframe[src*='recaptcha'], iframe[src*='hcaptcha'], .g-recaptcha, .h-captcha, .cf-turnstile\") !== null"):
                return "manual", "送信の途中で画像認証が表示されました。続きを手動で行ってください", _shot(page, campaign["id"])
            # まだ入力欄が残っている＝入力エラーか、送信済みで画面が変わらない。二重送信を避けて止める
            if frame.evaluate(EDITABLE_COUNT_JS) > 0:
                return "manual", "送信ボタンを押しても入力画面のままでした（入力エラーの可能性）。スクリーンショットを確認してください", _shot(page, campaign["id"])
            # 入力欄がない＝確認画面とみなして、次の「送信する」を押す
        return "manual", "送信しましたが完了画面を確認できませんでした。もう一度応募する前に、スクリーンショットとメールを確認してください", _shot(page, campaign["id"])
    finally:
        page.close()
