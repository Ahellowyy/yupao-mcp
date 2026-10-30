"""Local SQLite state. Credentials are never stored in this database."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .client import YupaoError


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def action_review_hash(account, kind, review, payload):
    return digest({'account': account, 'kind': kind, 'review': review, 'payload': payload})


class Store:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'workspace.sqlite3'

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS documents (
                kind TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL, PRIMARY KEY(kind,key));
              CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY, account TEXT NOT NULL, kind TEXT NOT NULL,
                dedupe_key TEXT NOT NULL, review TEXT NOT NULL, review_hash TEXT NOT NULL,
                payload TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
                expires REAL NOT NULL, receipt TEXT, approved REAL);
              CREATE UNIQUE INDEX IF NOT EXISTS actions_no_duplicates ON actions(dedupe_key)
                WHERE status IN ('awaiting_confirmation','approved','dispatching','sent','submitted','outcome_unknown');
            ''')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def put(self, kind, key, value):
        now = time.time()
        with self.connection() as db:
            db.execute('INSERT INTO documents VALUES (?,?,?,?,?) ON CONFLICT(kind,key) '
                       'DO UPDATE SET value=excluded.value,updated=excluded.updated',
                       (kind, key, encoded(value), now, now))
        return value

    def get(self, kind, key):
        with self.connection() as db:
            row = db.execute('SELECT value FROM documents WHERE kind=? AND key=?', (kind, key)).fetchone()
        if row is None:
            raise YupaoError('NOT_FOUND', '本机记录不存在')
        return json.loads(row['value'])

    def list(self, kind, limit=100):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise YupaoError('INVALID_ARGUMENT', 'limit 范围 1–500')
        with self.connection() as db:
            rows = db.execute('SELECT value FROM documents WHERE kind=? ORDER BY updated DESC LIMIT ?',
                              (kind, limit)).fetchall()
        return [json.loads(row['value']) for row in rows]

    def delete(self, kind, key):
        with self.connection() as db:
            count = db.execute('DELETE FROM documents WHERE kind=? AND key=?', (kind, key)).rowcount
        return {'removed': bool(count), 'key': key}

    def prepare_action(self, account, kind, review, payload, dedupe_identity=None):
        now = time.time()
        identity = digest({'account': account, 'kind': kind, 'target': dedupe_identity or payload})
        review_hash = action_review_hash(account, kind, review, payload)
        with self.connection() as db:
            db.execute("UPDATE actions SET status='expired' WHERE expires<? AND status IN "
                       "('awaiting_confirmation','approved')", (now,))
            existing = db.execute("SELECT * FROM actions WHERE dedupe_key=? AND status IN "
                                  "('awaiting_confirmation','approved','dispatching','sent','submitted','outcome_unknown','rejected')",
                                  (identity,)).fetchone()
            if existing:
                if existing['status'] in ('sent', 'submitted', 'dispatching', 'outcome_unknown', 'rejected'):
                    raise YupaoError('DUPLICATE_OR_UNKNOWN_ACTION', '相同操作已执行、被平台拒绝或结果待核实，禁止再次发送')
                if existing['review_hash'] == review_hash:
                    if existing['status'] == 'awaiting_confirmation':
                        db.execute('UPDATE actions SET expires=? WHERE id=?', (now + 1800, existing['id']))
                        existing = db.execute('SELECT * FROM actions WHERE id=?', (existing['id'],)).fetchone()
                    return self._action(existing)
                db.execute("UPDATE actions SET status='superseded' WHERE id=?", (existing['id'],))
            aid = uuid.uuid4().hex
            db.execute('INSERT INTO actions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (aid, account, kind, identity, encoded(review), review_hash, encoded(payload),
                        'awaiting_confirmation', now, now + 1800, None, None))
            row = db.execute('SELECT * FROM actions WHERE id=?', (aid,)).fetchone()
        return self._action(row)

    @staticmethod
    def _action(row):
        result = dict(row)
        for key in ('review', 'payload', 'receipt'):
            if result[key] is not None:
                result[key] = json.loads(result[key])
        return result

    def action(self, action_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM actions WHERE id=?', (action_id,)).fetchone()
        if row is None:
            raise YupaoError('NOT_FOUND', '操作预览不存在')
        return self._action(row)

    def approve(self, action_id, review_hash):
        with self.connection() as db:
            count = db.execute("UPDATE actions SET status='approved',approved=? WHERE id=? AND review_hash=? "
                               "AND status IN ('awaiting_confirmation','approved') AND expires>?",
                               (time.time(), action_id, review_hash, time.time())).rowcount
        if count != 1:
            raise YupaoError('CONFIRMATION_INVALID', '预览已过期、变更或不再等待确认')

    def claim(self, action_id, account, review_hash):
        with self.connection() as db:
            count = db.execute("UPDATE actions SET status='dispatching' WHERE id=? AND account=? AND review_hash=? "
                               "AND status='approved' AND expires>? AND approved>?",
                               (action_id, account, review_hash, time.time(), time.time() - 120)).rowcount
        if count != 1:
            raise YupaoError('CONFIRMATION_REQUIRED', '需对当前账号、目标及内容重新确认；每次批准仅能执行一次')

    def finish(self, action_id, status, receipt):
        if status not in ('sent', 'submitted', 'outcome_unknown', 'rejected'):
            raise ValueError('Invalid final action state')
        with self.connection() as db:
            count = db.execute("UPDATE actions SET status=?,receipt=? WHERE id=? AND status='dispatching'",
                               (status, encoded(receipt), action_id)).rowcount
        if count != 1:
            raise YupaoError('ACTION_STATE_CONFLICT', '本机执行状态不一致，请核对回执，禁止重发')
