import os
import uuid 
import json
import random
import logging
import datetime
import azure.functions as func
from azure.core import MatchConditions
from azure.storage.queue import QueueClient
from azure.data.tables import TableClient, TableTransactionError

logging.getLogger("azure").setLevel(logging.WARNING)

app = func.FunctionApp()

# CONSTANTS 
RECHECK = 5
BORROW_AFTER = 15
MAX_WAIT = 60

RP_CHANGE = 25
MATCH_TIME = 30
REJOIN_WIN = 0.6
REJOIN_LOSE = 0.4

# QUEUES and TABLES
conn = os.environ["AzureWebJobsStorage"]
waiting = TableClient.from_connection_string(conn, table_name="waiting")
checks  = QueueClient.from_connection_string(conn, "check-tier")
matches_q = QueueClient.from_connection_string(conn, "matches-queue")
matches_t = TableClient.from_connection_string(conn, table_name="matches")

# function to make sure that all players are claimed together for a match
# if even 1 of the 10 isnt claimable then the match cannot start
def claim_all(players):
    ops = []
    for p in players:
        version_i_saw = p.metadata["etag"]     # the rows version stamp when we read it
        conditions = {"etag": version_i_saw, "match_condition": MatchConditions.IfNotModified}
        ops.append(("delete", p, conditions))  # delete p only if unchanged

    try:
        waiting.submit_transaction(ops)        # all deletes happen together or none do
        return True
    except TableTransactionError:
        return False


# helper function to add a plyer to the waiting queue
def add_to_queue(p_id, rp):
    join_time = datetime.datetime.now(datetime.timezone.utc)
    waiting.upsert_entity({"PartitionKey": "queue", "RowKey": str(p_id), "rp": rp, "joined_at": join_time})
    checks.send_message("check")


# FUNCTION 1: GETS THE PLAYER AND PUTS THEM IN THE WAITING QUEUE
@app.route(route="join", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def join(req: func.HttpRequest) -> func.HttpResponse:
    body = req.get_json()          # {"player_id": ..., "rp": ...}
    
    p_id = body["player_id"]
    p_rp = int(body["rp"])

    add_to_queue(p_id, p_rp)

    return func.HttpResponse(f"{p_id} joined with {p_rp}RP\n")


# finding the range of rp for the match that will be created
def assign_band(prp, wait_time):
    tier  = min( prp // 100, 5)     # 0=Bronze, 1=Silver, ..., 5=Champion
    widen = int(wait_time // BORROW_AFTER)
    low   = tier * 100 - (100*widen)
    high  = tier * 100 + 99 + (100*widen)
    if tier == 5:
        high = 999999
    return low, high


# FUNCTION 2: FINDS 10 PLAYERS WITH SIMILAR RP AND STARTS A MATCH
@app.queue_trigger(arg_name="msg", queue_name="check-tier", connection="AzureWebJobsStorage")
def matchmaker(msg: func.QueueMessage):

    # get all waiting players, oldest first
    players = list(waiting.query_entities("PartitionKey eq 'queue'"))
    players.sort(key=lambda p: p["joined_at"])
    
    # sort out the active and timed_out players
    active = []
    timed_out = []
    time_now = datetime.datetime.now(datetime.timezone.utc)
    for player in players:
        waited = (time_now - player["joined_at"]).total_seconds()
        if waited > MAX_WAIT:
            timed_out.append(player)
        else:
            active.append(player)
    #remove the timed_out players from queueing
    for player in timed_out:
        if claim_all([player]):
            logging.info(f"TIMEOUT {player['RowKey']} rp={player['rp']}")
    #try to start a match with the active players
    for player in active:
        waited = (time_now - player["joined_at"]).total_seconds()
        low, high = assign_band(player["rp"], waited)
    #find the active players whose rp is inside that band
        in_band = []
        for p2 in active:
            if p2["rp"] >= low and p2["rp"] <= high:
                in_band.append(p2)
            if len(in_band) >= 10:
                break
        if len(in_band) == 10:
            if claim_all(in_band):
                match = []
                for playerInMatch in in_band:
                    temp = {"player_id": playerInMatch["RowKey"], "rp": playerInMatch["rp"], "waited": (time_now - playerInMatch["joined_at"]).total_seconds()}
                    match.append(temp)
                matches_q.send_message(json.dumps(match), visibility_timeout=MATCH_TIME)
                logging.info(f"MATCH players={len(match)}")
                if len(active) > 10:
                    checks.send_message("check", visibility_timeout=RECHECK)
                return
            else:
                logging.info("CLAIM_FAIL")
                if len(active) > 10:
                    checks.send_message("check", visibility_timeout=RECHECK)
                return

    if len(active) > 0:
        checks.send_message("check", visibility_timeout=RECHECK)


# these functions change the RP of the players and decide if they will play more games or quit
def winning_team(players):
    for player in players:
        player["rp"] += RP_CHANGE
        if random.random() < REJOIN_WIN:
            add_to_queue(player["player_id"], player["rp"])
        else:
            logging.info(f"QUIT {player['player_id']} rp={player['rp']}")
def losing_team(players):
    for player in players:
        player["rp"] -= RP_CHANGE
        if player["rp"] < 0:
            player["rp"] = 0
        if random.random() < REJOIN_LOSE:
            add_to_queue(player["player_id"], player["rp"])
        else:
            logging.info(f"QUIT {player['player_id']} rp={player['rp']}")

# FUNCTION 3: the 10 players in a match play against one another
#winning and losing teams are decided
@app.queue_trigger(arg_name="msg", queue_name="matches-queue", connection="AzureWebJobsStorage")
def match_resolver(msg: func.QueueMessage):
    players = json.loads(msg.get_body().decode())   # back into a list of dicts

    #the match is teama vs teamb, players 0-4 vs 5-9, first find avg rp of each team
    avg_a = 0.00
    avg_b = 0.00
    for i in range(10):
        if i<5:
            avg_a += players[i]["rp"]
        else:
            avg_b += players[i]["rp"]
    avg_a /= 5
    avg_b /= 5

    #find the winning team
    chance_a = 0.5 + (avg_a - avg_b) / 1000
    team_a_wins = random.random() < chance_a

    #before the rp is updated and players rejoin or quit, we find the stats
    #create a record in the table for the match that was just played
    rps = []
    for p in players:
        rps.append(p["rp"])
    waits = []
    for p in players:
        waits.append(p["waited"])
    matches_t.create_entity({"PartitionKey": "match", "RowKey": str(uuid.uuid4()), "avg_a": avg_a, "avg_b": avg_b,
    "rp_spread": max(rps) - min(rps),        # match quality: smaller = fairer
    "avg_wait": sum(waits) / len(waits),     # how long players queued
    "max_wait": max(waits), "winner": "A" if team_a_wins else "B", "resolved_at": datetime.datetime.now(datetime.timezone.utc), })
    logging.info(f"RESOLVED winner={'A' if team_a_wins else 'B'} spread={max(rps) - min(rps)}")

    #now these functions can be called depending on which team won, so the players can either quit or rejoin the matchmaking
    if team_a_wins:
        winning_team(players[0:5])
        losing_team(players[5:10])
    else:
        losing_team(players[0:5])
        winning_team(players[5:10])