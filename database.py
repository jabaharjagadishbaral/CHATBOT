import logging
import os
import threading
import time
from contextlib import contextmanager

from psycopg2 import pool, sql
from psycopg2.extras import RealDictCursor

logger = logging.getLogger('neighborhood_helpboard')


class Database:
    def __init__(self, max_posts=50):
        self.max_posts = max_posts
        self.lock = threading.RLock()
        self.schema = os.getenv('SUPABASE_SCHEMA', 'public')
        self.table_name = os.getenv('SUPABASE_POSTS_TABLE', 'posts')
        self.pool = None

        db_url = os.getenv('SUPABASE_DATABASE_URL') or os.getenv('DATABASE_URL')
        if not db_url:
            raise RuntimeError('Set SUPABASE_DATABASE_URL or DATABASE_URL to your Supabase PostgreSQL connection string.')

        min_pool_size = self._positive_int(os.getenv('DATABASE_POOL_MIN', '1'), 1)
        max_pool_size = self._positive_int(os.getenv('DATABASE_POOL_MAX', '10'), min_pool_size)
        ssl_mode = os.getenv('DATABASE_SSL_MODE', 'require')

        self.pool = pool.ThreadedConnectionPool(
            min_pool_size,
            max_pool_size,
            dsn=db_url,
            sslmode=ssl_mode,
            connect_timeout=10,
            application_name='neighborhood-helpboard',
        )

        self._initialize_db()

    def _positive_int(self, value, default):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @contextmanager
    def _cursor(self):
        if self.pool is None:
            raise RuntimeError('Database pool is not initialized.')

        conn = self.pool.getconn()
        try:
            try:
                conn.rollback()
            except Exception:
                pass
            conn.autocommit = True
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                yield cur
        except Exception:
            try:
                conn.rollback()
            except Exception:
                logger.exception('Failed to roll back Supabase PostgreSQL connection.')
            raise
        finally:
            self.pool.putconn(conn)

    def _table(self):
        return sql.Identifier(self.schema, self.table_name)

    def _index_name(self, suffix):
        safe_table_name = ''.join(
            character if character.isalnum() else '_'
            for character in self.table_name
        ).strip('_') or 'posts'
        return f'idx_{safe_table_name}_{suffix}'

    def _initialize_db(self):
        with self.lock:
            with self._cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            id BIGSERIAL PRIMARY KEY,
                            username TEXT NOT NULL,
                            type TEXT NOT NULL,
                            message TEXT NOT NULL,
                            timestamp DOUBLE PRECISION NOT NULL
                        )
                        """
                    ).format(self._table())
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE INDEX IF NOT EXISTS {}
                        ON {} (type, id DESC)
                        """
                    ).format(
                        sql.Identifier(self._index_name('type_id')),
                        self._table(),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE INDEX IF NOT EXISTS {}
                        ON {} (timestamp DESC, id DESC)
                        """
                    ).format(
                        sql.Identifier(self._index_name('timestamp_id')),
                        self._table(),
                    )
                )

    def load(self):
        return self.list_posts(limit=self.max_posts)

    def save(self):
        return None

    def add_post(self, username, type_, message):
        timestamp = time.time()
        with self._cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    INSERT INTO {} (username, type, message, timestamp)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, username, type, message, timestamp
                    """
                ).format(self._table()),
                (username, type_, message, timestamp),
            )
            return dict(cur.fetchone())

    def list_posts(self, type_filter=None, limit=10):
        try:
            requested_limit = int(limit or self.max_posts)
        except (TypeError, ValueError):
            requested_limit = self.max_posts
        limit = min(max(requested_limit, 1), self.max_posts)

        with self._cursor() as cur:
            if type_filter:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT id, username, type, message, timestamp
                        FROM {}
                        WHERE type = %s
                        ORDER BY id DESC
                        LIMIT %s
                        """
                    ).format(self._table()),
                    (type_filter, limit),
                )
            else:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT id, username, type, message, timestamp
                        FROM {}
                        ORDER BY id DESC
                        LIMIT %s
                        """
                    ).format(self._table()),
                    (limit,),
                )
            rows = cur.fetchall()

        return [dict(row) for row in reversed(rows)]

    def get_post(self, id_):
        try:
            post_id = int(id_)
        except (TypeError, ValueError):
            return None

        with self._cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    SELECT id, username, type, message, timestamp
                    FROM {}
                    WHERE id = %s
                    """
                ).format(self._table()),
                (post_id,),
            )
            row = cur.fetchone()

        return dict(row) if row else None

    def close(self):
        if self.pool is not None:
            self.pool.closeall()
            self.pool = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False
