# Legends FPL → WhatsApp

Turns the official Fantasy Premier League API into ready-to-paste WhatsApp posts
for the "Legends" mini league, and puts them on a phone page with a Share button.

Admin effort after setup: **open a bookmark, tap Share, pick the group, send.**

---

## What it posts

| Post | When | Contains |
|---|---|---|
| `recap` — Damage Report | Tuesday 3pm MYT | GW winner, top 3, league average vs global, biggest climbers and fallers, wooden spoon, table, next deadline |
| `lastcall` — Last Call | 6h before that gameweek's first kickoff | Countdown, flagged players, price watch, full fixture list in MYT, top 5, captain prompt |
| `combo` — both | When the two would land within 36h | Three-line recap, then the full last call |
| `radar` — Radar | Optional midweek | Short version: flags + price momentum only |

The script picks the right one from the calendar by itself, or you force one
with `--type`.

---

## Setup (about 10 minutes, once)

### 1. Put these files in a GitHub repo

```
fpl_post.py
.github/workflows/fpl-post.yml
```

Public repo is free and unlimited for Actions. Private also works (2,000 free
minutes a month — this job uses roughly 1 minute per run).

### 2. Turn on GitHub Pages

Repo → **Settings → Pages** → Source: *Deploy from a branch* →
Branch: `main`, folder: `/docs` → Save.

Your page lands at `https://<username>.github.io/<repo>/`.
Bookmark it on your phone home screen, and subscribe to
`https://<username>.github.io/<repo>/legends.ics` in Google Calendar.

### 3. Check it works

Repo → **Actions → Build FPL post → Run workflow**.
Wait a minute, then open the Pages URL.

### 4. Set your own league

Edit the `env:` block in the workflow, or set these when running locally:

```
FPL_LEAGUE_ID    220903
FPL_LEAGUE_NICK  Legends
```

The league ID is the number in your mini league URL.

---

## Running it locally instead

```bash
pip install requests
python fpl_post.py --all      # writes docs/index.html and prints the text
```

Open `docs/index.html` in a browser. On a desktop the Share button falls back to
the WhatsApp desktop app; the Copy button always works.

---

## When it posts

Two rules, and the rest falls out of them.

**Last call → 6 hours before that gameweek's own first match.**

Three things this is *not*, because they are easy to confuse:

- Not the deadline. That is 90 minutes before kickoff and lands at 1.30am MYT
  in some weeks, which is no use as an anchor.
- Not the first match of the calendar week. Gameweeks straddle weeks: GW4 ends
  with a Monday night English kickoff, which is 3am Tuesday here, so the first
  match in that calendar week belongs to a gameweek that is already finished.
  Fixtures are grouped by their own `event` id to avoid exactly this.
- Not a fixed clock time. It moves with the fixture list every week.

**Everything lands between 12pm and 10.30pm MYT** (`WINDOW_START` /
`WINDOW_END`). A slot that computes earlier than midday is nudged forward to
12pm; one later than 10.30pm is pulled back to 10.30pm; and if that would ever
put the post past the deadline, it moves to 10.30pm the night before. With a
six hour lead the window never actually has to intervene: over the full season
every last call lands at exactly six hours before kickoff, with at least four
and a half hours of deadline margin.

**Recap → first Tuesday 3pm MYT after the gameweek settles.**

**When those collide, they merge.** A recap within 36 hours of a last call
becomes one `combo` post: last week's result in three lines, then this week's
deadline. That is what keeps the congested weeks at two posts instead of four.
Over the full 2026/27 fixture list this produces 67 posts, five of them combos,
every one inside the 12pm–10.30pm window, and every gameweek gets a last call.

See the whole season at once:

```bash
python fpl_post.py --calendar
```

`radar` is the optional third format. Use it only for weeks that earn it: a
double gameweek, a blank, or the week everyone is wildcarding.

## Knowing when to post

`docs/legends.ics` is a calendar feed with the next twelve slots and a 10 minute
alarm on each. Subscribe once in Google Calendar (*Other calendars → From URL*)
and it tells you when to post for the rest of the season, adjusting itself as
the Premier League moves kickoffs for TV.

Google refreshes external feeds slowly, sometimes only once a day, which is why
the feed publishes twelve slots ahead rather than just the next one.

The page itself also shows a green **Post this now** banner when a slot is live,
and otherwise says what is coming next.

---

## Notes on the data

- All of it comes from `https://fantasy.premierleague.com/api/` — free, no key,
  no rate limit published. Endpoints used:
  `/bootstrap-static/`, `/fixtures/?event=N`, `/leagues-classic/<id>/standings/`.
- Injury flags are FPL's own `status` / `news` / `chance_of_playing_next_round`
  fields, which is exactly what the game uses to grey out a player. That makes
  them authoritative for FPL purposes even when a press conference says
  something slightly different.
- **Price watch is a heuristic**, not a prediction. It ranks by net transfers
  this gameweek, which is the main driver of FPL's (undisclosed) price
  algorithm. The top of the list is where rises come from, but it will
  occasionally be wrong. If you want calibrated probabilities, cross-check
  LiveFPL before posting, or drop the section.
- Everything is converted to Malaysia time (UTC+8) in `TZ`.

## Things that will eventually bite you

- GitHub disables scheduled workflows after 60 days of repo inactivity. This one
  commits on most runs, which counts as activity, so it stays alive on its own.
- Scheduled Actions can fire late when GitHub is busy. Harmless here, because the
  page is a pull not a push and the job runs every three hours regardless.
- FPL sometimes returns 503 during the hour after a deadline. The job fails that
  run and the previous page stays up.
- **Far-future post times move.** The Premier League only confirms kickoff times
  about five weeks ahead; everything beyond that sits at a provisional Saturday
  3pm UK. The calendar recomputes on every run, so slots firm up as the TV picks
  land. Don't treat April's times as settled in October.
- **In congested stretches, only the most recent gameweek gets recapped.** Two
  gameweeks can share one Tuesday slot; the fresher one wins and the older one
  is skipped rather than posted a week late. Over this season that skips three
  recaps, all in the December and New Year pile-up.
