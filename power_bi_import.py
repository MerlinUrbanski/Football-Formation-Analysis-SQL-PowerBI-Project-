"""
Paste this into Power BI's "Python script" data source
(Get Data -> Other -> Python script). Power BI runs it and offers each
DataFrame below as a table to load.

This is a star schema: formation_segment_stats is the FACT table (one row
per match+team+formation, with raw counts - not pre-averaged), and
matches/teams/competitions/seasons are the LOOKUP tables that let Power BI
filter it by competition, season, or team. After loading, set up
relationships in Power BI's Model view:
    formation_segment_stats.match_id  -> matches.match_id
    formation_segment_stats.team_id   -> teams.team_id
    matches.competition_id            -> competitions.competition_id
    matches.season_id                 -> seasons.season_id
Then build rate measures (shots per 90, pass %, etc.) with DAX so they
recompute live as slicers/filters change.
"""

import sqlite3
import pandas as pd

conn = sqlite3.connect(r"c:\Users\merli\OneDrive\Dokumente\Statsbomb\statsbomb.db")

formation_segment_stats = pd.read_sql("SELECT * FROM formation_segment_stats", conn)
matches = pd.read_sql("SELECT * FROM matches", conn)
teams = pd.read_sql("SELECT * FROM teams", conn)
competitions = pd.read_sql("SELECT * FROM competitions", conn)
seasons = pd.read_sql("SELECT * FROM seasons", conn)

conn.close()
