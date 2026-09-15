#!/usr/bin/env python3
"""
FPL WhatsApp post generator - "Legends" mini league.

Pulls live data from the official (free, no-key) Fantasy Premier League API and
writes ready-to-paste WhatsApp messages plus a small web page with Copy /
Share buttons.

Usage:
    python fpl_post.py                 # auto-pick the right post for today
    python fpl_post.py --type lastcall # force one
    python fpl_post.py --type radar
    python fpl_post.py --type recap
    python fpl_post.py --all           # build all three into the page

Outputs:
    docs/index.html   the phone page (Copy + Share buttons)
    docs/posts.json   raw text, in case you want it elsewhere
    stdout            the message text
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ----------------------------------------------------------------------------
# Config - the only things you should ever need to change
# ----------------------------------------------------------------------------
LEAGUE_ID = int(os.environ.get("FPL_LEAGUE_ID", "220903"))
LEAGUE_NICK = os.environ.get("FPL_LEAGUE_NICK", "Legends")
TZ = timezone(timedelta(hours=8))          # Asia/Kuala_Lumpur
TABLE_ROWS = 5                             # how many league rows in each post
FLAG_MIN_OWNERSHIP = 3.0                   # ignore flags on players nobody owns
PRICE_NAMES = 4                            # names per rise/fall line

# --- when to post ------------------------------------------------------------
# The deadline moves around and some gameweeks kick off at 3am Malaysia time,
# so the last-call post is pinned to the FIRST KICKOFF, not to the deadline.
POST_LEAD_HOURS = 6        # post this long before the gameweek's own first match
WINDOW_START = (12, 0)     # nothing before 12pm MYT
WINDOW_END = (22, 30)      # nothing after 10.30pm MYT
RECAP_WEEKDAY = 1          # Tuesday (Mon=0)
RECAP_HOUR = 15            # 3pm MYT
UK = ZoneInfo("Europe/London")
LOCKDOWN_HOUR_UK = 9       # FPL scores go final at 9am UK the day after the last match
POST_LOCKDOWN_H = 1        # breathing room after lockdown before quoting scores
MERGE_WINDOW_H = 36        # a recap this close to a last call becomes one post
RECAP_STALE_DAYS = 5       # older than this and last week's scores aren't news
MAX_PER_WINDOW = 2         # hard cap on posts in any CAP_WINDOW_DAYS stretch
CAP_WINDOW_DAYS = 6        # 6 not 7: a Tue recap and the previous Tue recap sit
                           # almost exactly 7 days apart, so a 7 day window counts
                           # last week's post and wrongly drops this week's
DUE_WINDOW_H = 3           # how long a slot stays flagged as "post this now"

API = "https://fantasy.premierleague.com/api"
HEADERS = {"User-Agent": "Mozilla/5.0 (fpl-legends-bot)"}
OUT = Path(__file__).parent / "docs"


# ----------------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------------
def get(path: str) -> dict:
    r = requests.get(f"{API}{path}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def load() -> dict:
    bs = get("/bootstrap-static/")
    events = bs["events"]
    nxt = next((e for e in events if e["is_next"]), None)
    cur = next((e for e in events if e["is_current"]), None)
    gw = (nxt or cur)["id"]
    allfix = get("/fixtures/")            # whole season, needed to plan the calendar
    return {
        "bs": bs,
        "next": nxt,
        "current": cur,
        "teams": {t["id"]: t["short_name"] for t in bs["teams"]},
        "all_fixtures": allfix,
        "fixtures": [f for f in allfix if f.get("event") == gw],
        "league": get(f"/leagues-classic/{LEAGUE_ID}/standings/"),
    }


def local(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ)


def net_transfers(p: dict) -> int:
    return p["transfers_in_event"] - p["transfers_out_event"]


# ----------------------------------------------------------------------------
# Scheduling
#
# Two rules, and everything else falls out of them:
#   last call  ->  POST_LEAD_HOURS before THIS GAMEWEEK'S first match
#   recap      ->  the first Tuesday 3pm after the previous gameweek settles
#
# "This gameweek's first match" is deliberate, and it is neither of the two
# things it gets confused with:
#   * not the deadline - that is 90 minutes before kickoff and lands at 1.30am
#     Malaysia time on some weeks, which is no use as an anchor;
#   * not the first match of the calendar week - gameweeks overlap weeks. GW4
#     finishes with a Monday night English kickoff, which is Tuesday 3am here,
#     so the first match in that calendar week belongs to the gameweek that is
#     already over. Fixtures are grouped by their own `event` id for exactly
#     this reason.
# Everything then gets pulled into the WINDOW_START..WINDOW_END window.
# ----------------------------------------------------------------------------
def gw_bounds(fixtures: list[dict]) -> dict[int, tuple[datetime, datetime]]:
    """{gameweek: (first kickoff, last kickoff)} in local time."""
    out: dict[int, tuple[datetime, datetime]] = {}
    for f in fixtures:
        gw, ko = f.get("event"), f.get("kickoff_time")
        if not gw or not ko:
            continue
        t = local(ko)
        lo, hi = out.get(gw, (t, t))
        out[gw] = (min(lo, t), max(hi, t))
    return out


def at_time(d: datetime, hm: tuple[int, int]) -> datetime:
    return d.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)


def in_window(d: datetime) -> bool:
    return WINDOW_START <= (d.hour, d.minute) <= WINDOW_END


def lastcall_at(first_ko: datetime, deadline: datetime) -> datetime:
    """POST_LEAD_HOURS before this gameweek's own first match, pulled into the
    allowed window. Never later than an hour before the deadline; when the ideal
    slot would break that, it moves to the window's end the night before."""
    t = first_ko - timedelta(hours=POST_LEAD_HOURS)
    if (t.hour, t.minute) > WINDOW_END:
        t = at_time(t, WINDOW_END)                       # pull back into the evening
    elif (t.hour, t.minute) < WINDOW_START:
        t = at_time(t, WINDOW_START)                     # nudge forward to midday
    cutoff = deadline - timedelta(hours=1)
    if t > cutoff:
        t = at_time(t - timedelta(days=1), WINDOW_END)   # the night before
    return t


def lockdown_at(last_ko: datetime) -> datetime:
    """When FPL scores go final: 9am UK on the day after the gameweek's last
    match. New for 2026/27, replacing the old one hour after the final whistle.
    It matters here because a Monday night kickoff pushes lockdown to Tuesday
    morning UK, which is Tuesday afternoon in Malaysia, after the usual slot."""
    uk = last_ko.astimezone(UK) + timedelta(days=1)
    uk = uk.replace(hour=LOCKDOWN_HOUR_UK, minute=0, second=0, microsecond=0)
    return uk.astimezone(TZ)


def recap_at(last_ko: datetime) -> datetime:
    """Tuesday 3pm, or as soon after lockdown as the rules allow."""
    ready = lockdown_at(last_ko) + timedelta(hours=POST_LOCKDOWN_H)
    day = at_time(ready, (RECAP_HOUR, 0))
    if day < ready:                       # lockdown lands after the usual slot
        day = ready
    while day.weekday() != RECAP_WEEKDAY:
        day = at_time(day + timedelta(days=1), (RECAP_HOUR, 0))
    if (day.hour, day.minute) > WINDOW_END:
        day = at_time(day, WINDOW_END)
    return day


def fresh(recap: dict, host: dict) -> bool:
    """Worth attaching last week's scores to this post, or already old news?"""
    return host["at"] - recap["end"] <= timedelta(days=RECAP_STALE_DAYS)


def plan(d: dict) -> list[dict]:
    """The whole season's posting calendar, merged and capped."""
    bounds = gw_bounds(d["all_fixtures"])
    events = {e["id"]: e for e in d["bs"]["events"]}

    slots: list[dict] = []
    for gw, (first_ko, last_ko) in sorted(bounds.items()):
        ev = events.get(gw)
        if not ev:
            continue
        slots.append({"kind": "lastcall", "gw": gw,
                      "at": lastcall_at(first_ko, local(ev["deadline_time"]))})
        if gw + 1 in bounds:          # no recap after the final gameweek
            slots.append({"kind": "recap", "gw": gw,
                          "at": recap_at(last_ko), "end": last_ko})
    slots.sort(key=lambda s: s["at"])
    # Congested weeks can push two gameweeks into one Tuesday slot. Only the
    # freshest one is worth recapping.
    freshest: dict[datetime, dict] = {}
    rest: list[dict] = []
    for s in slots:
        if s["kind"] != "recap":
            rest.append(s)
        elif s["at"] not in freshest or s["gw"] > freshest[s["at"]]["gw"]:
            freshest[s["at"]] = s
    slots = sorted(rest + list(freshest.values()), key=lambda s: s["at"])

    # A recap landing close to a last call is folded into it: one message
    # carrying last week's result and this week's deadline.
    merged: list[dict] = []
    for s in slots:
        if s["kind"] != "recap":
            merged.append(s)
            continue
        nxt = next((x for x in slots
                    if x["kind"] in ("lastcall", "combo") and x["at"] > s["at"]), None)
        if nxt and nxt["at"] - s["at"] <= timedelta(hours=MERGE_WINDOW_H) and fresh(s, nxt):
            nxt["kind"] = "combo"
            nxt["recap_gw"] = s["gw"]
        else:
            merged.append(s)
    merged.sort(key=lambda s: s["at"])

    # Rolling cap. A last call is never dropped - a deadline reminder is always
    # worth sending, and the festive pile-up genuinely has more deadlines. Only
    # recaps give way, and they ride along with the next post when they do.
    kept: list[dict] = []
    for s in merged:
        window = [k for k in kept if s["at"] - k["at"] < timedelta(days=CAP_WINDOW_DAYS)]
        if s["kind"] == "recap" and len(window) >= MAX_PER_WINDOW:
            nxt = next((x for x in merged
                        if x["kind"] in ("lastcall", "combo") and x["at"] > s["at"]), None)
            if nxt and "recap_gw" not in nxt and fresh(s, nxt):
                nxt["kind"] = "combo"
                nxt["recap_gw"] = s["gw"]
            continue
        kept.append(s)
    return kept


def current_slot(d: dict, now: datetime | None = None) -> tuple[dict | None, dict | None]:
    """(slot due right now or None, the next slot after that)."""
    now = now or datetime.now(TZ)
    cal = plan(d)
    due = next((s for s in cal
                if s["at"] <= now < s["at"] + timedelta(hours=DUE_WINDOW_H)), None)
    upcoming = next((s for s in cal if s["at"] > now), None)
    return due, upcoming


# ----------------------------------------------------------------------------
# Building blocks - each returns a list of lines
# ----------------------------------------------------------------------------
def clock(dt: datetime) -> str:
    """7.30pm, or 10pm when it's on the hour."""
    s = f"{dt:%-I.%M%p}".lower()
    return s.replace(".00", "")


def block_deadline(d: dict) -> list[str]:
    ev = d["next"]
    if not ev:
        return ["Season habis. Jumpa next year 👋"]
    dl = local(ev["deadline_time"])
    left = dl - datetime.now(TZ)
    hrs = left.total_seconds() / 3600
    today = dl.date() == datetime.now(TZ).date()
    if hrs < 0:
        when = "deadline dah lepas 💀"
    elif hrs < 2:
        when = f"*{int(left.total_seconds() // 60)} minit lagi* ({clock(dl)})"
    elif hrs < 24:
        night = "malam ni" if today else "esok"
        when = f"*{night} {clock(dl)}* ({int(hrs)} jam lagi)"
    else:
        when = f"*{dl:%a %-d %b}, {clock(dl)}*"
    return [f"⏰ Deadline: {when}"]


def block_fixtures(d: dict) -> list[str]:
    fx = sorted(
        (f for f in d["fixtures"] if f.get("kickoff_time")),
        key=lambda f: f["kickoff_time"],
    )
    if not fx:
        return []
    T = d["teams"]
    # group games that kick off at the same time onto one line
    lines, bucket, stamp = [], [], None
    for f in fx:
        ko = local(f["kickoff_time"])
        key = f"{ko:%a} {clock(ko)}"
        if key != stamp and bucket:
            lines.append(f"{stamp}  " + ", ".join(bucket))
            bucket = []
        stamp = key
        bucket.append(f"{T[f['team_h']]}-{T[f['team_a']]}")
    if bucket:
        lines.append(f"{stamp}  " + ", ".join(bucket))
    # flag the tastiest game: lowest combined fixture difficulty gap
    tie = min(fx, key=lambda f: abs(f["team_h_difficulty"] - f["team_a_difficulty"]) - (f["team_h_difficulty"] + f["team_a_difficulty"]))
    big = f"{T[tie['team_h']]} v {T[tie['team_a']]}"
    return ["", "📅 *Jadual*"] + lines + [f"Game of the week: *{big}* 👀"]


def block_flags(d: dict, limit: int = 7) -> list[str]:
    flagged = [
        p for p in d["bs"]["elements"]
        if p["status"] != "a" and float(p["selected_by_percent"]) >= FLAG_MIN_OWNERSHIP
    ]
    if not flagged:
        return ["", "🚑 *Injury watch*", "Semua clear. Takde alasan minggu ni."]
    flagged.sort(key=lambda p: -float(p["selected_by_percent"]))
    T = d["teams"]
    out = ["", "🚑 *Check team korang*"]
    for p in flagged[:limit]:
        pct = p["chance_of_playing_next_round"]
        if p["status"] == "u":
            tag = "GONE"                      # left the league
        elif p["status"] in ("i", "s", "n") or pct == 0:
            tag = "OUT"                       # injured / suspended / not in squad
        elif pct is None:
            tag = "doubt"
        else:
            tag = f"{pct}%"
        note = (p["news"] or "").split(" - ")[0][:34]
        out.append(f"• {p['web_name']} ({T[p['team']]}) {tag}" + (f" · {note.lower()}" if note else ""))
    if len(flagged) > limit:
        out.append(f"_+{len(flagged) - limit} lagi flagged_")
    return out


def block_prices(d: dict, names: int = PRICE_NAMES) -> list[str]:
    """Momentum watchlist from net transfers this gameweek.

    This is a heuristic, not FPL's actual (undisclosed) price algorithm. Net
    transfers is the main driver, so the top of this list is where rises come
    from, but treat it as 'watch these', not 'these will definitely move'.
    """
    els = d["bs"]["elements"]
    T = d["teams"]
    ups = sorted(els, key=net_transfers, reverse=True)[:names]
    downs = sorted(els, key=net_transfers)[:names]
    done = [p for p in els if p["cost_change_event"] != 0]
    out = ["", "💸 *Price watch*"]
    out.append("⬆️ " + ", ".join(f"{p['web_name']} ({T[p['team']]})" for p in ups))
    out.append("⬇️ " + ", ".join(f"{p['web_name']} ({T[p['team']]})" for p in downs))
    if done:
        out.append(f"_{len(done)} player dah tukar harga GW ni_")
    return out


def block_table(d: dict, rows: int = TABLE_ROWS) -> list[str]:
    res = d["league"]["standings"]["results"]
    if not res:
        return []
    out = ["", f"🏆 *{LEAGUE_NICK}*"]
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for r in res[:rows]:
        out.append(f"{medals.get(r['rank'], str(r['rank']) + '.')} {r['entry_name']} - {r['total']}")
    if len(res) > rows:
        out.append(f"_...dan {len(res) - rows} lagi kat bawah_")
    return out


def block_recap_league(d: dict) -> list[str]:
    res = d["league"]["standings"]["results"]
    if not res:
        return []
    by_gw = sorted(res, key=lambda r: -r["event_total"])
    avg = sum(r["event_total"] for r in res) / len(res)
    moves = [
        (r["entry_name"], (r["last_rank"] or r["rank"]) - r["rank"], r["last_rank"], r["rank"])
        for r in res
    ]
    up = sorted(moves, key=lambda m: -m[1])[:3]
    down = sorted(moves, key=lambda m: m[1])[:3]
    gw = d["current"]
    out = [
        f"👑 Raja minggu ni: *{by_gw[0]['entry_name']}* - {by_gw[0]['event_total']} pts",
        f"🥈 {by_gw[1]['entry_name']} - {by_gw[1]['event_total']}",
        f"🥉 {by_gw[2]['entry_name']} - {by_gw[2]['event_total']}",
        "",
        f"Average league: {avg:.1f}" + (f"  |  global: {gw['average_entry_score']}" if gw else ""),
        "",
        "📈 *Naik laju*",
    ]
    out += [f"{n} +{d_} ({lr} ➜ {r})" for n, d_, lr, r in up if d_ > 0] or ["Takde sesiapa naik. Semua stuck."]
    out += ["", "📉 *Terjun*"]
    out += [f"{n} {d_} ({lr} ➜ {r})" for n, d_, lr, r in down if d_ < 0] or ["Takde sesiapa jatuh."]
    out += [
        "",
        f"🪦 Wooden spoon minggu ni: *{by_gw[-1]['entry_name']}* - {by_gw[-1]['event_total']}",
    ]
    return out


def block_poll(d: dict) -> list[str]:
    """Captain prompt, with the three most-transferred-in names as options."""
    els = d["bs"]["elements"]
    picks = sorted(
        (p for p in els if p["element_type"] in (3, 4) and p["status"] == "a"),
        key=net_transfers,
        reverse=True,
    )[:3]
    names = ", ".join(p["web_name"] for p in picks)
    return ["", f"©️ Captain sapa? {names}, atau punting sendiri? 👇"]


# ----------------------------------------------------------------------------
# The three posts
# ----------------------------------------------------------------------------
def post_radar(d: dict) -> str:
    """Short midweek nudge. Deliberately leaner than the deadline post."""
    gw = d["next"]["name"] if d["next"] else "GW"
    lines = [f"🔭 *{gw.upper()} RADAR*"]
    lines += block_deadline(d)
    lines += block_flags(d, limit=4)
    lines += block_prices(d, names=3)
    lines += block_poll(d)
    return "\n".join(lines)


def post_lastcall(d: dict) -> str:
    gw = d["next"]["name"] if d["next"] else "GW"
    lines = [f"🚨 *{gw.upper()} LAST CALL* 🚨"]
    lines += block_deadline(d)
    lines += block_flags(d)
    lines += block_prices(d)
    lines += block_fixtures(d)
    lines += block_table(d)
    lines += block_poll(d)
    return "\n".join(lines)


def block_next_deadline(d: dict) -> list[str]:
    """Closes the recap, so the group always knows when the next one bites."""
    ev = d["next"]
    if not ev:
        return []
    dl = local(ev["deadline_time"])
    days = (dl - datetime.now(TZ)).days
    tail = "  (lama lagi, international break)" if days >= 8 else ""
    return ["", f"⏰ Next deadline: *{dl:%a %-d %b}, {clock(dl)}*{tail}"]


def post_recap(d: dict) -> str:
    gw = d["current"]["name"] if d["current"] else "Last GW"
    lines = [f"📊 *{gw.upper()} DAMAGE REPORT*", ""]
    lines += block_recap_league(d)
    lines += block_table(d, rows=8)
    lines += block_next_deadline(d)
    return "\n".join(lines)


def post_combo(d: dict) -> str:
    """Midweek and congested rounds: last week's result and this week's deadline
    in one message, so a compressed fixture list never costs a third post."""
    last = d["current"]["name"] if d["current"] else "Last GW"
    res = d["league"]["standings"]["results"]
    head = [f"📊 *{last.upper()} in short*"]
    if res:
        by_gw = sorted(res, key=lambda r: -r["event_total"])
        avg = sum(r["event_total"] for r in res) / len(res)
        head += [
            f"👑 {by_gw[0]['entry_name']} - {by_gw[0]['event_total']} pts",
            f"🪦 {by_gw[-1]['entry_name']} - {by_gw[-1]['event_total']} pts",
            f"Average league: {avg:.1f}",
        ]
    return "\n".join(head + ["", "➖➖➖➖➖", ""]) + post_lastcall(d)


BUILDERS = {
    "radar": post_radar,
    "lastcall": post_lastcall,
    "recap": post_recap,
    "combo": post_combo,
}


def auto_pick(d: dict) -> str:
    """Whatever the calendar says is due now, else the next thing coming up."""
    due, upcoming = current_slot(d)
    return (due or upcoming or {"kind": "radar"})["kind"]


# ----------------------------------------------------------------------------
# The phone page
# ----------------------------------------------------------------------------
PAGE = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{nick} FPL post</title>
<link rel="manifest" href="manifest.webmanifest">
<meta name="theme-color" content="#0f1419">
<link rel="icon" href="icon-192.png">
<link rel="apple-touch-icon" href="icon-192.png">
<style>
:root{{color-scheme:light dark;--bg:#0f1419;--card:#1a2027;--ink:#e8eef4;--dim:#8fa3b5;--acc:#25d366;--line:#2a323b}}
@media(prefers-color-scheme:light){{:root{{--bg:#f4f6f8;--card:#fff;--ink:#16202a;--dim:#5d7183;--line:#e2e8ee}}}}
*{{box-sizing:border-box}}
body{{margin:0;padding:18px 16px 40px;background:var(--bg);color:var(--ink);
 font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;max-width:640px;margin-inline:auto}}
h1{{font-size:17px;margin:0 0 2px;letter-spacing:-.01em}}
.sub{{color:var(--dim);font-size:12.5px;margin:0 0 14px}}
.banner{{border-radius:12px;padding:12px 14px;margin-bottom:18px;font-size:14px;
 border:1px solid var(--line);background:var(--card)}}
.banner.now{{background:var(--acc);color:#052e16;border-color:transparent;font-weight:600}}
.banner.hold{{background:#8a5412;color:#ffeeda;border-color:transparent}}
.banner b{{font-weight:700}}
.due{{font-size:12px;color:var(--dim);margin:-6px 0 10px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:16px}}
.tag{{display:inline-block;font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;
 color:var(--dim);margin-bottom:10px}}
pre{{white-space:pre-wrap;word-wrap:break-word;margin:0 0 12px;font:14px/1.55 inherit}}
.row{{display:flex;gap:8px;flex-wrap:wrap}}
button{{flex:1 1 130px;border:0;border-radius:10px;padding:11px 14px;font:600 14px inherit;cursor:pointer}}
.go{{background:var(--acc);color:#052e16}}
.cp{{background:transparent;color:var(--ink);border:1px solid var(--line)}}
.ok{{background:var(--acc)!important;color:#052e16!important}}
</style>
<h1>{nick} &middot; ready to post</h1>
<p class="sub">Built {built} &middot; tap Share, pick the group, send.</p>
{banner}
{cards}
<script>
const share=async(id,btn)=>{{
  const t=document.getElementById(id).textContent;
  try{{ if(navigator.share){{ await navigator.share({{text:t}}); return; }} }}catch(e){{ if(e.name==='AbortError')return; }}
  location.href='whatsapp://send?text='+encodeURIComponent(t);
}};
const copy=async(id,btn)=>{{
  const t=document.getElementById(id).textContent;
  try{{ await navigator.clipboard.writeText(t); }}
  catch(e){{ const a=document.createElement('textarea');a.value=t;document.body.appendChild(a);
            a.select();document.execCommand('copy');a.remove(); }}
  const o=btn.textContent; btn.textContent='Copied'; btn.classList.add('ok');
  setTimeout(()=>{{btn.textContent=o;btn.classList.remove('ok')}},1400);
}};
</script>
"""

CARD = """<div class="card"><div class="tag">{label}</div>
<p class="due">{due}</p>
<pre id="{id}">{body}</pre>
<div class="row">
<button class="go" onclick="share('{id}',this)">Share to WhatsApp</button>
<button class="cp" onclick="copy('{id}',this)">Copy</button>
</div></div>
"""

LABELS = {
    "lastcall": "Last call",
    "radar": "Midweek radar",
    "recap": "Damage report",
    "combo": "Recap + last call",
}


def when(dt: datetime) -> str:
    today = datetime.now(TZ).date()
    if dt.date() == today:
        return f"today {clock(dt)}"
    if dt.date() == today + timedelta(days=1):
        return f"tomorrow {clock(dt)}"
    return f"{dt:%a %-d %b}, {clock(dt)}"


def write_page(posts: dict[str, str], due: dict | None, upcoming: dict | None,
               dues: dict[str, datetime] | None = None, hold: str | None = None) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    dues = dues or {}

    if hold:
        banner = f'<div class="banner hold">Hold off &mdash; {hold}</div>'
    elif due:
        banner = (f'<div class="banner now">Post this now &mdash; '
                  f'{LABELS.get(due["kind"], due["kind"])} was due {clock(due["at"])}.</div>')
    elif upcoming:
        banner = (f'<div class="banner">Next post: <b>{LABELS.get(upcoming["kind"], upcoming["kind"])}</b>, '
                  f'{when(upcoming["at"])}.</div>')
    else:
        banner = '<div class="banner">Nothing scheduled. Season over?</div>'

    cards = "".join(
        CARD.format(
            label=LABELS.get(k, k),
            id=k,
            due=(f"Due {when(dues[k])}" if k in dues else "Ready whenever you want it"),
            body=html.escape(v),
        )
        for k, v in posts.items()
    )
    page = PAGE.format(
        nick=html.escape(LEAGUE_NICK),
        built=f"{datetime.now(TZ):%a %-d %b}, {clock(datetime.now(TZ))} MYT",
        banner=banner,
        cards=cards,
    )
    (OUT / "index.html").write_text(page, encoding="utf-8")
    (OUT / "posts.json").write_text(
        json.dumps({
            "built": datetime.now(TZ).isoformat(),
            "due_now": due["kind"] if due else None,
            "next": {"kind": upcoming["kind"], "at": upcoming["at"].isoformat()} if upcoming else None,
            "posts": posts,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return OUT / "index.html"


# ----------------------------------------------------------------------------
# Home screen app
#
# A web manifest turns the page into something Chrome will "Install" rather than
# bookmark: its own icon, no browser chrome, opens like an app. The icons are
# embedded below so the repo needs no binary files and nothing to re-upload.
# ----------------------------------------------------------------------------
ICON_192 = (
    "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"
)

ICON_512 = (
    "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"
    "axvBAAAAAElFTkSuQmCC"
)


def write_pwa() -> None:
    import base64
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "icon-192.png").write_bytes(base64.b64decode(ICON_192))
    (OUT / "icon-512.png").write_bytes(base64.b64decode(ICON_512))
    (OUT / "manifest.webmanifest").write_text(json.dumps({
        "name": f"{LEAGUE_NICK} FPL",
        "short_name": LEAGUE_NICK,
        "start_url": ".",
        "scope": ".",
        "display": "standalone",
        "background_color": "#0f1419",
        "theme_color": "#0f1419",
        "icons": [
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------------
# Calendar feed - subscribe once, it tells you when to post for the rest of the
# season. Google refreshes external feeds slowly, so publish a long horizon.
# ----------------------------------------------------------------------------
def write_ics(cal: list[dict], horizon: int = 12) -> Path:
    now = datetime.now(TZ)
    upcoming = [s for s in cal if s["at"] > now - timedelta(days=1)][:horizon]
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "CALSCALE:GREGORIAN",
           f"PRODID:-//{LEAGUE_NICK}//FPL post//EN",
           f"X-WR-CALNAME:{LEAGUE_NICK} FPL posts", "X-PUBLISHED-TTL:PT3H"]
    for s in upcoming:
        start = s["at"].astimezone(timezone.utc)
        end = start + timedelta(minutes=20)
        out += [
            "BEGIN:VEVENT",
            f"UID:fpl-{LEAGUE_ID}-{s['kind']}-gw{s['gw']}@legends",
            f"DTSTAMP:{now.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}",
            f"DTSTART:{start:%Y%m%dT%H%M%SZ}",
            f"DTEND:{end:%Y%m%dT%H%M%SZ}",
            f"SUMMARY:FPL post - {LABELS.get(s['kind'], s['kind'])} (GW{s['gw']})",
            "DESCRIPTION:Open the Legends page and tap Share.",
            "BEGIN:VALARM", "TRIGGER:-PT10M", "ACTION:DISPLAY",
            "DESCRIPTION:FPL post due", "END:VALARM",
            "END:VEVENT",
        ]
    out.append("END:VCALENDAR")
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "legends.ics"
    p.write_text("\r\n".join(out) + "\r\n", encoding="utf-8")
    return p


# ----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", choices=list(BUILDERS))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--calendar", action="store_true", help="print the season plan and exit")
    a = ap.parse_args()

    d = load()
    cal = plan(d)

    if a.calendar:
        for s in cal:
            print(f"{s['at']:%a %d %b %Y  %H:%M}  {s['kind']:9s} GW{s['gw']}"
                  + (f" (+GW{s['recap_gw']} recap)" if "recap_gw" in s else ""))
        return 0

    due, upcoming = current_slot(d)
    if a.all:
        # Whatever is due (or due next) goes on top - that is the one you came
        # to send. The rest sit below in case you want a different format.
        lead = (due or upcoming or {}).get("kind")
        order = ([lead] if lead in BUILDERS else []) + [k for k in BUILDERS if k != lead]
        posts = {k: BUILDERS[k](d) for k in order}
    else:
        kind = a.type or (due or upcoming or {"kind": "radar"})["kind"]
        posts = {kind: BUILDERS[kind](d)}

    dues = {k: s["at"] for k in posts
            for s in ([due] if due and due["kind"] == k else
                      [x for x in cal if x["kind"] == k and x["at"] > datetime.now(TZ)][:1])}

    # Safety net. Scores are provisional until FPL locks the gameweek at 9am UK
    # the day after its last match, so never let a recap go out before then.
    hold = None
    cur = d["current"]
    lead = (due or upcoming or {}).get("kind")
    if cur and not cur.get("data_checked") and lead in ("recap", "combo"):
        bounds = gw_bounds(d["all_fixtures"]).get(cur["id"])
        if bounds:
            lock = lockdown_at(bounds[1])
            hold = (f"{cur['name']} scores are still provisional. "
                    f"FPL locks them at 9am UK, {clock(lock)} here.")

    path = write_page(posts, due, upcoming, dues, hold)
    write_ics(cal)
    write_pwa()
    for k, v in posts.items():
        print(f"\n===== {k} =====\n{v}")
    print(f"\n[page] {path}", file=sys.stderr)
    if upcoming:
        print(f"[next] {upcoming['kind']} at {upcoming['at']:%a %d %b %H:%M} MYT", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
