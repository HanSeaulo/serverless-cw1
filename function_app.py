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
RANKS = ["Bronze", "Silver", "Gold", "Platinum", "Diamond", "Champion"]

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
    for e in players:
        options = {
            "etag": e.metadata["etag"],                         # the version we read earlier
            "match_condition": MatchConditions.IfNotModified,   # only delete if it's still that version
        }
        operation = ("delete", e, options)     # what to do, which row, and the conditions
        ops.append(operation)

    try:
        waiting.submit_transaction(ops)        # send all the deletes together as one transaction
        return True                            # every delete succeeded, so all the players are ours
    except TableTransactionError:
        return False                           # at least one failed, so Azure deleted none of them

# FUNCTION 1: GETS THE PLAYER AND PUTS THEM IN THE WAITING QUEUE
@app.route(route="join", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def join(req: func.HttpRequest) -> func.HttpResponse:
    body = req.get_json()          # {"player_id": ..., "rp": ...}
    
    #ill write teh stuff here?
    p_id = body["player_id"]
    p_rp = int(body["rp"])
    
    p_rank = get_rank(p_rp)
    join_time = datetime.datetime.now(datetime.timezone.utc)

    tempDict = {"PartitionKey": "queue", "RowKey": str(p_id), "rank": p_rank, "rp": p_rp, "joined_at": join_time}

    waiting.upsert_entity(tempDict)
    checks.send_message(p_rank)

    return func.HttpResponse(f"{p_id} joined {p_rank} queue")

# FUNCTION 2: USES THE WAITING TABLE TO GET A PARTY OF 10 PLAYERS SO THAT A MATCH CAN BE STARTED
@app.queue_trigger(arg_name="msg", queue_name="check-tier", connection="AzureWebJobsStorage")
def matchmaker(msg: func.QueueMessage):
    rank = msg.get_body().decode()

    # 1. get this rank's players, oldest first   (CHANGED: filter on the rank column)
    players = list(waiting.query_entities("PartitionKey eq 'queue' and rank eq @r", parameters={"r": rank}))
    players.sort(key=lambda p: p["joined_at"])

    # 2. split into active and timed out
    active = []
    timed_out = []
    time_now = datetime.datetime.now(datetime.timezone.utc)
    for p in players:
        waited = (time_now - p["joined_at"]).total_seconds()
        if waited > MAX_WAIT:
            timed_out.append(p)
        else:
            active.append(p)

    # 3. remove timed-out players   (CHANGED: claim_all with a list of one)
    for player in timed_out:
        if claim_all([player]):
            logging.info(f"TIMEOUT {player['RowKey']} rank={rank}")

    # 4. try to make a match from the oldest 10
    if len(active) >= 10:
        match_list = active[:10]
        if claim_all(match_list):
            # we got all 10, so send the match   (CHANGED: rank comes from the rank column)
            match = []
            for player in match_list:
                temp = {"player_id": player["RowKey"], "rp": player["rp"], "rank": player["rank"],
                        "waited": (time_now - player["joined_at"]).total_seconds()}
                match.append(temp)
            matches_q.send_message(json.dumps(match))
            logging.info(f"MATCH rank={rank} players={len(match)}")
            if len(active) > 10:
                checks.send_message(rank, visibility_timeout=RECHECK)
        else:
            # another matchmaker got them first, so check again shortly   (CHANGED: no put-back needed)
            checks.send_message(rank, visibility_timeout=RECHECK)

    # 5. fewer than 10 waiting, so check again shortly
    elif len(active) > 0:
        checks.send_message(rank, visibility_timeout=RECHECK)