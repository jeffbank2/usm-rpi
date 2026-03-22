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

SUN_BELT_SLUGS: Dict[str, str] = {
    "Southern Miss":    "Southern-Miss",
    "Louisiana":        "Louisiana",
    "Troy":             "Troy",
    "Arkansas State":   "Arkansas-State",
    "South Alabama":    "South-Alabama",
    "Louisiana Monroe": "Louisiana-Monroe",
    "App State":        "App-State",
    "Georgia State":    "Georgia-State",
    "Georgia Southern": "Georgia-Southern",
    "Old Dominion":     "Old-Dominion",
    "Marshall":         "Marshall",
    "James Madison":    "James-Madison",
    "Texas State":      "Texas-State",
    "Louisiana Tech":   "Louisiana-Tech",
    "Coastal Carolina": "Coastal-Carolina",
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
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------
    def fetch_text(self, url: str) -> str:
        self._log(f"Fetching {url}")
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.text

    @staticmethod
    def html_to_text(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)
        text = re.sub(r"\n{2,}", "\n", text)
        return text

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_simple_value(text: str, label: str) -> Optional[str]:
        pattern = re.escape(label) + r"\s+([^\n]+)"
        m = re.search(pattern, text, flags=re.IGNORECASE)
        return m.group(1).strip() if m else None

    @staticmethod
    def _extract_rank_and_value(text: str, label: str) -> Tuple[Optional[int], Optional[float]]:
        pattern = re.escape(label) + r"\s+(\d+)\s+\(([0-9.]+)\)"
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            return None, None
        return int(m.group(1)), float(m.group(2))

    @staticmethod
    def _extract_team_sheet_rpi(text: str) -> Optional[int]:
        m = re.search(r"RPI\s+(\d+)\s+Southern Miss", text, flags=re.IGNORECASE)
        return int(m.group(1)) if m else None

    @staticmethod
    def _extract_quadrant_record(text: str, quadrant: int) -> Optional[str]:
        # Primary: match the table row Warren Nolan uses:
        # "QUADRANT 1\nQ1\n10-3\n..." -- first W-L after Q{n} is the overall record
        pattern = rf"QUADRANT\s+{quadrant}\s+Q{quadrant}\s+(\d+-\d+)"
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return m.group(1)
        # Fallback: older "overall W-L" format
        pattern2 = rf"Quadrant\s+{quadrant}[^\n]*\n.*?overall\s+(\d+-\d+)"
        m2 = re.search(pattern2, text, flags=re.IGNORECASE | re.DOTALL)
        return m2.group(1) if m2 else None

    @staticmethod
    def _extract_game_blocks(text: str) -> List[Dict[str, Any]]:
        months = "JAN|FEB|MAR|APR|MAY|JUN"
        starts = list(re.finditer(rf"\b(?:{months})\s+\d{{1,2}}\s+(?:MON|TUE|WED|THU|FRI|SAT|SUN)\b", text))
        games: List[Dict[str, Any]] = []

        def clean_team_name(val: Optional[str]) -> Optional[str]:
            if not val:
                return None
            val = val.strip()
            val = re.sub(r"^[@vVsS. ]+", "", val).strip()
            val = re.sub(r"\s+", " ", val)
            if not val:
                return None
            if re.search(r"RPI:|Opponent RPI:|\b\d{1,2}:\d{2}\s+[AP]M\b|^[WL]\s+\d+\s+-\s+\d+$", val, re.I):
                return None
            if re.match(r"^\(?\d+-\d+\)?$", val):
                return None
            if re.match(rf"^(?:{months})\b", val):
                return None
            if val.upper() in {"AT", "VS", "HOME", "AWAY", "NEUTRAL", "FINAL", "LIVE", "PREVIEW"}:
                return None
            if re.match(r'^#\s*\d+$', val.strip()):
                return None
            return val

        for idx, start_match in enumerate(starts):
            end_pos = starts[idx + 1].start() if idx + 1 < len(starts) else len(text)
            block = text[start_match.start():end_pos]
            lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
            if len(lines) < 4:
                continue

            date_str = f"{lines[0]} {lines[1]} {lines[2]}" if len(lines) >= 3 else lines[0]
            opponent = None
            location_type = "HOME"
            result = None
            score = None
            opp_rpi = None

            if "AT" in lines[:8]:
                location_type = "AWAY"
            elif "VS" in lines[:8]:
                location_type = "NEUTRAL"

            # Primary: line before OR 2 lines before '(record) RPI: nnn'
            for i, line in enumerate(lines[1:15], start=1):
                if re.match(r"^\(\d+-\d+\)\s+RPI:\s+\d+", line):
                    for offset in (-1, -2):
                        idx2 = i + offset
                        if idx2 >= 0:
                            candidate = clean_team_name(lines[idx2])
                            if candidate and candidate.lower() not in {
                                "southern miss", "golden eagles", "upcoming opponent",
                                "home", "away", "neutral", "at", "vs",
                            }:
                                opponent = candidate
                                break
                    m = re.search(r"RPI:\s+(\d+)", line)
                    if m:
                        opp_rpi = int(m.group(1))
                    break

            # Secondary: after AT/VS
            if opponent is None:
                for i, line in enumerate(lines[1:15], start=1):
                    if line in {"AT", "VS"} and i + 1 < len(lines):
                        candidate = clean_team_name(lines[i + 1])
                        if candidate and candidate.lower() not in {"southern miss", "golden eagles", "upcoming opponent"}:
                            opponent = candidate
                            break

            # Tertiary: name before bare record line
            if opponent is None:
                for i, line in enumerate(lines[1:15], start=1):
                    if re.match(r"^\(?\d+-\d+\)?$", line):
                        for offset in (-1, -2):
                            idx2 = i + offset
                            if idx2 >= 1:
                                candidate = clean_team_name(lines[idx2])
                                if candidate and candidate.lower() not in {
                                    "southern miss", "golden eagles", "upcoming opponent",
                                    "home", "away", "neutral", "at", "vs",
                                }:
                                    opponent = candidate
                                    break
                        if opponent:
                            break

            # Home fallback
            if opponent is None:
                for line in lines[1:15]:
                    candidate = clean_team_name(line)
                    if not candidate:
                        continue
                    if candidate.lower() in {"southern miss", "golden eagles", "baseball", "upcoming opponent"}:
                        continue
                    if re.search(r"conference|stadium|field|impact games|rpi points", candidate, re.I):
                        continue
                    opponent = candidate
                    break

            score_match = re.search(r"\b([WL])\s+(\d+)\s+-\s+(\d+)\b", block)
            if score_match:
                result = score_match.group(1)
                score = f"{score_match.group(2)}-{score_match.group(3)}"
            else:
                time_match = re.search(r"\b\d{1,2}:\d{2}\s+[AP]M\b", block)
                if time_match:
                    result = "UPCOMING"
                    score = time_match.group(0)

            if opp_rpi is None:
                m = re.search(r"Opponent RPI:\s+(\d+)", block)
                if m:
                    opp_rpi = int(m.group(1))

            games.append({
                "raw_block": block[:1500],
                "date_label": date_str,
                "opponent": opponent,
                "location_type": location_type,
                "result": result,
                "score_or_time": score,
                "opponent_rpi": opp_rpi,
            })
        return games

    @staticmethod
    def _extract_impact_notes(text: str) -> List[str]:
        notes: List[str] = []
        if "Direct Impact Games" in text:
            notes.append("Impact page includes direct games against Southern Miss and indirect games involving prior opponents.")
        return notes[:6]

    # ------------------------------------------------------------------
    # Snapshot collection
    # ------------------------------------------------------------------
    def collect_snapshot(self, team_slug: str = "Southern-Miss", team_name: str = "Southern Miss") -> TeamSnapshot:
        schedule_html   = self.fetch_text(f"{BASE}/schedule/{team_slug}")
        team_sheet_html = self.fetch_text(f"{BASE}/team-sheet?team={team_slug}")
        impact_html     = self.fetch_text(f"{BASE}/team-impact?team={team_slug}")

        schedule_text   = self.html_to_text(schedule_html)
        team_sheet_text = self.html_to_text(team_sheet_html)
        impact_text     = self.html_to_text(impact_html)

        games          = self._extract_game_blocks(schedule_text)
        recent_games   = [g for g in games if g["result"] in {"W", "L"}][-7:]
        upcoming_games = [g for g in games if g["result"] == "UPCOMING"][:5]

        rpi_rank, rpi_value       = self._extract_rank_and_value(schedule_text, "RPI")
        nc_rpi_rank, nc_rpi_value = self._extract_rank_and_value(schedule_text, "Non-Conference RPI")
        sos_rank, sos_value       = self._extract_rank_and_value(schedule_text, "SOS")
        nc_sos_rank, nc_sos_value = self._extract_rank_and_value(schedule_text, "Non-Conference SOS")

        if rpi_rank is None:
            rpi_rank = self._extract_team_sheet_rpi(team_sheet_text)

        return TeamSnapshot(
            captured_at=dt.datetime.now().isoformat(timespec="seconds"),
            season=self.season,
            team=team_name,
            overall_record=self._extract_simple_value(schedule_text, "Overall"),
            home_record=self._extract_simple_value(schedule_text, "Home"),
            road_record=self._extract_simple_value(schedule_text, "Road"),
            neutral_record=self._extract_simple_value(schedule_text, "Neutral"),
            conf_record=self._extract_simple_value(schedule_text, "Conf"),
            last_10=self._extract_simple_value(schedule_text, "Last 10"),
            streak=self._extract_simple_value(schedule_text, "Streak"),
            rpi_rank=rpi_rank,
            rpi_value=rpi_value,
            nc_rpi_rank=nc_rpi_rank,
            nc_rpi_value=nc_rpi_value,
            sos_rank=sos_rank,
            sos_value=sos_value,
            nc_sos_rank=nc_sos_rank,
            nc_sos_value=nc_sos_value,
            q1=self._extract_quadrant_record(team_sheet_text, 1),
            q2=self._extract_quadrant_record(team_sheet_text, 2),
            q3=self._extract_quadrant_record(team_sheet_text, 3),
            q4=self._extract_quadrant_record(team_sheet_text, 4),
            impact_notes=self._extract_impact_notes(impact_text),
            recent_games=recent_games,
            upcoming_games=upcoming_games,
            source_urls={
                "schedule":   f"{BASE}/schedule/{team_slug}",
                "team_sheet": f"{BASE}/team-sheet?team={team_slug}",
                "impact":     f"{BASE}/team-impact?team={team_slug}",
            },
        )

    def collect_rival_snapshots(self) -> List[Dict[str, Any]]:
        rivals = []
        for name, slug in RIVAL_TEAMS.items():
            try:
                snap = self.collect_snapshot(team_slug=slug, team_name=name)
                rivals.append({
                    "team": name,
                    "rpi_rank": snap.rpi_rank,
                    "rpi_value": snap.rpi_value,
                    "overall_record": snap.overall_record,
                    "conf": get_conference(name),
                })
            except Exception as exc:
                self._log(f"Could not fetch rival {name}: {exc}")
                rivals.append({
                    "team": name, "rpi_rank": None, "rpi_value": None,
                    "overall_record": None, "conf": get_conference(name),
                })
        return rivals

    def collect_sunbelt_conf_records(self) -> Dict[str, Dict[str, str]]:
        """Fetch overall and conf records for all Sun Belt teams.
        Returns {team_name: {"overall": str, "conf": str}}."""
        records: Dict[str, Dict[str, str]] = {}
        for name, slug in SUN_BELT_SLUGS.items():
            try:
                schedule_html = self.fetch_text(f"{BASE}/schedule/{slug}")
                schedule_text = self.html_to_text(schedule_html)
                overall_rec = self._extract_simple_value(schedule_text, "Overall")
                conf_rec    = self._extract_simple_value(schedule_text, "Conf")
                records[name.lower()] = {
                    "overall": overall_rec or "",
                    "conf":    conf_rec    or "",
                }
            except Exception as exc:
                self._log(f"Could not fetch records for {name}: {exc}")
        return records
    def save_snapshot(self, snapshot: TeamSnapshot) -> None:
        conn = sqlite3.connect(self.db_path)
        today = snapshot.captured_at[:10]
        try:
            conn.execute(
                "DELETE FROM snapshots WHERE team = ? AND substr(captured_at,1,10) = ?",
                (snapshot.team, today),
            )
            payload_json = json.dumps(dataclasses.asdict(snapshot), ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO snapshots (
                    captured_at, season, team, rpi_rank, rpi_value, sos_rank, sos_value,
                    overall_record, home_record, road_record, neutral_record, conf_record,
                    last_10, streak, q1, q2, q3, q4, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.captured_at, snapshot.season, snapshot.team,
                    snapshot.rpi_rank, snapshot.rpi_value, snapshot.sos_rank, snapshot.sos_value,
                    snapshot.overall_record, snapshot.home_record, snapshot.road_record,
                    snapshot.neutral_record, snapshot.conf_record,
                    snapshot.last_10, snapshot.streak,
                    snapshot.q1, snapshot.q2, snapshot.q3, snapshot.q4,
                    payload_json,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_previous_snapshot(self) -> Optional[TeamSnapshot]:
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT payload_json FROM snapshots
                WHERE team = ?
                ORDER BY captured_at DESC
                LIMIT 1 OFFSET 1
                """,
                ("Southern Miss",),
            ).fetchone()
            if not row:
                return None
            return TeamSnapshot(**json.loads(row[0]))
        finally:
            conn.close()

    def get_rank_history(self, days: int = 45) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        try:
            cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
            rows = conn.execute(
                """
                SELECT substr(captured_at,1,10) as day, rpi_rank, rpi_value, overall_record
                FROM snapshots
                WHERE team = 'Southern Miss' AND captured_at >= ?
                ORDER BY captured_at ASC
                """,
                (cutoff,),
            ).fetchall()
            return [{"date": r[0], "rpi_rank": r[1], "rpi_value": r[2], "overall_record": r[3]}
                    for r in rows]
        finally:
            conn.close()

    def get_week_snapshots(self) -> List[TeamSnapshot]:
        conn = sqlite3.connect(self.db_path)
        try:
            cutoff = (dt.date.today() - dt.timedelta(days=7)).isoformat()
            rows = conn.execute(
                """
                SELECT payload_json FROM snapshots
                WHERE team = 'Southern Miss' AND captured_at >= ?
                ORDER BY captured_at ASC
                """,
                (cutoff,),
            ).fetchall()
            return [TeamSnapshot(**json.loads(r[0])) for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # RPI Radar
    # ------------------------------------------------------------------
    def get_rpi_radar(self, team_name: str = "Southern Miss", window: int = 3) -> List[str]:
        html = self.fetch_text(RPI_LIVE_URL)
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.find_all("tr")
        teams = []

        def clean_team(team: str) -> str:
            team = re.sub(
                r"\s+(Sun Belt|SEC|ACC|Big 12|Big Ten|AAC|C-USA|Conference USA|PAC-12|Pac-12|Big East|Big West).*",
                "", team).strip()
            team = re.sub(r"\s+\(\d+-\d+.*?\)$", "", team).strip()
            return team

        for row in rows:
            cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
            if len(cells) < 2:
                continue
            rank = cells[0].strip()
            team = clean_team(cells[1])
            if rank.isdigit() and team:
                teams.append((int(rank), team))

        if not teams:
            return ["RPI radar unavailable."]

        aliases = {team_name.lower(), "southern miss", "southern mississippi", "southern miss golden eagles"}
        usm_index = None
        for idx, (_, team) in enumerate(teams):
            if team.lower() in aliases or "southern miss" in team.lower():
                usm_index = idx
                break

        if usm_index is None:
            return ["Southern Miss not found in live RPI table."]

        start = max(0, usm_index - window)
        end   = min(len(teams), usm_index + window + 1)
        result = []
        for rank, team in teams[start:end]:
            conf  = get_conference(team)
            label = f"#{rank} {team}"
            if conf:
                label += f" ({conf})"
            if team.lower() in aliases or "southern miss" in team.lower():
                label += " <"
            result.append(label)
        return result

    # ------------------------------------------------------------------
    # Sun Belt Conference RPI Standings
    # ------------------------------------------------------------------
    SUN_BELT_TEAMS = {
        "Southern Miss", "Louisiana", "Troy", "Arkansas State", "South Alabama",
        "Louisiana Monroe", "ULM", "App State", "Appalachian State", "Georgia State",
        "Georgia Southern", "Old Dominion", "Marshall", "James Madison", "Texas State",
        "Louisiana Tech", "Coastal Carolina",
    }

    def get_sunbelt_standings(self) -> List[Dict[str, Any]]:
        html = self.fetch_text(RPI_LIVE_URL)
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.find_all("tr")
        seen: Dict[str, Dict[str, Any]] = {}  # team -> best entry

        def clean_team(team: str) -> str:
            team = re.sub(
                r"\s+(Sun Belt|SEC|ACC|Big 12|Big Ten|AAC|C-USA|Conference USA|PAC-12|Pac-12|Big East|Big West).*",
                "", team).strip()
            team = re.sub(r"\s+\(\d+-\d+.*?\)$", "", team).strip()
            return team

        def extract_record(cell: str) -> Optional[str]:
            m = re.search(r"\((\d+-\d+)\)", cell)
            return m.group(1) if m else None

        def extract_rpi_value(cell: str) -> Optional[float]:
            m = re.search(r"(0\.\d+)", cell)
            return float(m.group(1)) if m else None

        for row in rows:
            cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
            if len(cells) < 2:
                continue
            rank_str = cells[0].strip()
            if not rank_str.isdigit():
                continue
            rank = int(rank_str)
            raw_team = cells[1] if len(cells) > 1 else ""
            team = clean_team(raw_team)
            record = extract_record(raw_team)
            rpi_val = extract_rpi_value(cells[2]) if len(cells) > 2 else None

            # Match against Sun Belt team list (flexible)
            matched = None
            for sb_team in self.SUN_BELT_TEAMS:
                if sb_team.lower() in team.lower() or team.lower() in sb_team.lower():
                    matched = sb_team
                    break
            if matched is None:
                continue

            # Keep only the best (lowest) rank per team
            key = matched.lower()
            if key not in seen or rank < seen[key]["rank"]:
                seen[key] = {
                    "team":        matched,
                    "rank":        rank,
                    "rpi_val":     rpi_val,
                    "record":      record,
                    "conf_record": None,
                    "is_usm":      "southern miss" in matched.lower(),
                }

        # Sort by national RPI rank
        standings = sorted(seen.values(), key=lambda x: x["rank"])
        return standings

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_rank_delta(old: Optional[int], new: Optional[int]) -> Optional[int]:
        if old is None or new is None:
            return None
        return old - new

    @staticmethod
    def _safe_value_delta(old: Optional[float], new: Optional[float]) -> Optional[float]:
        if old is None or new is None:
            return None
        return round(new - old, 4)

    @staticmethod
    def _record_delta(old: Optional[str], new: Optional[str]) -> Tuple[int, int]:
        def parse(rec: Optional[str]) -> Tuple[int, int]:
            if not rec or "-" not in rec:
                return 0, 0
            a, b = rec.split("-", 1)
            return int(a), int(b)
        ow, ol = parse(old)
        nw, nl = parse(new)
        return nw - ow, nl - ol

    @staticmethod
    def _quadrant_bucket(location_type: str, opp_rpi: Optional[int]) -> str:
        if opp_rpi is None:
            return "quadrant unknown"
        loc = location_type.upper()
        if loc == "HOME":
            if opp_rpi <= 25:  return "Q1"
            if opp_rpi <= 50:  return "Q2"
            if opp_rpi <= 100: return "Q3"
            return "Q4"
        if loc == "NEUTRAL":
            if opp_rpi <= 40:  return "Q1"
            if opp_rpi <= 80:  return "Q2"
            if opp_rpi <= 160: return "Q3"
            return "Q4"
        if opp_rpi <= 60:  return "Q1"
        if opp_rpi <= 120: return "Q2"
        if opp_rpi <= 240: return "Q3"
        return "Q4"

    @staticmethod
    def _sos_trajectory(upcoming_games: List[Dict]) -> str:
        rpis = [g["opponent_rpi"] for g in upcoming_games if g.get("opponent_rpi")]
        if not rpis:
            return "Upcoming schedule strength unknown."
        avg = sum(rpis) / len(rpis)
        if avg <= 40:
            return f"Upcoming schedule is loaded -- avg opponent RPI {avg:.0f}. Tough stretch ahead."
        if avg <= 80:
            return f"Upcoming schedule is moderate -- avg opponent RPI {avg:.0f}."
        return f"Upcoming schedule is soft -- avg opponent RPI {avg:.0f}. Limited Q1/Q2 opportunities."

    # ------------------------------------------------------------------
    # Evidence builder
    # ------------------------------------------------------------------
    def build_evidence(
        self,
        previous: Optional[TeamSnapshot],
        current: TeamSnapshot,
        rivals: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        evidence: Dict[str, Any] = {
            "captured_at": current.captured_at,
            "team": current.team,
            "current": dataclasses.asdict(current),
            "previous": dataclasses.asdict(previous) if previous else None,
            "rank_delta": None,
            "rpi_value_delta": None,
            "sos_rank_delta": None,
            "sos_value_delta": None,
            "record_delta": None,
            "drivers": [],
            "watchlist": [],
            "rivals": rivals or [],
            "sos_trajectory": self._sos_trajectory(current.upcoming_games),
        }

        if previous is None:
            evidence["drivers"].append("No prior snapshot exists yet. Today's run establishes the baseline.")
            return evidence

        rank_delta = self._safe_rank_delta(previous.rpi_rank, current.rpi_rank)
        evidence["rank_delta"]      = rank_delta
        evidence["rpi_value_delta"] = self._safe_value_delta(previous.rpi_value, current.rpi_value)
        evidence["sos_rank_delta"]  = self._safe_rank_delta(previous.sos_rank, current.sos_rank)
        evidence["sos_value_delta"] = self._safe_value_delta(previous.sos_value, current.sos_value)
        evidence["record_delta"]    = self._record_delta(previous.overall_record, current.overall_record)

        win_delta, loss_delta = evidence["record_delta"]
        latest_game = current.recent_games[-1] if current.recent_games else None

        if rank_delta is not None:
            if rank_delta > 0:
                evidence["drivers"].append(f"RPI rank improved by {rank_delta} spot(s).")
            elif rank_delta < 0:
                evidence["drivers"].append(f"RPI rank slipped by {abs(rank_delta)} spot(s).")
            else:
                evidence["drivers"].append("RPI rank did not change from the prior snapshot.")

        if latest_game and (win_delta or loss_delta):
            opp      = latest_game.get("opponent") or "the latest opponent"
            loc      = latest_game.get("location_type", "UNKNOWN")
            opp_rpi  = latest_game.get("opponent_rpi")
            conf     = get_conference(opp)
            conf_str = f", {conf}" if conf else ""
            bucket   = self._quadrant_bucket(loc, opp_rpi)
            if latest_game.get("result") == "W":
                evidence["drivers"].append(
                    f"Win against {opp} ({loc.lower()}, RPI {opp_rpi or 'unknown'}{conf_str}, {bucket})."
                )
                if loc == "AWAY":
                    evidence["drivers"].append("Road wins carry extra RPI weight.")
            elif latest_game.get("result") == "L":
                evidence["drivers"].append(
                    f"Loss against {opp} ({loc.lower()}, RPI {opp_rpi or 'unknown'}{conf_str}, {bucket})."
                )
                if loc == "HOME":
                    evidence["drivers"].append("Home losses sting more in RPI evaluation.")

        sos_delta = evidence["sos_rank_delta"]
        if sos_delta is not None:
            if sos_delta > 0:
                evidence["drivers"].append(f"Strength of schedule improved by {sos_delta} spot(s).")
            elif sos_delta < 0:
                evidence["drivers"].append(f"Strength of schedule worsened by {abs(sos_delta)} spot(s).")

        for q in ["q1", "q2", "q3", "q4"]:
            old_q = getattr(previous, q)
            new_q = getattr(current, q)
            if old_q and new_q and old_q != new_q:
                evidence["drivers"].append(f"{q.upper()} record changed from {old_q} to {new_q}.")

        if current.impact_notes:
            evidence["watchlist"].extend(current.impact_notes[:3])

        for game in current.upcoming_games[:3]:
            opp = game.get("opponent")
            if not opp:
                raw = game.get("raw_block", "")
                m = re.search(
                    r"([A-Z][A-Za-z&.' -]{2,})\s*[\n\r]+\s*(?:\(?\d+-\d+\)?\s+RPI:\s+\d+|\(?\d+-\d+\)?)",
                    raw,
                )
                if m:
                    candidate = m.group(1).strip()
                    if candidate.lower() not in {"southern miss", "golden eagles", "upcoming opponent",
                                                  "home", "away", "neutral", "at", "vs"}:
                        opp = candidate
            opp      = opp or "upcoming opponent"
            loc      = game.get("location_type", "UNKNOWN").lower()
            opp_rpi  = game.get("opponent_rpi")
            conf     = get_conference(opp)
            conf_str = f", {conf}" if conf else ""
            bucket   = self._quadrant_bucket(loc.upper(), opp_rpi)
            evidence["watchlist"].append(
                f"Upcoming: {opp} ({loc}, RPI {opp_rpi or 'unknown'}{conf_str}, {bucket})."
            )

        evidence["drivers"]   = list(dict.fromkeys(evidence["drivers"]))
        evidence["watchlist"] = list(dict.fromkeys(evidence["watchlist"]))
        return evidence

    # ------------------------------------------------------------------
    # Week-in-review
    # ------------------------------------------------------------------
    def build_week_review(self) -> str:
        snaps = self.get_week_snapshots()
        if len(snaps) < 2:
            return "Not enough data for a week-in-review yet."
        first, last = snaps[0], snaps[-1]
        rank_delta = self._safe_rank_delta(first.rpi_rank, last.rpi_rank)
        w_delta, l_delta = self._record_delta(first.overall_record, last.overall_record)
        sign = "+" if (rank_delta or 0) > 0 else ""
        lines = [
            f"Week in Review ({first.captured_at[:10]} to {last.captured_at[:10]})",
            f"  Rank: {first.rpi_rank} -> {last.rpi_rank}  ({sign}{rank_delta if rank_delta is not None else 'N/A'})",
            f"  Record: {first.overall_record} -> {last.overall_record}  (+{w_delta}W / +{l_delta}L)",
            f"  RPI Value: {first.rpi_value} -> {last.rpi_value}",
            f"  SOS Rank: {first.sos_rank} -> {last.sos_rank}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Windows toast alert
    # ------------------------------------------------------------------
    @staticmethod
    def send_toast(title: str, message: str) -> None:
        ps_script = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null\n"
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null\n"
            "$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02\n"
            "$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template)\n"
            f"$xml.GetElementsByTagName('text')[0].AppendChild($xml.CreateTextNode('{title}')) | Out-Null\n"
            f"$xml.GetElementsByTagName('text')[1].AppendChild($xml.CreateTextNode('{message}')) | Out-Null\n"
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)\n"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Southern Miss RPI Bot').Show($toast)"
        )
        try:
            subprocess.run(
                ["powershell", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass

    def check_and_alert(self, evidence: Dict[str, Any], threshold: int = 3) -> None:
        rank_delta = evidence.get("rank_delta")
        if rank_delta is not None and rank_delta <= -threshold:
            drop      = abs(rank_delta)
            curr_rank = evidence["current"].get("rpi_rank", "?")
            self.send_toast(
                "Southern Miss RPI Alert",
                f"Rank dropped {drop} spots to #{curr_rank}. Check the dashboard.",
            )

    # ------------------------------------------------------------------
    # GPT narrative (situational tone)
    # ------------------------------------------------------------------
    def render_llm_summary(self, evidence: Dict[str, Any], model: str = "gpt-4.1-mini") -> Optional[str]:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        try:
            from openai import OpenAI
        except Exception:
            return None

        rank = evidence["current"].get("rpi_rank")
        if rank and rank <= 8:
            tone = (
                "Southern Miss is in national seed territory. Write with confidence and clarity. "
                "Acknowledge the position, note what could threaten it, keep the tone assured."
            )
        elif rank and rank <= 16:
            tone = (
                "Southern Miss is in strong regional host position. Write with steady optimism. "
                "Highlight what is working and what needs to hold."
            )
        elif rank and rank <= 25:
            tone = (
                "Southern Miss is on the host bubble. Write with urgency and precision. "
                "Every data point matters. Make clear what the team needs to do."
            )
        else:
            tone = (
                "Southern Miss is outside the host range. Write with honest assessment. "
                "Be direct about the gap and what the realistic path back looks like."
            )

        prompt = textwrap.dedent(f"""
            You are a college baseball analyst writing a concise Southern Miss RPI daily brief.
            Tone directive: {tone}
            Use ONLY the facts in the JSON below. Do not invent data.
            Include: rank movement, record change, why it moved, SOS trajectory, upcoming series preview.
            Write exactly 2 short paragraphs, 100 words total maximum.
            Paragraph 1: rank movement, record change, and why it moved.
            Paragraph 2: SOS trajectory and one upcoming series to watch.
            No bullet points. No filler phrases. Every word must earn its place.

            JSON:
            {json.dumps(evidence, ensure_ascii=False, indent=2)}
        """)

        client = OpenAI(api_key=api_key)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=180,
            )
            return response.choices[0].message.content.strip()
        except Exception as exc:
            self._log(f"OpenAI error: {exc}")
            return None


    # ------------------------------------------------------------------
    # What-If Analysis Engine
    # ------------------------------------------------------------------
    def build_whatif_scenarios(
        self,
        current: TeamSnapshot,
        model: str = "gpt-4.1-mini",
    ) -> List[Dict[str, Any]]:
        """
        For each upcoming game, project RPI rank impact of a WIN vs LOSS.

        RPI projection model (NCAA-calibrated approximations):
        - Each win/loss shifts RPI value by a weight based on opponent RPI and location.
        - Win weight:  home=0.6, neutral=0.7, away=0.8 × (1 / opponent_rpi * 30)
        - Loss weight: home=-0.9, neutral=-0.8, away=-0.6 × (1 / opponent_rpi * 30)
        - Rank delta estimated from historical RPI-to-rank sensitivity (~0.0015 per spot at top 25)
        - This is a directional model, not a simulation -- designed to show relative stakes.
        """
        api_key  = os.getenv("OPENAI_API_KEY")
        use_llm  = bool(api_key)
        client   = None
        if use_llm:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=api_key)
            except Exception:
                use_llm = False

        current_rank  = current.rpi_rank or 0
        current_rpi   = current.rpi_value or 0.0
        scenarios: List[Dict[str, Any]] = []

        # Location multipliers (win / loss)
        WIN_MULT  = {"HOME": 0.60, "NEUTRAL": 0.70, "AWAY": 0.85}
        LOSS_MULT = {"HOME": 0.90, "NEUTRAL": 0.80, "AWAY": 0.65}
        RPI_PER_RANK = 0.0015  # approx RPI value per rank spot near top 25

        # Group consecutive games against same opponent+location into series
        raw_games = current.upcoming_games[:8]
        grouped: List[Dict[str, Any]] = []
        i = 0
        while i < len(raw_games):
            game = raw_games[i]
            opp = game.get("opponent") or "upcoming opponent"
            loc = (game.get("location_type") or "HOME").upper()
            # Count consecutive same opp+loc
            j = i + 1
            while j < len(raw_games):
                next_g = raw_games[j]
                if (next_g.get("opponent") or "upcoming opponent") == opp and                    (next_g.get("location_type") or "HOME").upper() == loc:
                    j += 1
                else:
                    break
            series_count = j - i
            grouped.append({
                "opponent":    opp,
                "location":    loc,
                "opp_rpi":     game.get("opponent_rpi"),
                "time":        game.get("score_or_time") or "TBD",
                "game_count":  series_count,
                "label":       f"{opp} ({series_count}-game series)" if series_count > 1 else opp,
            })
            i = j
            if len(grouped) >= 5:
                break

        for grp in grouped:
            opp       = grp["opponent"]
            loc       = grp["location"]
            opp_rpi   = grp["opp_rpi"]
            time_str  = grp["time"]
            label     = grp["label"]
            game_count = grp["game_count"]
            conf      = get_conference(opp)
            bucket    = self._quadrant_bucket(loc, opp_rpi)

            # RPI impact estimate — scale by number of games in series
            if opp_rpi and opp_rpi > 0 and current_rpi > 0:
                opp_strength  = max(0.001, min(1.0, (150 - opp_rpi) / 150))
                scale         = 1 + (game_count - 1) * 0.5  # series amplifies impact
                win_rpi_gain  = WIN_MULT.get(loc, 0.65)  * opp_strength * 0.003 * scale
                loss_rpi_drop = LOSS_MULT.get(loc, 0.75) * opp_strength * 0.003 * scale
                win_rank_proj  = max(1, round(current_rank - win_rpi_gain  / RPI_PER_RANK))
                loss_rank_proj = max(1, round(current_rank + loss_rpi_drop / RPI_PER_RANK))
            else:
                win_rank_proj  = max(1, current_rank - 1)
                loss_rank_proj = current_rank + 2

            win_delta  = current_rank - win_rank_proj
            loss_delta = loss_rank_proj - current_rank

            # Quadrant stakes label
            if bucket == "Q1":
                stakes_level = "HIGH"
                stakes_color = "#00c853"
            elif bucket == "Q2":
                stakes_level = "MEDIUM"
                stakes_color = "#69c0ff"
            elif bucket == "Q3":
                stakes_level = "LOW"
                stakes_color = "#ffa940"
            else:
                stakes_level = "MINIMAL"
                stakes_color = "#888"

            # GPT one-liner for this specific game/series
            gpt_stake = ""
            if use_llm and client and opp != "upcoming opponent":
                try:
                    series_str = f"{game_count}-game series" if game_count > 1 else "game"
                    upset_note = " This is a potential upset trap — a loss here won't move the RPI number much but will damage the tournament resume." if (loss_delta == 0 and bucket in ("Q3","Q4") and (current_rank or 99) <= 25) else ""
                    gpt_prompt = (
                        f"Southern Miss is currently RPI #{current_rank} ({current_rpi:.4f}). "
                        f"They face {opp} in a {series_str} ({loc.lower()}, opponent RPI #{opp_rpi or 'unknown'}, {conf or 'unknown conf'}, {bucket}). "
                        f"Sweeping projects to #{win_rank_proj}, getting swept projects to #{loss_rank_proj}.{upset_note} "
                        f"Write ONE punchy sentence (max 20 words) describing the stakes for Southern Miss RPI positioning. "
                        f"Be direct and specific. No filler phrases."
                    )
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": gpt_prompt}],
                        max_tokens=60,
                    )
                    gpt_stake = resp.choices[0].message.content.strip().strip('"')
                except Exception:
                    gpt_stake = ""

            upset_risk = (loss_delta == 0 and bucket in ("Q3", "Q4") and (current_rank or 99) <= 25)
            scenarios.append({
                "opponent":     opp,
                "label":        label,
                "conf":         conf,
                "location":     loc.title(),
                "opp_rpi":      opp_rpi,
                "bucket":       bucket,
                "stakes_level": stakes_level,
                "stakes_color": stakes_color,
                "time":         time_str,
                "game_count":   game_count,
                "current_rank": current_rank,
                "win_rank":     win_rank_proj,
                "loss_rank":    loss_rank_proj,
                "win_delta":    win_delta,
                "loss_delta":   loss_delta,
                "upset_risk":   upset_risk,
                "gpt_stake":    gpt_stake,
            })

        return scenarios

    # ------------------------------------------------------------------
    # Plain text summary (fallback / stdout)
    # ------------------------------------------------------------------
    def render_plain_summary(self, evidence: Dict[str, Any]) -> str:
        current  = evidence["current"]
        previous = evidence.get("previous")
        lines = []
        lines.append(f"Southern Miss RPI Daily Brief | {current['captured_at'][:10]}")
        lines.append("")

        if previous:
            lines.append(f"RPI Rank:  {previous.get('rpi_rank')} -> {current.get('rpi_rank')}")
            lines.append(f"RPI Value: {previous.get('rpi_value')} -> {current.get('rpi_value')}")
            lines.append(f"SOS Rank:  {previous.get('sos_rank')} -> {current.get('sos_rank')}")
            lines.append(f"Record:    {previous.get('overall_record')} -> {current.get('overall_record')}")
        else:
            lines.append(f"RPI Rank:  {current.get('rpi_rank')}")
            lines.append(f"RPI Value: {current.get('rpi_value')}")
            lines.append(f"SOS Rank:  {current.get('sos_rank')}")
            lines.append(f"Record:    {current.get('overall_record')}")

        rank = current.get("rpi_rank")
        host_status = "Unknown"
        if rank is not None:
            if rank <= 8:    host_status = "National seed territory"
            elif rank <= 16: host_status = "Strong regional host position"
            elif rank <= 25: host_status = "Host bubble"
            else:            host_status = "Outside host range"
        lines.append(f"\nHost Outlook: {host_status}")

        lines.append("\nTrend Summary:")
        rank_delta   = evidence.get("rank_delta")
        record_delta = evidence.get("record_delta")
        if rank_delta is None:
            lines.append("- No trend available yet.")
        elif rank_delta == 0 and record_delta:
            w, l = record_delta
            lines.append(f"- Flat in rank, record changed +{w}W / +{l}L.")
        elif rank_delta > 0:
            lines.append(f"- Improving: up {rank_delta} spot(s).")
        elif rank_delta < 0:
            lines.append(f"- Slipping: down {abs(rank_delta)} spot(s).")
        else:
            lines.append("- Flat: no change.")

        lines.append("\nWhy it moved:")
        for d in evidence.get("drivers", [])[:6]:
            lines.append(f"- {d}")

        lines.append(f"\nSOS Trajectory: {evidence.get('sos_trajectory', '')}")

        lines.append("\nRPI Radar:")
        try:
            for item in self.get_rpi_radar("Southern Miss", window=3):
                lines.append(f"- {item}")
        except Exception:
            lines.append("- RPI radar unavailable.")

        lines.append("\nImpact Games / Watchlist:")
        for item in evidence.get("watchlist", [])[:6]:
            lines.append(f"- {item}")

        rivals = evidence.get("rivals", [])
        if rivals:
            lines.append("\nRival Watch:")
            for r in sorted(rivals, key=lambda x: x.get("rpi_rank") or 9999):
                rk      = r.get("rpi_rank") or "N/A"
                rec     = r.get("overall_record") or "N/A"
                conf    = r.get("conf") or ""
                conf_str = f" ({conf})" if conf else ""
                lines.append(f"- {r['team']}{conf_str}: RPI #{rk}, {rec}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # HTML dashboard
    # ------------------------------------------------------------------
    def render_html_dashboard(
        self,
        evidence: Dict[str, Any],
        narrative: Optional[str],
        rank_history: List[Dict],
        week_review: str,
        whatif_scenarios: Optional[List[Dict]] = None,
        sb_conf_records: Optional[Dict[str, str]] = None,
    ) -> str:
        current  = evidence["current"]
        rank     = current.get("rpi_rank") or 0
        record   = current.get("overall_record") or "N/A"
        rpi_val  = current.get("rpi_value") or 0.0
        sos_rank = current.get("sos_rank") or "N/A"
        date_str = current["captured_at"][:10]
        rank_delta = evidence.get("rank_delta")

        if rank <= 8:
            status_color = "#00c853"
            status_label = "National Seed Territory"
            status_icon  = "STAR"
        elif rank <= 16:
            status_color = "#69c0ff"
            status_label = "Strong Regional Host"
            status_icon  = "UP"
        elif rank <= 25:
            status_color = "#ffa940"
            status_label = "Host Bubble"
            status_icon  = "WATCH"
        else:
            status_color = "#ff4d4f"
            status_label = "Outside Host Range"
            status_icon  = "ALERT"

        if rank_delta is None:
            delta_html = ""
        elif rank_delta > 0:
            delta_html = f'<span class="delta up">+{rank_delta}</span>'
        elif rank_delta < 0:
            delta_html = f'<span class="delta down">{rank_delta}</span>'
        else:
            delta_html = '<span class="delta flat">--</span>'

        chart_labels = json.dumps([r["date"] for r in rank_history])
        chart_data   = json.dumps([r["rpi_rank"] for r in rank_history])

        drivers_html  = "".join(f"<li>{d}</li>" for d in evidence.get("drivers", [])[:6])
        watchlist_html = "".join(f"<li>{w}</li>" for w in evidence.get("watchlist", [])[:6])

        rivals = sorted(evidence.get("rivals", []), key=lambda x: x.get("rpi_rank") or 9999)
        rivals_rows = ""
        for r in rivals:
            rk   = r.get("rpi_rank") or "--"
            rec  = r.get("overall_record") or "--"
            conf = r.get("conf") or "--"
            rivals_rows += f"<tr><td>{r['team']}</td><td>{conf}</td><td>#{rk}</td><td>{rec}</td></tr>"

        # Sun Belt Conference standings — enrich with records from sb_conf_records
        rec_lookup: Dict[str, Dict[str, str]] = dict(sb_conf_records) if sb_conf_records else {}
        # Ensure Southern Miss records from current snapshot take priority
        usm_overall_rec = current.get("overall_record")
        usm_conf_rec    = current.get("conf_record")
        if usm_overall_rec or usm_conf_rec:
            rec_lookup["southern miss"] = {
                "overall": usm_overall_rec or rec_lookup.get("southern miss", {}).get("overall", ""),
                "conf":    usm_conf_rec    or rec_lookup.get("southern miss", {}).get("conf", ""),
            }

        try:
            sb_standings = self.get_sunbelt_standings()
            sb_rows = ""
            for i, s in enumerate(sb_standings):
                rk       = s["rank"]
                team     = s["team"]
                team_recs = rec_lookup.get(team.lower(), {})
                rec      = team_recs.get("overall") or "--"
                conf_rec = team_recs.get("conf")    or "--"
                pos      = i + 1
                row_cls  = "sb-usm" if s["is_usm"] else ""
                sb_rows += (
                    f"<tr class='{row_cls}'>"
                    f"<td>{pos}</td>"
                    f"<td>{team}" + (" <span class='usm-tag'>YOU</span>" if s["is_usm"] else "") + "</td>"
                    f"<td>#{rk}</td>"
                    f"<td>{rec}</td>"
                    f"<td>{conf_rec}</td>"
                    f"</tr>"
                )
            if not sb_rows:
                sb_rows = "<tr><td colspan='5' style='color:var(--text-3)'>No Sun Belt data found.</td></tr>"
        except Exception:
            sb_rows = "<tr><td colspan='5' style='color:var(--text-3)'>Unavailable.</td></tr>"

        try:
            radar_items = self.get_rpi_radar("Southern Miss", window=4)
            radar_html = "".join(
                f'<li class="{"usm" if "<" in item else ""}">{item.replace("<","").strip()}'
                + (' <span class="usm-tag">YOU ARE HERE</span>' if "<" in item else "")
                + "</li>"
                for item in radar_items
            )
        except Exception:
            radar_html = "<li>Unavailable</li>"

        upcoming_rows = ""
        for g in current.get("upcoming_games", [])[:5]:
            opp     = g.get("opponent") or "TBD"
            loc     = g.get("location_type", "").title()
            opp_rpi = g.get("opponent_rpi") or "--"
            time    = g.get("score_or_time") or "--"
            conf    = get_conference(opp)
            bucket  = self._quadrant_bucket(g.get("location_type", "HOME"), g.get("opponent_rpi"))
            bclass  = bucket.lower().replace(" ", "")
            conf_td = f" <small class='conf-tag'>({conf})</small>" if conf else ""
            upcoming_rows += (
                f"<tr><td>{opp}{conf_td}</td><td>{loc}</td><td>{opp_rpi}</td>"
                f"<td><span class='q-badge {bclass}'>{bucket}</span></td><td>{time}</td></tr>"
            )

        recent_rows = ""
        for g in current.get("recent_games", [])[-7:]:
            opp     = g.get("opponent") or "?"
            res     = g.get("result") or "?"
            score   = g.get("score_or_time") or "--"
            loc     = g.get("location_type", "").title()
            opp_rpi = g.get("opponent_rpi") or "--"
            rclass  = "win" if res == "W" else "loss"
            recent_rows += (
                f"<tr><td>{opp}</td><td class='{rclass}'>{res} {score}</td>"
                f"<td>{loc}</td><td>{opp_rpi}</td></tr>"
            )

        narrative_html = ""
        if narrative:
            safe_narrative = narrative.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            narrative_html = (
                '<div class="card col-12 narrative">'
                "<h2>Daily Brief</h2>"
                f"<p>{safe_narrative}</p>"
                "</div>"
            )

        week_html = week_review.replace("\n", "<br>")
        sos_traj  = evidence.get("sos_trajectory", "")

        q_html = ""
        for q in ["q1", "q2", "q3", "q4"]:
            val = current.get(q) or "--"
            q_tip = {"Q1": "Toughest games. Home vs RPI 1-25, Neutral 1-40, Away 1-60. These wins matter most to the NCAA selection committee.", "Q2": "Solid games. Home vs RPI 26-50, Neutral 41-80, Away 61-120. Good wins, manageable losses.", "Q3": "Below-average games. Home vs RPI 51-100, Neutral 81-160, Away 121-240. Wins expected, losses hurt.", "Q4": "Weakest games. Home vs RPI 101+. Wins count little — losses here are most damaging."}.get(q.upper(), "")
            q_html += f'<div class="q-card"><div class="q-label"><span class="tip" data-tip="{q_tip}">{q.upper()}</span></div><div class="q-val">{val}</div></div>'

        # ------------------------------------------------------------------
        # What-If Analysis HTML
        # ------------------------------------------------------------------
        whatif_cards_html = ""
        whatif_matrix_html = ""
        if whatif_scenarios:
            # Detail cards for first 2 games
            for sc in whatif_scenarios[:2]:
                opp       = sc.get("label") or sc["opponent"]
                conf_str2 = sc["conf"]
                conf_tag  = f' <small class="conf-tag">({conf_str2})</small>' if conf_str2 else ""
                win_dir  = "up" if sc["win_delta"] >= 0 else "down"
                loss_dir = "down" if sc["loss_delta"] >= 0 else "up"
                win_arrow  = "▲" if sc["win_delta"] > 0 else ("▼" if sc["win_delta"] < 0 else "--")
                loss_arrow = "▼" if sc["loss_delta"] > 0 else ("▲" if sc["loss_delta"] < 0 else "--")
                gpt_txt  = sc["gpt_stake"]
                gpt_line = f'<p class="gpt-stake">{gpt_txt}</p>' if gpt_txt else ""
                whatif_cards_html += f"""
                <div class="wi-card">
                  <div class="wi-header">
                    <span class="wi-opp">{opp}{conf_tag}</span>
                    <span class="wi-meta">{sc["location"]} &nbsp;|&nbsp; Opp RPI #{sc["opp_rpi"] or "?"} &nbsp;|&nbsp; {sc["time"]}</span>
                    <span class="q-badge {sc["bucket"].lower().replace(" ","")}">{sc["bucket"]}</span>
                    <span class="stakes-badge" style="background:{sc["stakes_color"]}22;color:{sc["stakes_color"]};border:1px solid {sc["stakes_color"]}44">{sc["stakes_level"]} STAKES</span>
                  </div>
                  {gpt_line}
                  <div class="wi-outcomes">
                    <div class="wi-outcome win-outcome">
                      <div class="wi-label"><span class="tip" data-tip="Projected RPI rank if Southern Miss wins this game or sweeps this series.">IF WIN</span></div>
                      <div class="wi-rank">#{sc["win_rank"]}</div>
                      <div class="wi-delta {win_dir}">{win_arrow} {abs(sc["win_delta"])} spot{"s" if abs(sc["win_delta"]) != 1 else ""}</div>
                    </div>
                    <div class="wi-vs">VS</div>
                    <div class="wi-outcome loss-outcome">
                      <div class="wi-label"><span class="tip" data-tip="Projected RPI rank if Southern Miss loses this game or gets swept in this series.">IF LOSS</span></div>
                      <div class="wi-rank">#{sc["loss_rank"]}</div>
                      <div class="wi-delta {loss_dir}">{loss_arrow} {abs(sc["loss_delta"])} spot{"s" if abs(sc["loss_delta"]) != 1 else ""}</div>
                      {'<div class="wi-upset-warn">&#9888; Resume damage</div>' if sc["loss_delta"] == 0 and sc["bucket"] in ("Q3","Q4") else ''}
                    </div>
                  </div>
                </div>"""

            # Matrix for all games
            matrix_rows = ""
            cumulative_win_rank  = whatif_scenarios[0]["current_rank"] if whatif_scenarios else 0
            cumulative_loss_rank = whatif_scenarios[0]["current_rank"] if whatif_scenarios else 0
            for sc in whatif_scenarios:
                cumulative_win_rank  = max(1, cumulative_win_rank  - sc["win_delta"])
                cumulative_loss_rank = max(1, cumulative_loss_rank + sc["loss_delta"])
                win_cls  = "win"  if sc["win_rank"]  < sc["current_rank"] else ""
                loss_cls = "loss" if sc["loss_rank"] > sc["current_rank"] else ""
                matrix_rows += (
                    f"<tr>"
                    f"<td>{sc.get('label') or sc['opponent']}" + (f" <small class='conf-tag'>({sc['conf']})</small>" if sc['conf'] else "") + "</td>"
                    f"<td>{sc['location']}</td>"
                    f"<td>#{sc['opp_rpi'] or '?'}</td>"
                    f"<td><span class='q-badge {sc['bucket'].lower().replace(' ','')}'>{sc['bucket']}</span></td>"
                    f"<td class='{win_cls}'>#{sc['win_rank']} ({'+' if sc['win_delta']>=0 else ''}{sc['win_delta']})</td>"
                    f"<td class='{loss_cls}'>" + f"#{sc['loss_rank']} (-{sc['loss_delta']})" + (" <span class='wi-upset-tag'>resume</span>" if sc["loss_delta"] == 0 and sc["bucket"] in ("Q3","Q4") else "") + "</td>"
                    f"</tr>"
                )
            whatif_matrix_html = f"""
            <table>
              <thead>
                <tr>
                  <th>Opponent</th><th>Site</th><th>Opp RPI</th><th>Quadrant</th>
                  <th style="color:#00c853">If Win</th><th style="color:#ff4d4f">If Loss</th>
                </tr>
              </thead>
              <tbody>{matrix_rows}</tbody>
            </table>"""

        whatif_section = ""
        if whatif_scenarios:
            whatif_section = f'''
  <div class="card col-12" style="border-color:#2a2a00;">
    <h2 style="color:#f5c518;">What-If Scenarios <span class="tip" data-tip="Directional projections of how Southern Miss RPI rank could move based on winning or losing each upcoming game or series. Not a simulation — shows relative stakes.">&#x24D8;</span></h2>
    <div class="wi-cards-row">{whatif_cards_html}</div>
    <div style="margin-top:20px;">
      <div style="font-size:0.72rem;color:var(--text-3);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">Full Schedule Scenario Matrix</div>
      {whatif_matrix_html}
    </div>
    <p style="font-size:0.7rem;color:var(--text-3);margin-top:12px;">Projections are directional estimates based on RPI weight modeling. Not a simulation.</p>
  </div>
'''

        # ── template-only helpers (presentation, not data logic) ──
        _is_one = (rank == 1)
        _number_one_banner = (
            '<div class="number-one-banner">&#9733;&nbsp;NATIONAL #1 IN RPI&nbsp;&#9733;</div>'
            if _is_one else ''
        )
        _rank_glow = (
            'text-shadow:0 0 40px rgba(245,197,24,.65),0 0 80px rgba(245,197,24,.3),0 0 140px rgba(245,197,24,.12);'
            if _is_one else
            'text-shadow:0 2px 28px rgba(245,197,24,.18);'
        )
        _hero_one_css = (
            '.hero{border-color:rgba(245,197,24,.45)!important;'
            'background:linear-gradient(135deg,#0f0d00 0%,#101010 55%)!important}'
            '.hero::before{background:radial-gradient(ellipse 70% 110% at 0% 50%,'
            'rgba(245,197,24,.11) 0%,transparent 65%)!important}'
            if _is_one else ''
        )
        _conf_rec  = current.get('conf_record')   or '--'
        # Reformat streak: "1L" → "L1", "5W" → "W5"
        _raw_streak = current.get('streak') or ''
        _sm = re.match(r'^(\d+)([WL])$', _raw_streak.strip())
        _streak = (_sm.group(2) + _sm.group(1)) if _sm else (_raw_streak or '--')
        # Strip trailing junk from last_10 (e.g. "7-3 / STREAKS" → "7-3")
        _raw_last10 = current.get('last_10') or ''
        _last10 = re.split(r'\s*/\s*|\s+STREAK', _raw_last10)[0].strip() or '--'
        _home_rec  = current.get('home_record')   or '--'
        _road_rec  = current.get('road_record')   or '--'
        _neut_rec  = current.get('neutral_record') or '--'
        # Pre-compute hero brief HTML before the f-string to avoid nested f-string issues
        if narrative:
            _safe_n = narrative.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            _hero_brief_html = (
                '<div class="brief-body" id="briefBody">'
                '<div class="brief-text">' + _safe_n + '</div>'
                '<div class="brief-fade" id="briefFade"></div>'
                '</div>'
                '<button class="brief-toggle" id="briefToggle" onclick="toggleBrief()">Read More &#9660;</button>'
            )
        else:
            _hero_brief_html = (
                '<div class="brief-placeholder">No brief generated yet.'
                '<br>Run with <code>--llm</code> flag to generate today\'s brief.</div>'
            )


        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Southern Miss RPI — {date_str}</title>
<!-- Google Analytics -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-TSNBFTDKSN"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', 'G-TSNBFTDKSN');
</script>
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="USM RPI">
<meta name="theme-color" content="#f5c518">
<link rel="manifest" href="manifest.json">
<link rel="apple-touch-icon" href="RPI_Logo.jpg">
<link rel="icon" type="image/jpeg" href="RPI_Logo.jpg">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
/* ─── SOUTHERN MISS RPI DASHBOARD — Design System ─── */
:root {{
  --gold:       #f5c518;
  --gold-dim:   rgba(245,197,24,.12);
  --gold-glow:  rgba(245,197,24,.25);
  --bg:         #080808;
  --s1:         #0f0f0f;
  --s2:         #161616;
  --s3:         #1e1e1e;
  --s4:         #272727;
  --border:     #222;
  --border-sub: #181818;
  --text:       #f0f0f0;
  --text-2:     #c0c0c0;
  --text-3:     #909090;
  --green:      #00e676;
  --green-dim:  rgba(0,230,118,.12);
  --red:        #ff5252;
  --red-dim:    rgba(255,82,82,.12);
  --amber:      #ffab40;
  --amber-dim:  rgba(255,171,64,.12);
  --blue:       #448aff;
  --blue-dim:   rgba(68,138,255,.12);
  --status:     {status_color};
  --r:          10px;
}}
*,*::before,*::after {{ box-sizing:border-box; margin:0; padding:0; }}
html {{ scroll-behavior:smooth; }}
body {{
  background:var(--bg); color:var(--text);
  font-family:'Inter',system-ui,sans-serif;
  font-size:14px; line-height:1.6;
  -webkit-font-smoothing:antialiased;
}}

/* ── SCROLLBAR ── */
::-webkit-scrollbar {{ width:5px; height:5px; }}
::-webkit-scrollbar-track {{ background:var(--bg); }}
::-webkit-scrollbar-thumb {{ background:var(--s4); border-radius:3px; }}

/* ── HEADER ── */
.site-header {{
  position:sticky; top:0; z-index:100;
  background:rgba(8,8,8,.96);
  backdrop-filter:blur(14px);
  -webkit-backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border);
}}
.header-inner {{
  max-width:1480px; margin:0 auto;
  height:60px;
  display:flex; align-items:center; gap:14px;
  padding:0 clamp(16px,4vw,48px);
}}
.header-logo {{
  width:40px; height:40px; border-radius:9px;
  object-fit:cover; border:2px solid var(--gold); flex-shrink:0;
}}
.header-brand {{ flex:1; }}
.header-brand h1 {{
  font-family:'Bebas Neue',sans-serif;
  font-size:1.35rem; color:var(--gold);
  letter-spacing:2.5px; line-height:1.1;
}}
.header-brand p {{
  font-size:0.64rem; color:var(--text-3);
  letter-spacing:2.5px; text-transform:uppercase; margin-top:1px;
}}
.status-pill {{
  background:var(--status); color:#000;
  font-family:'Bebas Neue',sans-serif;
  font-size:0.8rem; letter-spacing:1.5px;
  padding:5px 15px; border-radius:50px;
  white-space:nowrap;
}}

/* ── PAGE SHELL ── */
.page {{
  max-width:1480px; margin:0 auto;
  padding:clamp(16px,3vw,28px) clamp(12px,4vw,48px);
  display:flex; flex-direction:column; gap:16px;
}}

/* ── HERO ── */
.hero {{
  position:relative; overflow:hidden;
  background:var(--s1);
  border:1px solid var(--border);
  border-radius:16px;
  padding:clamp(22px,4vw,44px) clamp(22px,4vw,44px);
  display:grid;
  grid-template-columns:auto 1fr minmax(260px,340px);
  gap:clamp(20px,4vw,48px);
  align-items:start;
}}
.hero::before {{
  content:''; position:absolute; inset:0; pointer-events:none;
  background:radial-gradient(ellipse 55% 90% at 0% 50%,rgba(245,197,24,.05) 0%,transparent 60%);
}}
{_hero_one_css}
.number-one-banner {{
  position:absolute; top:0; right:0;
  background:linear-gradient(110deg,#c49a10 0%,var(--gold) 40%,#ffe566 60%,var(--gold) 80%,#c49a10 100%);
  background-size:300% 100%;
  animation:banner-shine 3s linear infinite;
  color:#000;
  font-family:'Bebas Neue',sans-serif;
  font-size:0.7rem; letter-spacing:2.5px;
  padding:5px 22px 5px 28px;
  border-radius:0 16px 0 14px;
}}
@keyframes banner-shine {{
  0%  {{ background-position:0% 50%; }}
  100%{{ background-position:300% 50%; }}
}}
.hero-rank {{ text-align:center; }}
.rank-eyebrow {{
  font-size:0.6rem; font-weight:700;
  color:var(--text-3); text-transform:uppercase;
  letter-spacing:3.5px; margin-bottom:2px;
}}
.rank-number {{
  font-family:'Bebas Neue',sans-serif;
  font-size:clamp(5rem,10vw,8.5rem);
  line-height:.88; color:var(--gold);
  {_rank_glow}
}}
.rank-delta-row {{ margin-top:8px; display:flex; justify-content:center; gap:6px; }}
.delta {{
  display:inline-block;
  font-size:0.78rem; font-weight:700;
  padding:3px 11px; border-radius:50px; letter-spacing:.5px;
}}
.delta.up   {{ background:var(--green-dim); color:var(--green); }}
.delta.down {{ background:var(--red-dim);   color:var(--red); }}
.delta.flat {{ background:var(--s3);        color:var(--text-3); }}

/* hero stat tiles */
.hero-stats {{
  display:grid;
  grid-template-columns:repeat(3,1fr);
  gap:10px;
}}
.hstat {{
  background:var(--s2); border:1px solid var(--border-sub);
  border-radius:var(--r); padding:13px 14px;
  transition:border-color .2s, background .2s;
}}
.hstat:hover {{ border-color:var(--gold-dim); background:var(--s3); }}
.hstat-val {{
  font-family:'Bebas Neue',sans-serif;
  font-size:1.75rem; color:var(--gold); line-height:1;
  letter-spacing:.5px;
}}
.hstat-lbl {{
  font-size:0.6rem; color:var(--text-3);
  text-transform:uppercase; letter-spacing:2px;
  margin-top:4px; font-weight:600;
}}

/* hero brief sidebar */
.hero-brief {{
  background:var(--s2);
  border:1px solid var(--border);
  border-left:3px solid rgba(245,197,24,.45);
  border-radius:0 var(--r) var(--r) 0;
  padding:18px 18px 18px 20px;
  display:flex; flex-direction:column; gap:10px;
  align-self:stretch;
}}
.brief-label {{
  font-size:0.58rem; font-weight:700;
  text-transform:uppercase; letter-spacing:3px;
  color:var(--gold); opacity:.8;
}}
.brief-text {{
  font-style:italic;
  font-size:0.84rem; line-height:1.75;
  color:#dcdcdc;
}}
.brief-body {{
  position:relative;
  overflow:hidden;
  /* ~4 lines: font-size 0.84rem * line-height 1.75 * 4 */
  max-height:calc(0.84rem * 1.75 * 4);
  transition:max-height .35s ease;
}}
.brief-body.expanded {{
  max-height:600px;
}}
.brief-fade {{
  position:absolute; bottom:0; left:0; right:0;
  height:2.2em;
  background:linear-gradient(to bottom, transparent, var(--s2));
  pointer-events:none;
  transition:opacity .25s ease;
}}
.brief-body.expanded .brief-fade {{
  opacity:0;
}}
.brief-toggle {{
  background:none; border:none; cursor:pointer;
  font-family:'Bebas Neue',sans-serif;
  font-size:0.7rem; letter-spacing:2.5px;
  color:var(--gold); opacity:.75;
  padding:2px 0; margin-top:2px;
  text-transform:uppercase;
  transition:opacity .15s;
  align-self:flex-start;
}}
.brief-toggle:hover {{ opacity:1; }}
.brief-placeholder {{
  font-style:italic;
  font-size:0.8rem; color:var(--text-3);
  line-height:1.7;
}}
.brief-placeholder code {{
  font-style:normal;
  font-family:'JetBrains Mono',monospace;
  font-size:0.75rem;
  background:var(--s3); color:#b8b8b8;
  padding:1px 6px; border-radius:4px;
}}

/* splits strip (below hero) */
.splits-strip {{
  display:flex; gap:10px;
}}
.split-pill {{
  flex:1;
  display:flex; align-items:center; gap:12px;
  background:var(--s1); border:1px solid var(--border);
  border-radius:var(--r); padding:10px 16px;
}}
.split-lbl {{
  font-size:0.6rem; font-weight:700;
  text-transform:uppercase; letter-spacing:2px;
  color:var(--text-3);
}}
.split-val {{
  font-family:'JetBrains Mono',monospace;
  font-size:0.92rem; color:var(--text); font-weight:500;
  margin-left:auto;
}}

@media (max-width:960px) {{
  .hero {{ grid-template-columns:auto 1fr; grid-template-rows:auto auto; }}
  .hero-brief {{ grid-column:1 / -1; border-left:3px solid rgba(245,197,24,.45); border-radius:var(--r); }}
  .hero-stats {{ grid-template-columns:repeat(3,1fr); }}
}}
@media (max-width:600px) {{
  .hero {{ grid-template-columns:1fr; text-align:center; }}
  .hero-rank {{ text-align:center; }}
  .hero-stats {{ grid-template-columns:repeat(2,1fr); }}
  .splits-strip {{ flex-wrap:wrap; }}
  .split-pill {{ min-width:calc(50% - 5px); }}
}}

/* ── NARRATIVE (generated by Python, keep .card .narrative selectors) ── */
.card {{ /* used by narrative_html and whatif_section */
  background:var(--s1); border:1px solid var(--border);
  border-radius:var(--r); padding:20px 22px;
}}
.col-12 {{ display:block; }} /* no-op width; already block */
.narrative {{
  background:linear-gradient(135deg,#0e0c00 0%,var(--s1) 55%) !important;
  border-color:rgba(245,197,24,.15) !important;
}}
.narrative h2 {{
  font-family:'Bebas Neue',sans-serif;
  font-size:0.82rem; letter-spacing:2.5px;
  color:var(--gold); text-transform:uppercase;
  margin-bottom:14px;
  padding-bottom:10px; border-bottom:1px solid var(--border-sub);
}}
.narrative p {{
  font-size:1.0rem; line-height:1.9;
  color:#ddd; max-width:1000px;
}}

/* ── LAYOUT ROWS ── */
.row {{ display:grid; gap:16px; }}
.row-3 {{ grid-template-columns:repeat(3,1fr); }}
.row-2 {{ grid-template-columns:repeat(2,1fr); }}
@media (max-width:1060px) {{ .row-3 {{ grid-template-columns:1fr 1fr; }} }}
@media (max-width:720px)  {{ .row-3,.row-2 {{ grid-template-columns:1fr; }} }}

/* ── PANEL (new card style) ── */
.panel {{
  background:var(--s1); border:1px solid var(--border);
  border-radius:var(--r);
  padding:0; overflow:hidden;
  display:flex; flex-direction:column;
}}
.panel-head {{
  display:flex; align-items:center; gap:8px;
  padding:14px 18px 12px;
  border-bottom:1px solid var(--border-sub);
}}
.panel-title {{
  font-family:'Bebas Neue',sans-serif;
  font-size:0.8rem; letter-spacing:2.5px;
  color:var(--gold); text-transform:uppercase; flex:1;
}}
.panel-body {{ padding:14px 18px; flex:1; display:flex; flex-direction:column; gap:12px; }}

/* ── QUADRANT RECORDS (q_html generates .q-card .q-label .q-val) ── */
.q-row {{
  display:grid; grid-template-columns:repeat(4,1fr); gap:8px;
}}
.q-card {{
  background:var(--s2); border:1px solid var(--border-sub);
  border-radius:8px; padding:12px 8px; text-align:center;
  transition:border-color .2s, transform .15s;
}}
.q-card:hover {{ border-color:var(--border); transform:translateY(-1px); }}
.q-label {{
  font-size:0.58rem; color:var(--text-3);
  text-transform:uppercase; letter-spacing:1.5px;
  font-weight:700; margin-bottom:6px;
}}
.q-val {{
  font-family:'Bebas Neue',sans-serif;
  font-size:1.5rem; color:var(--gold); line-height:1;
}}
.sos-note {{
  background:rgba(245,197,24,.04);
  border-left:3px solid rgba(245,197,24,.35);
  padding:10px 14px; border-radius:0 6px 6px 0;
  font-size:0.81rem; color:var(--text-2); line-height:1.6;
}}

/* ── CHART ── */
.chart-wrap {{ flex:1; position:relative; min-height:170px; }}

/* ── LISTS (radar, drivers, watchlist) ── */
ul.ilist {{ list-style:none; padding:0; }}
ul.ilist li {{
  padding:8px 0; border-bottom:1px solid var(--border-sub);
  font-size:0.82rem; color:var(--text-2); line-height:1.5;
}}
ul.ilist li:last-child {{ border-bottom:none; }}
ul.radar-list li {{ font-family:'JetBrains Mono',monospace; font-size:0.77rem; }}
ul.radar-list li.usm {{ color:var(--gold); font-weight:500; }}
.usm-tag {{
  background:var(--gold); color:#000;
  font-size:0.54rem; padding:1px 5px; border-radius:3px;
  margin-left:6px; font-family:'Inter',sans-serif;
  font-weight:700; letter-spacing:.5px; vertical-align:middle;
}}
.conf-tag {{ color:var(--text-3); font-size:.72em; }}
.sb-usm td {{ background:#2a2000 !important; color:var(--gold) !important; font-weight:600; }}
.sb-usm td:first-child {{ border-left:3px solid var(--gold); }}

/* ── TABLES ── */
.tbl-wrap {{ overflow-x:auto; -webkit-overflow-scrolling:touch; }}
table {{ width:100%; border-collapse:collapse; font-size:0.81rem; }}
thead th {{
  color:var(--text-3); font-size:0.6rem; font-weight:700;
  text-transform:uppercase; letter-spacing:1.5px;
  padding:0 10px 10px; border-bottom:1px solid var(--border);
  text-align:left; white-space:nowrap;
}}
tbody td {{
  padding:9px 10px; border-bottom:1px solid var(--border-sub);
  color:var(--text-2); vertical-align:middle;
}}
tbody tr:last-child td {{ border-bottom:none; }}
tbody tr:hover td {{ background:var(--s2); }}
.win  {{ color:var(--green) !important; font-weight:600; }}
.loss {{ color:var(--red)   !important; font-weight:600; }}

/* ── BADGES ── */
.q-badge {{
  display:inline-flex; align-items:center;
  padding:2px 7px; border-radius:4px;
  font-size:0.66rem; font-weight:700; letter-spacing:.5px; white-space:nowrap;
}}
.q1 {{ background:rgba(0,230,118,.1);  color:#00e676; border:1px solid rgba(0,230,118,.25); }}
.q2 {{ background:rgba(68,138,255,.1); color:#448aff; border:1px solid rgba(68,138,255,.25); }}
.q3 {{ background:rgba(255,171,64,.1); color:#ffab40; border:1px solid rgba(255,171,64,.25); }}
.q4 {{ background:rgba(255,82,82,.1);  color:#ff5252; border:1px solid rgba(255,82,82,.25); }}
.quadrantunknown {{ background:rgba(80,80,80,.1); color:#666; border:1px solid rgba(80,80,80,.2); }}

/* ── WEEK BOX ── */
.week-box {{
  background:var(--s2); border-radius:8px;
  padding:14px 16px;
  font-family:'JetBrains Mono',monospace;
  font-size:0.74rem; color:#c0c0c0; line-height:2.0;
}}

/* ── WHAT-IF (classes used by Python-generated whatif_section) ── */
.wi-cards-row {{
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
  gap:14px;
}}
.wi-card {{
  background:var(--s2); border:1px solid rgba(245,197,24,.1);
  border-radius:var(--r); padding:18px;
  display:flex; flex-direction:column; gap:12px;
}}
.wi-header {{
  display:flex; flex-wrap:wrap; align-items:center; gap:8px;
}}
.wi-opp {{ font-weight:600; font-size:0.95rem; color:var(--text); flex:1; }}
.wi-meta {{ font-size:0.71rem; color:var(--text-3); }}
.stakes-badge {{
  font-size:0.58rem; font-weight:700;
  padding:2px 8px; border-radius:4px;
  letter-spacing:1px; text-transform:uppercase;
}}
.gpt-stake {{
  font-style:italic; font-size:0.83rem; color:#c0c0c0;
  border-left:2px solid rgba(245,197,24,.3);
  padding-left:12px; line-height:1.65;
}}
.wi-outcomes {{
  display:grid; grid-template-columns:1fr auto 1fr;
  gap:10px; align-items:center;
}}
.wi-outcome {{ text-align:center; padding:14px 10px; border-radius:8px; }}
.win-outcome  {{ background:var(--green-dim); border:1px solid rgba(0,230,118,.2); }}
.loss-outcome {{ background:var(--red-dim);   border:1px solid rgba(255,82,82,.2); }}
.wi-label {{
  font-size:0.58rem; text-transform:uppercase;
  letter-spacing:1.5px; color:var(--text-3);
  font-weight:700; margin-bottom:4px;
}}
.wi-rank {{
  font-family:'Bebas Neue',sans-serif;
  font-size:2.5rem; line-height:1; color:var(--gold);
}}
.wi-delta {{ font-size:0.74rem; font-weight:700; margin-top:3px; }}
.wi-delta.up   {{ color:var(--green); }}
.wi-delta.down {{ color:var(--red); }}
.wi-vs {{
  font-family:'Bebas Neue',sans-serif;
  font-size:1.1rem; color:var(--text-3); text-align:center;
}}
.wi-upset-warn {{
  font-size:0.67rem; font-weight:600;
  color:var(--amber); margin-top:4px; letter-spacing:.5px;
}}
.wi-upset-tag {{
  font-size:0.6rem; font-weight:600; color:var(--amber);
  border:1px solid rgba(255,171,64,.3); background:var(--amber-dim);
  padding:1px 5px; border-radius:3px; margin-left:4px;
}}

/* ── TOOLTIPS ── */
.tip {{
  position:relative; cursor:help; display:inline;
  text-decoration:underline; text-decoration-style:dotted;
  text-decoration-color:var(--text-3); text-underline-offset:3px;
}}
.tip::after {{
  content:attr(data-tip);
  position:absolute; bottom:calc(100% + 8px); left:50%;
  transform:translateX(-50%);
  background:var(--s3); color:var(--text);
  border:1px solid var(--border); border-radius:8px;
  padding:10px 14px; font-size:0.75rem; line-height:1.55;
  width:240px; white-space:normal; text-align:left;
  opacity:0; pointer-events:none;
  transition:opacity .15s ease;
  z-index:999; font-family:'Inter',sans-serif;
  font-style:normal; font-weight:400;
  box-shadow:0 8px 32px rgba(0,0,0,.65);
}}
.tip::before {{
  content:''; position:absolute; bottom:calc(100% + 2px); left:50%;
  transform:translateX(-50%);
  border:5px solid transparent; border-top-color:var(--border);
  opacity:0; pointer-events:none;
  transition:opacity .15s ease; z-index:999;
}}
.tip:hover::after,.tip:hover::before {{ opacity:1; }}

/* ── FOOTER ── */
footer {{
  text-align:center; color:var(--text-3);
  font-size:0.68rem; letter-spacing:1px;
  padding:22px clamp(16px,4vw,48px);
  border-top:1px solid var(--border);
}}

/* ── INFO BANNER ── */
.info-banner {{
  background:linear-gradient(135deg,#0d0900 0%,var(--s1) 60%);
  border:1px solid rgba(245,197,24,.18);
  border-left:3px solid var(--gold);
  border-radius:var(--r);
  padding:14px 20px;
  display:flex; align-items:center; gap:16px; flex-wrap:wrap;
}}
.info-banner-text {{ flex:1; min-width:220px; font-size:0.84rem; color:var(--text-2); line-height:1.6; }}
.info-banner-text strong {{ color:var(--gold); }}
.info-banner-meta {{ font-size:0.72rem; color:var(--text-3); margin-top:5px; letter-spacing:.5px; }}
.copy-btn {{
  background:var(--gold-dim); color:var(--gold);
  border:1px solid rgba(245,197,24,.35);
  border-radius:8px; padding:9px 18px;
  font-family:'Bebas Neue',sans-serif;
  font-size:0.78rem; letter-spacing:2px;
  cursor:pointer; white-space:nowrap;
  transition:background .15s, border-color .15s;
}}
.copy-btn:hover {{ background:rgba(245,197,24,.22); border-color:var(--gold); }}
.copy-btn.copied {{ color:var(--green); border-color:var(--green); background:var(--green-dim); }}

/* ── RPI EXPLAINER ── */
.explainer-toggle {{
  width:100%; background:none; border:none;
  display:flex; align-items:center; gap:10px;
  padding:14px 18px 12px; cursor:pointer; text-align:left;
}}
.explainer-toggle:hover .panel-title {{ color:#ffe066; }}
.explainer-chevron {{
  font-size:0.7rem; color:var(--text-3);
  transition:transform .25s ease; flex-shrink:0; margin-left:auto;
}}
.explainer-chevron.open {{ transform:rotate(180deg); }}
.explainer-body {{
  max-height:0; overflow:hidden;
  transition:max-height .35s ease, padding .25s ease;
  padding:0 18px;
}}
.explainer-body.open {{ max-height:700px; padding:0 18px 18px; }}
.explainer-grid {{
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
  gap:12px; margin-top:4px;
}}
.explainer-block {{
  background:var(--s2); border:1px solid var(--border-sub);
  border-radius:8px; padding:14px 16px;
}}
.explainer-block h4 {{
  font-family:'Bebas Neue',sans-serif;
  font-size:0.75rem; letter-spacing:2px;
  color:var(--gold); margin-bottom:8px;
}}
.explainer-block p {{ font-size:0.8rem; color:var(--text-2); line-height:1.65; }}
.explainer-block strong {{ color:var(--text); }}
</style>
</head>
<body>

<!-- ═══ HEADER ═══ -->
<header class="site-header">
  <div class="header-inner">
    <img src="RPI_Logo.jpg" alt="Southern Miss" class="header-logo">
    <div class="header-brand">
      <h1>Southern Miss Baseball</h1>
      <p>RPI Intelligence Dashboard &nbsp;·&nbsp; {date_str}</p>
    </div>
    <div class="status-pill">{status_icon}&nbsp;&nbsp;{status_label}</div>
  </div>
</header>

<!-- ═══ PAGE ═══ -->
<main class="page">

  <!-- HERO -->
  <div class="hero">
    {_number_one_banner}
    <div class="hero-rank">
      <div class="rank-eyebrow">RPI Rank</div>
      <div class="rank-number">#{rank}</div>
      <div class="rank-delta-row">{delta_html}</div>
    </div>
    <div class="hero-stats">
      <div class="hstat">
        <div class="hstat-val">{record}</div>
        <div class="hstat-lbl">Overall Record</div>
      </div>
      <div class="hstat">
        <div class="hstat-val">{rpi_val:.4f}</div>
        <div class="hstat-lbl"><span class="tip" data-tip="Raw RPI value. Higher is better. Teams near 0.62+ are typically top-25.">RPI Value</span></div>
      </div>
      <div class="hstat">
        <div class="hstat-val">#{sos_rank}</div>
        <div class="hstat-lbl"><span class="tip" data-tip="Strength of Schedule rank. #1 = hardest schedule in college baseball.">SOS Rank</span></div>
      </div>
      <div class="hstat">
        <div class="hstat-val">{_conf_rec}</div>
        <div class="hstat-lbl">Conf Record</div>
      </div>
      <div class="hstat">
        <div class="hstat-val">{_streak}</div>
        <div class="hstat-lbl">Streak</div>
      </div>
      <div class="hstat">
        <div class="hstat-val">{_last10}</div>
        <div class="hstat-lbl">Last 10</div>
      </div>
    </div>
    <div class="hero-brief">
      <div class="brief-label">AI Brief</div>
      {_hero_brief_html}
    </div>
  </div>

  <!-- SPLITS STRIP (home / away / neutral) -->
  <div class="splits-strip">
    <div class="split-pill">
      <span class="split-lbl">Home</span>
      <span class="split-val">{_home_rec}</span>
    </div>
    <div class="split-pill">
      <span class="split-lbl">Away</span>
      <span class="split-val">{_road_rec}</span>
    </div>
    <div class="split-pill">
      <span class="split-lbl">Neutral</span>
      <span class="split-val">{_neut_rec}</span>
    </div>
  </div>

  <!-- DESCRIPTION BANNER -->
  <div class="info-banner">
    <div class="info-banner-text">
      <strong>Southern Miss Baseball RPI Tracker</strong> — Automated daily tracker pulling live RPI, SOS, and schedule data from Warren Nolan. Bookmark it and check back every morning during the season to follow Southern Miss's NCAA Tournament positioning.
      <div class="info-banner-meta">&#128197;&nbsp; Last updated: {date_str} &nbsp;·&nbsp; Data: Warren Nolan &nbsp;·&nbsp; Updates every morning</div>
    </div>
    <button class="copy-btn" id="copyBtn" onclick="copyDashLink()">&#128279;&nbsp; Copy Link</button>
  </div>

  <!-- ROW 1: QUADRANTS · TREND CHART · RPI RADAR -->
  <div class="row row-3">

    <div class="panel">
      <div class="panel-head">
        <span class="panel-title">Quadrant Records</span>
        <span class="tip" data-tip="Q1–Q4 grades based on opponent RPI and location. Q1 is toughest; NCAA weighs Q1 wins most heavily for seeding.">&#x24D8;</span>
      </div>
      <div class="panel-body">
        <div class="q-row">{q_html}</div>
        <div class="sos-note">{sos_traj}</div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head">
        <span class="panel-title">Rank Trend — 45 days</span>
        <span class="tip" data-tip="Daily RPI rank. Y-axis inverted: lower on the chart = better rank. Each point = one bot run.">&#x24D8;</span>
      </div>
      <div class="panel-body">
        <div class="chart-wrap"><canvas id="rankChart"></canvas></div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head">
        <span class="panel-title">RPI Radar</span>
        <span class="tip" data-tip="Teams ranked immediately above and below Southern Miss in live RPI standings.">&#x24D8;</span>
      </div>
      <div class="panel-body">
        <ul class="ilist radar-list">{radar_html}</ul>
      </div>
    </div>

  </div>

  <!-- ROW 2: WHY IT MOVED · RIVAL WATCH · WEEK IN REVIEW -->
  <div class="row row-3">

    <div class="panel">
      <div class="panel-head"><span class="panel-title">Why It Moved</span></div>
      <div class="panel-body">
        <ul class="ilist">{drivers_html or "<li>No drivers yet.</li>"}</ul>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head">
        <span class="panel-title">Sun Belt RPI Standings</span>
        <span style="font-size:0.7rem;color:var(--text-3);letter-spacing:1px;">NATIONAL RANK</span>
      </div>
      <div class="panel-body">
        <div class="tbl-wrap">
          <table>
            <thead><tr><th>#</th><th>Team</th><th>Nat'l RPI</th><th>Overall</th><th>Conf</th></tr></thead>
            <tbody>{sb_rows}</tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head"><span class="panel-title">Week in Review</span></div>
      <div class="panel-body">
        <div class="week-box">{week_html}</div>
      </div>
    </div>

  </div>

  <!-- ROW 3: UPCOMING GAMES · RECENT RESULTS -->
  <div class="row row-2">

    <div class="panel">
      <div class="panel-head"><span class="panel-title">Upcoming Games</span></div>
      <div class="panel-body">
        <div class="tbl-wrap">
          <table>
            <thead><tr><th>Opponent</th><th>Site</th><th>Opp RPI</th><th>Quadrant</th><th>Time</th></tr></thead>
            <tbody>{upcoming_rows or "<tr><td colspan='5' style='color:var(--text-3)'>No upcoming games found.</td></tr>"}</tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head"><span class="panel-title">Recent Results</span></div>
      <div class="panel-body">
        <div class="tbl-wrap">
          <table>
            <thead><tr><th>Opponent</th><th>Result</th><th>Site</th><th>Opp RPI</th></tr></thead>
            <tbody>{recent_rows or "<tr><td colspan='4' style='color:var(--text-3)'>No recent games found.</td></tr>"}</tbody>
          </table>
        </div>
      </div>
    </div>

  </div>

  <!-- WATCHLIST (full width) -->
  <div class="panel">
    <div class="panel-head"><span class="panel-title">Impact Games / Watchlist</span></div>
    <div class="panel-body">
      <ul class="ilist">{watchlist_html or "<li>No items.</li>"}</ul>
    </div>
  </div>

  <!-- WHAT IS RPI? (collapsible explainer) -->
  <div class="panel">
    <button class="explainer-toggle" onclick="toggleExplainer()" id="explainerToggle" aria-expanded="false">
      <span class="panel-title">What is RPI?</span>
      <span class="explainer-chevron" id="explainerChevron">&#9660;</span>
    </button>
    <div class="explainer-body" id="explainerBody">
      <div class="explainer-grid">
        <div class="explainer-block">
          <h4>The Formula</h4>
          <p>RPI stands for <strong>Ratings Percentage Index</strong>. It's calculated from three components: <strong>25%</strong> your own winning percentage, <strong>50%</strong> your opponents' winning percentage, and <strong>25%</strong> your opponents' opponents' winning percentage. That heavy weight on <em>who you play</em> is why scheduling tough opponents matters — even losses against elite teams can help your RPI.</p>
        </div>
        <div class="explainer-block">
          <h4>Why It Matters for USM</h4>
          <p>The NCAA Tournament selection committee uses RPI as a key factor when choosing and seeding the 64-team field. A top-30 RPI puts Southern Miss in national seed contention. Falling below #50 makes earning an at-large bid much harder. Every conference series and non-conference road trip directly shapes the number on this dashboard.</p>
        </div>
        <div class="explainer-block">
          <h4>The Quadrant System</h4>
          <p>The committee grades wins and losses by <strong>quadrant</strong> based on opponent RPI and game location. <strong>Q1 wins</strong> (top opponents, neutral/road) are the gold standard. <strong>Q4 losses</strong> (weak opponents at home) are the most damaging to a tournament résumé. Stacking Q1 and Q2 wins is the fastest way to improve seeding.</p>
        </div>
        <div class="explainer-block">
          <h4>The Road to Omaha</h4>
          <p>Sun Belt teams typically need a conference title or top-1-2 finish to earn an automatic bid. But a strong RPI opens the door to national seeds and home regional hosting — a massive advantage. Hosting a regional means playing the first two weekends at Pete Taylor Park, where USM has historically dominated. RPI is the key to that path.</p>
        </div>
      </div>
    </div>
  </div>

  <!-- WHAT-IF (conditional full width, wraps itself in .card) -->
  {whatif_section}

</main>

<footer>
  Southern Miss RPI Bot &nbsp;·&nbsp; Data: Warren Nolan &nbsp;·&nbsp; Generated {date_str}
</footer>

<script>
function toggleBrief() {{
  const body   = document.getElementById('briefBody');
  const toggle = document.getElementById('briefToggle');
  if (!body) return;
  const expanded = body.classList.toggle('expanded');
  toggle.innerHTML = expanded ? 'Read Less &#9650;' : 'Read More &#9660;';
}}
function copyDashLink() {{
  const url = 'https://jeffbank2.github.io/usm-rpi';
  navigator.clipboard.writeText(url).then(() => {{
    const btn = document.getElementById('copyBtn');
    btn.textContent = '✓  Copied!';
    btn.classList.add('copied');
    setTimeout(() => {{
      btn.innerHTML = '&#128279;&nbsp; Copy Link';
      btn.classList.remove('copied');
    }}, 2200);
  }}).catch(() => {{
    prompt('Copy this link:', url);
  }});
}}
function toggleExplainer() {{
  const body    = document.getElementById('explainerBody');
  const chevron = document.getElementById('explainerChevron');
  const toggle  = document.getElementById('explainerToggle');
  const open    = body.classList.toggle('open');
  chevron.classList.toggle('open', open);
  toggle.setAttribute('aria-expanded', open);
}}
</script>

<script>
(function() {{
  const ctx = document.getElementById('rankChart').getContext('2d');
  const grad = ctx.createLinearGradient(0, 0, 0, 220);
  grad.addColorStop(0,   'rgba(245,197,24,0.28)');
  grad.addColorStop(0.65,'rgba(245,197,24,0.05)');
  grad.addColorStop(1,   'rgba(245,197,24,0)');
  new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: {chart_labels},
      datasets: [{{
        label: 'RPI Rank',
        data: {chart_data},
        borderColor: '#f5c518',
        borderWidth: 2.5,
        backgroundColor: grad,
        tension: 0.42,
        pointBackgroundColor: '#f5c518',
        pointBorderColor: '#080808',
        pointBorderWidth: 2,
        pointRadius: 4,
        pointHoverRadius: 7,
        fill: true,
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: true,
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          backgroundColor: '#1e1e1e',
          borderColor: '#333',
          borderWidth: 1,
          titleColor: '#f5c518',
          bodyColor: '#c0c0c0',
          padding: 10,
          displayColors: false,
          callbacks: {{
            title: (items) => 'Rank: #' + items[0].raw,
            label: (item) => item.label,
          }}
        }}
      }},
      scales: {{
        y: {{
          reverse: true,
          ticks: {{ color: '#888888', font: {{ size: 10, family: "'Inter'" }} }},
          grid: {{ color: 'rgba(255,255,255,0.04)' }},
          border: {{ color: 'rgba(255,255,255,0.06)' }},
          title: {{
            display: true,
            text: 'Rank  (lower = better)',
            color: '#707070',
            font: {{ size: 9, weight: '600' }}
          }}
        }},
        x: {{
          ticks: {{ color: '#888888', maxTicksLimit: 7, font: {{ size: 10 }} }},
          grid: {{ color: 'rgba(255,255,255,0.02)' }},
          border: {{ color: 'rgba(255,255,255,0.06)' }}
        }}
      }}
    }}
  }});
}})();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Southern Miss RPI Bot -- Full Edition")
    parser.add_argument("--db",        default="southern_miss_rpi.db")
    parser.add_argument("--season",    type=int, default=2026)
    parser.add_argument("--json",      action="store_true", help="Print evidence bundle as JSON")
    parser.add_argument("--llm",       action="store_true", help="Use GPT narrative (requires OPENAI_API_KEY)")
    parser.add_argument("--verbose",   action="store_true")
    parser.add_argument("--html",      default="daily_dashboard.html", help="HTML output path")
    parser.add_argument("--no-open",   action="store_true", help="Do not auto-open browser")
    parser.add_argument("--no-rivals", action="store_true", help="Skip rival collection (faster)")
    parser.add_argument("--history",   action="store_true", help="Print rank history and exit")
    parser.add_argument("--week",      action="store_true", help="Print week-in-review and exit")
    args = parser.parse_args()

    bot = SouthernMissRPIBot(db_path=args.db, season=args.season, verbose=args.verbose)

    if args.history:
        for row in bot.get_rank_history():
            print(f"{row['date']}  RPI #{row['rpi_rank']}  ({row['rpi_value']})  {row['overall_record']}")
        return 0

    if args.week:
        print(bot.build_week_review())
        return 0

    try:
        print("Collecting Southern Miss snapshot...")
        current  = bot.collect_snapshot()
        bot.save_snapshot(current)
        previous = bot.get_previous_snapshot()

        rivals: List[Dict] = []
        if not args.no_rivals:
            print("Collecting rival snapshots (6 teams)...")
            rivals = bot.collect_rival_snapshots()

        evidence = bot.build_evidence(previous, current, rivals)

    except requests.HTTPError as exc:
        print(f"HTTP error: {exc}", file=sys.stderr)
        return 2
    except requests.RequestException as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1

    # Toast alert check
    bot.check_and_alert(evidence, threshold=3)

    if args.json:
        print(json.dumps(evidence, indent=2, ensure_ascii=False))
        return 0

    # GPT narrative
    narrative: Optional[str] = None
    if args.llm:
        print("Generating GPT narrative...")
        narrative = bot.render_llm_summary(evidence)
        if narrative:
            print("\n--- GPT Narrative ---")
            print(narrative)
            print("---------------------\n")

    # Plain text to stdout / daily_brief.txt
    print(bot.render_plain_summary(evidence))

    # HTML dashboard
    rank_history     = bot.get_rank_history(days=45)
    week_review      = bot.build_week_review()
    print("Building what-if scenarios...")
    whatif_scenarios = bot.build_whatif_scenarios(current)
    print("Collecting Sun Belt records...")
    sb_conf_records  = bot.collect_sunbelt_conf_records()
    html_content     = bot.render_html_dashboard(evidence, narrative, rank_history, week_review, whatif_scenarios, sb_conf_records)
    html_path = Path(args.html)
    html_path.write_text(html_content, encoding="utf-8")
    print(f"\nDashboard saved: {html_path.resolve()}")

    if not args.no_open:
        webbrowser.open(html_path.resolve().as_uri())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
