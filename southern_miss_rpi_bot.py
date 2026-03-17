#!/usr/bin/env python3
"""
Southern Miss RPI Bot - Full Edition
=====================================
Daily workflow:
1. Fetch Southern Miss schedule, team sheet, and impact pages from Warren Nolan.
2. Parse current RPI / SOS / record / recent games / impact-game context.
3. Save a dated snapshot to SQLite.
4. Compare today's snapshot to the latest prior snapshot.
5. Collect rival team snapshots (Ole Miss, Mississippi State, etc.)
6. Produce a deterministic evidence bundle.
7. Generate GPT-powered narrative with situational tone.
8. Render a polished HTML dashboard with rank trend chart.
9. Fire Windows toast alert if rank drops 3+ spots.
10. Auto-open dashboard in browser.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------
BASE = "https://www.warrennolan.com/baseball/2026"
SCHEDULE_URL   = f"{BASE}/schedule/Southern-Miss"
TEAM_SHEET_URL = f"{BASE}/team-sheet?team=Southern-Miss"
IMPACT_URL     = f"{BASE}/team-impact?team=Southern-Miss"
RANK_URL       = f"{BASE}/team-rank?team=Southern-Miss"
RPI_LIVE_URL   = f"{BASE}/rpi-live"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Rival teams to track alongside Southern Miss
# ---------------------------------------------------------------------------
RIVAL_TEAMS: Dict[str, str] = {
    "Ole Miss":          "Ole-Miss",
    "Mississippi State": "Mississippi-State",
    "Coastal Carolina":  "Coastal-Carolina",
    "LSU":               "LSU",
    "Arkansas State":    "Arkansas-State",
    "UC Santa Barbara":  "UC-Santa-Barbara",
}

# ---------------------------------------------------------------------------
# Conference lookup table
# ---------------------------------------------------------------------------
CONFERENCE_MAP: Dict[str, str] = {
    # Sun Belt
    "Southern Miss": "Sun Belt", "Louisiana": "Sun Belt", "Troy": "Sun Belt",
    "Arkansas State": "Sun Belt", "South Alabama": "Sun Belt",
    "Louisiana Monroe": "Sun Belt", "ULM": "Sun Belt",
    "App State": "Sun Belt", "Appalachian State": "Sun Belt",
    "Georgia State": "Sun Belt", "Georgia Southern": "Sun Belt",
    "Old Dominion": "Sun Belt", "Marshall": "Sun Belt",
    "James Madison": "Sun Belt", "Texas State": "Sun Belt",
    "Louisiana Tech": "Sun Belt", "Coastal Carolina": "Sun Belt",
    # SEC
    "Ole Miss": "SEC", "Mississippi State": "SEC", "LSU": "SEC",
    "Alabama": "SEC", "Auburn": "SEC", "Tennessee": "SEC",
    "Arkansas": "SEC", "Florida": "SEC", "Georgia": "SEC",
    "Kentucky": "SEC", "Missouri": "SEC", "South Carolina": "SEC",
    "Texas A&M": "SEC", "Vanderbilt": "SEC", "Oklahoma": "SEC",
    "Texas": "SEC",
    # ACC
    "Florida State": "ACC", "Clemson": "ACC", "NC State": "ACC",
    "Virginia": "ACC", "Virginia Tech": "ACC", "Wake Forest": "ACC",
    "Duke": "ACC", "Miami": "ACC", "North Carolina": "ACC",
    "Notre Dame": "ACC", "Pittsburgh": "ACC", "Louisville": "ACC",
    "Georgia Tech": "ACC", "Boston College": "ACC", "Syracuse": "ACC",
    "Stanford": "ACC", "Cal": "ACC", "SMU": "ACC",
    # Big 12
    "Texas Tech": "Big 12", "Oklahoma State": "Big 12", "TCU": "Big 12",
    "Baylor": "Big 12", "Kansas State": "Big 12", "Kansas": "Big 12",
    "West Virginia": "Big 12", "Iowa State": "Big 12", "BYU": "Big 12",
    "UCF": "Big 12", "Cincinnati": "Big 12", "Houston": "Big 12",
    "Arizona": "Big 12", "Arizona State": "Big 12", "Utah": "Big 12",
    "Colorado": "Big 12",
    # Big Ten
    "Michigan": "Big Ten", "Ohio State": "Big Ten", "Indiana": "Big Ten",
    "Maryland": "Big Ten", "Minnesota": "Big Ten", "Nebraska": "Big Ten",
    "Northwestern": "Big Ten", "Penn State": "Big Ten", "Rutgers": "Big Ten",
    "Illinois": "Big Ten", "Iowa": "Big Ten", "Michigan State": "Big Ten",
    "Purdue": "Big Ten", "Wisconsin": "Big Ten",
    # AAC
    "East Carolina": "AAC", "Memphis": "AAC", "Tulane": "AAC",
    "Wichita State": "AAC", "South Florida": "AAC", "Temple": "AAC",
    "Tulsa": "AAC", "Navy": "AAC", "Army": "AAC", "Rice": "AAC",
    "Charlotte": "AAC", "UAB": "AAC", "UTSA": "AAC", "North Texas": "AAC",
    "Florida Atlantic": "AAC", "FIU": "AAC", "Middle Tennessee": "AAC",
    "Western Kentucky": "AAC", "UTEP": "AAC",
    # Big West
    "UC Santa Barbara": "Big West", "Cal Poly": "Big West",
    "Long Beach State": "Big West", "UC Irvine": "Big West",
    "UC Riverside": "Big West", "UC San Diego": "Big West",
    "Cal State Fullerton": "Big West", "Hawaii": "Big West",
    # Southland / SWAC
    "Nicholls": "Southland", "Nicholls State": "Southland",
    "Southern": "SWAC", "Grambling": "SWAC",
    "Jackson State": "SWAC", "Alcorn State": "SWAC",
}


def get_conference(team_name: str) -> str:
    if not team_name:
        return ""
    return CONFERENCE_MAP.get(team_name, "")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class TeamSnapshot:
    captured_at: str
    season: int
    team: str
    overall_record: Optional[str] = None
    home_record: Optional[str] = None
    road_record: Optional[str] = None
    neutral_record: Optional[str] = None
    conf_record: Optional[str] = None
    last_10: Optional[str] = None
    streak: Optional[str] = None
    rpi_rank: Optional[int] = None
    rpi_value: Optional[float] = None
    nc_rpi_rank: Optional[int] = None
    nc_rpi_value: Optional[float] = None
    sos_rank: Optional[int] = None
    sos_value: Optional[float] = None
    nc_sos_rank: Optional[int] = None
    nc_sos_value: Optional[float] = None
    q1: Optional[str] = None
    q2: Optional[str] = None
    q3: Optional[str] = None
    q4: Optional[str] = None
    impact_notes: List[str] = field(default_factory=list)
    recent_games: List[Dict[str, Any]] = field(default_factory=list)
    upcoming_games: List[Dict[str, Any]] = field(default_factory=list)
    source_urls: Dict[str, str] = field(default_factory=dict)


class RPIBotError(Exception):
    pass


# ---------------------------------------------------------------------------
# Main bot class
# ---------------------------------------------------------------------------
class SouthernMissRPIBot:
    def __init__(self, db_path: str, season: int = 2026, verbose: bool = False) -> None:
        self.db_path = db_path
        self.season = season
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._init_db()

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at TEXT NOT NULL,
                    season INTEGER NOT NULL,
                    team TEXT NOT NULL,
                    rpi_rank INTEGER,
                    rpi_value REAL,
                    sos_rank INTEGER,
                    sos_value REAL,
                    overall_record TEXT,
                    home_record TEXT,
                    road_record TEXT,
                    neutral_record TEXT,
                    conf_record TEXT,
                    last_10 TEXT,
                    streak TEXT,
                    q1 TEXT,
                    q2 TEXT,
                    q3 TEXT,
                    q4 TEXT,
                    impact_notes TEXT,
                    recent_games TEXT,
                    upcoming_games TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def save_snapshot(self, snap: TeamSnapshot) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO snapshots
                  (captured_at,season,team,rpi_rank,rpi_value,sos_rank,sos_value,
                   overall_record,home_record,road_record,neutral_record,conf_record,
                   last_10,streak,q1,q2,q3,q4,impact_notes,recent_games,upcoming_games)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    snap.captured_at, snap.season, snap.team,
                    snap.rpi_rank, snap.rpi_value, snap.sos_rank, snap.sos_value,
                    snap.overall_record, snap.home_record, snap.road_record,
                    snap.neutral_record, snap.conf_record, snap.last_10, snap.streak,
                    snap.q1, snap.q2, snap.q3, snap.q4,
                    json.dumps(snap.impact_notes),
                    json.dumps(snap.recent_games),
                    json.dumps(snap.upcoming_games),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_previous_snapshot(self) -> Optional[TeamSnapshot]:
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                """
                SELECT * FROM snapshots
                WHERE team = 'Southern Miss'
                ORDER BY captured_at DESC
                LIMIT 2
                """
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        finally:
            conn.close()

        if len(rows) < 2:
            return None
        row = rows[1]
        d = dict(zip(cols, row))
        snap = TeamSnapshot(
            captured_at=d["captured_at"],
            season=d["season"],
            team=d["team"],
            rpi_rank=d["rpi_rank"],
            rpi_value=d["rpi_value"],
            sos_rank=d["sos_rank"],
            sos_value=d["sos_value"],
            overall_record=d["overall_record"],
            conf_record=d["conf_record"],
            q1=d["q1"], q2=d["q2"], q3=d["q3"], q4=d["q4"],
            impact_notes=json.loads(d["impact_notes"] or "[]"),
            recent_games=json.loads(d["recent_games"] or "[]"),
            upcoming_games=json.loads(d["upcoming_games"] or "[]"),
        )
        return snap

    def get_rank_history(self, days: int = 45) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        try:
            cutoff = (dt.datetime.utcnow() - dt.timedelta(days=days)).strftime("%Y-%m-%d")
            cur = conn.execute(
                """
                SELECT captured_at, rpi_rank, rpi_value, overall_record
                FROM snapshots
                WHERE team='Southern Miss' AND captured_at >= ?
                ORDER BY captured_at ASC
                """,
                (cutoff,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()
        return [
            {"date": r[0][:10], "rpi_rank": r[1], "rpi_value": r[2], "overall_record": r[3]}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _get(self, url: str) -> BeautifulSoup:
        self._log(f"  GET {url}")
        r = self.session.get(url, timeout=20)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")

    # ------------------------------------------------------------------
    # Scraping
    # ------------------------------------------------------------------
    def _parse_record(self, text: str) -> Optional[str]:
        m = re.search(r"(\d+)-(\d+)", text)
        return m.group(0) if m else None

    def _scrape_team_sheet(self) -> Dict[str, Any]:
        soup = self._get(TEAM_SHEET_URL)
        data: Dict[str, Any] = {}
        for row in soup.select("table tr"):
            cells = [td.get_text(strip=True) for td in row.select("td")]
            if len(cells) < 2:
                continue
            key, val = cells[0].lower(), cells[1]
            if "overall" in key:
                data["overall_record"] = self._parse_record(val)
            elif "home" in key:
                data["home_record"] = self._parse_record(val)
            elif "road" in key or "away" in key:
                data["road_record"] = self._parse_record(val)
            elif "neutral" in key:
                data["neutral_record"] = self._parse_record(val)
            elif "conference" in key or "conf" in key:
                data["conf_record"] = self._parse_record(val)
            elif "last 10" in key or "last10" in key:
                data["last_10"] = self._parse_record(val) or val
            elif "streak" in key:
                data["streak"] = val
            elif "rpi rank" in key and "nc" not in key:
                try:
                    data["rpi_rank"] = int(re.sub(r"\D", "", val))
                except ValueError:
                    pass
            elif "rpi" in key and "value" in key and "nc" not in key:
                try:
                    data["rpi_value"] = float(val)
                except ValueError:
                    pass
            elif "sos rank" in key and "nc" not in key:
                try:
                    data["sos_rank"] = int(re.sub(r"\D", "", val))
                except ValueError:
                    pass
            elif "sos" in key and "value" in key and "nc" not in key:
                try:
                    data["sos_value"] = float(val)
                except ValueError:
                    pass
            elif key.startswith("q1"):
                data["q1"] = val
            elif key.startswith("q2"):
                data["q2"] = val
            elif key.startswith("q3"):
                data["q3"] = val
            elif key.startswith("q4"):
                data["q4"] = val
        return data

    def _scrape_rpi_live(self) -> Dict[str, Any]:
        soup = self._get(RPI_LIVE_URL)
        data: Dict[str, Any] = {}
        for row in soup.select("table tr"):
            cells = [td.get_text(strip=True) for td in row.select("td")]
            if len(cells) < 3:
                continue
            if "southern miss" in cells[1].lower() or (len(cells) > 2 and "southern miss" in cells[2].lower()):
                try:
                    data["rpi_rank"] = int(re.sub(r"\D", "", cells[0]))
                except (ValueError, IndexError):
                    pass
                for cell in cells:
                    m = re.search(r"0\.\d{4}", cell)
                    if m:
                        data["rpi_value"] = float(m.group())
                        break
                break
        return data

    def _scrape_schedule(self) -> Tuple[List[Dict], List[Dict]]:
        soup = self._get(SCHEDULE_URL)
        recent: List[Dict] = []
        upcoming: List[Dict] = []
        today = dt.date.today()

        for row in soup.select("table tr"):
            cells = [td.get_text(strip=True) for td in row.select("td")]
            if len(cells) < 3:
                continue
            date_str = cells[0] if cells else ""
            opponent = cells[1] if len(cells) > 1 else ""
            result_or_time = cells[2] if len(cells) > 2 else ""
            site = cells[3] if len(cells) > 3 else ""
            opp_rpi_str = cells[4] if len(cells) > 4 else ""

            try:
                game_date = dt.datetime.strptime(date_str, "%m/%d/%Y").date()
            except ValueError:
                try:
                    game_date = dt.datetime.strptime(date_str, "%m/%d").replace(year=today.year).date()
                except ValueError:
                    continue

            opp_rpi: Optional[int] = None
            m = re.search(r"\d+", opp_rpi_str)
            if m:
                opp_rpi = int(m.group())

            win_loss = None
            m2 = re.match(r"([WL])\s+(\d+-\d+)", result_or_time)
            if m2:
                win_loss = m2.group(1)

            if game_date < today:
                if win_loss:
                    recent.append({
                        "date": str(game_date),
                        "opponent": opponent,
                        "result": result_or_time,
                        "site": site,
                        "opp_rpi": opp_rpi,
                    })
            else:
                upcoming.append({
                    "date": str(game_date),
                    "opponent": opponent,
                    "time": result_or_time,
                    "site": site,
                    "opp_rpi": opp_rpi,
                })

        return recent[-10:], upcoming[:10]

    def _scrape_impact(self) -> List[str]:
        soup = self._get(IMPACT_URL)
        notes: List[str] = []
        for el in soup.select("p, li, .impact-note, .note"):
            t = el.get_text(strip=True)
            if t and len(t) > 20:
                notes.append(t)
        return notes[:10]

    def _scrape_rpi_radar(self) -> List[Dict]:
        soup = self._get(RPI_LIVE_URL)
        teams = []
        usm_rank = None
        for row in soup.select("table tr"):
            cells = [td.get_text(strip=True) for td in row.select("td")]
            if len(cells) < 2:
                continue
            try:
                rank = int(re.sub(r"\D", "", cells[0]))
            except (ValueError, IndexError):
                continue
            name = cells[1] if len(cells) > 1 else ""
            conf = cells[2] if len(cells) > 2 else ""
            if "southern miss" in name.lower():
                usm_rank = rank
            teams.append({"rank": rank, "name": name, "conf": conf})

        if usm_rank is None:
            return []

        radar = []
        for t in teams:
            if abs(t["rank"] - usm_rank) <= 4:
                radar.append(t)
        radar.sort(key=lambda x: x["rank"])
        return radar

    def _scrape_rival(self, slug: str) -> Dict[str, Any]:
        url = f"{BASE}/team-sheet?team={slug}"
        try:
            soup = self._get(url)
        except Exception:
            return {}
        data: Dict[str, Any] = {"slug": slug}
        for row in soup.select("table tr"):
            cells = [td.get_text(strip=True) for td in row.select("td")]
            if len(cells) < 2:
                continue
            key, val = cells[0].lower(), cells[1]
            if "overall" in key:
                data["overall_record"] = self._parse_record(val)
            elif "rpi rank" in key and "nc" not in key:
                try:
                    data["rpi_rank"] = int(re.sub(r"\D", "", val))
                except ValueError:
                    pass
            elif "conference" in key or "conf" in key:
                data["conf_record"] = self._parse_record(val)
        return data

    # ------------------------------------------------------------------
    # Snapshot collection
    # ------------------------------------------------------------------
    def collect_snapshot(self) -> TeamSnapshot:
        now = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        sheet = self._scrape_team_sheet()
        if not sheet.get("rpi_rank"):
            live = self._scrape_rpi_live()
            sheet.update({k: v for k, v in live.items() if v})
        recent, upcoming = self._scrape_schedule()
        impact = self._scrape_impact()

        return TeamSnapshot(
            captured_at=now,
            season=self.season,
            team="Southern Miss",
            overall_record=sheet.get("overall_record"),
            home_record=sheet.get("home_record"),
            road_record=sheet.get("road_record"),
            neutral_record=sheet.get("neutral_record"),
            conf_record=sheet.get("conf_record"),
            last_10=sheet.get("last_10"),
            streak=sheet.get("streak"),
            rpi_rank=sheet.get("rpi_rank"),
            rpi_value=sheet.get("rpi_value"),
            sos_rank=sheet.get("sos_rank"),
            sos_value=sheet.get("sos_value"),
            q1=sheet.get("q1"),
            q2=sheet.get("q2"),
            q3=sheet.get("q3"),
            q4=sheet.get("q4"),
            impact_notes=impact,
            recent_games=recent,
            upcoming_games=upcoming,
            source_urls={
                "schedule": SCHEDULE_URL,
                "team_sheet": TEAM_SHEET_URL,
                "impact": IMPACT_URL,
            },
        )

    def collect_rival_snapshots(self) -> List[Dict]:
        rivals = []
        for name, slug in RIVAL_TEAMS.items():
            d = self._scrape_rival(slug)
            if d:
                d["name"] = name
                conf = get_conference(name)
                if conf:
                    d["conf"] = conf
                rivals.append(d)
        return rivals

    # ------------------------------------------------------------------
    # Evidence bundle
    # ------------------------------------------------------------------
    def build_evidence(
        self,
        previous: Optional[TeamSnapshot],
        current: TeamSnapshot,
        rivals: List[Dict],
    ) -> Dict[str, Any]:
        rank_delta = None
        if previous and previous.rpi_rank and current.rpi_rank:
            rank_delta = previous.rpi_rank - current.rpi_rank

        def _q_wins(q: Optional[str]) -> int:
            if not q:
                return 0
            m = re.match(r"(\d+)", q)
            return int(m.group(1)) if m else 0

        drivers: List[str] = []
        if rank_delta is not None:
            if rank_delta > 0:
                drivers.append(f"RPI rank improved by {rank_delta} spot(s).")
            elif rank_delta < 0:
                drivers.append(f"RPI rank dropped by {abs(rank_delta)} spot(s).")
            else:
                drivers.append("RPI rank did not change from the prior snapshot.")

        if previous and current.q1 and previous.q1:
            prev_w = _q_wins(previous.q1)
            curr_w = _q_wins(current.q1)
            if curr_w > prev_w:
                drivers.append(f"Q1 wins increased from {prev_w} to {curr_w}.")

        radar = self._scrape_rpi_radar()

        upcoming_rpi_vals = [
            g["opp_rpi"] for g in current.upcoming_games if g.get("opp_rpi")
        ]
        avg_opp_rpi = (
            round(sum(upcoming_rpi_vals) / len(upcoming_rpi_vals))
            if upcoming_rpi_vals else None
        )
        schedule_note = ""
        if avg_opp_rpi:
            if avg_opp_rpi <= 30:
                schedule_note = f"Upcoming schedule is very tough -- avg opponent RPI {avg_opp_rpi}. High Q1/Q2 opportunity."
            elif avg_opp_rpi <= 75:
                schedule_note = f"Upcoming schedule is moderately challenging -- avg opponent RPI {avg_opp_rpi}. Some Q1/Q2 opportunities."
            else:
                schedule_note = f"Upcoming schedule is soft -- avg opponent RPI {avg_opp_rpi}. Limited Q1/Q2 opportunities."

        return {
            "date": dt.date.today().isoformat(),
            "team": current.team,
            "season": current.season,
            "current": dataclasses.asdict(current),
            "previous": dataclasses.asdict(previous) if previous else None,
            "rank_delta": rank_delta,
            "drivers": drivers,
            "rivals": rivals,
            "radar": radar,
            "schedule_note": schedule_note,
            "avg_upcoming_opp_rpi": avg_opp_rpi,
        }

    # ------------------------------------------------------------------
    # Alert
    # ------------------------------------------------------------------
    def check_and_alert(self, evidence: Dict, threshold: int = 3) -> None:
        delta = evidence.get("rank_delta")
        if delta is None or delta >= -threshold:
            return
        rank = evidence["current"].get("rpi_rank", "?")
        msg = f"Southern Miss RPI dropped {abs(delta)} spots to #{rank}!"
        try:
            subprocess.run(
                [
                    "powershell", "-Command",
                    f"[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null; "
                    f"$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText01; "
                    f"$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template); "
                    f"$xml.GetElementsByTagName('text')[0].AppendChild($xml.CreateTextNode('{msg}')) | Out-Null; "
                    f"$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
                    f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('RPI Bot').Show($toast)",
                ],
                timeout=10,
                capture_output=True,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Week review
    # ------------------------------------------------------------------
    def build_week_review(self) -> str:
        conn = sqlite3.connect(self.db_path)
        try:
            cutoff = (dt.datetime.utcnow() - dt.timedelta(days=7)).strftime("%Y-%m-%d")
            cur = conn.execute(
                """
                SELECT captured_at, rpi_rank, rpi_value, sos_rank, overall_record
                FROM snapshots
                WHERE team='Southern Miss' AND captured_at >= ?
                ORDER BY captured_at ASC
                """,
                (cutoff,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        if not rows:
            return "No data for the past 7 days."

        first, last = rows[0], rows[-1]
        start_date = first[0][:10]
        end_date = last[0][:10]

        rank_start, rank_end = first[1], last[1]
        rpi_start, rpi_end = first[2], last[2]
        sos_start, sos_end = first[3], last[3]
        rec_start, rec_end = first[4], last[4]

        def _w(r):
            if not r: return 0
            m = re.match(r"(\d+)", r); return int(m.group(1)) if m else 0
        def _l(r):
            if not r: return 0
            m = re.search(r"-(\d+)", r); return int(m.group(1)) if m else 0

        dw = _w(rec_end) - _w(rec_start)
        dl = _l(rec_end) - _l(rec_start)
        rank_arrow = rank_end - rank_start if (rank_start and rank_end) else 0
        rpi_arrow = round(rpi_end - rpi_start, 4) if (rpi_start and rpi_end) else 0
        sos_arrow = (sos_end - sos_start) if (sos_start and sos_end) else 0

        lines = [
            f"Week in Review ({start_date} to {end_date})",
            f" Rank: {rank_start} -> {rank_end} ({rank_arrow:+d})",
            f" Record: {rec_start} -> {rec_end} (+{dw}W / +{dl}L)",
            f" RPI Value: {rpi_start} -> {rpi_end}",
            f" SOS Rank: {sos_start} -> {sos_end}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Plain text summary
    # ------------------------------------------------------------------
    def render_plain_summary(self, evidence: Dict) -> str:
        cur = evidence["current"]
        rank = cur.get("rpi_rank", "?")
        record = cur.get("overall_record", "?")
        rpi_val = cur.get("rpi_value", "?")
        sos = cur.get("sos_rank", "?")
        delta = evidence.get("rank_delta")
        delta_str = f" ({delta:+d})" if delta is not None else ""

        lines = [
            f"=== Southern Miss RPI Update {evidence['date']} ===",
            f"RPI Rank : #{rank}{delta_str}",
            f"Record   : {record}",
            f"RPI Value: {rpi_val}",
            f"SOS Rank : #{sos}",
            "",
        ]
        if evidence.get("drivers"):
            lines.append("Why It Moved:")
            for d in evidence["drivers"]:
                lines.append(f"  • {d}")
            lines.append("")

        if evidence.get("schedule_note"):
            lines.append(evidence["schedule_note"])
            lines.append("")

        upcoming = cur.get("upcoming_games", [])
        if upcoming:
            lines.append("Upcoming:")
            for g in upcoming[:5]:
                opp = g.get("opponent", "?")
                site = g.get("site", "")
                orpi = g.get("opp_rpi")
                t = g.get("time", "")
                orpi_str = f"  Opp RPI #{orpi}" if orpi else ""
                lines.append(f"  {opp} ({site}){orpi_str}  {t}")
            lines.append("")

        recent = cur.get("recent_games", [])
        if recent:
            lines.append("Recent Results:")
            for g in recent[-5:]:
                opp = g.get("opponent", "?")
                res = g.get("result", "?")
                orpi = g.get("opp_rpi")
                orpi_str = f"  Opp RPI #{orpi}" if orpi else ""
                lines.append(f"  {res}  vs {opp}{orpi_str}")
            lines.append("")

        summary = "\n".join(lines)
        try:
            Path("daily_brief.txt").write_text(summary, encoding="utf-8")
        except Exception:
            pass
        return summary

    # ------------------------------------------------------------------
    # LLM narrative
    # ------------------------------------------------------------------
    def render_llm_summary(self, evidence: Dict) -> Optional[str]:
        try:
            import openai
        except ImportError:
            print("openai package not installed.", file=sys.stderr)
            return None

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("OPENAI_API_KEY not set.", file=sys.stderr)
            return None

        client = openai.OpenAI(api_key=api_key)
        cur = evidence["current"]
        rank = cur.get("rpi_rank", "?")
        record = cur.get("overall_record", "?")
        rpi_val = cur.get("rpi_value", "?")
        sos_rank = cur.get("sos_rank", "?")
        delta = evidence.get("rank_delta")
        drivers = evidence.get("drivers", [])
        schedule_note = evidence.get("schedule_note", "")
        avg_opp = evidence.get("avg_upcoming_opp_rpi")
        q1 = cur.get("q1", "?")
        q2 = cur.get("q2", "?")
        conf = cur.get("conf_record", "?")
        recent = cur.get("recent_games", [])[-5:]
        upcoming = cur.get("upcoming_games", [])[:5]

        recent_str = "; ".join(
            f"{g.get('result','?')} vs {g.get('opponent','?')} (RPI #{g.get('opp_rpi','?')})"
            for g in recent
        ) or "None"
        upcoming_str = "; ".join(
            f"{g.get('opponent','?')} ({g.get('site','?')}, RPI #{g.get('opp_rpi','?')})"
            for g in upcoming
        ) or "None"

        delta_str = f"{delta:+d}" if delta is not None else "unknown"
        drivers_str = " ".join(drivers) if drivers else "No specific drivers noted."

        prompt = f"""You are the Southern Miss Baseball RPI analyst bot. Write a concise, sharp 3-paragraph narrative for today's daily dashboard.

Data:
- Date: {evidence['date']}
- RPI Rank: #{rank} (change: {delta_str})
- Record: {record} (Conf: {conf})
- RPI Value: {rpi_val}
- SOS Rank: #{sos_rank}
- Q1: {q1}  Q2: {q2}
- Recent: {recent_str}
- Upcoming: {upcoming_str}
- Schedule context: {schedule_note}
- Avg upcoming opp RPI: {avg_opp}
- Drivers: {drivers_str}

Tone: Knowledgeable, direct, like a beat reporter who actually understands RPI math. Reference specific opponents and results. End with a forward-looking statement about what the next week means for tournament positioning. Do not use bullet points — write in flowing paragraphs."""

        try:
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
