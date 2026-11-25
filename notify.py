"""LINE への通知（LINE 公式アカウントの Messaging API で、自分宛てに送る）。

通知には件数とタイトルだけを入れ、住所などの個人情報は入れない。
無料プランは送れる通数に上限があるため、まとめて 1 通で送る。
"""
import json
import urllib.error
import urllib.request

import db

PUSH_URL = "https://api.line.me/v2/bot/message/push"


def line_configured():
    s = db.get_kv("settings")
    return bool(s.get("line_token") and s.get("line_user_id"))


def send_line(text, opener=urllib.request.urlopen):
    """送れたら True。未設定なら False。送信エラーは例外（呼び出し側で記録する）。"""
    s = db.get_kv("settings")
    token, to = s.get("line_token"), s.get("line_user_id")
    if not (token and to):
        return False
    body = json.dumps({"to": to, "messages": [{"type": "text", "text": text[:4900]}]}).encode()
    req = urllib.request.Request(PUSH_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with opener(req, timeout=20) as res:
            res.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"LINE 通知に失敗しました（{e.code}）：{detail}") from e
    return True
