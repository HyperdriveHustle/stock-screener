"""
SQLite 缓存层
用于持久化 API 响应, 减少重复拉取并提升稳定性
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from runtime.utils import news_fingerprint

logger = logging.getLogger(__name__)


def _json_default(obj: Any):
    """兜底 JSON 序列化"""
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    return str(obj)


class SQLiteCache:
    """轻量级 SQLite 缓存"""

    def __init__(self, db_file: str):
        os.makedirs(os.path.dirname(db_file), exist_ok=True)
        self.conn = sqlite3.connect(db_file, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self._lock = threading.Lock()
        self._init_schema()
        self.stats = {
            "hit": 0,
            "miss": 0,
            "expired": 0,
            "write": 0,
        }

    def _init_schema(self):
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_cache (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    source TEXT,
                    symbol TEXT,
                    data_type TEXT,
                    fetched_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    as_of TEXT
                )
                """
            )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_api_cache_symbol_type
                ON api_cache(symbol, data_type)
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS news_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    published_at INTEGER,
                    headline TEXT,
                    source TEXT,
                    url TEXT,
                    payload TEXT NOT NULL,
                    inserted_at TEXT NOT NULL
                )
                """
            )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_news_symbol_time
                ON news_items(symbol, published_at DESC)
                """
            )

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _to_iso(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).isoformat()

    def get(self, key: str) -> Any | None:
        """读取缓存 (自动检查过期)"""
        now = self._utc_now()
        with self._lock:
            row = self.conn.execute(
                "SELECT payload, expires_at FROM api_cache WHERE key = ?",
                (key,),
            ).fetchone()

        if not row:
            self.stats["miss"] += 1
            return None

        payload, expires_at = row
        if datetime.fromisoformat(expires_at) < now:
            self.stats["expired"] += 1
            return None

        self.stats["hit"] += 1
        return json.loads(payload)

    def get_meta(self, key: str) -> dict | None:
        """读取缓存元信息"""
        with self._lock:
            row = self.conn.execute(
                """
                SELECT source, symbol, data_type, fetched_at, expires_at, as_of
                FROM api_cache
                WHERE key = ?
                """,
                (key,),
            ).fetchone()

        if not row:
            return None

        return {
            "source": row[0],
            "symbol": row[1],
            "data_type": row[2],
            "fetched_at": row[3],
            "expires_at": row[4],
            "as_of": row[5],
        }

    def get_stale(self, key: str) -> Any | None:
        """读取缓存, 忽略过期时间, 用于显式 stale fallback"""
        with self._lock:
            row = self.conn.execute(
                "SELECT payload FROM api_cache WHERE key = ?",
                (key,),
            ).fetchone()

        if not row:
            return None

        try:
            return json.loads(row[0])
        except Exception:
            return None

    def set(
        self,
        key: str,
        payload: Any,
        ttl_seconds: int,
        source: str = "",
        symbol: str = "",
        data_type: str = "",
        as_of: str = "",
    ):
        """写入缓存"""
        now = self._utc_now()
        expires_at = now + timedelta(seconds=max(1, ttl_seconds))
        payload_text = json.dumps(payload, ensure_ascii=False, default=_json_default)

        with self._lock:
            with self.conn:
                self.conn.execute(
                    """
                    INSERT INTO api_cache (
                        key, payload, source, symbol, data_type,
                        fetched_at, expires_at, as_of
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        payload = excluded.payload,
                        source = excluded.source,
                        symbol = excluded.symbol,
                        data_type = excluded.data_type,
                        fetched_at = excluded.fetched_at,
                        expires_at = excluded.expires_at,
                        as_of = excluded.as_of
                    """,
                    (
                        key,
                        payload_text,
                        source,
                        symbol,
                        data_type,
                        self._to_iso(now),
                        self._to_iso(expires_at),
                        as_of or self._to_iso(now),
                    ),
                )
        self.stats["write"] += 1

    @staticmethod
    def news_fingerprint(article: dict) -> str:
        """为新闻生成稳定指纹"""
        return news_fingerprint(article)

    def store_news_and_get_recent(
        self,
        symbol: str,
        articles: list[dict],
        limit: int,
    ) -> list[dict]:
        """
        写入新闻去重表, 返回该 symbol 最新去重后的 N 条
        """
        now_iso = self._to_iso(self._utc_now())
        with self._lock:
            with self.conn:
                for article in articles:
                    payload = json.dumps(article, ensure_ascii=False, default=_json_default)
                    fp = self.news_fingerprint(article)
                    published_at = int(article.get("datetime") or 0)
                    self.conn.execute(
                        """
                        INSERT OR IGNORE INTO news_items (
                            symbol, fingerprint, published_at, headline, source, url, payload, inserted_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            symbol,
                            fp,
                            published_at,
                            article.get("headline", ""),
                            article.get("source", ""),
                            article.get("url", ""),
                            payload,
                            now_iso,
                        ),
                    )

                rows = self.conn.execute(
                    """
                    SELECT payload
                    FROM news_items
                    WHERE symbol = ?
                    ORDER BY published_at DESC, id DESC
                    LIMIT ?
                    """,
                    (symbol, limit),
                ).fetchall()

        return [json.loads(row[0]) for row in rows]

    def prune_news(self, keep_days: int = 30):
        """清理旧新闻, 控制库体积"""
        cutoff = int((self._utc_now() - timedelta(days=keep_days)).timestamp())
        with self._lock:
            with self.conn:
                self.conn.execute(
                    "DELETE FROM news_items WHERE published_at > 0 AND published_at < ?",
                    (cutoff,),
                )

    def close(self):
        with self._lock:
            self.conn.close()
