"""懸賞マネージャー：懸賞の収集・管理・応募フォーム自動入力。

    python app.py   →  http://127.0.0.1:5000
"""
import json
import os
import secrets
import subprocess
import sys
from datetime import date

from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, session, url_for

import browser_ops
import collector
import db
import notify
import worker

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)
app.secret_key = os.environ.get("KENSHO_SECRET") or secrets.token_hex(32)


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.before_request
def check_csrf():
    # 他のサイトから勝手に「自動入力で開く」などを実行されないようにする
    if request.method == "POST":
        if not secrets.compare_digest(request.form.get("_csrf", ""), session.get("csrf", "")):
            abort(400, "不正なリクエストです（ページを再読み込みしてやり直してください）")


@app.template_filter("days_left")
def days_left(deadline):
    if not deadline:
        return None
    try:
        return (date.fromisoformat(deadline) - date.today()).days
    except ValueError:
        return None


@app.context_processor
def inject():
    w = db.get_kv("worker")
    return {"STATUSES": db.STATUSES, "csrf_token": csrf_token, "worker_alive": worker.worker_alive(),
            "worker_state": w.get("state", ""), "pending_count": db.stats().get("pending", 0)}


@app.template_filter("fromjson")
def fromjson(s):
    try:
        return json.loads(s) if s else {}
    except ValueError:
        return {}


@app.route("/")
def index():
    status = request.args.get("status") or None
    q = request.args.get("q") or None
    hide_expired = request.args.get("all") != "1"
    campaigns = db.list_campaigns(status=status, q=q, hide_expired=hide_expired)
    soon = [c for c in db.list_campaigns(hide_expired=True)
            if c["status"] in ("new", "pending", "manual") and (d := days_left(c["deadline"])) is not None and d <= 3]
    return render_template("index.html", campaigns=campaigns, stats=db.stats(), soon=soon,
                           status=status, q=q or "", show_all=not hide_expired,
                           has_profile=bool(db.get_profile()))


@app.route("/campaign/new", methods=["GET", "POST"])
@app.route("/campaign/<int:cid>/edit", methods=["GET", "POST"])
def edit(cid=None):
    c = db.get_campaign(cid) if cid else None
    if request.method == "POST":
        f = request.form
        url = f.get("url", "").strip()
        if not url.startswith(("http://", "https://")):
            flash("URL は http:// か https:// で始めてください。", "error")
            return render_template("edit.html", c=f, cid=cid)
        data = dict(title=f.get("title", "").strip() or url, url=url, prize=f.get("prize", "").strip(),
                    deadline=f.get("deadline") or None, note=f.get("note", "").strip())
        if cid:
            new_status = f.get("status", "new")
            if new_status == "approved" and c["status"] != "approved":
                new_status = c["status"]  # 承認は承認画面からだけ行う
            if url != c["url"]:  # URL を直したら事前チェックをやり直す
                data.update(form_url=None, checked_at=None, check_json=None, result=None, attempts=0)
                if new_status in ("pending", "approved", "manual"):
                    new_status = "new"
            db.update_campaign(cid, status=new_status, **data)
            flash("更新しました。")
        elif db.add_campaign(**data):
            flash("追加しました。")
        else:
            flash("同じ URL の懸賞がすでに登録されています。", "error")
        return redirect(url_for("index"))
    return render_template("edit.html", c=c, cid=cid)


@app.post("/campaign/<int:cid>/status/<status>")
def set_status(cid, status):
    if status in db.STATUSES and status != "approved":
        db.update_campaign(cid, status=status)
    return redirect(request.referrer or url_for("index"))


@app.post("/campaign/<int:cid>/delete")
def delete(cid):
    db.delete_campaign(cid)
    flash("削除しました。")
    return redirect(url_for("index"))


@app.post("/campaign/<int:cid>/autofill")
def autofill(cid):
    c = db.get_campaign(cid)
    if not c:
        return redirect(url_for("index"))
    if not db.get_profile():
        flash("先にプロフィールを登録してください。", "error")
        return redirect(url_for("profile"))
    # ブラウザは別プロセスで起動し、管理画面はそのまま使えるようにする。
    # 起動直後に失敗したときは、その理由を画面に表示する。
    log_path = os.path.join(HERE, "autofill.log")
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "autofill.py"), c["url"]], cwd=HERE,
                            stdout=log, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    try:
        code = proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        code = None  # ブラウザが開いて、利用者の操作を待っている
    log.close()
    if code:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = [l.strip() for l in f if l.strip()]
        msg = next((l[len("ERROR: "):] for l in reversed(lines) if l.startswith("ERROR: ")), None)
        flash(f"{msg or 'ブラウザを開けませんでした：' + (lines[-1] if lines else '原因不明')}（詳細は autofill.log）", "error")
        return redirect(request.referrer or url_for("index"))
    flash(f"「{c['title']}」をブラウザで開きました。入力内容を確認して自分で送信し、終わったら「応募済み」を押してください。")
    return redirect(request.referrer or url_for("index"))


@app.route("/profile", methods=["GET", "POST"])
def profile():
    if request.method == "POST":
        db.set_kv("profile", {k: request.form.get(k, "").strip() for k, _, _ in db.PROFILE_FIELDS})
        flash("プロフィールを保存しました。")
        return redirect(url_for("profile"))
    return render_template("profile.html", fields=db.PROFILE_FIELDS, p=db.get_profile())


@app.route("/feeds", methods=["GET", "POST"])
def feeds():
    if request.method == "POST":
        if request.form.get("action") == "filter":
            db.set_kv("filter", {"include": request.form.get("include", ""), "exclude": request.form.get("exclude", "")})
            flash("キーワードを保存しました。")
        else:
            url = request.form.get("url", "").strip()
            if url.startswith(("http://", "https://")):
                db.add_feed(request.form.get("name", "").strip() or url, url)
                flash("収集元を追加しました。")
            else:
                flash("URL が正しくありません。", "error")
        return redirect(url_for("feeds"))
    return render_template("feeds.html", feeds=db.list_feeds(), f=db.get_kv("filter"))


@app.post("/feeds/<int:fid>/delete")
def feed_delete(fid):
    db.delete_feed(fid)
    return redirect(url_for("feeds"))


@app.post("/feeds/<int:fid>/toggle")
def feed_toggle(fid):
    db.toggle_feed(fid)
    return redirect(url_for("feeds"))


@app.post("/collect")
def collect():
    added, errors = collector.collect_all()
    flash(f"{added} 件の新しい懸賞を取り込みました。")
    for e in errors:
        flash(f"取得失敗 — {e}", "error")
    return redirect(request.referrer or url_for("index"))


# ---------- 24 時間運用：承認・設定 ----------
@app.route("/review")
def review():
    return render_template("review.html", pending=db.campaigns_by_status("pending"),
                           approved=db.campaigns_by_status("approved"), line_ok=notify.line_configured())


@app.post("/review/approve")
def approve():
    n = 0
    for cid in request.form.getlist("ids"):
        c = db.get_campaign(int(cid))
        if c and c["status"] == "pending" and c["check_json"]:
            db.update_campaign(c["id"], status="approved", result="承認済み。ワーカーが順番に送信します")
            n += 1
    flash(f"{n} 件を承認しました。ワーカーが動いていれば、数分以内に送信されます。" if n else "承認する懸賞を選んでください。",
          "info" if n else "error")
    if n and not worker.worker_alive():
        flash("ワーカーが動いていません。送信するには worker.py を起動してください。", "error")
    return redirect(url_for("review"))


@app.post("/campaign/<int:cid>/unapprove")
def unapprove(cid):
    c = db.get_campaign(cid)
    if c and c["status"] == "approved":
        db.update_campaign(cid, status="pending", result="承認を取り消しました")
    return redirect(request.referrer or url_for("review"))


@app.post("/campaign/<int:cid>/recheck")
def recheck(cid):
    db.update_campaign(cid, status="new", checked_at=None, check_json=None, result=None, attempts=0)
    flash("次の取り込みのときに、もう一度事前チェックします。")
    return redirect(request.referrer or url_for("index"))


@app.route("/screenshots/<path:name>")
def screenshot(name):
    return send_from_directory(browser_ops.SHOT_DIR, name)


SETTING_FIELDS = ["line_user_id", "collect_hours", "check_limit", "submit_limit", "submit_interval", "notify_hour"]


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        data = {k: request.form.get(k, "").strip() for k in SETTING_FIELDS}
        for k in SETTING_FIELDS[1:]:
            if data[k] and not data[k].isdigit():
                flash("数値の項目には半角数字を入れてください。", "error")
                return redirect(url_for("settings"))
        token = request.form.get("line_token", "").strip()
        if token:  # 空欄なら今のトークンをそのまま使う
            data["line_token"] = token
        if request.form.get("clear_token"):
            data["line_token"] = ""
        db.set_kv("settings", data)
        flash("設定を保存しました。")
        return redirect(url_for("settings"))
    s = db.get_kv("settings")
    return render_template("settings.html", s=s, has_token=bool(s.get("line_token")), defaults=worker.DEFAULTS,
                           events=db.recent_events(), worker_kv=db.get_kv("worker"))


@app.post("/settings/test-line")
def test_line():
    try:
        ok = notify.send_line("【懸賞マネージャー】テスト通知です。この通知が届いていれば設定は完了です。")
        flash("テスト通知を送りました。LINE を確認してください。" if ok else "LINE のトークンとユーザー ID を設定してください。",
              "info" if ok else "error")
    except Exception as e:
        flash(str(e), "error")
    return redirect(url_for("settings"))


@app.post("/worker/run-now")
def run_now():
    db.set_kv("worker", {"last_collect": ""})
    flash("1 分以内に、ワーカーが取り込みと事前チェックを始めます。" if worker.worker_alive()
          else "ワーカーが動いていません。worker.py を起動してください。", "info" if worker.worker_alive() else "error")
    return redirect(request.referrer or url_for("index"))


db.init_db()

if __name__ == "__main__":
    # 個人情報を扱うため、自分の PC からだけアクセスできるようにする
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)
