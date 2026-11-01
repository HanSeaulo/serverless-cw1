# Serverless Matchmaking Workflow – COMP3212 Coursework 1

A serverless matchmaking system for a competitive 5v5 game, built with Azure Functions (Python 3.12, Flex Consumption plan, France Central). Players join through an HTTP request and wait to be placed into a match. Ten players with similar Rank Points (RP) who have waited longest are grouped into two teams of five. One team wins and gains RP while the other loses RP, and players then either stop playing or rejoin the queue. If a player waits too long, the RP search range widens, and players who still cannot find a match are timed out.

## Workflow
HTTP POST /join → **join** → `waiting` table + `check-tier` queue → **matchmaker** → `matches-queue` (30 s delay) → **match_resolver** → `matches` table, and rejoining players go back to `waiting`.

The functions never call each other directly; they communicate only through queue messages and the tables. The matchmaker claims all 10 players in a single table transaction, so either all of them are claimed or none are, which prevents the same player being put in two matches.

## Files
| File | Purpose |
|---|---|
| `function_app.py` | The three functions: join, matchmaker, match_resolver |
| `host.json` | Queue message encoding, polling interval, logging settings |
| `requirements.txt` | Python packages installed by Azure |
| `loadtest.py` | Load generator: creates N players with random RP (0–599) and measures response time |
| `plot.py` | Generates the result figures |
| `results.txt` | Load test output per run (start/end time, failures, avg, p95) |
| `counts.json`, `functions.json`, `matches.json` | Data exported from App Insights and the matches table |
| `*.png` | Result plots |

## Deployment
Requires a Python 3.12 Azure Function App (Flex Consumption) with a storage account containing the `waiting` and `matches` tables and the `check-tier` and `matches-queue` queues. Deploy with:

    func azure functionapp publish <function-app-name> --python

## Configuration
Constants at the top of `function_app.py`:
- `RECHECK = 5` – seconds before the matchmaker checks again
- `BORROW_AFTER = 15` – seconds of waiting before the RP range widens by one rank each side
- `MAX_WAIT = 60` – seconds before a player is timed out
- `MATCH_TIME = 30` – simulated match length
- `RP_CHANGE = 25` – RP gained or lost per match
- `REJOIN_WIN = 0.6`, `REJOIN_LOSE = 0.4` – chance a winner / loser plays again

## Running the experiments
    python loadtest.py 5           # warm-up
    python loadtest.py 50 | tee -a results.txt

Runs used N = 50, 100, 250, 500 and 1000. Before each run a warm-up of 5 players was sent, and the next run only started once the waiting table was empty, so no two tests interfered with one another. The load test keeps 50 requests in flight at once.

Matches, timeouts, claim failures and quits were counted from Application Insights logs; invocations, execution times and instance counts from Application Insights requests; average wait and RP spread from the `matches` table.

## Plots
    pip install matplotlib
    python plot.py
