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
        pattern = rf"Quadrant\s+{quadrant}.*?overall\s+(\d+-\d+)"
        m = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        return m.group(1) if m else None

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

    # ------------------------------------------------------------------
    # Database: save / retrieve
    # ------------------------------------------------------------------
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
            Keep it under 220 words. Write in clean prose paragraphs, no bullet points.

            JSON:
            {json.dumps(evidence, ensure_ascii=False, indent=2)}
        """)

        client = OpenAI(api_key=api_key)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
            )
            return response.choices[0].message.content.strip()
        except Exception as exc:
            self._log(f"OpenAI error: {exc}")
            return None

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
            q_html += f'<div class="q-card"><div class="q-label">{q.upper()}</div><div class="q-val">{val}</div></div>'

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Southern Miss RPI Dashboard -- {date_str}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:ital,wght@0,300;0,400;0,500;0,700;1,400&family=DM+Mono:wght@400;500&display=swap');
:root {{
  --gold: #f5c518; --black: #0a0a0a; --dark: #111; --card: #181818;
  --border: #262626; --text: #e2e2e2; --muted: #777; --status: {status_color};
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: var(--black); color: var(--text); font-family: 'DM Sans', sans-serif; font-size: 14px; line-height: 1.6; }}
header {{
  background: linear-gradient(135deg, #0a0a0a 50%, #1a1200 100%);
  border-bottom: 3px solid var(--gold);
  padding: 24px 36px 20px;
  display: flex; justify-content: space-between; align-items: flex-end;
}}
header h1 {{ font-family: 'Bebas Neue', sans-serif; font-size: 2.8rem; color: var(--gold); letter-spacing: 2px; line-height: 1; }}
header p  {{ color: var(--muted); font-size: 0.72rem; letter-spacing: 3px; text-transform: uppercase; margin-top: 4px; }}
.status-badge {{
  background: var(--status); color: #000;
  font-family: 'Bebas Neue', sans-serif; font-size: 1rem;
  padding: 6px 14px; border-radius: 4px; letter-spacing: 1px;
}}
.grid {{
  display: grid; grid-template-columns: repeat(12, 1fr);
  gap: 14px; padding: 20px 36px; max-width: 1500px; margin: 0 auto;
}}
.col-4  {{ grid-column: span 4; }}
.col-6  {{ grid-column: span 6; }}
.col-8  {{ grid-column: span 8; }}
.col-12 {{ grid-column: span 12; }}
@media (max-width: 900px) {{ .col-4,.col-6,.col-8 {{ grid-column: span 12; }} }}
.card {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 18px 22px; }}
.card h2 {{
  font-family: 'Bebas Neue', sans-serif; font-size: 0.9rem;
  letter-spacing: 2px; color: var(--gold); text-transform: uppercase;
  margin-bottom: 14px; border-bottom: 1px solid var(--border); padding-bottom: 8px;
}}
.metric-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
.metric {{ background: var(--dark); border-radius: 6px; padding: 14px; text-align: center; }}
.metric .val {{ font-family: 'Bebas Neue', sans-serif; font-size: 2.2rem; color: var(--gold); line-height: 1; }}
.metric .lbl {{ font-size: 0.65rem; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; margin-top: 3px; }}
.delta {{ font-size: 0.85rem; font-weight: 700; margin-left: 5px; }}
.delta.up   {{ color: #00c853; }}
.delta.down {{ color: #ff4d4f; }}
.delta.flat {{ color: var(--muted); }}
canvas {{ max-height: 190px; }}
ul {{ list-style: none; padding: 0; }}
ul li {{ padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 0.84rem; color: #ccc; }}
ul li:last-child {{ border-bottom: none; }}
ul.radar-list li {{ font-family: 'DM Mono', monospace; font-size: 0.82rem; }}
ul.radar-list li.usm {{ color: var(--gold); font-weight: 500; }}
.usm-tag {{ background: var(--gold); color: #000; font-size: 0.6rem; padding: 1px 5px; border-radius: 3px; margin-left: 6px; font-family: 'DM Sans', sans-serif; font-weight: 700; letter-spacing: 0.5px; vertical-align: middle; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.83rem; }}
th {{ color: var(--muted); font-weight: 500; text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--border); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px; }}
td {{ padding: 7px 8px; border-bottom: 1px solid #1e1e1e; }}
tr:last-child td {{ border-bottom: none; }}
.win  {{ color: #00c853; font-weight: 600; }}
.loss {{ color: #ff4d4f; font-weight: 600; }}
.q-row {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }}
.q-card {{ flex: 1; min-width: 55px; background: var(--dark); border-radius: 6px; padding: 10px 6px; text-align: center; }}
.q-label {{ font-size: 0.65rem; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; }}
.q-val   {{ font-family: 'Bebas Neue', sans-serif; font-size: 1.35rem; color: var(--gold); }}
.q-badge {{ display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 0.72rem; font-weight: 600; }}
.q1 {{ background: #00c85322; color: #00c853; border: 1px solid #00c85344; }}
.q2 {{ background: #69c0ff22; color: #69c0ff; border: 1px solid #69c0ff44; }}
.q3 {{ background: #ffa94022; color: #ffa940; border: 1px solid #ffa94044; }}
.q4 {{ background: #ff4d4f22; color: #ff4d4f; border: 1px solid #ff4d4f44; }}
.quadrantunknown {{ background: #33333322; color: #888; border: 1px solid #333; }}
.narrative {{ background: linear-gradient(135deg, #1a1400 0%, #181818 60%); border-color: #3a2e00; }}
.narrative h2 {{ color: var(--gold); border-color: #3a2e00; }}
.narrative p {{ font-size: 1.05rem; line-height: 1.85; color: #e0e0e0; font-style: normal; max-width: 960px; }}
.week-box {{ background: var(--dark); border-radius: 6px; padding: 12px 16px; font-family: 'DM Mono', monospace; font-size: 0.8rem; color: #bbb; line-height: 1.9; }}
.sos-note {{ background: #1a1300; border-left: 3px solid var(--gold); padding: 9px 12px; border-radius: 0 6px 6px 0; font-size: 0.84rem; color: #ccc; }}
.conf-tag {{ color: var(--muted); font-size: 0.75em; }}
footer {{ text-align: center; color: var(--muted); font-size: 0.72rem; padding: 20px 36px; border-top: 1px solid var(--border); margin-top: 4px; }}
</style>
</head>
<body>
<header>
  <div>
    <h1>Southern Miss Baseball</h1>
    <p>RPI Intelligence Dashboard &nbsp;·&nbsp; {date_str}</p>
  </div>
  <div class="status-badge">{status_icon} &nbsp; {status_label}</div>
</header>

<div class="grid">

  {narrative_html}

  <div class="card col-4">
    <h2>Key Metrics</h2>
    <div class="metric-grid">
      <div class="metric"><div class="val">#{rank} {delta_html}</div><div class="lbl">RPI Rank</div></div>
      <div class="metric"><div class="val">{record}</div><div class="lbl">Record</div></div>
      <div class="metric"><div class="val">{rpi_val:.4f}</div><div class="lbl">RPI Value</div></div>
      <div class="metric"><div class="val">#{sos_rank}</div><div class="lbl">SOS Rank</div></div>
    </div>
  </div>

  <div class="card col-4">
    <h2>Quadrant Records</h2>
    <div class="q-row">{q_html}</div>
    <div class="sos-note">{sos_traj}</div>
  </div>

  <div class="card col-4">
    <h2>Rank Trend (45 days)</h2>
    <canvas id="rankChart"></canvas>
  </div>

  <div class="card col-4">
    <h2>Why It Moved</h2>
    <ul>{drivers_html or "<li>No drivers yet.</li>"}</ul>
  </div>

  <div class="card col-4">
    <h2>RPI Radar</h2>
    <ul class="radar-list">{radar_html}</ul>
  </div>

  <div class="card col-4">
    <h2>Rival Watch</h2>
    <table>
      <thead><tr><th>Team</th><th>Conf</th><th>RPI</th><th>Record</th></tr></thead>
      <tbody>{rivals_rows or "<tr><td colspan='4'>No rival data.</td></tr>"}</tbody>
    </table>
  </div>

  <div class="card col-6">
    <h2>Upcoming Games</h2>
    <table>
      <thead><tr><th>Opponent</th><th>Site</th><th>Opp RPI</th><th>Quadrant</th><th>Time</th></tr></thead>
      <tbody>{upcoming_rows or "<tr><td colspan='5'>No upcoming games found.</td></tr>"}</tbody>
    </table>
  </div>

  <div class="card col-6">
    <h2>Recent Results</h2>
    <table>
      <thead><tr><th>Opponent</th><th>Result</th><th>Site</th><th>Opp RPI</th></tr></thead>
      <tbody>{recent_rows or "<tr><td colspan='4'>No recent games found.</td></tr>"}</tbody>
    </table>
  </div>

  <div class="card col-6">
    <h2>Impact Games / Watchlist</h2>
    <ul>{watchlist_html or "<li>No items.</li>"}</ul>
  </div>

  <div class="card col-6">
    <h2>Week in Review</h2>
    <div class="week-box">{week_html}</div>
  </div>

</div>

<footer>Southern Miss RPI Bot &nbsp;·&nbsp; Data: Warren Nolan &nbsp;·&nbsp; Generated {date_str}</footer>

<script>
const ctx = document.getElementById('rankChart').getContext('2d');
new Chart(ctx, {{
  type: 'line',
  data: {{
    labels: {chart_labels},
    datasets: [{{
      label: 'RPI Rank',
      data: {chart_data},
      borderColor: '#f5c518',
      backgroundColor: 'rgba(245,197,24,0.07)',
      tension: 0.35,
      pointBackgroundColor: '#f5c518',
      pointRadius: 4,
      fill: true,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{
        reverse: true,
        ticks: {{ color: '#666', stepSize: 1 }},
        grid: {{ color: '#1e1e1e' }},
        title: {{ display: true, text: 'Rank (lower = better)', color: '#555', font: {{ size: 10 }} }}
      }},
      x: {{
        ticks: {{ color: '#666', maxTicksLimit: 8 }},
        grid: {{ color: '#161616' }}
      }}
    }}
  }}
}});
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
    rank_history = bot.get_rank_history(days=45)
    week_review  = bot.build_week_review()
    html_content = bot.render_html_dashboard(evidence, narrative, rank_history, week_review)

    html_path = Path(args.html)
    html_path.write_text(html_content, encoding="utf-8")
    print(f"\nDashboard saved: {html_path.resolve()}")

    if not args.no_open:
        webbrowser.open(html_path.resolve().as_uri())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
