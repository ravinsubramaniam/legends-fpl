# Notes

Working rules and the reasoning behind them. `README.md` covers how the thing
runs; this covers how to write for it and why it is shaped the way it is.

Read this before changing post content or timing.

---

## Posting rules

- Two posts a week. Never more, whatever the fixture list does.
- Last Call: 6 hours before that gameweek's own first match. Not the deadline,
  not the first match of the calendar week.
- Damage Report: the day the gameweek concludes, 30 minutes after lockdown.
  A Sunday finish recaps Monday, a Monday finish Tuesday. No fixed weekday.
- Never post a recap before lockdown. Bonus and defensive contribution points
  can still move until 9am UK the day after the last match.
- Everything lands between 12pm and 10.30pm MYT.

## Editorial rules

- English and Malay mixed, the way the group actually talks.
- **No prize language.** There are no prizes. The top weekly score is
  "Raja minggu ni", never "winner".
- Banter is the point of the recap, so name people. "Wooden spoon minggu ni"
  stays.
- WhatsApp formatting only: `*bold*` and `_italic_`. No markdown headers, no
  bullet characters WhatsApp will not render.
- **Under about 700 characters**, or WhatsApp folds it behind "Read more". The
  recap sits near that ceiling, so anything added has to displace something.
- The story section carries at most three lines, never two about the same
  manager, never two from the same family. Group lines are the exception.
- Never claim a price change will happen. The price watch is a watchlist.
- Every detector gets at least six phrasings. One template per detector is what
  made the posts feel identical in the first place.

## Key decisions

**Generate automatically, send manually.** A fully automated bot was rejected:
Meta's Groups API caps groups at 8 participants and only works on groups a
business created, so the only route into an existing group is an unofficial
linked-device session that risks a ban on the personal number. One tap is worth
more than the risk.

**Recap plus deadline-day Last Call**, replacing an early plan for two
pre-deadline posts. Two information posts a day apart repeat each other. The
recap is the one that draws replies, because it names people.

**Last Call anchors to first kickoff, not the deadline.** Several deadlines land
at 1.30am or 2am MYT, useless as an anchor for a Malaysian group.

**Six hours of lead**, revised down from eight. At six the 12pm to 10.30pm
window never has to intervene and every last call sits exactly six hours out.

**Recap fires on conclusion day, not a fixed Tuesday.** Waiting for a set
weekday only made the scores staler.

**Relative thresholds, recency penalties and families**, added after six weeks
when the posts started sounding the same. Three detectors out of eight were
firing almost every week, because their bars were absolute and 27 managers clear
an absolute bar by chance. Replaying GW4 to GW8 through the new engine gave nine
distinct detectors across ten slots, against three before.

**A short section beats a stale one.** Anything scoring under 45 after penalties
is dropped. GW5 and GW7 come out at two lines rather than three, and that is
correct.

**Season records are not detectors.** A new high or low is appended to the Raja
and wooden spoon lines, so the same fact is never said twice.

**Streaks require consecutive gameweeks.** With GW6 missing from the cache, GW5
and GW7 are not "berturut" and must not be reported as such.

**Reminders come from the calendar feed**, not push. Web push was considered and
rejected: GitHub Pages cannot send pushes, and doing it from the job would add a
service worker and key management to a system with almost no moving parts.

**The repo is public** because GitHub Pages requires a paid plan for private
repos. The page shows only names already visible on the public FPL league page.
This is a deliberate exception to the general preference for private repos.

**Notes live here, not on a personal computer.** The running system never needed
one, but the notes used to sit in a local vault folder that had to be awake.
Keeping them beside the code means they cannot drift from it.

## Review point

Judge the whole thing by engagement, not by output. If the recap is not drawing
replies, the problem was never the automation and posting more will not fix it.
