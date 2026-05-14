#!/usr/bin/env python3
"""
fetch_progress.py
==================
Pulls live Vote Paele canvass counts from Solidarity Tech and writes progress.json
next to this script. Designed to run on a cron every 30 min.

Phase goals (non-GOTV only, source: gotv_phases_2026-05-13.json):
  DOOR  (PAELE-SUPER-VTR):   universe 1812  | 2026-05-11 -> 2026-07-21
  PHONE (LAHUI-REGULAR-PPL): universe 8489  | 2026-05-11 -> 2026-07-21

Door counts: users in Vote Paele Voters (chapter 1790) whose
  custom_user_properties['last-canvass-date'] is set on/after the phase start.
Phone counts: rows in /calls with chapter_id=1514 and created_at >= phase start.
"""

import os, sys, json, time, requests
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding='utf-8')
load_dotenv(r"C:\Firefly's Path\.env")

ST_BASE = "https://api.solidarity.tech/v1"
ST_API_KEY = os.environ["ST_API_KEY"]
HEADERS = {"Authorization": f"Bearer {ST_API_KEY}"}

VOTE_PAELE_CHAPTER = 1514
VOTE_PAELE_VOTERS_CHAPTER = 1790

PHASES = [
    {
        "id": "ph_moywoyi4060e",
        "name": "PAELE-SUPER-VTR",
        "channel": "door",
        "universe": 1812,
        "passes": 1,
        "start": "2026-05-11",
        "end":   "2026-07-21",
    },
    {
        "id": "ph_moywqwn6w5uu",
        "name": "LĀHUI-REGULAR-PPL",
        "channel": "phone",
        "universe": 8489,
        "passes": 1,
        "start": "2026-05-11",
        "end":   "2026-07-21",
    },
]

ATTRITION = 0.08
HST = timezone(timedelta(hours=-10))


def goal_total(universe: int, passes: int) -> int:
    """Same formula the phase builder uses."""
    full = int(passes)
    frac = passes - full
    total = 0.0
    for i in range(full):
        total += universe * ((1 - ATTRITION) ** i)
    if frac > 0:
        total += universe * ((1 - ATTRITION) ** full) * frac
    return int(round(total))


def hst_date(iso_ts: str) -> date:
    return datetime.fromisoformat(iso_ts).astimezone(HST).date()


def fetch_calls_for_chapter(chapter_id: int, since_date: date):
    """Paginate /calls; return list of calls in chapter on/after since_date (HST)."""
    rows = []
    offset = 0
    limit = 100
    while True:
        params = {"_limit": limit, "_offset": offset, "chapter_id": chapter_id}
        r = requests.get(f"{ST_BASE}/calls", headers=HEADERS, params=params, timeout=30)
        r.raise_for_status()
        page = r.json().get("data", [])
        if not page:
            break
        rows.extend(page)
        if len(page) < limit:
            break
        offset += limit
        time.sleep(0.3)
        # Safety stop: chapter just launched 2026-05-11, shouldn't exceed a few thousand
        if offset > 50_000:
            break
    # client-side chapter+date filter (chapter_id param is honored on /calls)
    filtered = [c for c in rows
                if c.get("chapter_id") == chapter_id
                and c.get("created_at")
                and hst_date(c["created_at"]) >= since_date]
    return filtered


def fetch_paele_voters():
    """
    Pull the full Vote Paele Voters universe from local snapshot first, then
    overlay any updates from ST (only users whose updated_at is recent enough
    that their last-canvass-date might have changed).
    For now: trust the snapshot for the universe + pull fresh user records by id
    for any users we suspect were touched today.

    Simpler path: count by reading users.json and looking at last-canvass-date.
    The snapshot is refreshed by .claude/skills/solidarity-tech/tools/refresh_st_data.py.
    For a real-time door count we additionally hit /users?_since=<recent timestamp>
    and merge in updated records.
    """
    snapshot = Path(r"C:\Firefly's Path\st_data\users.json")
    if not snapshot.exists():
        return []
    d = json.loads(snapshot.read_text(encoding="utf-8"))
    data = d.get("data") if isinstance(d, dict) else d
    return [u for u in data if VOTE_PAELE_VOTERS_CHAPTER in (u.get("chapter_ids") or [])]


def fetch_recent_user_updates(since_ts: int):
    """Pull /users with _since to overlay fresh updates (cheap incremental)."""
    rows = []
    offset = 0
    limit = 100
    while True:
        params = {"_limit": limit, "_offset": offset, "_since": since_ts}
        r = requests.get(f"{ST_BASE}/users", headers=HEADERS, params=params, timeout=30)
        r.raise_for_status()
        page = r.json().get("data", [])
        if not page:
            break
        rows.extend(page)
        if len(page) < limit:
            break
        offset += limit
        time.sleep(0.3)
        if offset > 5_000:
            break
    return rows


def parse_canvass_date(lcd):
    if not lcd:
        return None
    if isinstance(lcd, list) and lcd:
        lcd = lcd[0]
    if isinstance(lcd, dict):
        lcd = lcd.get("value") or lcd.get("label")
    if not isinstance(lcd, str):
        return None
    try:
        return datetime.fromisoformat(lcd[:10]).date()
    except (ValueError, TypeError):
        return None


def door_dates(users, since_date: date):
    out = []
    for u in users:
        cup = u.get("custom_user_properties") or {}
        d = parse_canvass_date(cup.get("last-canvass-date"))
        if d and d >= since_date:
            out.append(d)
    return out


def call_dates(calls):
    out = []
    for c in calls:
        ts = c.get("created_at")
        if ts:
            out.append(hst_date(ts))
    return out


def count_in_range(dates, lo: date, hi: date) -> int:
    return sum(1 for d in dates if lo <= d <= hi)


def week_window(today: date):
    """Monday..Sunday week containing today (ISO week)."""
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def month_window(today: date):
    first = today.replace(day=1)
    if first.month == 12:
        nxt = first.replace(year=first.year + 1, month=1)
    else:
        nxt = first.replace(month=first.month + 1)
    last = nxt - timedelta(days=1)
    return first, last


def compute_breakdown(dates, total_goal: int, start: date, end: date):
    days = (end - start).days + 1
    weeks = days / 7.0
    months = days / 30.0
    today = datetime.now(HST).date()
    elapsed_days = max(0, (today - start).days + 1)

    weekly_goal = total_goal / weeks
    monthly_goal = total_goal / months
    daily_goal = total_goal / days

    total_count = len(dates)
    wk_lo, wk_hi = week_window(today)
    mo_lo, mo_hi = month_window(today)
    # clamp to phase window so we don't credit pre-phase activity
    wk_lo = max(wk_lo, start)
    mo_lo = max(mo_lo, start)
    week_count  = count_in_range(dates, wk_lo, wk_hi)
    month_count = count_in_range(dates, mo_lo, mo_hi)

    return {
        "count": total_count,
        "count_week": week_count,
        "count_month": month_count,
        "total_goal": total_goal,
        "weekly_goal": int(round(weekly_goal)),
        "monthly_goal": int(round(monthly_goal)),
        "daily_goal": round(daily_goal, 1),
        "days_total": days,
        "days_elapsed": elapsed_days,
        "week_window": [wk_lo.isoformat(), wk_hi.isoformat()],
        "month_window": [mo_lo.isoformat(), mo_hi.isoformat()],
        "pct_total": round(100 * total_count / total_goal, 1) if total_goal else 0,
        "expected_to_date": int(round(daily_goal * elapsed_days)),
        "on_pace_pct": round(100 * total_count / (daily_goal * elapsed_days), 1)
                       if elapsed_days > 0 and daily_goal > 0 else 0,
    }


def main():
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "campaign": "Vote Paele",
        "phases": [],
    }

    # Pull /users updates since 7 days before the earliest phase start
    earliest_start = min(datetime.fromisoformat(p["start"]).date() for p in PHASES)
    since_ts = int((datetime.combine(earliest_start, datetime.min.time(), tzinfo=HST)
                    - timedelta(days=7)).timestamp())

    base_voters = fetch_paele_voters()
    voters_by_id = {u["id"]: u for u in base_voters}
    print(f"snapshot voters in chapter {VOTE_PAELE_VOTERS_CHAPTER}: {len(base_voters)}")

    try:
        recent = fetch_recent_user_updates(since_ts)
        for u in recent:
            if VOTE_PAELE_VOTERS_CHAPTER in (u.get("chapter_ids") or []):
                voters_by_id[u["id"]] = u
        print(f"merged recent updates: +{len(recent)} fetched")
    except requests.HTTPError as e:
        print(f"warn: recent-user pull failed ({e}); using snapshot only")

    voters = list(voters_by_id.values())

    for ph in PHASES:
        start_d = datetime.fromisoformat(ph["start"]).date()
        end_d   = datetime.fromisoformat(ph["end"]).date()
        total_goal = goal_total(ph["universe"], ph["passes"])

        if ph["channel"] == "door":
            dates = door_dates(voters, start_d)
        elif ph["channel"] == "phone":
            calls = fetch_calls_for_chapter(VOTE_PAELE_CHAPTER, start_d)
            dates = call_dates(calls)
        else:
            dates = []

        breakdown = compute_breakdown(dates, total_goal, start_d, end_d)
        out["phases"].append({
            **ph,
            **breakdown,
        })
        print(f"{ph['channel'].upper():5}  {ph['name']:25}  total={breakdown['count']}  "
              f"week={breakdown['count_week']}  month={breakdown['count_month']}  "
              f"goal={total_goal}  pct={breakdown['pct_total']}%")

    target = ROOT / "progress.json"
    target.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {target}")


if __name__ == "__main__":
    main()
