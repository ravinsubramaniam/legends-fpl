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
SETTLE_HOURS = 8           # after the last kickoff before scores are safe to quote
MERGE_WINDOW_H = 36        # a recap this close to a last call becomes one post
RECAP_STALE_DAYS = 5       # older than this and last week's scores aren't news
MAX_PER_7_DAYS = 2         # hard cap, whatever the fixture list does
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


def recap_at(last_ko: datetime) -> datetime:
    """First Tuesday 3pm once the scores have settled."""
    t = last_ko + timedelta(hours=SETTLE_HOURS)
    day = t.replace(hour=RECAP_HOUR, minute=0, second=0, microsecond=0)
    if day < t:
        day += timedelta(days=1)
    while day.weekday() != RECAP_WEEKDAY:
        day += timedelta(days=1)
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
        window = [k for k in kept if s["at"] - k["at"] < timedelta(days=7)]
        if s["kind"] == "recap" and len(window) >= MAX_PER_7_DAYS:
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
        f"👑 GW winner: *{by_gw[0]['entry_name']}* - {by_gw[0]['event_total']} pts",
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
               dues: dict[str, datetime] | None = None) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    dues = dues or {}

    if due:
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
    path = write_page(posts, due, upcoming, dues)
    write_ics(cal)
    for k, v in posts.items():
        print(f"\n===== {k} =====\n{v}")
    print(f"\n[page] {path}", file=sys.stderr)
    if upcoming:
        print(f"[next] {upcoming['kind']} at {upcoming['at']:%a %d %b %H:%M} MYT", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
