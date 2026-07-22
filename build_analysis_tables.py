"""
Rebuilds every derived table/view used by queries.sql and the Power BI
dashboard, in the correct dependency order:

  1. Indexes on the base tables (events, formation_events) - needed for
     everything below to run in reasonable time at full-dataset scale.
  2. event_formations   - which formation each event's team was using.
  3. formation_segments - per team/match, exact minutes played per formation.
  4. segment_possession - possession share per formation segment.
  5. formation_summary        - the flat, all-competitions-combined table.
  6. formation_segment_stats  - the granular (match+team+formation) fact
                                table Power BI actually connects to, so it
                                can filter by competition/season/team.

Run this once after etl.py finishes (or re-run any time to refresh these
tables after reloading the underlying data - e.g. with more competitions).
Safe to re-run: every object is dropped and recreated from scratch.

This consolidates SQL that was originally run ad hoc while debugging
performance at full-dataset scale (see queries.sql for the reasoning
behind each design choice - the materialized-table-instead-of-view fix,
the possession self-normalization fix, the type_name index, etc).
"""

import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "statsbomb.db"

STEPS = [
    (
        "base indexes",
        """
        CREATE INDEX IF NOT EXISTS idx_events_match_team_index
            ON events(match_id, team_id, event_index);
        CREATE INDEX IF NOT EXISTS idx_formation_events_match_team
            ON formation_events(match_id, team_id);
        CREATE INDEX IF NOT EXISTS idx_events_type_name
            ON events(type_name, event_id);
        CREATE INDEX IF NOT EXISTS idx_events_match_period_possteam_time
            ON events(match_id, period, possession_team_id, minute, second);
        """,
    ),
    (
        "event_formations",
        """
        DROP TABLE IF EXISTS event_formations;
        CREATE TABLE event_formations AS
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

        CREATE INDEX idx_event_formations_covering
            ON event_formations(event_id, formation);
        """,
    ),
    (
        "formation_segments",
        """
        DROP VIEW IF EXISTS formation_segments;
        CREATE VIEW formation_segments AS
        WITH match_end AS (
            SELECT match_id, MAX(minute * 60 + second) AS end_seconds
            FROM events WHERE period IN (1, 2) GROUP BY match_id
        ),
        ordered_formations AS (
            SELECT f.match_id, f.team_id, f.formation, fe.event_index,
                   fe.minute * 60 + fe.second AS start_seconds,
                   LEAD(fe.minute * 60 + fe.second) OVER (
                       PARTITION BY f.match_id, f.team_id ORDER BY fe.event_index
                   ) AS next_start_seconds
            FROM formation_events f JOIN events fe ON f.event_id = fe.event_id
            WHERE fe.period IN (1, 2)
        )
        SELECT o.match_id, o.team_id, o.formation, o.start_seconds,
               COALESCE(o.next_start_seconds, me.end_seconds) AS end_seconds,
               COALESCE(o.next_start_seconds, me.end_seconds) - o.start_seconds AS duration_seconds
        FROM ordered_formations o JOIN match_end me ON o.match_id = me.match_id;
        """,
    ),
    (
        "segment_possession",
        """
        DROP TABLE IF EXISTS segment_possession;
        CREATE TABLE segment_possession AS
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

        CREATE INDEX idx_segment_possession_formation
            ON segment_possession(formation);
        """,
    ),
    (
        "formation_summary",
        """
        DROP TABLE IF EXISTS formation_summary;
        CREATE TABLE formation_summary AS
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
        """,
    ),
    (
        "formation_segment_stats",
        """
        DROP TABLE IF EXISTS formation_segment_stats;
        CREATE TABLE formation_segment_stats AS
        WITH seg_minutes AS (
            SELECT match_id, team_id, formation, SUM(duration_seconds) / 60.0 AS minutes_played
            FROM formation_segments GROUP BY match_id, team_id, formation
        ),
        seg_shots AS (
            SELECT ef.match_id, ef.team_id, ef.formation, COUNT(*) AS shots,
                   SUM(s.statsbomb_xg) AS shots_xg
            FROM event_formations ef JOIN shot_events s ON ef.event_id = s.event_id
            GROUP BY ef.match_id, ef.team_id, ef.formation
        ),
        seg_passes AS (
            SELECT ef.match_id, ef.team_id, ef.formation, COUNT(*) AS passes,
                   SUM(CASE WHEN p.outcome_name IS NULL THEN 1 ELSE 0 END) AS passes_completed
            FROM event_formations ef JOIN pass_events p ON ef.event_id = p.event_id
            GROUP BY ef.match_id, ef.team_id, ef.formation
        ),
        seg_fouls AS (
            SELECT ef.match_id, ef.team_id, ef.formation, COUNT(*) AS fouls
            FROM event_formations ef JOIN events e ON ef.event_id = e.event_id
            WHERE e.type_name = 'Foul Committed' GROUP BY ef.match_id, ef.team_id, ef.formation
        ),
        seg_dribbles AS (
            SELECT ef.match_id, ef.team_id, ef.formation, COUNT(*) AS dribbles
            FROM event_formations ef JOIN events e ON ef.event_id = e.event_id
            WHERE e.type_name = 'Dribble' GROUP BY ef.match_id, ef.team_id, ef.formation
        ),
        seg_poss AS (
            SELECT match_id, team_id, formation,
                   SUM(own_ball_seconds) AS own_ball_seconds,
                   SUM(opp_ball_seconds) AS opp_ball_seconds
            FROM segment_possession GROUP BY match_id, team_id, formation
        )
        SELECT sm.match_id, sm.team_id, sm.formation, sm.minutes_played,
               COALESCE(sh.shots, 0) AS shots,
               COALESCE(sh.shots_xg, 0) AS shots_xg,
               COALESCE(p.passes, 0) AS passes,
               COALESCE(p.passes_completed, 0) AS passes_completed,
               COALESCE(f.fouls, 0) AS fouls,
               COALESCE(d.dribbles, 0) AS dribbles,
               COALESCE(pos.own_ball_seconds, 0) AS own_ball_seconds,
               COALESCE(pos.opp_ball_seconds, 0) AS opp_ball_seconds
        FROM seg_minutes sm
        LEFT JOIN seg_shots sh ON sm.match_id = sh.match_id AND sm.team_id = sh.team_id AND sm.formation = sh.formation
        LEFT JOIN seg_passes p ON sm.match_id = p.match_id AND sm.team_id = p.team_id AND sm.formation = p.formation
        LEFT JOIN seg_fouls f ON sm.match_id = f.match_id AND sm.team_id = f.team_id AND sm.formation = f.formation
        LEFT JOIN seg_dribbles d ON sm.match_id = d.match_id AND sm.team_id = d.team_id AND sm.formation = d.formation
        LEFT JOIN seg_poss pos ON sm.match_id = pos.match_id AND sm.team_id = pos.team_id AND sm.formation = pos.formation;

        CREATE INDEX idx_fss_match ON formation_segment_stats(match_id);
        CREATE INDEX idx_fss_team ON formation_segment_stats(team_id);
        """,
    ),
]


def build():
    conn = sqlite3.connect(DB_PATH)
    start = time.time()
    for name, sql in STEPS:
        t0 = time.time()
        conn.executescript(sql)
        conn.commit()
        print(f"  {name}: {time.time() - t0:.1f}s", flush=True)
    print(f"Done in {time.time() - start:.1f}s")
    conn.close()


if __name__ == "__main__":
    build()
