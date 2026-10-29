import requests, random, time, sys, datetime
from concurrent.futures import ThreadPoolExecutor

URL = "https://han-cw1-func-c7b4hrh4adddc0eb.francecentral-01.azurewebsites.net/api/join"
N = int(sys.argv[1])

def add_a_player(i):
    body = {"player_id": f"run{N}-{i}", "rp": random.randint(0, 599)}
    start = time.time()
    r = requests.post(URL, json=body)
    return time.time() - start, r.status_code == 200

with ThreadPoolExecutor(max_workers=50) as pool:   # 50 requests in flight at once
    print(f"Start time: {datetime.datetime.now(datetime.timezone.utc).isoformat()}")
    times = list(pool.map(add_a_player, range(N)))     # runs send_one for 0..N-1, collects the times
    print(f"End time: {datetime.datetime.now(datetime.timezone.utc).isoformat()}")

    duration = []
    failures = 0
    for t, ok in times:
        duration.append(t)
        if not ok:
            failures += 1

avg_duration = sum(duration) / len(duration)
duration.sort()
p95 = duration[int(len(duration) * 0.95) - 1]

print(f"N={N}, failures={failures}, avg={avg_duration:.3f}s, p95={p95:.3f}s")