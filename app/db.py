"""Postgres access: one pool, a tiny schema, JSON helpers."""
import os

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key text PRIMARY KEY, value jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS lessons (
  id uuid PRIMARY KEY,
  title text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'queued',
  step text NOT NULL DEFAULT '',
  options jsonb NOT NULL DEFAULT '{}',
  paper_text text NOT NULL DEFAULT '',
  state jsonb NOT NULL DEFAULT '{}',
  usage jsonb NOT NULL DEFAULT '[]',
  error text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS eval_checks (
  id bigserial PRIMARY KEY,
  lesson_id uuid NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
  gate text NOT NULL,
  name text NOT NULL,
  passed boolean NOT NULL,
  score real,
  section int,
  evidence text NOT NULL DEFAULT ''
);
"""

pool: AsyncConnectionPool | None = None


async def init():
    global pool
    url = os.environ.get("DATABASE_URL", "postgresql://lesson:lesson@localhost:5432/lesson")
    pool = AsyncConnectionPool(url, open=False, kwargs={"row_factory": dict_row, "autocommit": True})
    await pool.open(wait=True, timeout=60)
    async with pool.connection() as c:
        await c.execute(SCHEMA)


async def close():
    if pool:
        await pool.close()


async def q(sql, *args):
    async with pool.connection() as c:
        cur = await c.execute(sql, args)
        return await cur.fetchall() if cur.description else []


async def one(sql, *args):
    rows = await q(sql, *args)
    return rows[0] if rows else None


async def get_setting(key, default=None):
    row = await one("SELECT value FROM settings WHERE key=%s", key)
    return row["value"] if row else default


async def set_setting(key, value):
    await q(
        "INSERT INTO settings(key,value) VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
        key, Jsonb(value),
    )


async def save_lesson(lesson_id, **fields):
    sets, args = [], []
    for k, v in fields.items():
        sets.append(f"{k}=%s")
        args.append(Jsonb(v) if isinstance(v, (dict, list)) else v)
    await q(f"UPDATE lessons SET {', '.join(sets)} WHERE id=%s", *args, lesson_id)
