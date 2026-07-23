# Football Formation Analysis — SQL + Power BI

This repository includes a short demo video, `Dashboard-Demo.mp4`, showing the finished Power BI dashboard in action — worth watching first for a quick sense of what this project produces before reading how it was built. Also available on YouTube: https://youtu.be/s2RuBZ_v4vE

## What this project is

This is a portfolio project built around one question: **does a team's formation (4-3-3, 4-4-2, and so on) actually affect how it plays — its shots, passing, fouls, and possession — and can that be measured fairly from raw match data?**

It's built end-to-end, starting from raw, messy JSON files and ending in an interactive dashboard, specifically to demonstrate SQL and data engineering skills alongside dashboard design — not just plotting a ready-made spreadsheet.

## The data

The data comes from [StatsBomb's open-data project](https://github.com/hudl/open-data) (StatsBomb is now part of Hudl), which freely publishes extremely detailed event-by-event data for thousands of professional football matches. Every pass, shot, tackle, foul, and tactical substitution is recorded as its own event, tagged with the exact minute, second, and pitch coordinates it happened at.

This project uses every competition and season in the dataset starting from the year 2000 — major leagues (Premier League, La Liga, Ligue 1, Bundesliga, Serie A), international tournaments (World Cup, European Championships, Copa América), and several women's competitions. In total: roughly **3,900 matches and 13.8 million individual events**.

## How it's built

The raw data ships as one deeply nested JSON file per match — not something you can plug directly into a dashboard. The pipeline works in three stages:

1. **Extract & flatten (Python).** A script (`etl.py`) reads every match's JSON files (events, lineups, match metadata) and pulls out the fields that matter, handling the fact that a "Pass" event and a "Shot" event carry completely different nested data.
2. **Load into a relational database (SQLite).** The flattened data is loaded into a proper SQL schema (`schema.sql`) with separate, linked tables — competitions, matches, teams, players, events, plus detail tables for shots and passes — rather than one giant flat spreadsheet. This is the same kind of schema design used in real production databases.
3. **Analyze and model for Power BI.** SQL queries (`queries.sql`) compute the actual tactical statistics, and the results are loaded into Power BI as a star schema (one fact table plus lookup tables for competition, season, and team) so the dashboard can be filtered live, rather than showing one fixed, pre-computed view.

## The tricky part: measuring formations fairly

Teams frequently change formation mid-match (e.g. switching from an attacking 4-3-3 to a defensive 5-4-1 to protect a lead). A naive analysis would just count "how many shots happened while a team was in each formation" — but that unfairly favors whichever formation a team happened to play the most minutes in.

The fix: every formation change is tracked with its exact timestamp, so the database knows precisely how many minutes each team spent in each formation, down to the segment. Every statistic is then expressed **per 90 minutes actually played in that formation**, not per game. The same care was needed for possession, which isn't a field StatsBomb provides directly — it had to be derived from event timing data, and an early version of that calculation was quietly wrong (it compared two things that don't actually measure the same clock, deflating every possession number to ~30% instead of ~50%) until it was caught by sanity-checking the result against what should be mathematically true.

## Repository contents

| File | Purpose |
|---|---|
| `schema.sql` | SQL table definitions for the database |
| `etl.py` | Reads the raw JSON and builds `statsbomb.db` |
| `validate.py` | Cross-checks the loaded database against the raw JSON to catch loading bugs |
| `queries.sql` | The SQL logic behind every dashboard metric, with reasoning comments |
| `build_analysis_tables.py` | Rebuilds all derived/summary tables in one step |
| `power_bi_import.py` | Script used by Power BI to pull tables from the database |
| `explore.ipynb` | A guided notebook for exploring the raw data interactively |
| `Formation_Analysis.pbix` | The finished Power BI dashboard file |
| `Dashboard-Demo.mp4` | A short screen recording of the dashboard in use |

`statsbomb.db` (the built database) and the raw `data/` folder are not included in this repository due to size (several GB) — see "Reproducing this project" below.

## Reproducing this project

1. Clone the [StatsBomb open-data repository](https://github.com/hudl/open-data) into a `data/` subfolder.
2. Run `python etl.py` to build `statsbomb.db` (takes roughly 15-20 minutes for the full 2000+ dataset).
3. Run `python build_analysis_tables.py` to build the summary tables the dashboard uses.
4. Run `python validate.py` to confirm the load matches the source data.
5. Open `Formation_Analysis.pbix` in Power BI Desktop and refresh the data source.

## Skills demonstrated

- Relational database schema design
- Writing and optimizing SQL, including window functions and performance tuning at multi-million-row scale
- Building a Python ETL pipeline from raw, nested JSON to a structured database
- Debugging data pipelines: catching both loading bugs and subtle logical/statistical errors through systematic cross-checking
- Data modeling (star schema) and dashboard design in Power BI

## Data source & license

Data provided by [StatsBomb](https://github.com/hudl/open-data) (Hudl), made freely available for research and educational use under their open-data license.
