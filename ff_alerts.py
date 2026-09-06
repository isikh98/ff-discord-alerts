"""
ForexFactory -> Discord alerts for SHER-E-PANJAB TRADERZ

Posts:
  1. T-10 warning before every red/orange USD event
  2. NY AM pre-open brief at 09:15 ET
  3. Monday week-ahead with difficulty warning
  4. FOMC press conference day playbook

Runs on GitHub Actions every 5 minutes. No server needed.
"""

import json
import os
import sys
import datetime as dt
from zoneinfo import ZoneInfo

import requests

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()

TZ = ZoneInfo("America/New_York")   # change if you are not on Eastern

CURRENCIES = {"USD"}               # indices trader -> USD only
IMPACTS = {"High", "Medium"}       # red + orange

LEAD_MIN, LEAD_MAX = 8, 100000         # T-10 window (wide, GH cron drifts)
PREOPEN_HOUR, PREOPEN_MIN = 9, 15  # NY AM pre-open ping
WEEKAHEAD_HOUR = 7                 # Monday week-ahead

STATE_FILE = "state.json"

# Events that make a week hard to trade
HARD_WEEK = ["Non-Farm", "Nonfarm", "NFP", "FOMC", "CPI", "Federal Funds"]

# Events that trigger the FOMC day playbook
FOMC_KEYS = ["FOMC Press Conference", "Federal Funds Rate", "FOMC Statement"]


# ----------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------

def post(msg):
    """Send a message to Discord. Splits if over the 2000 char limit."""
    if not WEBHOOK:
        print("NO WEBHOOK SET -- would have posted:\n" + msg)
        return
    for chunk in [msg[i:i + 1900] for i in range(0, len(msg), 1900)]:
        r = requests.post(WEBHOOK, json={"content": chunk}, timeout=20)
        if r.status_code >= 300:
            print(f"Discord error {r.status_code}: {r.text}", file=sys.stderr)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"sent": []}


def save_state(state):
    # keep the file small - only this week matters
    state["sent"] = state["sent"][-300:]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=1)


def already(state, key):
    return key in state["sent"]


def mark(state, key):
    state["sent"].append(key)


def get_events():
    """Pull the weekly feed and return parsed, filtered events in ET."""
    r = requests.get(FEED_URL, timeout=30,
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    out = []
    for e in r.json():
        if e.get("country") not in CURRENCIES:
            continue
        if e.get("impact") not in IMPACTS:
            continue
        try:
            when = dt.datetime.fromisoformat(e["date"]).astimezone(TZ)
        except Exception:
            continue
        out.append({
            "title": e.get("title", "?"),
            "when": when,
            "impact": e.get("impact"),
            "forecast": e.get("forecast") or "-",
            "previous": e.get("previous") or "-",
        })
    return sorted(out, key=lambda x: x["when"])


def dot(impact):
    return "🔴" if impact == "High" else "🟠"


def line(e):
    return (f"{dot(e['impact'])} `{e['when']:%H:%M}` **{e['title']}** "
            f"(f: {e['forecast']} / p: {e['previous']})")


def is_fomc_day(events, today):
    return any(
        e["when"].date() == today and
        any(k.lower() in e["title"].lower() for k in FOMC_KEYS)
        for e in events
    )


# ----------------------------------------------------------------------
# ALERTS
# ----------------------------------------------------------------------

def t_minus_10(events, now, state):
    """Warn ~10 min before each red/orange event."""
    for e in events:
        mins = (e["when"] - now).total_seconds() / 60
        if not (LEAD_MIN <= mins <= LEAD_MAX):
            continue
        key = f"t10|{e['when'].isoformat()}|{e['title']}"
        if already(state, key):
            continue
        post(
            f"{dot(e['impact'])} **{int(mins)} MIN WARNING**\n"
            f"**{e['title']}** at `{e['when']:%H:%M} ET`\n"
            f"Forecast {e['forecast']} · Previous {e['previous']}\n\n"
            f"🚫 No entries from `{e['when'] - dt.timedelta(minutes=15):%H:%M}` "
            f"to `{e['when'] + dt.timedelta(minutes=15):%H:%M}` ET.\n"
            f"If you are in a position: no adds, stop stays where it is."
        )
        mark(state, key)


def pre_open(events, now, state):
    """09:15 ET — what is left today before the 09:30 open."""
    key = f"preopen|{now.date()}"
    if already(state, key):
        return
    if not (now.hour == PREOPEN_HOUR and PREOPEN_MIN <= now.minute < PREOPEN_MIN + 20):
        return

    today = now.date()
    todays = [e for e in events if e["when"].date() == today]
    later = [e for e in todays if e["when"] >= now]

    msg = [f"⏰ **NY AM OPEN IN 15 MIN** — {now:%A %d %b}\n"]

    if is_fomc_day(events, today):
        msg.append(fomc_playbook(todays))
    elif any(e["impact"] == "High" and e["when"].hour < 12 for e in todays):
        msg.append("🔴 **SKIP DAY** — high impact release this morning.")
        msg.append("Your rule: no NY AM session. Do not look for a reason around it.\n")
    elif later:
        msg.append("🟡 **NORMAL DAY — closed windows below**\n")
    else:
        msg.append("🟢 **CLEAN** — nothing red or orange left today.\n")

    if later:
        msg.append("**Still to come:**")
        msg += [line(e) for e in later]
        msg.append("")
        msg.append("**No-entry windows (±15 min):**")
        msg += [f"`{e['when'] - dt.timedelta(minutes=15):%H:%M}`–"
                f"`{e['when'] + dt.timedelta(minutes=15):%H:%M}` {e['title']}"
                for e in later]

    msg.append(
        "\n**The gate — all must be true:**\n"
        "SSMT present · HTF orderflow aligned · correct side of True Open · "
        "PSP confirmation · stop in the platform, not in your head."
    )

    post("\n".join(msg))
    mark(state, key)


def fomc_playbook(todays):
    """The special FOMC press conference day rules."""
    return (
        "🔴🔴 **FOMC PRESS CONFERENCE DAY**\n\n"
        "**NO NY AM SESSION.** 09:30–14:00 is chop and consolidation. "
        "The market is waiting, not trending.\n\n"
        "**Your plan today:**\n"
        "• Pre-market only — trade before `08:30 ET`, be flat and done by `08:30`\n"
        "• `14:00` Federal Funds Rate — this is **manipulation**, not direction. "
        "Let it sweep SSL/BSL. Do not trade into it.\n"
        "• `14:30` press conference — real move usually begins here, "
        "once liquidity has been taken\n"
        "• **Trade after 14:30**, not before\n\n"
        "If you find yourself clicking at 10:15 today, that is tilt, not a setup.\n"
    )


def week_ahead(events, now, state):
    """Monday 07:00 ET — the whole week, plus a difficulty read."""
    key = f"week|{now.date()}"
    if already(state, key):
        return
    if now.weekday() != 0:
        return
    if not (now.hour == WEEKAHEAD_HOUR and now.minute < 20):
        return

    high = [e for e in events if e["impact"] == "High"]
    hard = [e for e in high
            if any(k.lower() in e["title"].lower() for k in HARD_WEEK)]

    msg = [f"📅 **WEEK AHEAD** — week of {now:%d %b}\n"]

    if hard:
        names = ", ".join(sorted({e["title"] for e in hard}))
        msg.append(
            f"⚠️ **HARD WEEK.** This week has: **{names}**\n\n"
            "Expect compression and false moves on the days *before* the print. "
            "Ranges tighten, liquidity sits untouched, setups look valid and fail. "
            "Size down or sit out the lead-up. The real range usually comes "
            "after the event, not before it.\n"
        )
    else:
        msg.append(
            "🟢 **No NFP / FOMC / CPI this week.**\n\n"
            "Expect a shorter range and less follow-through. No big runs. "
            "Take what the week gives you — do not force size looking for a "
            "move that is not scheduled.\n"
        )

    if high:
        msg.append("**Red folder this week:**")
        cur = None
        for e in high:
            if e["when"].date() != cur:
                cur = e["when"].date()
                msg.append(f"\n__{e['when']:%A %d %b}__")
            msg.append(line(e))
    else:
        msg.append("No red folder events on the calendar.")

    fomc = [e["when"] for e in high
            if any(k.lower() in e["title"].lower() for k in FOMC_KEYS)]
    if fomc:
        msg.append(
            f"\n🔴 **FOMC this week — {min(fomc):%A}.** "
            "No NY AM that day. Pre-market before 08:30 only, "
            "then wait for 14:30."
        )

    post("\n".join(msg))
    mark(state, key)


# ----------------------------------------------------------------------

def main():
    now = dt.datetime.now(TZ)
    state = load_state()
    post(f"✅ Webhook test — it works. Local time is {now:%A %H:%M}")

    try:
        events = get_events()
    except Exception as exc:
        print(f"Feed failed: {exc}", file=sys.stderr)
        return

    week_ahead(events, now, state)
    pre_open(events, now, state)
    t_minus_10(events, now, state)

    save_state(state)


if __name__ == "__main__":
    main()
