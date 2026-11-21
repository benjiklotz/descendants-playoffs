#!/usr/bin/env python3
"""Build league.json for the Descendants of Travel playoff calculator from ESPN Fantasy.

ESPN's fantasy API has no browser access (no CORS), so this script runs on a schedule in GitHub Actions and
writes everything the page needs into one static file: teams and divisions, every week's results, the current
week's lineups with live per-player points, projected points for each remaining week (in league scoring, straight
from ESPN), NFL kickoff times, and how far actual scores have landed from projections so far this season.

Private league: set repository secrets ESPN_S2 and SWID (the two cookies from a logged-in espn.com session).
Standard library only. Run: python build_league.py [league_id] [season]
"""
import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

LEAGUE_ID = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LEAGUE_ID")) or "1591268402"
SEASON = (sys.argv[2] if len(sys.argv) > 2 else os.environ.get("SEASON")) or str(datetime.now(timezone.utc).year)
OUT = os.environ.get("OUT", "league.json")
BASE = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36", "Accept": "application/json"}
COOKIE = "; ".join(f"{k}={v}" for k, v in (("espn_s2", os.environ.get("ESPN_S2", "")), ("SWID", os.environ.get("SWID", ""))) if v)

PRO = {1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB", 28: "WAS", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU"}
POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST", 9: "DT", 10: "DE", 11: "LB", 12: "CB", 13: "S", 7: "P", 14: "HC"}
SLOT_NAME = {0: "QB", 1: "TQB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE", 7: "OP", 8: "DT", 9: "DE", 10: "LB", 11: "DL", 12: "CB", 13: "S", 14: "DB", 15: "DP", 16: "D/ST", 17: "K", 18: "P", 19: "HC", 23: "FLEX", 24: "ER"}
BENCH = {20, 21}  # bench, IR
FLEXY = {3, 5, 7, 23, 11, 14, 15}
ESPN_TO_STD = {"WSH": "WAS"}


def get(url, retries=3, timeout=60):
    last = None
    for i in range(retries):
        try:
            h = dict(UA)
            if COOKIE:
                h["Cookie"] = COOKIE
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    print(f"warn: {url} failed: {last}", file=sys.stderr)
    return None


def league_get(views, period=None):
    q = "&".join(f"view={v}" for v in views)
    if period is not None:
        q += f"&scoringPeriodId={period}"
    return get(f"{BASE}?{q}")


def stat_total(player, period, source):
    """appliedTotal for one scoring period: source 0 actual, 1 projected."""
    for st in player.get("stats") or []:
        if st.get("scoringPeriodId") == period and st.get("statSourceId") == source and st.get("statSplitTypeId", 1) == 1:
            return float(st.get("appliedTotal") or 0.0)
    return None


def fetch_kickoffs(season, week):
    url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?seasontype=2&week={week}&dates={season}"
    data = get(url, retries=2)
    out = {}
    if isinstance(data, dict):
        for ev in data.get("events") or []:
            when = ev.get("date")
            for comp in ev.get("competitions") or []:
                for c in comp.get("competitors") or []:
                    ab = ((c.get("team") or {}).get("abbreviation") or "").upper()
                    if ab and when:
                        out[ESPN_TO_STD.get(ab, ab)] = when
    return out


def main():
    root = league_get(["mSettings", "mTeam", "mStatus"])
    if not root or "settings" not in root:
        sys.exit("could not load the league from ESPN (private league? set ESPN_S2 and SWID secrets; also check the league id and season)")
    settings = root["settings"]
    status = root.get("status") or {}
    name = settings.get("name") or "League"
    sched = settings.get("scheduleSettings") or {}
    last_reg = int(sched.get("matchupPeriodCount") or 14)
    divisions = {str(d["id"]): d.get("name") or f"Division {d['id']}" for d in (sched.get("divisions") or [])}
    cur = int(status.get("currentMatchupPeriod") or status.get("latestScoringPeriod") or 1)
    scoring_period = int(status.get("latestScoringPeriod") or cur)
    if str(status.get("currentMatchupPeriod")) and status.get("currentMatchupPeriod", 0) > last_reg:
        cur = last_reg + 1
    print(f"{name}: season {SEASON}, matchup period {cur}, regular season {last_reg} weeks, divisions {divisions}")

    members = {m["id"]: m for m in (root.get("members") or [])}
    teams = []
    for t in root.get("teams") or []:
        tname = t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip() or f"Team {t['id']}"
        owners = []
        for oid in t.get("owners") or []:
            m = members.get(oid) or {}
            disp = m.get("displayName") or f"{m.get('firstName', '')} {m.get('lastName', '')}".strip() or str(oid)
            owners.append({"id": oid, "name": disp})
        teams.append({"id": t["id"], "name": tname, "abbrev": t.get("abbrev") or "", "div": str(t.get("divisionId", 0)), "owners": owners, "players": []})
    teams.sort(key=lambda x: x["id"])
    if not divisions:
        divisions = {d: f"Division {d}" for d in sorted({t["div"] for t in teams})}

    # lineup slots, expanded by count, specific slots before flex ones
    counts = (settings.get("rosterSettings") or {}).get("lineupSlotCounts") or {}
    slot_ids = []
    for sid, n in counts.items():
        sid = int(sid)
        if sid in BENCH or n <= 0:
            continue
        slot_ids += [sid] * int(n)
    slot_ids.sort(key=lambda s: (1 if s in FLEXY else 0, s))
    slots = [f"S{s}" for s in slot_ids]

    players, proj, kickoffs, weeks = {}, {}, {}, []
    residuals = []
    for week in range(1, last_reg + 1):
        data = league_get(["mMatchup", "mMatchupScore", "mRoster"], period=week)
        if not data:
            print(f"week {week}: no data", file=sys.stderr)
            continue
        games, scores, lineups = [], {}, {}
        wk_proj = {}
        for m in data.get("schedule") or []:
            if m.get("matchupPeriodId") != week:
                continue
            sides = []
            for side in ("home", "away"):
                sd = m.get(side)
                if not sd or sd.get("teamId") is None:
                    continue
                tid = sd["teamId"]
                pts = float(sd.get("totalPoints") or 0.0)
                live = sd.get("totalPointsLive")
                if week == scoring_period and live is not None:
                    pts = float(live)
                roster = (sd.get("rosterForCurrentScoringPeriod") or sd.get("rosterForMatchupPeriod") or {}).get("entries") or []
                starters, ppts, p_sum, a_sum = [], {}, 0.0, 0.0
                for en in roster:
                    pe = en.get("playerPoolEntry") or {}
                    pl = pe.get("player") or {}
                    pid = str(pe.get("id") or pl.get("id") or en.get("playerId"))
                    if pid not in players:
                        players[pid] = {"name": pl.get("fullName") or pid, "pos": POS.get(pl.get("defaultPositionId"), ""), "fp": [f"S{s}" for s in (pl.get("eligibleSlots") or []) if s not in BENCH], "team": PRO.get(pl.get("proTeamId"), "")}
                    pr = stat_total(pl, week, 1)
                    ac = stat_total(pl, week, 0)
                    if pr is not None:
                        wk_proj[pid] = round(pr, 2)
                    if en.get("lineupSlotId") not in BENCH:
                        starters.append(pid)
                        if ac is not None:
                            ppts[pid] = round(ac, 2)
                            a_sum += ac
                        if pr is not None:
                            p_sum += pr
                sides.append(tid)
                scores[str(tid)] = round(pts, 2)
                lineups[str(tid)] = {"starters": starters, "pts": ppts}
                if week < cur and starters and p_sum > 0:
                    residuals.append(pts - p_sum)
            if len(sides) == 2:
                games.append({"a": sides[0], "b": sides[1], "pa": scores[str(sides[0])], "pb": scores[str(sides[1])]})
        # current rosters for optimal lineups (from the live week's data)
        if week == scoring_period or (week == cur and not any(t["players"] for t in teams)):
            for t in data.get("teams") or []:
                tt = next((x for x in teams if x["id"] == t["id"]), None)
                if tt is None:
                    continue
                ids = []
                for en in ((t.get("roster") or {}).get("entries") or []):
                    pe = en.get("playerPoolEntry") or {}
                    pl = pe.get("player") or {}
                    pid = str(pe.get("id") or pl.get("id") or en.get("playerId"))
                    ids.append(pid)
                    if pid not in players:
                        players[pid] = {"name": pl.get("fullName") or pid, "pos": POS.get(pl.get("defaultPositionId"), ""), "fp": [f"S{s}" for s in (pl.get("eligibleSlots") or []) if s not in BENCH], "team": PRO.get(pl.get("proTeamId"), "")}
                tt["players"] = ids
        if week >= cur:
            # projections for this week for every rostered player (future weeks: from the roster view at that period)
            for t in data.get("teams") or []:
                for en in ((t.get("roster") or {}).get("entries") or []):
                    pe = en.get("playerPoolEntry") or {}
                    pl = pe.get("player") or {}
                    pid = str(pe.get("id") or pl.get("id") or en.get("playerId"))
                    if pid not in players:
                        players[pid] = {"name": pl.get("fullName") or pid, "pos": POS.get(pl.get("defaultPositionId"), ""), "fp": [f"S{s}" for s in (pl.get("eligibleSlots") or []) if s not in BENCH], "team": PRO.get(pl.get("proTeamId"), "")}
                    pr = stat_total(pl, week, 1)
                    if pr is not None:
                        wk_proj[pid] = round(pr, 2)
            if wk_proj:
                proj[str(week)] = wk_proj
            kick = fetch_kickoffs(SEASON, week)
            if kick:
                kickoffs[str(week)] = kick
        weeks.append({"week": week, "locked": week < cur, "games": games, "scores": scores, "lineups": lineups})
        print(f"week {week}: {len(games)} games, {len(wk_proj)} projections, {'locked' if week < cur else 'open'}")
        time.sleep(0.3)

    k, prior = 6, 20.0
    sigma = math.sqrt((sum(r * r for r in residuals) + k * prior * prior) / (len(residuals) + k))
    bias = (sum(residuals) / len(residuals)) if residuals else 0.0
    out = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "espn", "league_id": str(LEAGUE_ID), "season": str(SEASON), "name": name,
        "cur": cur, "lastReg": last_reg, "divisions": divisions, "slots": slots,
        "teams": teams, "weeks": weeks, "players": players, "proj": proj, "kickoffs": kickoffs,
        "sigma": round(sigma, 2), "bias": round(bias, 2),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"wrote {OUT}: {len(teams)} teams, {len(weeks)} weeks, projections for weeks {sorted(proj, key=int)}, sigma {sigma:.1f} (n={len(residuals)})")
    if not weeks or not teams:
        sys.exit("league data incomplete")


if __name__ == "__main__":
    main()
