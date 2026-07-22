-- StatsBomb open-data schema (World Cup 2018 scope)
-- Loaded by etl.py. Not hand-edited data, just the table definitions.

CREATE TABLE competitions (
    competition_id INTEGER PRIMARY KEY,
    competition_name TEXT,
    country_name TEXT,
    competition_gender TEXT,
    competition_international INTEGER
);

CREATE TABLE seasons (
    season_id INTEGER PRIMARY KEY,
    season_name TEXT
);

-- One row per entry in competitions.json: which competition+season pairs exist.
CREATE TABLE competition_editions (
    competition_id INTEGER,
    season_id INTEGER,
    PRIMARY KEY (competition_id, season_id),
    FOREIGN KEY (competition_id) REFERENCES competitions(competition_id),
    FOREIGN KEY (season_id) REFERENCES seasons(season_id)
);

CREATE TABLE teams (
    team_id INTEGER PRIMARY KEY,
    team_name TEXT
);

CREATE TABLE matches (
    match_id INTEGER PRIMARY KEY,
    competition_id INTEGER,
    season_id INTEGER,
    match_date TEXT,
    kick_off TEXT,
    home_team_id INTEGER,
    away_team_id INTEGER,
    home_score INTEGER,
    away_score INTEGER,
    match_status TEXT,
    match_week INTEGER,
    competition_stage TEXT,
    stadium_name TEXT,
    referee_name TEXT,
    FOREIGN KEY (competition_id, season_id) REFERENCES competition_editions(competition_id, season_id),
    FOREIGN KEY (home_team_id) REFERENCES teams(team_id),
    FOREIGN KEY (away_team_id) REFERENCES teams(team_id)
);

CREATE TABLE players (
    player_id INTEGER PRIMARY KEY,
    player_name TEXT,
    player_nickname TEXT,
    country_name TEXT
);

CREATE TABLE lineups (
    match_id INTEGER,
    team_id INTEGER,
    player_id INTEGER,
    jersey_number INTEGER,
    PRIMARY KEY (match_id, team_id, player_id),
    FOREIGN KEY (match_id) REFERENCES matches(match_id),
    FOREIGN KEY (team_id) REFERENCES teams(team_id),
    FOREIGN KEY (player_id) REFERENCES players(player_id)
);

-- A player's position timeline within one match (starts, subs, tactical moves).
CREATE TABLE player_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER,
    player_id INTEGER,
    position_name TEXT,
    from_time TEXT,
    to_time TEXT,
    from_period INTEGER,
    to_period INTEGER,
    start_reason TEXT,
    end_reason TEXT,
    FOREIGN KEY (match_id) REFERENCES matches(match_id),
    FOREIGN KEY (player_id) REFERENCES players(player_id)
);

-- Base event table: fields every event type has in common.
CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    match_id INTEGER,
    event_index INTEGER,
    period INTEGER,
    timestamp TEXT,
    minute INTEGER,
    second INTEGER,
    type_name TEXT,
    possession INTEGER,
    possession_team_id INTEGER,
    play_pattern TEXT,
    team_id INTEGER,
    player_id INTEGER,
    position_name TEXT,
    location_x REAL,
    location_y REAL,
    duration REAL,
    FOREIGN KEY (match_id) REFERENCES matches(match_id),
    FOREIGN KEY (team_id) REFERENCES teams(team_id),
    FOREIGN KEY (player_id) REFERENCES players(player_id)
);

CREATE TABLE shot_events (
    event_id TEXT PRIMARY KEY,
    outcome_name TEXT,
    statsbomb_xg REAL,
    technique_name TEXT,
    body_part_name TEXT,
    end_location_x REAL,
    end_location_y REAL,
    end_location_z REAL,
    first_time INTEGER,
    FOREIGN KEY (event_id) REFERENCES events(event_id)
);

CREATE TABLE pass_events (
    event_id TEXT PRIMARY KEY,
    recipient_player_id INTEGER,
    length REAL,
    angle REAL,
    height_name TEXT,
    outcome_name TEXT,
    end_location_x REAL,
    end_location_y REAL,
    FOREIGN KEY (event_id) REFERENCES events(event_id),
    FOREIGN KEY (recipient_player_id) REFERENCES players(player_id)
);

-- Formation at a point in time for one team in one match.
-- Populated from BOTH 'Starting XI' events (the opening formation) and
-- 'Tactical Shift' events (mid-match changes) - same tactics.formation field
-- on both event types. To find "what formation was team X playing when event
-- Y happened", join to the latest formation_events row for that team/match
-- with event_index <= Y's event_index (see queries.sql once written).
CREATE TABLE formation_events (
    event_id TEXT PRIMARY KEY,
    match_id INTEGER,
    team_id INTEGER,
    formation INTEGER,
    FOREIGN KEY (event_id) REFERENCES events(event_id),
    FOREIGN KEY (match_id) REFERENCES matches(match_id),
    FOREIGN KEY (team_id) REFERENCES teams(team_id)
);

-- 360 freeze-frame data - only populated for matches that have it.
CREATE TABLE freeze_frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT,
    teammate INTEGER,
    actor INTEGER,
    keeper INTEGER,
    location_x REAL,
    location_y REAL,
    FOREIGN KEY (event_id) REFERENCES events(event_id)
);
