"""
ForexFactory -> Discord alerts  v4  |  SHER-E-PANJAB TRADERZ

Started by cron-job.org every 5 minutes (minutes 4, 9, 14 ... 59),
Sunday to Friday, 6 AM to 7:59 PM Toronto time.

Alerts (Toronto / New York time)
  Sunday 7:00 PM     week ahead + Monday plan
  Mon-Thu 7:00 PM    tomorrow's news + plan
  Mon-Fri 8:45 AM    morning brief + FREEDOM model + no-trade times
  15 min before      every red folder event
Every alert starts 1 minute early to cover GitHub's start-up delay.
"""

import json
import os
import sys
import datetime as dt
from zoneinfo import ZoneInfo

import requests

# ----------------------------------------------------------------------
# SETTINGS
# ----------------------------------------------------------------------

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()
PREVIEW = os.environ.get("PREVIEW", "false").strip().lower() == "true"

TZ = ZoneInfo("America/New_York")      # same clock as Toronto
STATE_FILE = "state.json"

# Firms that ban ALL red folder news (checked on official sites, Oct 2026)
NO_NEWS_FIRMS = ["Lucid", "Top One", "Goat", "AquaFunded"]

NO_TRADE_MIN = 5            # no trades 5 min before and 5 min after
WARN_FROM, WARN_TO = 5, 16.5   # warning goes out 16 min before (1 min early)
NIGHT_START = (18, 59)      # 6:59 PM
BRIEF_START = (8, 44)       # 8:44 AM
RETRY_MIN = 35              # daily alerts keep trying this long if a run is missed

NFP_KEYS = ["non-farm", "nonfarm"]
FOMC_KEYS = ["federal funds rate", "fomc statement", "fomc press conference"]
CPI_KEYS = ["cpi"]
PPI_KEYS = ["ppi"]
SPEECH_KEYS = ["president", "fed chair"]   # always shown, as orange

MODEL = [
    "1. Premium or discount: long from discount, short from premium",
    "2. Liquidity taken: PDH/PDL, session high/low",
    "3. SMT with the partner index at the raid",
    "4. Your opens agree: short above, long below",
    "5. Setup candle inside a killzone (10 AM–12 PM, 2–4 PM) or a macro",
    "6. Entry on 1m: FREEDOM iFVG, or 2022 model MSS that leaves an FVG",
    "7. Stop at the high or low of the setup",
    "8. Target the other side, REQs, BSL/SSL",
    "No setup, no trade.",
]


# ----------------------------------------------------------------------
# PLUMBING
# ----------------------------------------------------------------------

def post(msg):
    if not WEBHOOK:
        print("NO WEBHOOK -- would post:\n" + msg + "\n")
        return
    for chunk in [msg[i:i + 1900] for i in range(0, len(msg), 1900)]:
        r = requests.post(WEBHOOK, json={"content": chunk}, timeout=20)
        if r.status_code >= 300:
            print(f"Discord {r.status_code}: {r.text}", file=sys.stderr)


def load_state():
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
            s.setdefault("sent", [])
            return s
    except Exception:
        return {"sent": []}


def save_state(s):
    s["sent"] = s["sent"][-400:]
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, indent=1)


def get_events():
    """Return (USD red + speech events, latest date seen in the feed)."""
    r = requests.get(FEED_URL, timeout=30,
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    out, latest = [], None
    for e in r.json():
        try:
            when = dt.datetime.fromisoformat(e["date"]).astimezone(TZ)
        except Exception:
            continue
        if latest is None or when.date() > latest:
            latest = when.date()
        if e.get("country") != "USD":
            continue
        title = (e.get("title") or "").strip()
        speech = any(k in title.lower() for k in SPEECH_KEYS)
        if e.get("impact") == "High":
            is_red = True
        elif speech:
            is_red = False
        else:
            continue
        out.append({"title": title, "when": when, "red": is_red})
    return sorted(out, key=lambda x: x["when"]), latest


# ----------------------------------------------------------------------
# SMALL HELPERS
# ----------------------------------------------------------------------

def tm(t):
    return t.strftime("%-I:%M %p")


def day_name(d):
    return d.strftime("%a %-d %b")


def has(evs, keys):
    return [e for e in evs if any(k in e["title"].lower() for k in keys)]


def on_day(evs, d):
    return [e for e in evs if e["when"].date() == d]


def reds(evs):
    return [e for e in evs if e["red"]]


def line(e):
    dot = "🔴" if e["red"] else "🟠"
    return f"{dot} `{tm(e['when'])}`  {e['title']}"


def group(evs):
    """Events with the same time go together."""
    g = {}
    for e in evs:
        g.setdefault(e["when"], []).append(e)
    return sorted(g.items())


def names(g):
    return ", ".join(e["title"] for e in g)


def no_trade_window(t):
    a = t - dt.timedelta(minutes=NO_TRADE_MIN)
    b = t + dt.timedelta(minutes=NO_TRADE_MIN)
    return f"{tm(a)} – {tm(b)}"


def week_tags(evs):
    tags = []
    if has(evs, NFP_KEYS):
        tags.append("NFP WEEK")
    if has(reds(evs), CPI_KEYS):
        tags.append("CPI WEEK")
    if has(evs, FOMC_KEYS):
        tags.append("FOMC WEEK")
    return " · ".join(tags) if tags else "NORMAL WEEK"


def in_window(now, hm):
    start = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    start -= dt.timedelta(seconds=30)          # cron-job.org may start a few seconds early
    return start <= now < start + dt.timedelta(minutes=RETRY_MIN)


def firm_lines():
    return ["Firms that don't allow:", " · ".join(NO_NEWS_FIRMS)]


# ----------------------------------------------------------------------
# DAY PLAN  (your rules)
# ----------------------------------------------------------------------

def plan(evs, d):
    todays = on_day(evs, d)
    red_today = reds(todays)
    lines = []

    # 1. FOMC day outranks everything
    if has(todays, FOMC_KEYS):
        return ["FOMC day. Trade only overnight, before 9:30 AM, or after 2:30 PM."]

    ten = [e for e in red_today if e["when"].hour == 10 and e["when"].minute == 0]
    big = has(red_today, CPI_KEYS + PPI_KEYS)

    # 2. NFP day
    if has(red_today, NFP_KEYS):
        lines += ["NFP at 8:30 AM. The number does not matter, the volatility does.",
                  "Take a small, close target. No big swings."]

    # 3. CPI or PPI day (ICT way, option B)
    elif big:
        name = "CPI" if has(red_today, CPI_KEYS) else "PPI"
        t = big[0]["when"]
        lines += [f"Let {tm(t)} {name} print. Sit out the noise.",
                  f"Mark the high and low from before {tm(t)}.",
                  "Trade after 9:30 AM only after a stop hunt + displacement."]

    # 4. everything else
    else:
        if not red_today:
            lines.append("No red folder news.")
        nfp_week = bool(has(evs, NFP_KEYS))
        dow = d.weekday()
        if nfp_week and dow == 0:
            lines.append("NFP week. Monday is the reliable day. Be focused and look for a setup.")
        elif nfp_week and dow == 1:
            lines.append("NFP week. Tuesday is a normal trading day.")
        elif nfp_week and dow == 2:
            lines.append("NFP week. Best trading is before 11:00 AM. After that the week's move is often done.")
        elif nfp_week and dow == 3:
            lines.append("NFP week Thursday. Price often gets ready for Friday's 8:30 AM number. Moves can be less clean.")
        elif dow == 0:
            lines.append("Not NFP week, so Monday is a lottery. Trade only if a full setup forms.")
        other = [e for e in red_today if e not in ten]
        if other:
            times = ", ".join(tm(t) for t, _ in group(other))
            lines.append(f"Red folder news at {times}. Expect fast moves. Wait for clean structure after.")
        if lines == ["No red folder news."]:
            lines.append("Trade the plan. Full setups only.")

    # 10:00 AM release (ICT): let it print, trade after 10:30 AM
    if ten:
        lines.append(f"10:00 AM {names(ten)}: let it print, trade after 10:30 AM.")
    return lines


def bullets(lines):
    return [f"• {l}" for l in lines]


# ----------------------------------------------------------------------
# MESSAGES
# ----------------------------------------------------------------------

def msg_week(evs, mon):
    out = [f"📅 **WEEK AHEAD · {mon:%-d %b}**", week_tags(evs), ""]
    for i in range(5):
        d = mon + dt.timedelta(days=i)
        out.append(f"**{day_name(d)}**")
        de = on_day(evs, d)
        out += [line(e) for e in de] if de else ["No red folder news"]
        out.append("")
    fomc = has(evs, FOMC_KEYS)
    nfp = has(evs, NFP_KEYS)
    if fomc:
        out.append(f"FOMC {fomc[0]['when']:%A}: trade only overnight, "
                   f"before 9:30 AM, or after 2:30 PM.")
    if nfp:
        out.append(f"NFP {nfp[0]['when']:%A}: Monday is the best day. "
                   f"Thursday and Friday can be messy.")
    if fomc or nfp:
        out.append("")
    out.append("**MONDAY PLAN**")
    out += bullets(plan(evs, mon))
    return "\n".join(out)


def msg_night(evs, target):
    de = on_day(evs, target)
    out = [f"🌙 **TOMORROW · {day_name(target)}**", week_tags(evs), ""]
    out += [line(e) for e in de] if de else ["No red folder news"]
    out += ["", "**PLAN**"] + bullets(plan(evs, target))
    return "\n".join(out)


def msg_brief(evs, now):
    today = now.date()
    todays = on_day(evs, today)
    red_today = reds(todays)
    done = [e for e in red_today if e["when"] <= now]
    ahead = [e for e in red_today if e["when"] > now]
    speeches = [e for e in todays if not e["red"] and e["when"] > now]

    out = [f"☀️ **MORNING BRIEF · {day_name(today)}**", week_tags(evs), ""]
    if not red_today:
        out.append("News today: No red folder news.")
    else:
        parts = ["News today: Yes."]
        for t, g in group(done):
            parts.append(f"{tm(t)} {names(g)} {'is' if len(g) == 1 else 'are'} out.")
        for t, g in group(ahead):
            parts.append(f"{names(g)} at {tm(t)}.")
        out.append(" ".join(parts))
    for e in speeches:
        out.append(f"🟠 Also: {e['title']} at {tm(e['when'])}.")

    out += ["", "**PLAN**"] + bullets(plan(evs, today) + ["No FOMO."])
    out += ["", "**MODEL · FREEDOM**"] + MODEL

    if ahead:
        out += ["", "🔴🚫 **No trades allowed**"]
        for t, g in group(ahead):
            out.append(f"{tm(t)} {names(g)} ({no_trade_window(t)})")
        out += firm_lines()
    return "\n".join(out)


def msg_warning(evs, t, g, mins):
    label = max(1, int(mins))
    out = [f"⚠️ **{label} MIN** · {week_tags(evs)}"]
    out += [line(e) for e in g]
    out += ["", "🔴🚫 **No trades allowed**", f"({no_trade_window(t)})"]
    out += firm_lines()
    return "\n".join(out)


# ----------------------------------------------------------------------
# WHEN TO SEND
# ----------------------------------------------------------------------

def do_warnings(evs, now, state):
    for t, g in group(reds(evs)):
        mins = (t - now).total_seconds() / 60
        if not (WARN_FROM <= mins <= WARN_TO):
            continue
        key = f"warn|{t.isoformat()}"
        if key in state["sent"]:
            continue
        post(msg_warning(evs, t, g, mins))
        state["sent"].append(key)


def do_brief(evs, now, state):
    if now.weekday() > 4 or not in_window(now, BRIEF_START):
        return
    key = f"brief|{now.date()}"
    if key in state["sent"]:
        return
    post(msg_brief(evs, now))
    state["sent"].append(key)


def do_night(evs, latest, now, state):
    if not in_window(now, NIGHT_START):
        return
    dow = now.weekday()
    if dow == 6:                                   # Sunday: week ahead
        mon = now.date() + dt.timedelta(days=1)
        key = f"week|{mon}"
        if key in state["sent"]:
            return
        if latest is None or latest < mon:         # feed still shows last week
            start = now.replace(hour=NIGHT_START[0], minute=NIGHT_START[1],
                                second=0, microsecond=0)
            if now >= start + dt.timedelta(minutes=25):
                post("📅 **WEEK AHEAD**\nForexFactory has not updated "
                     "this week's calendar yet. Check forexfactory.com.")
                state["sent"].append(key)
            return
        post(msg_week(evs, mon))
        state["sent"].append(key)
    elif dow <= 3:                                 # Mon-Thu: tomorrow
        target = now.date() + dt.timedelta(days=1)
        key = f"night|{target}"
        if key in state["sent"]:
            return
        post(msg_night(evs, target))
        state["sent"].append(key)
    # Friday and Saturday: nothing


def do_preview(evs, now):
    """Run the workflow with 'preview' ticked to see every alert right now."""
    post("🧪 **PREVIEW** · test copies, not real alerts")
    today = now.date()
    mon = today - dt.timedelta(days=today.weekday())
    post(msg_week(evs, mon))
    target = today + dt.timedelta(days=1)
    if target.weekday() > 4:
        target = mon
    post(msg_night(evs, target))
    post(msg_brief(evs, now))
    future = [(t, g) for t, g in group(reds(evs)) if t > now]
    pick = future[0] if future else (group(reds(evs))[-1] if reds(evs) else None)
    if pick:
        post(msg_warning(evs, pick[0], pick[1], 15))


# ----------------------------------------------------------------------

def main():
    now = dt.datetime.now(TZ)
    try:
        evs, latest = get_events()
    except Exception as exc:
        print(f"Feed failed: {exc}", file=sys.stderr)
        return
    if PREVIEW:
        do_preview(evs, now)
        return
    state = load_state()
    do_night(evs, latest, now, state)
    do_brief(evs, now, state)
    do_warnings(evs, now, state)
    save_state(state)


if __name__ == "__main__":
    main()
