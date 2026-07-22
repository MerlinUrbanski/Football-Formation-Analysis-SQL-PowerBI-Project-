"""
Sanity-check statsbomb.db against the raw JSON it was built from.

This is "debugging the ETL": independently recompute a few numbers straight
from the source JSON, then compare against what's actually in the database.
If they match, the loader didn't drop/duplicate/mis-map anything for these
cases. If they don't, that's a bug to chase down before trusting any
dashboard built on top.
"""

import json
import sqlite3
from pathlib import Path

from etl import qualifying_editions

DATA_DIR = Path(__file__).parent / "data" / "data"
DB_PATH = Path(__file__).parent / "statsbomb.db"

CHECK_MATCH_ID = 8658  # 2018 World Cup Final: France 4-2 Croatia


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check(label, expected, actual):
    status = "OK" if expected == actual else "MISMATCH"
    print(f"  [{status}] {label}: expected {expected}, got {actual}")
    return expected == actual


def main():
    conn = sqlite3.connect(DB_PATH)
    all_ok = True

    print("=== Row-count sanity checks ===")
    all_competitions = load_json(DATA_DIR / "competitions.json")
    editions = qualifying_editions(all_competitions)
    n_matches_raw = sum(
        len(load_json(DATA_DIR / "matches" / str(c["competition_id"]) / f"{c['season_id']}.json"))
        for c in editions
    )
    n_matches_db = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    all_ok &= check("total matches (all 2000+ editions)", n_matches_raw, n_matches_db)

    print()
    print(f"=== Spot check: match {CHECK_MATCH_ID} (France 4-2 Croatia, final) ===")
    events = load_json(DATA_DIR / "events" / f"{CHECK_MATCH_ID}.json")

    n_events_raw = len(events)
    n_events_db = conn.execute(
        "SELECT COUNT(*) FROM events WHERE match_id = ?", (CHECK_MATCH_ID,)
    ).fetchone()[0]
    all_ok &= check("event count", n_events_raw, n_events_db)

    n_shots_raw = sum(1 for e in events if e["type"]["name"] == "Shot")
    n_shots_db = conn.execute(
        "SELECT COUNT(*) FROM events e JOIN shot_events s ON e.event_id = s.event_id "
        "WHERE e.match_id = ?",
        (CHECK_MATCH_ID,),
    ).fetchone()[0]
    all_ok &= check("shot count", n_shots_raw, n_shots_db)

    n_goals_raw = sum(
        1
        for e in events
        if e["type"]["name"] == "Shot" and e["shot"].get("outcome", {}).get("name") == "Goal"
    )
    n_goals_db = conn.execute(
        "SELECT COUNT(*) FROM events e JOIN shot_events s ON e.event_id = s.event_id "
        "WHERE e.match_id = ? AND s.outcome_name = 'Goal'",
        (CHECK_MATCH_ID,),
    ).fetchone()[0]
    all_ok &= check("goal count (should be 6: 4-2 scoreline)", n_goals_raw, n_goals_db)

    n_passes_raw = sum(1 for e in events if e["type"]["name"] == "Pass")
    n_passes_db = conn.execute(
        "SELECT COUNT(*) FROM events e JOIN pass_events p ON e.event_id = p.event_id "
        "WHERE e.match_id = ?",
        (CHECK_MATCH_ID,),
    ).fetchone()[0]
    all_ok &= check("pass count", n_passes_raw, n_passes_db)

    n_completed_raw = sum(
        1
        for e in events
        if e["type"]["name"] == "Pass" and "outcome" not in e.get("pass", {})
    )
    n_completed_db = conn.execute(
        "SELECT COUNT(*) FROM events e JOIN pass_events p ON e.event_id = p.event_id "
        "WHERE e.match_id = ? AND p.outcome_name IS NULL",
        (CHECK_MATCH_ID,),
    ).fetchone()[0]
    all_ok &= check("completed passes", n_completed_raw, n_completed_db)

    n_fouls_raw = sum(1 for e in events if e["type"]["name"] == "Foul Committed")
    n_fouls_db = conn.execute(
        "SELECT COUNT(*) FROM events WHERE match_id = ? AND type_name = 'Foul Committed'",
        (CHECK_MATCH_ID,),
    ).fetchone()[0]
    all_ok &= check("foul count", n_fouls_raw, n_fouls_db)

    print()
    print("=== Referential integrity ===")
    orphaned_shots = conn.execute(
        "SELECT COUNT(*) FROM shot_events s "
        "LEFT JOIN events e ON s.event_id = e.event_id WHERE e.event_id IS NULL"
    ).fetchone()[0]
    all_ok &= check("orphaned shot_events rows (should be 0)", 0, orphaned_shots)

    events_bad_team = conn.execute(
        "SELECT COUNT(*) FROM events e "
        "LEFT JOIN teams t ON e.team_id = t.team_id "
        "WHERE e.team_id IS NOT NULL AND t.team_id IS NULL"
    ).fetchone()[0]
    all_ok &= check("events with unknown team_id (should be 0)", 0, events_bad_team)

    conn.close()

    print()
    print("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED - investigate above")


if __name__ == "__main__":
    main()
