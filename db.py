"""SQLite によるデータ保存（懸賞・収集元フィード・設定/プロフィール）。"""
import os
import sqlite3
from datetime import datetime

DB_PATH = os.environ.get("KENSHO_DB", os.path.join(os.path.dirname(__file__), "kensho.db"))

STATUSES = {
    "new": "未応募",
    "pending": "送信待ち",      # 事前チェック済み。承認すると自動送信される
    "approved": "送信予定",     # 承認済み。ワーカーが送信する
    "manual": "手動で応募",     # 自動では応募できない（理由は result に入る）
    "applied": "応募済み",
    "won": "当選",
    "lost": "落選",
    "skipped": "スキップ",
}

PROFILE_FIELDS = [
    ("last_name", "姓", "山田"),
    ("first_name", "名", "太郎"),
    ("last_kana", "セイ（カタカナ）", "ヤマダ"),
    ("first_kana", "メイ（カタカナ）", "タロウ"),
    ("email", "メールアドレス", "taro@example.com"),
    ("phone", "電話番号（ハイフン区切り）", "090-1234-5678"),
    ("zip", "郵便番号（ハイフン区切り）", "123-4567"),
    ("prefecture", "都道府県", "東京都"),
    ("city", "市区町村", "千代田区千代田"),
    ("address1", "番地", "1-1"),
    ("address2", "建物名・部屋番号", "○○マンション101"),
    ("birth_year", "生年（西暦）", "1990"),
    ("birth_month", "生月", "1"),
    ("birth_day", "生日", "1"),
    ("gender", "性別（男性/女性/その他）", "男性"),
    ("job", "職業", "会社員"),
]


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                prize TEXT DEFAULT '',
                deadline TEXT,              -- YYYY-MM-DD
                source TEXT DEFAULT '手動',
                status TEXT DEFAULT 'new',
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                applied_at TEXT
            );
            CREATE TABLE IF NOT EXISTS feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                enabled INTEGER DEFAULT 1,
                last_fetched TEXT,
                last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL,
                level TEXT NOT NULL,       -- info / error
                message TEXT NOT NULL
            );
            """
        )
        # 24 時間運用で追加した列（古い DB にも足す）
        cols = {r["name"] for r in c.execute("PRAGMA table_info(campaigns)")}
        for name, ddl in [
            ("form_url", "TEXT"),          # 実際の応募フォームの URL（記事ページから辿った先）
            ("check_json", "TEXT"),        # 事前チェックの結果（入力内容・チェックボックスなど）
            ("checked_at", "TEXT"),
            ("result", "TEXT"),            # 手動にした理由・送信結果
            ("screenshot", "TEXT"),        # 送信後の画面のスクリーンショット
            ("attempts", "INTEGER DEFAULT 0"),
        ]:
            if name not in cols:
                c.execute(f"ALTER TABLE campaigns ADD COLUMN {name} {ddl}")


def now():
    return datetime.now().isoformat(timespec="seconds")


# ---- key/value（プロフィール・設定） ----
def get_kv(prefix):
    with connect() as c:
        rows = c.execute("SELECT key, value FROM kv WHERE key LIKE ?", (prefix + ".%",)).fetchall()
    return {r["key"][len(prefix) + 1:]: r["value"] for r in rows}


def set_kv(prefix, data):
    with connect() as c:
        for k, v in data.items():
            c.execute(
                "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"{prefix}.{k}", v),
            )


def get_profile():
    return get_kv("profile")


# ---- campaigns ----
def add_campaign(title, url, prize="", deadline=None, source="手動", note=""):
    """追加できたら True、同じ URL が既にあれば False。"""
    with connect() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO campaigns(title, url, prize, deadline, source, note, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (title, url, prize, deadline or None, source, note, now()),
        )
        return cur.rowcount > 0


def list_campaigns(status=None, q=None, hide_expired=False):
    sql = "SELECT * FROM campaigns WHERE 1=1"
    args = []
    if status:
        sql += " AND status = ?"
        args.append(status)
    if q:
        sql += " AND (title LIKE ? OR prize LIKE ? OR note LIKE ?)"
        args += [f"%{q}%"] * 3
    if hide_expired:
        sql += " AND (deadline IS NULL OR deadline >= date('now', 'localtime'))"
    sql += " ORDER BY deadline IS NULL, deadline ASC, id DESC"
    with connect() as c:
        return c.execute(sql, args).fetchall()


def get_campaign(cid):
    with connect() as c:
        return c.execute("SELECT * FROM campaigns WHERE id = ?", (cid,)).fetchone()


def update_campaign(cid, **fields):
    allowed = {"title", "url", "prize", "deadline", "status", "note", "applied_at",
               "form_url", "check_json", "checked_at", "result", "screenshot", "attempts"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    if fields.get("status") == "applied":
        fields.setdefault("applied_at", now())
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE campaigns SET {sets} WHERE id = ?", (*fields.values(), cid))


def delete_campaign(cid):
    with connect() as c:
        c.execute("DELETE FROM campaigns WHERE id = ?", (cid,))


def stats():
    with connect() as c:
        rows = c.execute("SELECT status, COUNT(*) n FROM campaigns GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


# ---- feeds ----
def list_feeds():
    with connect() as c:
        return c.execute("SELECT * FROM feeds ORDER BY id").fetchall()


def add_feed(name, url):
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO feeds(name, url) VALUES(?, ?)", (name, url))


def delete_feed(fid):
    with connect() as c:
        c.execute("DELETE FROM feeds WHERE id = ?", (fid,))


def toggle_feed(fid):
    with connect() as c:
        c.execute("UPDATE feeds SET enabled = 1 - enabled WHERE id = ?", (fid,))


def mark_feed(fid, error=None):
    with connect() as c:
        c.execute("UPDATE feeds SET last_fetched = ?, last_error = ? WHERE id = ?", (now(), error, fid))


# ---- worker 用 ----
def campaigns_to_check(limit):
    """事前チェックがまだの、締切前の未応募の懸賞。"""
    with connect() as c:
        return c.execute(
            "SELECT * FROM campaigns WHERE status = 'new' AND checked_at IS NULL "
            "AND (deadline IS NULL OR deadline >= date('now', 'localtime')) "
            "ORDER BY deadline IS NULL, deadline LIMIT ?", (limit,)).fetchall()


def campaigns_by_status(status, limit=1000):
    with connect() as c:
        return c.execute("SELECT * FROM campaigns WHERE status = ? ORDER BY deadline IS NULL, deadline, id LIMIT ?",
                         (status, limit)).fetchall()


def log_event(message, level="info"):
    with connect() as c:
        c.execute("INSERT INTO events(at, level, message) VALUES(?,?,?)", (now(), level, message))
        c.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 500)")


def recent_events(limit=50):
    with connect() as c:
        return c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
