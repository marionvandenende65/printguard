"""
PrintGuard — User & Subscription Management

Limietlogica:
  Maandelijks → maandlimiet, reset op de dag van de maand waarop ze lid werden
  Jaarlijks   → jaarlimiet (12× maandlimiet), reset op de jaardag van lidmaatschap
  Studio      → altijd onbeperkt
"""

import datetime, calendar, secrets, string
from typing import Optional
import bcrypt
from database import get_db

PLANS = {
    "demo": {
        "price_month":   0,
        "price_year":    0,
        "max_px":        4000,
        "monthly_limit": 5,
        "certificate":   False,
        "batch":         False,
        "registry":      False,
    },
    "basic": {
        "price_month":   9,
        "price_year":    90,
        "max_px":        4000,
        "monthly_limit": 5,
        "certificate":   False,
        "batch":         False,
        "registry":      False,
    },
    "starter": {
        "price_month":   19,
        "price_year":    190,
        "max_px":        4000,
        "monthly_limit": 25,
        "certificate":   False,
        "batch":         False,
        "registry":      False,
    },
    "professional": {
        "price_month":   49,
        "price_year":    490,
        "max_px":        12000,
        "monthly_limit": 100,
        "certificate":   True,
        "batch":         True,
        "registry":      False,
    },
    "studio": {
        "price_month":   99,
        "price_year":    990,
        "max_px":        20000,
        "monthly_limit": None,
        "certificate":   True,
        "batch":         True,
        "registry":      True,
    },
    "design_professional": {
        "price_month":   29,
        "price_year":    290,
        "max_px":        12000,
        "monthly_limit": 100,
        "certificate":   True,
        "batch":         True,
        "registry":      False,
    },
    "design_studio": {
        "price_month":   59,
        "price_year":    590,
        "max_px":        20000,
        "monthly_limit": None,
        "certificate":   True,
        "batch":         True,
        "registry":      True,
    },
}


def _today() -> datetime.date:
    return datetime.datetime.utcnow().date()


def _period_start(member_since: str, billing: str) -> datetime.date:
    since = datetime.date.fromisoformat(member_since)
    today = _today()
    day   = since.day

    if billing == "yearly":
        try:
            period_this_year = since.replace(year=today.year)
        except ValueError:
            period_this_year = since.replace(year=today.year, day=28)

        if today >= period_this_year:
            return period_this_year
        else:
            try:
                return since.replace(year=today.year - 1)
            except ValueError:
                return since.replace(year=today.year - 1, day=28)

    else:  # maandelijks
        last_day_this_month = calendar.monthrange(today.year, today.month)[1]
        actual_day = min(day, last_day_this_month)
        period_this_month = today.replace(day=actual_day)

        if today >= period_this_month:
            return period_this_month
        else:
            if today.month == 1:
                prev_year, prev_month = today.year - 1, 12
            else:
                prev_year, prev_month = today.year, today.month - 1
            last_day_prev = calendar.monthrange(prev_year, prev_month)[1]
            return datetime.date(prev_year, prev_month, min(day, last_day_prev))


def _period_key(member_since: str, billing: str) -> str:
    return _period_start(member_since, billing).isoformat()


def _get_limit(plan: dict, billing: str) -> Optional[int]:
    base = plan.get("monthly_limit")
    if base is None:
        return None
    return base * 12 if billing == "yearly" else base


# ── Kernfuncties ──────────────────────────────────────────────────────────────

def get_user(email: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
        ).fetchone()
        return dict(row) if row else None


def check_password(email: str, password: str) -> bool:
    user = get_user(email)
    if not user:
        return False
    return bcrypt.checkpw(password.encode(), user["password_hash"].encode())


def create_user(email: str, password: str, name: str, plan: str, billing: str,
                referred_by: str = "") -> bool:
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    member_since = _today().isoformat()
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO users
                   (email, password_hash, name, plan, billing, member_since, period_key, referred_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (email.lower().strip(), pw_hash, name, plan, billing,
                 member_since, member_since, referred_by or ""),
            )
            conn.commit()
        if referred_by:
            _increment_referral(referred_by)
        return True
    except Exception:
        return False


def upgrade_user(email: str, plan: str, billing: str) -> bool:
    with get_db() as conn:
        result = conn.execute(
            "UPDATE users SET plan=?, billing=?, cancelled=0 WHERE email=?",
            (plan, billing, email.lower().strip()),
        )
        conn.commit()
        return result.rowcount > 0


def _reset_if_new_period(user: dict) -> dict:
    current_key = _period_key(user["member_since"], user.get("billing", "monthly"))
    if user.get("period_key") != current_key:
        user["uploads_this_period"] = 0
        user["period_key"] = current_key
        with get_db() as conn:
            conn.execute(
                "UPDATE users SET uploads_this_period=0, period_key=? WHERE email=?",
                (current_key, user["email"]),
            )
            conn.commit()
    return user


def get_usage(email: str) -> dict:
    user = get_user(email)
    if not user:
        return {"error": "Gebruiker niet gevonden"}

    _reset_if_new_period(user)
    plan    = PLANS.get(user["plan"], PLANS["starter"])
    billing = user.get("billing", "monthly")
    limit   = _get_limit(plan, billing)

    unlimited = limit is None
    used      = user["uploads_this_period"]
    remaining = None if unlimited else max(0, limit - used)
    pct       = 0.0 if unlimited else (min(100.0, used / limit * 100) if limit else 0.0)

    since        = user["member_since"]
    period_start = _period_start(since, billing)

    if billing == "yearly":
        try:
            next_reset = period_start.replace(year=period_start.year + 1)
        except ValueError:
            next_reset = period_start.replace(year=period_start.year + 1, day=28)
    else:
        if period_start.month == 12:
            next_reset = period_start.replace(year=period_start.year + 1, month=1)
        else:
            last_day = calendar.monthrange(period_start.year, period_start.month + 1)[1]
            next_reset = period_start.replace(
                month=period_start.month + 1,
                day=min(period_start.day, last_day)
            )

    return {
        "used":         used,
        "limit":        limit,
        "remaining":    remaining,
        "pct":          round(pct, 1),
        "unlimited":    unlimited,
        "plan":         user["plan"],
        "billing":      billing,
        "member_since": since,
        "next_reset":   next_reset.isoformat(),
        "certificate":  plan["certificate"],
        "batch":        plan["batch"],
        "registry":     plan["registry"],
        "max_px":       plan["max_px"],
        "name":         user.get("name") or email.split("@")[0],
        "cancelled":    bool(user.get("cancelled", 0)),
        "referral_code": user.get("referral_code") or "",
    }


def can_upload(email: str) -> tuple:
    usage = get_usage(email)
    if "error" in usage:
        return False, "account_not_found"
    if not usage["unlimited"] and usage["remaining"] == 0:
        return False, "limit_reached"
    return True, ""


def record_upload(email: str) -> bool:
    user = get_user(email)
    if not user:
        return False
    _reset_if_new_period(user)
    allowed, _ = can_upload(email)
    if not allowed:
        return False
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET uploads_this_period = uploads_this_period + 1 WHERE email = ?",
            (email.lower().strip(),),
        )
        conn.commit()
    return True


def has_feature(email: str, feature: str) -> bool:
    user = get_user(email)
    if not user:
        return False
    plan = PLANS.get(user.get("plan", ""), {})
    return bool(plan.get(feature, False))


# ── Wachtwoord reset ──────────────────────────────────────────────────────────

def create_reset_token(email: str) -> Optional[str]:
    user = get_user(email)
    if not user:
        return None
    token = secrets.token_urlsafe(32)
    expires = (datetime.datetime.utcnow() + datetime.timedelta(hours=2)).isoformat()
    with get_db() as conn:
        # Verwijder oude tokens voor dit e-mailadres
        conn.execute("DELETE FROM reset_tokens WHERE email = ?", (email.lower().strip(),))
        conn.execute(
            "INSERT INTO reset_tokens (token, email, expires_at) VALUES (?, ?, ?)",
            (token, email.lower().strip(), expires),
        )
        conn.commit()
    return token


def consume_reset_token(token: str, new_password: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM reset_tokens WHERE token = ? AND used = 0",
            (token,)
        ).fetchone()
        if not row:
            return False
        expires = datetime.datetime.fromisoformat(row["expires_at"])
        if datetime.datetime.utcnow() > expires:
            return False
        pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE email = ?",
            (pw_hash, row["email"]),
        )
        conn.execute(
            "UPDATE reset_tokens SET used = 1 WHERE token = ?", (token,)
        )
        conn.commit()
    return True


# ── Opzegging ─────────────────────────────────────────────────────────────────

def cancel_subscription(email: str) -> bool:
    with get_db() as conn:
        result = conn.execute(
            "UPDATE users SET cancelled = 1 WHERE email = ?",
            (email.lower().strip(),),
        )
        conn.commit()
        return result.rowcount > 0


# ── Referral codes ────────────────────────────────────────────────────────────

def _gen_code() -> str:
    chars = string.ascii_uppercase + string.digits
    return "PG-" + "".join(secrets.choice(chars) for _ in range(8))


def get_referral_code(email: str) -> str:
    user = get_user(email)
    if not user:
        return ""
    if user.get("referral_code"):
        return user["referral_code"]
    # Genereer een nieuwe code
    for _ in range(10):
        code = _gen_code()
        with get_db() as conn:
            try:
                conn.execute(
                    "INSERT INTO referrals (code, owner) VALUES (?, ?)",
                    (code, email.lower().strip()),
                )
                conn.execute(
                    "UPDATE users SET referral_code = ? WHERE email = ?",
                    (code, email.lower().strip()),
                )
                conn.commit()
                return code
            except Exception:
                continue
    return ""


def validate_referral_code(code: str) -> Optional[str]:
    """Geeft de owner-email terug als de code geldig is, anders None."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT owner FROM referrals WHERE code = ?", (code.upper().strip(),)
        ).fetchone()
        return row["owner"] if row else None


def _increment_referral(code: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE referrals SET uses = uses + 1 WHERE code = ?", (code,)
        )
        conn.commit()


def get_referral_stats(email: str) -> dict:
    code = get_referral_code(email)
    if not code:
        return {"code": "", "uses": 0}
    with get_db() as conn:
        row = conn.execute(
            "SELECT uses FROM referrals WHERE code = ?", (code,)
        ).fetchone()
    return {"code": code, "uses": row["uses"] if row else 0}


# ── Certificaat archief ───────────────────────────────────────────────────────

def store_certificate(email: str, cert_id: str, title: str,
                      file_hash: str, zip_bytes: bytes) -> bool:
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO certificates
                   (email, cert_id, title, file_hash, zip_blob)
                   VALUES (?, ?, ?, ?, ?)""",
                (email.lower().strip(), cert_id, title, file_hash, zip_bytes),
            )
            conn.commit()
        return True
    except Exception as e:
        print(f"[cert-archive] Fout bij opslaan {cert_id}: {e}")
        return False


def list_certificates(email: str) -> list:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT cert_id, title, file_hash, created_at
               FROM certificates WHERE email = ?
               ORDER BY created_at DESC""",
            (email.lower().strip(),)
        ).fetchall()
    return [dict(r) for r in rows]


def get_certificate_zip(email: str, cert_id: str) -> Optional[bytes]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT zip_blob FROM certificates WHERE email = ? AND cert_id = ?",
            (email.lower().strip(), cert_id),
        ).fetchone()
    return bytes(row["zip_blob"]) if row else None


# ── Demo rate-limiting ────────────────────────────────────────────────────────

def can_demo(ip: str) -> bool:
    """Max 1 demo per IP per 24 uur."""
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(hours=24)).isoformat()
    with get_db() as conn:
        row = conn.execute(
            "SELECT last_used FROM demo_usage WHERE ip = ?", (ip,)
        ).fetchone()
        if not row or row["last_used"] < cutoff:
            return True
    return False


def record_demo(ip: str):
    now = datetime.datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO demo_usage (ip, last_used) VALUES (?, ?)",
            (ip, now),
        )
        conn.commit()
