"""
ForexFactory -> Discord alerts   |   SHER-E-PANJAB TRADERZ

Schedule
  Sunday  22:00 ET   full week
  Mon-Thu 22:00 ET   tomorrow's high impact + day instruction
  ~30 min before each high impact release   one warning, once

Day-type rules from the ICT 2026 Mentorship master rules file
plus Iqbal's own FOMC session rule.
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

TZ = ZoneInfo("America/New_York")

CURRENCIES = {"USD"}
IMPACTS = {"High", "Medium"}

WARN_ONLY_HIGH = True        # False = warn on medium impact too
LEAD_MIN, LEAD_MAX = 25, 45  # the "30 minutes before" window
NIGHT_HOUR = 22              # 10pm ET

STATE_FILE = "state.json"

NFP_KEYS = ["Non-Farm", "Nonfarm", "NFP"]
FOMC_KEYS = ["FOMC Press Conference", "Federal Funds Rate", "FOMC Statement"]


# ----------------------------------------------------------------------
# PLUMBING
# ----------------------------------------------------------------------

def post(msg):
    if not WEBHOOK:
        print("NO WEBHOOK -- would post:\n" + msg)
        return
    for chunk in [msg[i:i + 1900] for i in range(0, len(msg), 1900)]:
        r = requests.post(WEBHOOK, json={"content": chunk}, timeout=20)
        if r.status_code >= 300:
            print(f"Discord {r.status_code}: {r.text}", file=sys.stderr)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"sent": []}


def save_state(s):
    s["sent"] = s["sent"][-300:]
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, indent=1)


def get_events():
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
        out.append({"title": e.get("title", "?"),
                    "when": when,
                    "impact": e.get("impact")})
    return sorted(out, key=lambda x: x["when"])


def dot(impact):
    return "🔴" if impact == "High" else "🟠"


def row(e):
    return f"`{e['when']:%H:%M}` {dot(e['impact'])} **{e['title']}**"


def has(events, keys, day=None):
    for e in events:
        if day and e["when"].date() != day:
            continue
        if any(k.lower() in e["title"].lower() for k in keys):
            return e
    return None


# ----------------------------------------------------------------------
# DAY CLASSIFICATION
# ----------------------------------------------------------------------

def day_note(events, day):
    """Instruction block for a given date. Order = precedence."""

    todays = [e for e in events if e["when"].date() == day]
    highs = [e for e in todays if e["impact"] == "High"]
    dow = day.weekday()

    if has(events, FOMC_KEYS, day):
        return ("FOMC day. Only overnight, before 09:30, "
                "and after 14:30 are tradeable.")

    if has(events, NFP_KEYS, day):
        return ("NFP at 08:30. The number is unreliable and gets revised — "
                "the volatility is what matters.\n"
                "Take a low-hanging-fruit objective, not an ambitious one.")

    if has(events, NFP_KEYS):                      # NFP somewhere this week
        if dow == 0:
            return ("NFP week. Monday is the reliable day — everyone takes "
                    "their piece early to avoid Thursday and Friday.\n"
                    "Be focused. Actively look for a setup.")
        if dow == 2:
            return ("NFP week. ICT's safe zone closes at 11:00 — past that "
                    "the week's objective is usually already hit.\n"
                    "You trade through it. Just know what you're in.")
        if dow == 3:
            return ("NFP week Thursday. Expect positioning into tomorrow's "
                    "08:30 rather than clean delivery.")
        return "NFP week. Tuesday is tradeable — normal session."

    ten = [e for e in highs if e["when"].hour == 10 and e["when"].minute == 0]
    if ten:
        return ("Sit out the first 30 minutes. Let liquidity or inefficiency "
                "build into the release, let it print, then trade what's "
                "left behind.\n10:30 — dealing range in place, algos fire.")

    if dow == 0 and not highs:
        return ("Not NFP week. Monday is a lottery — it can work, but don't "
                "demand a setup of yourself.")

    if highs:
        names = ", ".join(sorted({e["title"] for e in highs}))
        return (f"High impact — {names}.\n"
                "Large interference in normal price movement around it.")

    if todays:
        return "Medium impact only. Trade the full session."

    return "Nothing scheduled. Rely purely on price action — normal and fine."


# ----------------------------------------------------------------------
# ALERTS
# ----------------------------------------------------------------------

def warn_before(events, now, state):
    """One grouped warning ~30 min ahead. Same-time events share a message."""
    groups = {}
    for e in events:
        if WARN_ONLY_HIGH and e["impact"] != "High":
            continue
        mins = (e["when"] - now).total_seconds() / 60
        if LEAD_MIN <= mins <= LEAD_MAX:
            groups.setdefault(e["when"], []).append(e)

    for when, evs in groups.items():
        key = f"warn|{when.isoformat()}"
        if key in state["sent"]:
            continue
        mins = int((when - now).total_seconds() / 60)
        names = "\n".join(f"{dot(e['impact'])} **{e['title']}**" for e in evs)
        post(f"**{mins} MIN** — `{when:%H:%M} ET`\n{names}")
        state["sent"].append(key)


def night_before(events, now, state):
    """22:00 ET — tomorrow's high impact plus the day instruction."""
    if not (now.hour == NIGHT_HOUR and now.minute < 30):
        return
    if now.weekday() in (4, 5, 6):        # Fri/Sat handled below, Sun = week
        return

    tom = (now + dt.timedelta(days=1)).date()
    key = f"night|{tom}"
    if key in state["sent"]:
        return

    todays = [e for e in events if e["when"].date() == tom]
    highs = [e for e in todays if e["impact"] == "High"]

    msg = [f"**TOMORROW · {tom:%A %-d %b}**\n"]
    if highs:
        msg += [row(e) for e in highs]
    else:
        msg.append("_No high impact events_")
    msg.append("")
    msg.append(day_note(events, tom))

    post("\n".join(msg))
    state["sent"].append(key)


def week_ahead(events, now, state):
    """Sunday 22:00 ET — the whole week."""
    if now.weekday() != 6:
        return
    if not (now.hour == NIGHT_HOUR and now.minute < 30):
        return
    key = f"week|{now.date()}"
    if key in state["sent"]:
        return

    msg = [f"**WEEK AHEAD · {now:%-d %b}**\n"]

    if has(events, NFP_KEYS):
        msg.append("**NFP week.** Monday focused · Tuesday tradeable · "
                   "Wednesday safe zone to 11:00 · Thursday and Friday "
                   "distorted by the 08:30 print.\n")
    f = has(events, FOMC_KEYS)
    if f:
        msg.append(f"**FOMC {f['when']:%A}.** Overnight, before 09:30, "
                   "and after 14:30 only.\n")

    cur = None
    any_ev = False
    for e in events:
        any_ev = True
        if e["when"].date() != cur:
            cur = e["when"].date()
            msg.append(f"\n__{e['when']:%A %-d %b}__")
        msg.append(row(e))

    if not any_ev:
        msg.append("Calendar is empty. Price action only.")

    post("\n".join(msg))
    state["sent"].append(key)


# ----------------------------------------------------------------------

def main():
    now = dt.datetime.now(TZ)
    state = load_state()
    try:
        events = get_events()
    except Exception as exc:
        print(f"Feed failed: {exc}", file=sys.stderr)
        return
    week_ahead(events, now, state)
    night_before(events, now, state)
    warn_before(events, now, state)
    save_state(state)


if __name__ == "__main__":
    main()
