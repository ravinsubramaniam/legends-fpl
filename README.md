# Legends FPL → WhatsApp

Turns the official Fantasy Premier League API into ready-to-paste WhatsApp posts
for the "Legends" mini league (id 220903) and serves them on a phone page with a
Share button.

Live since 12 September 2026. Nothing runs on a personal computer: GitHub runs
the job, GitHub serves the page, the phone opens a URL.

```
FPL API  ->  GitHub Actions  ->  GitHub Pages  ->  phone  ->  WhatsApp
```

| Thing | Where |
| :---- | :---- |
| Page | `https://ravinsubramaniam.github.io/legends-fpl/` |
| Calendar feed | `https://ravinsubramaniam.github.io/legends-fpl/legends.ics` |
| Generator | `fpl_post.py` at the repo root |
| Schedule | `.github/workflows/fpl-post.yml` |
| Published output | `docs/` — the page, `posts.json`, `legends.ics`, icons, manifest |
| Gameweek detail | `docs/cache/gw*.json` — fetched once per gameweek, never again |
| Story memory | `docs/cache/fired.json` — which detectors ran, to avoid repeats |

---

## The four post formats

| Format | When |
| :---- | :---- |
| `lastcall` | 6 hours before that gameweek's own first match |
| `recap` | The day the gameweek concludes, 30 min after lockdown |
| `combo` | When those two would fall within 36 hours. Short recap, then the full last call |
| `radar` | Optional. Only for double gameweeks, blanks, or wildcard week |

All four sit on the page every time. The one that is due sorts to the top and
the banner names it.

## When it posts

**Last call: 6 hours before that gameweek's own first match.** Not the deadline,
which lands at 1.30am MYT some weeks. Not the first match of the calendar week
either, since gameweeks straddle weeks and a Monday night kickoff belongs to the
gameweek that has just finished.

**Recap: the day the gameweek concludes.** FPL freezes scores at 9am UK on the
day after the last match, new for 2026/27. That is 4pm MYT in summer and 5pm in
winter, so a Sunday finish recaps on Monday and a Monday finish on Tuesday.
There is no fixed weekday.

**Everything lands between 12pm and 10.30pm MYT.** With a six hour lead the
window never actually has to intervene.

**When the two collide they merge** into one `combo` post, which is what keeps
the congested December weeks at two posts instead of four.

See the whole season at once:

```
python fpl_post.py --calendar
```

## The story section

The recap carries up to three lines under "Cerita minggu ni", picked from 17
detectors. Three things stop it sounding the same every week:

- **Thresholds are relative.** A 16 point bench is a story only if it is high
  against every bench this league has produced, not against a fixed number that
  27 managers clear weekly by chance.
- **Recency is penalised.** A detector that ran last week loses 45 points, two
  weeks ago 22, three weeks ago 10. Anything scoring under 45 is dropped rather
  than padding the post, so a short section beats a stale one.
- **Six phrasings each**, 114 in total, chosen from the gameweek number. The
  wording moves weekly; a rebuild of the same week is identical.

Only one line per manager and one per family, so two transfer stories or two
streak lines never sit together.

| Family | Detectors |
| :---- | :---- |
| story | turnaround |
| league | everyone_blanked, chips_left |
| chip | chip_bang, chip_flop, bench_boost |
| transfer | transfer_bad, transfer_good, hit_punished |
| captain | captain_split, captain_season |
| streak | cold_streak, hot_streak |
| shape | volatile, steady |
| bench | bench_pain, bench_season |

Season records are not detectors. A new high or low is appended to the Raja and
wooden spoon lines, so the same fact is never said twice.

Half the detectors read only cached history, so a week where the per-manager
fetch is skipped still gets a story section instead of nothing.

**Cost.** Three API calls per manager plus the live feed, about 80 for 27
players, roughly 14 seconds. It runs once per gameweek and the result is cached
forever. The fetch only fires when a recap is the post coming up and the scores
are final.

## Knowing when to post

`docs/legends.ics` carries the next twelve slots with a ten minute alarm on
each. Subscribe once in Google Calendar under *Other calendars → From URL*.

The Android Google Calendar app cannot add a calendar by URL, only the website
can, and the new calendar then has to be ticked in the phone app or the alerts
never fire. Google refreshes external feeds slowly, sometimes once a day, which
is why the feed publishes twelve slots ahead rather than one.

The page also shows a banner: green **Post this now** when a slot is live, amber
**Hold off** when scores are still provisional, plain grey otherwise.

## Changing things

Edit in the GitHub web editor, commit, then Actions → Run workflow if you do not
want to wait. Everything worth adjusting is in one config block at the top of
`fpl_post.py`:

| Setting | Now | What it does |
| :---- | :---- | :---- |
| `POST_LEAD_HOURS` | 6 | Gap before the gameweek's first match |
| `WINDOW_START` / `WINDOW_END` | `(12,0)` / `(22,30)` | Earliest and latest a post may land |
| `LOCKDOWN_HOUR_UK` | 9 | When FPL freezes scores, UK time, day after the last match |
| `POST_LOCKDOWN_H` | 0.5 | Breathing room after lockdown |
| `MERGE_WINDOW_H` | 36 | How close before a recap and last call merge |
| `MAX_PER_WINDOW` / `CAP_WINDOW_DAYS` | 2 / 6 | Posts allowed per stretch. Six days, not seven, or consecutive recaps collide |
| `DROUGHT_DAYS` | 7 | A gap this long ahead lets a recap survive the cap |
| `MAX_HIGHLIGHTS` | 3 | Lines in the story section |
| `MIN_SCORE` | 45 | Below this a line is dropped rather than padding |
| `RECENT_PENALTY` | 45, 22, 10 | Points off for firing 1, 2, 3 weeks ago |
| `TABLE_ROWS` / `MOVERS` | 5 / 2 | League rows and climbers named |
| `LEAGUE_ID` | 220903 | Swap it and the same repo runs a different league |

Wording lives in `PHRASES` near the bottom. Detector thresholds and families are
in `DETECTORS` just under it.

## If something looks wrong

| Symptom | Cause |
| :---- | :---- |
| Page 404s | Pages still deploying, or the folder is `/ (root)` instead of `/docs` |
| Page loads but never updates | Workflow permissions are read only. Settings → Actions → General |
| Actions tab empty | Workflow file is not at exactly `.github/workflows/fpl-post.yml` |
| A run failed red | Usually a 503 from FPL in the hour after a deadline. The next run picks it up |
| Page hours old | GitHub drops most scheduled runs. See below |
| Post times wrong months ahead | Expected. Kickoffs are confirmed about five weeks out |
| Calendar has no new entries | Google syncs feeds slowly. The feed carries twelve slots so stale is still correct |

## Known limits

- **GitHub honours roughly one scheduled run in four.** Observed over several
  weeks: runs configured hourly fired every three to six hours. The schedule is
  a request, not a guarantee. If the page is stale when you need it, press Run
  workflow. The real fix is an external scheduler poking GitHub, not yet built.
- **FPL's `data_checked` flag is not trusted.** It sat false for hours after
  every GW4 fixture had finished and the standings had updated. The guard checks
  fixtures finished plus lockdown passed instead.
- **The price watch is a ranking, not a prediction.** It orders by net transfers,
  the main driver of FPL's undisclosed pricing algorithm, but it will be wrong
  sometimes. Cross-check LiveFPL before sending if it matters.
- **Injury flags are FPL's own** `status` and `news` fields, the same data that
  greys a player out in the squad view. Correct for FPL purposes even when a
  press conference says otherwise.
- **In congested stretches only the most recent gameweek is recapped.** Two can
  share one slot; the fresher wins rather than posting stale scores.
- GitHub disables scheduled workflows after 60 days of repo inactivity. This one
  commits on most runs, so it keeps itself alive.
