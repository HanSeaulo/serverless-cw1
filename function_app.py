import azure.functions as func
import datetime
import json
import logging
import os
from azure.data.tables import TableClient
from azure.storage.queue import QueueClient
from azure.core import MatchConditions
from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError

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
def claim(p):
    try:
        waiting.delete_entity(p["PartitionKey"], p["RowKey"],
                              etag=p.metadata["etag"],
                              match_condition=MatchConditions.IfNotModified)
        return True
    except (ResourceModifiedError, ResourceNotFoundError):
        return False   # already taken by another matchmaker

# FUNCTION 1: GETS THE PLAYER AND PUTS THEM IN THE WAITING QUEUE
@app.route(route="join", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def join(req: func.HttpRequest) -> func.HttpResponse:
    body = req.get_json()          # {"player_id": ..., "rp": ...}
    
    #ill write teh stuff here?
    p_id = body["player_id"]
    p_rp = int(body["rp"])
    
    p_rank = get_rank(p_rp)
    join_time = datetime.datetime.now(datetime.timezone.utc)

    tempDict = {"PartitionKey": p_rank, "RowKey": str(p_id), "rp": p_rp, "joined_at": join_time}

    waiting.upsert_entity(tempDict)
    checks.send_message(p_rank)

    return func.HttpResponse(f"{p_id} joined {p_rank} queue")

# FUNCTION 2: USES THE WAITING QUEUE TO GET A PARTY OF 10 PLAYERS SO THAT A MATCH CAN BE STARTED
@app.queue_trigger(arg_name="msg", queue_name="check-tier", connection="AzureWebJobsStorage")
def matchmaker(msg: func.QueueMessage):
    rank = msg.get_body().decode()
    players = list(waiting.query_entities("PartitionKey eq @r", parameters={"r": rank}))
    players.sort(key=lambda p: p["joined_at"])
    active = []
    timed_out = []
    time_now = datetime.datetime.now(datetime.timezone.utc)
    for p in players:
        j_time = p["joined_at"]
        waited = (time_now - j_time).total_seconds()
        if waited > MAX_WAIT:
            timed_out.append(p)
        else:
            active.append(p)

    #deleting all the timed out players
    for player in timed_out:
        successful = claim(player)
        if successful:
            logging.info(f"TIMEOUT {player['RowKey']} rank={rank}")

    #trying to create a match
    if len(active) >= 10:
        match_list = []
        #attempt to claim 10 players from the waiting queue
        for player in active:
            if claim(player):
                match_list.append(player)
            if len(match_list) == 10:
                break
        if len(match_list) == 10:
            #if we successfully claimed 10 players then they can queue for a match
            match = []
            for player in match_list:
                temp = {"player_id": player["RowKey"], "rp": player["rp"], "rank": player["PartitionKey"], "waited": (time_now - player["joined_at"]).total_seconds()}
                match.append(temp)
            matches_q.send_message(json.dumps(match))
            logging.info(f"MATCH rank={rank} players={len(match)}")
            if len(active) > 10:
                checks.send_message(rank, visibility_timeout=RECHECK)
        else:
            #if less than 10 players were claimed then put the players back in the waiting list
            for player in match_list:
                waiting.upsert_entity(player)
            checks.send_message(rank, visibility_timeout=RECHECK)
    else:
        #look at other ranks?
        if len(active) > 0:
            checks.send_message(rank, visibility_timeout=RECHECK)

