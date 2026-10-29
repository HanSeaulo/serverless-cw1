import json
import matplotlib.pyplot as plt

#PLOT 1: JOIN RESPONSE TIME
N = [50, 100, 250, 500, 1000]
N_str = ["50", "100", "250", "500", "1000"]

# from results.txt, client side
client_avg = [1.339, 1.134, 1.065, 1.077, 1.057]
client_p95 = [2.538, 2.389, 2.634, 1.985, 1.568]

# from functions.json, server side join avg
server_join_ms = [190, 69, 76, 47, 37]

server_join_s = []
for t in server_join_ms:
    server_join_s.append(t/1000)

plt.figure()

plt.plot(N, client_avg, marker="o", label="Client avg")
plt.plot(N, client_p95, marker="o", label="Client p95")
plt.plot(N, server_join_s, marker="o", label="Server join avg")

plt.xscale("log")              
plt.xlabel("Players per run (N)")
plt.xticks(N, N_str)

plt.ylabel("Response time (s)")
plt.ylim(bottom=0)

plt.title("Join response time vs load")
plt.legend()
plt.savefig("plot1_response.png")
plt.close()


#PLOT 2: % TIMED OUT PLAYERS

timeout_pct = [21.8, 13.3, 7.1, 3.0, 1.5]   # from counts.json

plt.figure()
plt.bar(N_str, timeout_pct)
plt.title("Timed-out players vs load")
plt.xlabel("Players per run (N)")
plt.ylabel("Players that timed out %")
plt.savefig("plot2_timed_out.png")
plt.close()


#PLOT 3: QUEUE WAIT

waits = {"50": [], "100": [], "250": [], "500": [], "1000": []}
spreads = {"50": [], "100": [], "250": [], "500": [], "1000": []}

with open("matches.json") as f:
    matches = json.load(f)["items"]

# run boundaries
bounds = [("50", "2026-10-29T04:45:06"), ("100", "2026-10-29T04:53:10"),
          ("250", "2026-10-29T05:01:54"), ("500", "2026-10-29T05:10:36"), ("1000", "9999")]

def which_run(time_string):
    for run, end in bounds:
        if time_string < end:   
            return run

for m in matches:
    run = which_run(m["resolved_at"])
    waits[run].append(m["avg_wait"])
    spreads[run].append(m["rp_spread"])

avg_wait_times = []
for n in N:
    sum_wait_times = sum(waits[str(n)])
    total_wait_times = len(waits[str(n)])
    avg_wait_times.append(sum_wait_times / total_wait_times)

avg_spreads = []
for n in N:
    sum_spread = sum(spreads[str(n)])
    total_spread = len(spreads[str(n)])
    avg_spreads.append(sum_spread / total_spread)

fig, (left, right) = plt.subplots(1, 2, figsize=(10, 4))
left.plot(N, avg_wait_times, marker="o")
left.set_title("Avg queue wait"); left.set_xlabel("Players per run (N)"); left.set_ylabel("Seconds")
right.plot(N, avg_spreads, marker="o")
right.set_title("Avg RP spread per match"); right.set_xlabel("Players per run (N)"); right.set_ylabel("RP")
left.set_xscale("log");  left.set_xticks(N, N_str)
right.set_xscale("log"); right.set_xticks(N, N_str)
left.set_ylim(bottom=0)
right.set_ylim(bottom=0)
plt.tight_layout()
plt.savefig("plot3_wait_spread.png")
plt.close()


#PLOT 4: NUMBER OF INSTANED OF MATCHMAKER FUNCTION

mm_instances = [9, 16, 44, 38, 104]      # matchmaker, from functions.json

plt.figure()
plt.bar(N_str, mm_instances)
plt.title("Matchmaker Instances vs Load")
plt.xlabel("Players per run (N)")
plt.ylabel("Number of Instances of the Matchmaker function")
plt.savefig("plot4_num_instances.png")
plt.close()



#PLOT 5: CLAIM FAILS AND INSTANCES PER PLAYER

claim_fails_per_match = [4.7, 6.0, 4.8, 5.1, 7.8]   # CLAIM_FAIL / MATCH, from counts.json
mm_runs_per_player   = [31.5, 29.5, 49.1, 56.4, 55.5]   # matchmaker runs / players, from functions.json

fig, (left, right) = plt.subplots(1, 2, figsize=(10, 4))
left.plot(N, claim_fails_per_match, marker="o"); left.set_ylabel("Failed claims per match")
left.set_title("Failed Claims per Match"); left.set_xlabel("Players per run (N)")
right.plot(N, mm_runs_per_player, marker="o"); right.set_ylabel("Runs per Player")
right.set_title("Matchmaker runs per Player"); right.set_xlabel("Players per run (N)")
left.set_xscale("log");  left.set_xticks(N, N_str)
right.set_xscale("log"); right.set_xticks(N, N_str)
left.set_ylim(bottom=0); right.set_ylim(bottom=0)
plt.tight_layout()
plt.savefig("plot5_costof_contention.png")
plt.close()