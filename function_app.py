import azure.functions as func
import datetime
import json
import logging
import os
from azure.storage.queue import QueueClient
from azure.core import MatchConditions
from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError
from azure.data.tables import TableClient, TableTransactionError

logging.getLogger("azure").setLevel(logging.WARNING)

app = func.FunctionApp()

# CONSTANTS 
RECHECK = 2
BORROW_AFTER = 5
MAX_WAIT = 10

# QUEUES
conn = os.environ["AzureWebJobsStorage"]
waiting = TableClient.from_connection_string(conn, table_name="waiting")
checks  = QueueClient.from_connection_string(conn, "check-tier")
matches_q = QueueClient.from_connection_string(conn, "matches-queue")

# GET THE RANK THAT CORRESPONDS TO EACH RP
def get_rank(prp):
    if prp < 100:
        return "Bronze"
    elif prp < 200:
        return "Silver" 
    elif prp < 300:
        return "Gold" 
    elif prp < 400:
        return "Platinum" 
    elif prp < 500:
        return "Diamond"
    else:
        return "Champion" 

#
def claim_all(players):
    ops = []
    for p in players:
        version_i_saw = p.metadata["etag"]     # the row's version stamp when we read it
        conditions = {"etag": version_i_saw, "match_condition": MatchConditions.IfNotModified}
        ops.append(("delete", p, conditions))  # delete p, only if unchanged

    try:
        waiting.submit_transaction(ops)        # all deletes happen together, or none do
        return True
    except TableTransactionError:
        return False


# FUNCTION 1: GETS THE PLAYER AND PUTS THEM IN THE WAITING QUEUE

@app.route(route="join", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def join(req: func.HttpRequest) -> func.HttpResponse:
    body = req.get_json()          # {"player_id": ..., "rp": ...}
    
    p_id = body["player_id"]
    p_rp = int(body["rp"])
    join_time = datetime.datetime.now(datetime.timezone.utc)

    tempDict = {"PartitionKey": "queue", "RowKey": str(p_id), "rp": p_rp, "joined_at": join_time}

    waiting.upsert_entity(tempDict)
    checks.send_message("check")

    return func.HttpResponse(f"{p_id} joined with {p_rp}RP\n")


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
            logging.info(f"TIMEOUT {player['RowKey']}")
    #try to start a match with the active players
    for player in active:
        waited = (time_now - player["joined_at"]).total_seconds()
        low, high = assign_band(player["rp"], waited)
    #      - find the active players whose rp is inside that band
        in_band = []
        for p2 in active:
            if p2["rp"] >= low and p2["rp"] <= high:
                in_band.append(p2)
            if len(in_band) >= 10:
                break
    #      - if there are 10 or more: take the 10 oldest, claim_all them
    #          - if the claim worked: send the match, log it, and stop (return)
    #          - if it failed: someone else got them, so recheck and stop

    # no band had 10 players: if anyone is still waiting, recheck
