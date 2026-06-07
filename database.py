import os
from typing import Optional

import psycopg2
import psycopg2.extras

from config import ADMIN_IDS


DATABASE_URL = os.getenv("DATABASE_URL")


class Database:
    def __init__(self):
        self._init_db()

    def _conn(self):
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        return conn

    def _init_db(self):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id   BIGINT PRIMARY KEY,
                        username  TEXT,
                        joined_at TIMESTAMPTZ DEFAULT NOW()
                    );

                    CREATE TABLE IF NOT EXISTS giveaways (
                        id          SERIAL PRIMARY KEY,
                        title       TEXT NOT NULL,
                        description TEXT NOT NULL,
                        prize       TEXT NOT NULL,
                        end_date    TEXT NOT NULL,
                        is_active   BOOLEAN DEFAULT TRUE,
                        winner_id   BIGINT,
                        created_by  BIGINT NOT NULL,
                        created_at  TIMESTAMPTZ DEFAULT NOW()
                    );

                    CREATE TABLE IF NOT EXISTS participants (
                        id           SERIAL PRIMARY KEY,
                        giveaway_id  INTEGER NOT NULL REFERENCES giveaways(id) ON DELETE CASCADE,
                        user_id      BIGINT NOT NULL REFERENCES users(user_id),
                        base_tickets INTEGER DEFAULT 1,
                        joined_at    TIMESTAMPTZ DEFAULT NOW(),
                        UNIQUE(giveaway_id, user_id)
                    );

                    CREATE TABLE IF NOT EXISTS shares (
                        id          SERIAL PRIMARY KEY,
                        giveaway_id INTEGER NOT NULL REFERENCES giveaways(id) ON DELETE CASCADE,
                        sharer_id   BIGINT NOT NULL REFERENCES users(user_id),
                        referred_id BIGINT NOT NULL REFERENCES users(user_id),
                        shared_at   TIMESTAMPTZ DEFAULT NOW(),
                        UNIQUE(giveaway_id, referred_id)
                    );

                    CREATE TABLE IF NOT EXISTS invites (
                        id          SERIAL PRIMARY KEY,
                        inviter_id  BIGINT NOT NULL REFERENCES users(user_id),
                        invited_id  BIGINT NOT NULL REFERENCES users(user_id),
                        invited_at  TIMESTAMPTZ DEFAULT NOW(),
                        UNIQUE(invited_id)
                    );
                """)

    # ── Users ────────────────────────────────────────────────────────────────

    def add_user(self, user_id: int, username: Optional[str]):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (user_id, username) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET username=%s",
                    (user_id, username, username)
                )

    def get_user(self, user_id: int) -> Optional[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
                return dict(cur.fetchone()) if cur.rowcount > 0 else None

    def get_total_users(self) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM users")
                return cur.fetchone()[0] or 0

    # ── Invites ───────────────────────────────────────────────────────────────

    def track_invite(self, inviter_id: int, invited_id: int):
        with self._conn() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        "INSERT INTO invites (inviter_id, invited_id) VALUES (%s, %s) ON CONFLICT (invited_id) DO NOTHING",
                        (inviter_id, invited_id)
                    )
                except Exception:
                    pass

    def get_total_invites(self) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM invites")
                return cur.fetchone()[0] or 0

    def get_top_inviters(self, limit: int = 10) -> list:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT u.user_id, u.username, COUNT(i.id)::int as invite_count
                       FROM invites i JOIN users u ON u.user_id = i.inviter_id
                       GROUP BY i.inviter_id, u.user_id, u.username
                       ORDER BY invite_count DESC LIMIT %s""",
                    (limit,)
                )
                return [dict(r) for r in cur.fetchall()]

    def get_top_sharers_global(self, limit: int = 10) -> list:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT u.user_id, u.username, COUNT(s.id)::int as total_shares
                       FROM shares s JOIN users u ON u.user_id = s.sharer_id
                       GROUP BY s.sharer_id, u.user_id, u.username
                       ORDER BY total_shares DESC LIMIT %s""",
                    (limit,)
                )
                return [dict(r) for r in cur.fetchall()]

    # ── Giveaways ─────────────────────────────────────────────────────────────

    def create_giveaway(self, title, description, prize, end_date, created_by) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO giveaways (title, description, prize, end_date, created_by) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                    (title, description, prize, end_date, created_by)
                )
                return cur.fetchone()[0]

    def _enrich(self, conn, row) -> dict:
        g = dict(row)
        gid = g["id"]
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM participants WHERE giveaway_id=%s AND user_id != ALL(%s::bigint[])",
                (gid, ADMIN_IDS)
            )
            g["participants_count"] = cur.fetchone()[0] or 0

            cur.execute(
                f"SELECT COALESCE(SUM(base_tickets),0) FROM participants WHERE giveaway_id=%s AND user_id != ALL(%s::bigint[])",
                (gid, ADMIN_IDS)
            )
            base = cur.fetchone()[0] or 0

            cur.execute(
                f"SELECT COUNT(*) FROM shares WHERE giveaway_id=%s AND sharer_id != ALL(%s::bigint[])",
                (gid, ADMIN_IDS)
            )
            shares = cur.fetchone()[0] or 0

        g["total_tickets"] = base + shares
        return g

    def get_active_giveaways(self) -> list:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM giveaways WHERE is_active=TRUE ORDER BY id DESC")
                return [self._enrich(conn, r) for r in cur.fetchall()]

    def get_all_giveaways(self) -> list:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM giveaways ORDER BY id DESC")
                return [self._enrich(conn, r) for r in cur.fetchall()]

    def get_giveaway(self, giveaway_id: int) -> Optional[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM giveaways WHERE id=%s", (giveaway_id,))
                row = cur.fetchone()
                return self._enrich(conn, row) if row else None

    def toggle_giveaway(self, giveaway_id: int) -> bool:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT is_active FROM giveaways WHERE id=%s", (giveaway_id,))
                row = cur.fetchone()
                if not row:
                    return False
                new = not row["is_active"]
                cur.execute("UPDATE giveaways SET is_active=%s WHERE id=%s", (new, giveaway_id))
                return new

    def set_winner(self, giveaway_id: int, winner_id: int):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE giveaways SET winner_id=%s, is_active=FALSE WHERE id=%s",
                    (winner_id, giveaway_id)
                )

    # ── Participants ──────────────────────────────────────────────────────────

    def join_giveaway(self, giveaway_id: int, user_id: int) -> str:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT is_active FROM giveaways WHERE id=%s", (giveaway_id,))
                g = cur.fetchone()
                if not g or not g["is_active"]:
                    return "inactive"
                cur.execute(
                    "SELECT id FROM participants WHERE giveaway_id=%s AND user_id=%s",
                    (giveaway_id, user_id)
                )
                if cur.fetchone():
                    return "already"
                cur.execute(
                    "INSERT INTO participants (giveaway_id, user_id) VALUES (%s, %s)",
                    (giveaway_id, user_id)
                )
                return "joined"

    def get_user_tickets(self, giveaway_id: int, user_id: int) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT base_tickets FROM participants WHERE giveaway_id=%s AND user_id=%s",
                    (giveaway_id, user_id)
                )
                row = cur.fetchone()
                if not row:
                    return 0
                base = row[0]
                cur.execute(
                    "SELECT COUNT(*) FROM shares WHERE giveaway_id=%s AND sharer_id=%s",
                    (giveaway_id, user_id)
                )
                shares = cur.fetchone()[0] or 0
                return base + shares

    def get_total_tickets(self, giveaway_id: int) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT COALESCE(SUM(base_tickets),0) FROM participants WHERE giveaway_id=%s AND user_id != ALL(%s::bigint[])",
                    (giveaway_id, ADMIN_IDS)
                )
                base = cur.fetchone()[0] or 0
                cur.execute(
                    f"SELECT COUNT(*) FROM shares WHERE giveaway_id=%s AND sharer_id != ALL(%s::bigint[])",
                    (giveaway_id, ADMIN_IDS)
                )
                shares = cur.fetchone()[0] or 0
                return base + shares

    def get_user_shares(self, giveaway_id: int, user_id: int) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM shares WHERE giveaway_id=%s AND sharer_id=%s",
                    (giveaway_id, user_id)
                )
                return cur.fetchone()[0] or 0

    def get_user_entries(self, user_id: int) -> list:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT p.*, g.title, g.is_active,
                       (SELECT COUNT(*) FROM shares s WHERE s.giveaway_id=p.giveaway_id AND s.sharer_id=p.user_id)::int as shares
                       FROM participants p JOIN giveaways g ON g.id=p.giveaway_id
                       WHERE p.user_id=%s ORDER BY p.id DESC""",
                    (user_id,)
                )
                rows = cur.fetchall()
                result = []
                for r in rows:
                    d = dict(r)
                    cur.execute(
                        f"SELECT COALESCE(SUM(base_tickets),0) FROM participants WHERE giveaway_id=%s AND user_id != ALL(%s::bigint[])",
                        (d["giveaway_id"], ADMIN_IDS)
                    )
                    total_base = cur.fetchone()[0] or 0
                    cur.execute(
                        f"SELECT COUNT(*) FROM shares WHERE giveaway_id=%s AND sharer_id != ALL(%s::bigint[])",
                        (d["giveaway_id"], ADMIN_IDS)
                    )
                    total_shares = cur.fetchone()[0] or 0
                    d["total_tickets"] = total_base + total_shares
                    d["tickets"] = d["base_tickets"] + d["shares"]
                    result.append(d)
                return result

    def get_participants_with_tickets(self, giveaway_id: int) -> list:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT user_id, base_tickets FROM participants WHERE giveaway_id=%s AND user_id != ALL(%s::bigint[])",
                    (giveaway_id, ADMIN_IDS)
                )
                participants = cur.fetchall()
                result = []
                for p in participants:
                    cur.execute(
                        "SELECT COUNT(*) FROM shares WHERE giveaway_id=%s AND sharer_id=%s",
                        (giveaway_id, p["user_id"])
                    )
                    shares = cur.fetchone()[0] or 0
                    result.append({"user_id": p["user_id"], "tickets": p["base_tickets"] + shares})
                return result

    # ── Shares ────────────────────────────────────────────────────────────────

    def credit_share(self, giveaway_id: int, sharer_id: int, referred_id: int) -> bool:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT is_active FROM giveaways WHERE id=%s", (giveaway_id,))
                g = cur.fetchone()
                if not g or not g["is_active"]:
                    return False
                try:
                    cur.execute(
                        "INSERT INTO shares (giveaway_id, sharer_id, referred_id) VALUES (%s, %s, %s) ON CONFLICT (giveaway_id, referred_id) DO NOTHING",
                        (giveaway_id, sharer_id, referred_id)
                    )
                    return True
                except Exception:
                    return False

    def get_shares_leaderboard(self, giveaway_id: int) -> list:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT u.user_id, u.username,
                       COUNT(s.id)::int as shares,
                       COALESCE(p.base_tickets, 0)::int as base_tickets,
                       COUNT(s.id)::int as share_tickets
                       FROM shares s
                       JOIN users u ON u.user_id = s.sharer_id
                       LEFT JOIN participants p ON p.giveaway_id=s.giveaway_id AND p.user_id=s.sharer_id
                       WHERE s.giveaway_id=%s
                       GROUP BY s.sharer_id, u.user_id, u.username, p.base_tickets
                       ORDER BY shares DESC LIMIT 50""",
                    (giveaway_id,)
                )
                return [dict(r) for r in cur.fetchall()]


db = Database()
