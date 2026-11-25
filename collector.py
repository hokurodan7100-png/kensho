"""RSS/Atom フィードから懸賞情報を集める。

懸賞まとめサイトなどの RSS を「収集元」に登録しておくと、
新しい記事を懸賞として取り込み、本文から締切日を推定する。
"""
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date

import db

USER_AGENT = "KenshoManager/1.0 (personal feed reader)"

_TAG_RE = re.compile(r"<[^>]+>")
# 2026年12月31日 / 2026/12/31 / 2026-12-31 / 2026.12.31
_FULL_DATE = re.compile(r"(20\d{2})\s*[年/.\-]\s*(\d{1,2})\s*[月/.\-]\s*(\d{1,2})\s*日?")
# 12月31日 / 12/31
_SHORT_DATE = re.compile(r"(?<![\d/.\-])(\d{1,2})\s*(?:月\s*(\d{1,2})\s*日|/(\d{1,2})(?![\d/]))")
_DEADLINE_HINT = re.compile(r"締切|締め切り|〆切|〆|期限|まで|迄|応募期間|期間")


def strip_html(s):
    return html.unescape(_TAG_RE.sub(" ", s or "")).strip()


def _safe_date(y, m, d):
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def parse_deadline(text, today=None):
    """文章中の日付から締切日を推定して YYYY-MM-DD を返す（見つからなければ None）。

    「応募期間：11/1〜12/31」のような期間表記を考慮して、見つかった日付のうち
    最も遅いものを締切とみなす。年が書かれていない日付は、今日から見て
    直近の未来（または 60 日以内の過去）になる年を補う。
    """
    if not text:
        return None
    today = today or date.today()
    found = []
    for m in _FULL_DATE.finditer(text):
        d = _safe_date(*m.groups())
        if d:
            found.append(d)
    # 年付きの日付と重ならない部分から、年なしの日付を探す
    rest = _FULL_DATE.sub(" ", text)
    if _DEADLINE_HINT.search(text) or not found:
        for m in _SHORT_DATE.finditer(rest):
            mon, day = m.group(1), m.group(2) or m.group(3)
            d = _safe_date(today.year, mon, day)
            if not d:
                continue
            if (today - d).days > 60:  # 年をまたいだ表記（12月に「1/15まで」など）
                d = _safe_date(today.year + 1, mon, day)
            if d:
                found.append(d)
    if not found:
        return None
    return max(found).isoformat()


def _text(el, *names):
    for n in names:
        found = el.find(n)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def parse_feed(xml_bytes):
    """RSS 2.0 / RSS 1.0 / Atom をパースして [{title, url, summary}] を返す。"""
    root = ET.fromstring(xml_bytes)
    # 名前空間を取り除いて扱いやすくする
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    items = []
    for it in root.iter("item"):  # RSS 1.0 / 2.0
        items.append({
            "title": _text(it, "title"),
            "url": _text(it, "link", "guid"),
            "summary": strip_html(_text(it, "encoded", "description")),
        })
    for it in root.iter("entry"):  # Atom
        link = ""
        for ln in it.findall("link"):
            if ln.get("rel", "alternate") == "alternate" and ln.get("href"):
                link = ln.get("href")
                break
        items.append({
            "title": _text(it, "title"),
            "url": link,
            "summary": strip_html(_text(it, "content", "summary")),
        })
    return [i for i in items if i["title"] and i["url"].startswith(("http://", "https://"))]


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def _keywords(s):
    return [w.strip() for w in re.split(r"[,、\n]", s or "") if w.strip()]


def collect_all(fetcher=fetch):
    """有効な全フィードを取得して取り込む。(追加件数, エラー一覧) を返す。"""
    settings = db.get_kv("filter")
    include = _keywords(settings.get("include"))
    exclude = _keywords(settings.get("exclude"))
    added, errors = 0, []
    for feed in db.list_feeds():
        if not feed["enabled"]:
            continue
        try:
            items = parse_feed(fetcher(feed["url"]))
        except Exception as e:  # ネットワーク・XML エラーはフィード単位で記録
            db.mark_feed(feed["id"], error=str(e)[:300])
            errors.append(f"{feed['name']}: {e}")
            continue
        for item in items:
            text = f"{item['title']} {item['summary']}"
            if include and not any(w in text for w in include):
                continue
            if exclude and any(w in text for w in exclude):
                continue
            if db.add_campaign(
                title=item["title"][:200],
                url=item["url"],
                deadline=parse_deadline(text),
                source=feed["name"],
                note=item["summary"][:300],
            ):
                added += 1
        db.mark_feed(feed["id"])
    return added, errors
