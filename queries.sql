-- Example queries for the dashboard metrics, run against statsbomb.db.
-- "period IN (1, 2)" = 90 min + stoppage, excludes extra time (periods 3/4)
-- and penalties (period 5) - see the period check we ran earlier against a
-- match that went to ET+penalties.
--
-- Originally built and tuned against World Cup 2018 alone (227K events).
-- Rerun at full 2000+ scope (3,926 matches / 13.8M events) several of these
-- patterns turned out too slow to use as-is - see the notes next to
-- event_formations, segment_possession, and idx_events_type_name below for
-- what changed and why. General lesson: a query that's instant at 64
-- matches is not guaranteed fine at 3,926 - always re-time after a scope
-- change this large, don't assume it just scales.

-- Indexes to make the formation "as-of" join below fast.
CREATE INDEX IF NOT EXISTS idx_events_match_team_index
    ON events(match_id, team_id, event_index);
CREATE INDEX IF NOT EXISTS idx_formation_events_match_team
    ON formation_events(match_id, team_id);
-- Speeds up WHERE type_name = '...' filters (fouls, dribbles, etc.) - at
-- full scale this turned a 13.8M-row table scan into an index seek.
CREATE INDEX IF NOT EXISTS idx_events_type_name
    ON events(type_name, event_id);


-- 1. Shots FOR each team (shots they took), across all games, 90+stoppage only.
SELECT t.team_name, COUNT(*) AS shots_for
FROM events e
JOIN shot_events s ON e.event_id = s.event_id
JOIN teams t ON e.team_id = t.team_id
WHERE e.period IN (1, 2)
GROUP BY t.team_name
ORDER BY shots_for DESC;

-- 1b. Shots AGAINST each team (shots their opponent took), same scope.
SELECT t.team_name AS team, COUNT(*) AS shots_against
FROM events e
JOIN shot_events s ON e.event_id = s.event_id
JOIN matches m ON e.match_id = m.match_id
JOIN teams t ON t.team_id = CASE
    WHEN e.team_id = m.home_team_id THEN m.away_team_id
    ELSE m.home_team_id
END
WHERE e.period IN (1, 2)
GROUP BY t.team_name
ORDER BY shots_against DESC;


-- 2. Total passes + completion % per team, per game.
SELECT m.match_id, t.team_name,
       COUNT(*) AS total_passes,
       ROUND(SUM(CASE WHEN p.outcome_name IS NULL THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1)
           AS completion_pct
FROM events e
JOIN pass_events p ON e.event_id = p.event_id
JOIN matches m ON e.match_id = m.match_id
JOIN teams t ON e.team_id = t.team_id
WHERE e.period IN (1, 2)
GROUP BY m.match_id, t.team_name
ORDER BY m.match_id, t.team_name;


-- 3. Fouls per game, per team.
SELECT m.match_id, t.team_name, COUNT(*) AS fouls
FROM events e
JOIN matches m ON e.match_id = m.match_id
JOIN teams t ON e.team_id = t.team_id
WHERE e.type_name = 'Foul Committed' AND e.period IN (1, 2)
GROUP BY m.match_id, t.team_name
ORDER BY m.match_id, t.team_name;


-- 4. Dribbles per game, per team (attempted - complete + incomplete).
SELECT m.match_id, t.team_name, COUNT(*) AS dribbles
FROM events e
JOIN matches m ON e.match_id = m.match_id
JOIN teams t ON e.team_id = t.team_id
WHERE e.type_name = 'Dribble' AND e.period IN (1, 2)
GROUP BY m.match_id, t.team_name
ORDER BY m.match_id, t.team_name;


-- 5. Possession % per team per game, via share of total event duration
--    while that team had the ball (StatsBomb has no official possession%
--    field - this is the standard way analysts derive one from this data).
WITH poss AS (
    SELECT match_id, possession_team_id, SUM(duration) AS secs
    FROM events
    WHERE period IN (1, 2) AND possession_team_id IS NOT NULL
    GROUP BY match_id, possession_team_id
)
SELECT p.match_id, t.team_name,
       ROUND(p.secs * 100.0 / SUM(p.secs) OVER (PARTITION BY p.match_id), 1) AS possession_pct
FROM poss p
JOIN teams t ON p.possession_team_id = t.team_id
ORDER BY p.match_id, t.team_name;


-- 6. Formation comparison.
-- First, a TABLE (not a view - see below) tagging every event with whichever
-- formation that team was using AT THAT MOMENT (the most recent Starting XI
-- / Tactical Shift for that team+match, at or before this event). This is
-- what makes tactical shifts usable, not just the starting formation.
--
-- Why a materialized table instead of a view: at World Cup 2018 scale
-- (227K events) a per-row correlated subquery view was fine. At full
-- 2000+ scale (13.8M events) the same view took 60-180s PER QUERY that
-- joined against it, because SQLite's planner sometimes drove the join
-- from the wrong (larger) side. Materializing once (~80s, paid a single
-- time) plus a covering index brings every subsequent query down to
-- single-digit seconds. This is the same "does it actually scale"
-- debugging instinct as everywhere else in this project - a query that's
-- fine at 64 matches is not guaranteed fine at 3,926.
CREATE TABLE IF NOT EXISTS event_formations AS
WITH tagged AS (
    SELECT e.event_id, e.match_id, e.team_id, e.event_index,
           f.formation AS own_formation
    FROM events e
    LEFT JOIN formation_events f ON f.event_id = e.event_id
    WHERE e.team_id IS NOT NULL
),
governing AS (
    SELECT event_id, match_id, team_id, event_index,
           MAX(CASE WHEN own_formation IS NOT NULL THEN event_index END)
               OVER (PARTITION BY match_id, team_id ORDER BY event_index
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS governing_index
    FROM tagged
)
SELECT g.event_id, g.match_id, g.team_id, fe.formation
FROM governing g
JOIN events ge ON ge.match_id = g.match_id AND ge.team_id = g.team_id
    AND ge.event_index = g.governing_index
JOIN formation_events fe ON fe.event_id = ge.event_id;

-- Covering index: lets a JOIN ... ON event_id lookup read the formation
-- straight from the index without touching the table at all. This one
-- index is what took the passes-by-formation query from 177s to 5s.
CREATE INDEX IF NOT EXISTS idx_event_formations_covering
    ON event_formations(event_id, formation);

-- 6a. Total shots and average xG per formation, across all teams/games.
SELECT ef.formation,
       COUNT(*) AS total_shots,
       ROUND(AVG(s.statsbomb_xg), 3) AS avg_xg,
       COUNT(DISTINCT ef.match_id || '-' || ef.team_id) AS team_match_appearances
FROM event_formations ef
JOIN shot_events s ON ef.event_id = s.event_id
GROUP BY ef.formation
ORDER BY total_shots DESC;

-- 6b. Same idea for fouls per formation (swap the join/filter for any
--     other event type - dribbles, passes, etc. follow the same pattern).
SELECT ef.formation, COUNT(*) AS fouls
FROM event_formations ef
JOIN events e ON ef.event_id = e.event_id
WHERE e.type_name = 'Foul Committed'
GROUP BY ef.formation
ORDER BY fouls DESC;


-- 6c. Formation "playing time" - minutes of the 90+stoppage window each
--     team actually spent in each formation. A team that plays 80 minutes
--     in 4-3-3 and switches to 4-4-2 for the last 10 racks up far fewer
--     4-4-2 events just from less exposure, not because 4-4-2 performs
--     worse - so raw totals (6a/6b) are misleading without this.
--
-- Logic: each formation "segment" runs from its own start time (the
-- Starting XI / Tactical Shift event's minute:second - confirmed continuous
-- across the half-time boundary, no reset at 45') to the NEXT formation
-- change for that team, or to full-time for the last segment.
CREATE VIEW IF NOT EXISTS formation_segments AS
WITH match_end AS (
    SELECT match_id, MAX(minute * 60 + second) AS end_seconds
    FROM events
    WHERE period IN (1, 2)
    GROUP BY match_id
),
ordered_formations AS (
    SELECT
        f.match_id,
        f.team_id,
        f.formation,
        fe.event_index,
        fe.minute * 60 + fe.second AS start_seconds,
        LEAD(fe.minute * 60 + fe.second) OVER (
            PARTITION BY f.match_id, f.team_id ORDER BY fe.event_index
        ) AS next_start_seconds
    FROM formation_events f
    JOIN events fe ON f.event_id = fe.event_id
    WHERE fe.period IN (1, 2)
)
SELECT
    o.match_id,
    o.team_id,
    o.formation,
    o.start_seconds,
    COALESCE(o.next_start_seconds, me.end_seconds) AS end_seconds,
    COALESCE(o.next_start_seconds, me.end_seconds) - o.start_seconds AS duration_seconds
FROM ordered_formations o
JOIN match_end me ON o.match_id = me.match_id;

-- 6d. The actual normalized comparison: shots per 90 minutes played in
--     each formation, and xG per 90 - this is what you'd chart, not 6a.
WITH formation_minutes AS (
    SELECT formation, SUM(duration_seconds) / 60.0 AS total_minutes
    FROM formation_segments
    GROUP BY formation
),
formation_shots AS (
    SELECT ef.formation, COUNT(*) AS total_shots, SUM(s.statsbomb_xg) AS total_xg
    FROM event_formations ef
    JOIN shot_events s ON ef.event_id = s.event_id
    GROUP BY ef.formation
)
SELECT
    fm.formation,
    ROUND(fm.total_minutes, 0) AS minutes_played,
    fs.total_shots,
    ROUND(fs.total_shots * 90.0 / fm.total_minutes, 2) AS shots_per_90,
    ROUND(fs.total_xg * 90.0 / fm.total_minutes, 3) AS xg_per_90
FROM formation_minutes fm
JOIN formation_shots fs ON fm.formation = fs.formation
ORDER BY shots_per_90 DESC;


-- 7. Possession-by-formation.
--
-- IMPORTANT gotcha caught by sanity-checking: event `duration` only covers
-- the "active" portion of an action (e.g. a pass takes ~1 second to
-- execute) - it does NOT cover the full match clock (off-ball time, walking,
-- stoppages have no duration recorded against them at all). So comparing
-- summed durations against wall-clock minutes (like formation_minutes)
-- systematically deflates possession to ~30% instead of ~50%. Caught this
-- by checking that the weighted average across formations should land near
-- 50% (since exactly one team has the ball at any instant) - it didn't,
-- until fixed below.
--
-- The fix: for each formation segment, compare a team's ball-time only
-- against their OPPONENT's ball-time during that exact same clock window -
-- self-normalizing, same trick query 5 already used at the match level
-- (39.5% + 60.5% = 100%). This avoids the wall-clock mismatch entirely.
--
-- MATERIALIZED (not a view) for the same reason as event_formations: at
-- full 2000+ scale, evaluating these correlated subqueries on every read
-- took 180s+. Building once (~35s, helped by the index below) then reading
-- is instant.
CREATE INDEX IF NOT EXISTS idx_events_match_period_possteam_time
    ON events(match_id, period, possession_team_id, minute, second);

CREATE TABLE IF NOT EXISTS segment_possession AS
SELECT
    seg.match_id, seg.team_id, seg.formation, seg.duration_seconds,
    (
        SELECT SUM(e.duration) FROM events e
        WHERE e.match_id = seg.match_id AND e.period IN (1, 2)
          AND e.possession_team_id = seg.team_id
          AND (e.minute * 60 + e.second) >= seg.start_seconds
          AND (e.minute * 60 + e.second) < seg.end_seconds
    ) AS own_ball_seconds,
    (
        SELECT SUM(e.duration) FROM events e
        JOIN matches m ON e.match_id = m.match_id
        WHERE e.match_id = seg.match_id AND e.period IN (1, 2)
          AND e.possession_team_id = (
              CASE WHEN seg.team_id = m.home_team_id THEN m.away_team_id ELSE m.home_team_id END
          )
          AND (e.minute * 60 + e.second) >= seg.start_seconds
          AND (e.minute * 60 + e.second) < seg.end_seconds
    ) AS opp_ball_seconds
FROM formation_segments seg;

CREATE INDEX IF NOT EXISTS idx_segment_possession_formation
    ON segment_possession(formation);

-- sanity check: should print ~50.0
-- SELECT SUM(own_ball_seconds)*100.0/(SUM(own_ball_seconds)+SUM(opp_ball_seconds)) FROM segment_possession;


-- 8. THE MASTER TABLE - every requested metric, per formation, normalized
--    per 90 minutes played in that formation.
--
-- At full 2000+ scale this WITH-query itself still takes 90-125s to run
-- (it's aggregating over 13.8M events even with all the indexes above) -
-- fine to run once, but not something to make Power BI redo on every
-- refresh. So it's materialized into formation_summary below (26 rows,
-- instant to read) - THAT table is what Power BI should actually connect
-- to, not this query directly. Rerun this block (DROP + recreate
-- formation_summary) whenever the underlying data changes, e.g. after
-- reloading with more competitions.
WITH formation_minutes AS (
    SELECT formation, SUM(duration_seconds) / 60.0 AS total_minutes
    FROM formation_segments GROUP BY formation
),
shots AS (
    SELECT ef.formation, COUNT(*) AS n, SUM(s.statsbomb_xg) AS xg
    FROM event_formations ef JOIN shot_events s ON ef.event_id = s.event_id
    GROUP BY ef.formation
),
passes AS (
    SELECT ef.formation, COUNT(*) AS n,
           SUM(CASE WHEN p.outcome_name IS NULL THEN 1 ELSE 0 END) AS completed
    FROM event_formations ef JOIN pass_events p ON ef.event_id = p.event_id
    GROUP BY ef.formation
),
fouls AS (
    SELECT ef.formation, COUNT(*) AS n
    FROM event_formations ef JOIN events e ON ef.event_id = e.event_id
    WHERE e.type_name = 'Foul Committed' GROUP BY ef.formation
),
dribbles AS (
    SELECT ef.formation, COUNT(*) AS n
    FROM event_formations ef JOIN events e ON ef.event_id = e.event_id
    WHERE e.type_name = 'Dribble' GROUP BY ef.formation
),
possession AS (
    SELECT formation,
           SUM(own_ball_seconds) AS own_secs,
           SUM(opp_ball_seconds) AS opp_secs
    FROM segment_possession GROUP BY formation
)
SELECT
    fm.formation,
    ROUND(fm.total_minutes, 0) AS minutes_played,
    ROUND(sh.n * 90.0 / fm.total_minutes, 2) AS shots_per_90,
    ROUND(sh.xg * 90.0 / fm.total_minutes, 3) AS xg_per_90,
    ROUND(p.n * 90.0 / fm.total_minutes, 1) AS passes_per_90,
    ROUND(p.completed * 100.0 / p.n, 1) AS pass_completion_pct,
    ROUND(f.n * 90.0 / fm.total_minutes, 2) AS fouls_per_90,
    ROUND(d.n * 90.0 / fm.total_minutes, 2) AS dribbles_per_90,
    ROUND(pos.own_secs * 100.0 / (pos.own_secs + pos.opp_secs), 1) AS possession_pct
FROM formation_minutes fm
JOIN shots sh ON fm.formation = sh.formation
JOIN passes p ON fm.formation = p.formation
JOIN fouls f ON fm.formation = f.formation
JOIN dribbles d ON fm.formation = d.formation
JOIN possession pos ON fm.formation = pos.formation
ORDER BY minutes_played DESC;

-- To actually create/refresh formation_summary, wrap the query above as:
--   DROP TABLE IF EXISTS formation_summary;
--   CREATE TABLE formation_summary AS <query above>;
-- This is what Power BI connects to - see setup notes when we get there.
