import azure.functions as func
import datetime
import json
import logging
import os
from azure.data.tables import TableClient
from azure.storage.queue import QueueClient

app = func.FunctionApp()

conn = os.environ["AzureWebJobsStorage"]
waiting = TableClient.from_connection_string(conn, table_name="waiting")
checks  = QueueClient.from_connection_string(conn, "check-tier")

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


