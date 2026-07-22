"""
Load StatsBomb JSON into statsbomb.db (SQLite), following schema.sql.

Scoped to every (competition_id, season_id) whose season starts in
MIN_YEAR or later (derived live from competitions.json, not a hardcoded
list). Processes and commits one competition/season at a time so memory
stays bounded - at full 2000+ scope this is ~3,900 matches / ~13M events,
too much to hold in memory all at once (which the original World-Cup-2018-
only version did).
"""

import json
import sqlite3
import time
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data" / "data"
DB_PATH = Path(__file__).parent / "statsbomb.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

MIN_YEAR = 2000


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get(d, *keys, default=None):
    """Safe nested dict lookup: get(event, 'shot', 'outcome', 'name')."""
    for k in keys:
        if d is None:
            return default
        d = d.get(k)
    return d if d is not None else default


def start_year(season_name):
    # '2015/2016' -> 2015, '2023' -> 2023
    return int(season_name.split("/")[0])


def qualifying_editions(all_competitions):
    return [c for c in all_competitions if start_year(c["season_name"]) >= MIN_YEAR]


def load_edition(conn, edition):
    comp_id = edition["competition_id"]
    season_id = edition["season_id"]

    conn.execute(
        "INSERT OR IGNORE INTO competitions VALUES (?,?,?,?,?)",
        (
            comp_id,
            edition["competition_name"],
            edition["country_name"],
            edition["competition_gender"],
            int(edition["competition_international"]),
        ),
    )
    conn.execute(
        "INSERT OR IGNORE INTO seasons VALUES (?,?)",
        (season_id, edition["season_name"]),
    )
    conn.execute(
        "INSERT OR IGNORE INTO competition_editions VALUES (?,?)",
        (comp_id, season_id),
    )

    matches_path = DATA_DIR / "matches" / str(comp_id) / f"{season_id}.json"
    if not matches_path.exists():
        return 0, 0

    matches = load_json(matches_path)

    match_rows = []
    lineup_rows = []
    position_rows = []
    events_rows = []
    shot_rows = []
    pass_rows = []
    formation_rows = []
    freeze_frame_rows = []

    for m in matches:
        match_id = m["match_id"]
        home_id = m["home_team"]["home_team_id"]
        away_id = m["away_team"]["away_team_id"]

        conn.execute(
            "INSERT OR IGNORE INTO teams VALUES (?,?)",
            (home_id, m["home_team"]["home_team_name"]),
        )
        conn.execute(
            "INSERT OR IGNORE INTO teams VALUES (?,?)",
            (away_id, m["away_team"]["away_team_name"]),
        )

        match_rows.append(
            (
                match_id,
                comp_id,
                season_id,
                m["match_date"],
                m.get("kick_off"),
                home_id,
                away_id,
                m["home_score"],
                m["away_score"],
                m.get("match_status"),
                m.get("match_week"),
                get(m, "competition_stage", "name"),
                get(m, "stadium", "name"),
                get(m, "referee", "name"),
            )
        )

        # --- lineups + player positions ---
        lineups_path = DATA_DIR / "lineups" / f"{match_id}.json"
        if lineups_path.exists():
            for team_lineup in load_json(lineups_path):
                team_id = team_lineup["team_id"]
                for p in team_lineup["lineup"]:
                    player_id = p["player_id"]
                    conn.execute(
                        "INSERT OR IGNORE INTO players VALUES (?,?,?,?)",
                        (
                            player_id,
                            p["player_name"],
                            p.get("player_nickname"),
                            get(p, "country", "name"),
                        ),
                    )
                    lineup_rows.append(
                        (match_id, team_id, player_id, p.get("jersey_number"))
                    )
                    for pos in p.get("positions", []):
                        position_rows.append(
                            (
                                match_id,
                                player_id,
                                pos.get("position"),
                                pos.get("from"),
                                pos.get("to"),
                                pos.get("from_period"),
                                pos.get("to_period"),
                                pos.get("start_reason"),
                                pos.get("end_reason"),
                            )
                        )

        # --- events (+ shot/pass/formation detail rows) ---
        events_path = DATA_DIR / "events" / f"{match_id}.json"
        if not events_path.exists():
            continue
        events = load_json(events_path)

        for e in events:
            event_id = e["id"]
            type_name = e["type"]["name"]
            location = e.get("location")

            events_rows.append(
                (
                    event_id,
                    match_id,
                    e["index"],
                    e["period"],
                    e.get("timestamp"),
                    e.get("minute"),
                    e.get("second"),
                    type_name,
                    e.get("possession"),
                    get(e, "possession_team", "id"),
                    get(e, "play_pattern", "name"),
                    get(e, "team", "id"),
                    get(e, "player", "id"),
                    get(e, "position", "name"),
                    location[0] if location else None,
                    location[1] if location else None,
                    e.get("duration"),
                )
            )

            if type_name == "Shot" and "shot" in e:
                s = e["shot"]
                end_loc = s.get("end_location", [])
                shot_rows.append(
                    (
                        event_id,
                        get(s, "outcome", "name"),
                        s.get("statsbomb_xg"),
                        get(s, "technique", "name"),
                        get(s, "body_part", "name"),
                        end_loc[0] if len(end_loc) > 0 else None,
                        end_loc[1] if len(end_loc) > 1 else None,
                        end_loc[2] if len(end_loc) > 2 else None,
                        int(s.get("first_time", False)),
                    )
                )

            if type_name == "Pass" and "pass" in e:
                p = e["pass"]
                end_loc = p.get("end_location", [])
                pass_rows.append(
                    (
                        event_id,
                        get(p, "recipient", "id"),
                        p.get("length"),
                        p.get("angle"),
                        get(p, "height", "name"),
                        get(p, "outcome", "name"),
                        end_loc[0] if len(end_loc) > 0 else None,
                        end_loc[1] if len(end_loc) > 1 else None,
                    )
                )

            if type_name in ("Starting XI", "Tactical Shift") and "tactics" in e:
                formation_rows.append(
                    (
                        event_id,
                        match_id,
                        get(e, "team", "id"),
                        e["tactics"].get("formation"),
                    )
                )

        # --- 360 freeze frames (only exists for some matches) ---
        three_sixty_path = DATA_DIR / "three-sixty" / f"{match_id}.json"
        three_sixty_data = []
        if three_sixty_path.exists():
            try:
                three_sixty_data = load_json(three_sixty_path)
            except json.JSONDecodeError as exc:
                print(
                    f"    WARNING: skipping corrupted 360 file for match {match_id}: {exc}",
                    flush=True,
                )
        if three_sixty_data:
            for ff in three_sixty_data:
                event_id = ff["event_uuid"]
                for frame_player in ff.get("freeze_frame", []):
                    loc = frame_player.get("location", [None, None])
                    freeze_frame_rows.append(
                        (
                            event_id,
                            int(frame_player.get("teammate", False)),
                            int(frame_player.get("actor", False)),
                            int(frame_player.get("keeper", False)),
                            loc[0],
                            loc[1],
                        )
                    )

    conn.executemany(
        "INSERT INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", match_rows
    )
    conn.executemany("INSERT INTO lineups VALUES (?,?,?,?)", lineup_rows)
    conn.executemany(
        "INSERT INTO player_positions "
        "(match_id, player_id, position_name, from_time, to_time, "
        "from_period, to_period, start_reason, end_reason) VALUES (?,?,?,?,?,?,?,?,?)",
        position_rows,
    )
    conn.executemany(
        "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", events_rows
    )
    conn.executemany("INSERT INTO shot_events VALUES (?,?,?,?,?,?,?,?,?)", shot_rows)
    conn.executemany("INSERT INTO pass_events VALUES (?,?,?,?,?,?,?,?)", pass_rows)
    conn.executemany("INSERT INTO formation_events VALUES (?,?,?,?)", formation_rows)
    conn.executemany(
        "INSERT INTO freeze_frames "
        "(event_id, teammate, actor, keeper, location_x, location_y) VALUES (?,?,?,?,?,?)",
        freeze_frame_rows,
    )
    conn.commit()

    return len(match_rows), len(events_rows)


def build_database():
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    # Bulk one-time load, not a live multi-writer DB - trade durability for
    # speed (safe here since a crash just means rerunning this script).
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA journal_mode = MEMORY")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    all_competitions = load_json(DATA_DIR / "competitions.json")
    editions = qualifying_editions(all_competitions)

    print(f"{len(editions)} qualifying editions (season start year >= {MIN_YEAR})")

    total_matches = 0
    total_events = 0
    start = time.time()
    for i, edition in enumerate(editions, 1):
        n_matches, n_events = load_edition(conn, edition)
        total_matches += n_matches
        total_events += n_events
        elapsed = time.time() - start
        print(
            f"[{i}/{len(editions)}] {edition['competition_name']} {edition['season_name']}: "
            f"{n_matches} matches, {n_events} events ({elapsed:.0f}s elapsed)",
            flush=True,
        )

    print()
    print(f"Done: {total_matches} matches, {total_events} events loaded in {time.time()-start:.0f}s")
    print()
    print("Row counts:")
    for table in [
        "competitions",
        "seasons",
        "competition_editions",
        "teams",
        "matches",
        "players",
        "lineups",
        "player_positions",
        "events",
        "shot_events",
        "pass_events",
        "formation_events",
        "freeze_frames",
    ]:
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {n} rows")

    conn.close()


if __name__ == "__main__":
    build_database()
