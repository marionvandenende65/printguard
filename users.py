"""
PrintGuard — User & Subscription Management

Limietlogica:
  Maandelijks → maandlimiet, reset op de dag van de maand waarop ze lid werden
  Jaarlijks   → jaarlimiet (12× maandlimiet), reset op de jaardag van lidmaatschap
  Studio      → altijd onbeperkt
"""

import datetime, calendar
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
    "starter": {
        "price_month":   19,
        "price_year":    190,
        "max_px":        4000,
        "monthly_limit": 50,
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
        "batch":         False,
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
}


def _today() -> datetime.date:
    return datetime.datetime.utcnow().date()


def _period_start(member_since: str, billing: str) -> datetime.date:
    """
    Berekent de start van de huidige periode op basis van lidmaatschapsdatum.

    Voorbeeld maandelijks, lid op 15 april:
      - Op 10 mei  → periode start = 15 april
      - Op 20 mei  → periode start = 15 mei
      - Op 15 mei  → periode start = 15 mei  (reset op de dag zelf)

    Voorbeeld jaarlijks, lid op 15 april 2026:
      - Op 10 april 2027 → periode start = 15 april 2026
      - Op 20 april 2027 → periode start = 15 april 2027
    """
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


def create_user(email: str, password: str, name: str, plan: str, billing: str) -> bool:
    """Maak een nieuw account aan. Geeft False terug als email al bestaat."""
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    member_since = _today().isoformat()
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO users
                   (email, password_hash, name, plan, billing, member_since, period_key)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (email.lower().strip(), pw_hash, name, plan, billing,
                 member_since, member_since),
            )
            conn.commit()
        return True
    except Exception:
        return False


def upgrade_user(email: str, plan: str, billing: str) -> bool:
    """Upgrade plan van een bestaande gebruiker (na betaling)."""
    with get_db() as conn:
        result = conn.execute(
            "UPDATE users SET plan=?, billing=? WHERE email=?",
            (plan, billing, email.lower().strip()),
        )
        conn.commit()
        return result.rowcount > 0


def _reset_if_new_period(user: dict) -> dict:
    """Reset teller als we in een nieuwe periode zijn — schrijft naar DB."""
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
        "registry":     plan["registry"],
        "max_px":       plan["max_px"],
        "name":         user.get("name") or email.split("@")[0],
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
