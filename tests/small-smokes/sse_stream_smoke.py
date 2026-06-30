import httpx
from httpx_sse import connect_sse

from _smoke_models import skip_if_streaming_disabled

skip_if_streaming_disabled()  # token streaming temporarily off — see _smoke_models

BASE="http://127.0.0.1:8770"  # weird-personas dev backend (has live runs)

def run_chat(body, tag):
    deltas=0; messages=0; done=False; err=None
    with httpx.Client(base_url=BASE, timeout=120) as c:
        with connect_sse(c, "POST", "/api/chat", json=body) as es:
            for ev in es.iter_sse():
                if ev.event=="delta": deltas+=1
                elif ev.event=="message":
                    messages+=1
                elif ev.event=="done": done=True; break
                elif ev.event=="error": err=ev.data; break
    print(f"[{tag}] deltas={deltas} messages={messages} done={done} err={err}")
    return deltas, messages, done

# pick a run whose sampler is in Tinker's live servable window — `sampleable` alone
# is NOT enough (only a rolling window of sampler_weights is actually served).
servable={m["sampler_path"] for m in httpx.get(BASE+"/api/tinker-models",timeout=8).json()["models"] if m.get("sampler_path")}
runs=httpx.get(BASE+"/api/models",timeout=8).json()
rid=next((r["id"] for r in runs if any(c.get("sampler_path") in servable for c in r.get("checkpoints",[]))),None)
assert rid, "no run on this instance is in Tinker's servable window"
print("run:",rid)

# n=1 discovered run -> expect deltas>=1  (DESIRED contract; disabled while streaming off)
d,m,done=run_chat({"run_id":rid,"messages":[{"role":"user","content":"Hi in 5 words"}],"n_samples":1,"max_tokens":30,"temperature":0.7,"broadcast":False},"run n=1")
assert d>=1 and m>=1 and done, "run n=1 streaming failed"

# n=1 loose checkpoint -> expect deltas
tm=httpx.get(BASE+"/api/tinker-models",timeout=8).json()
loose=next(x["sampler_path"] for x in tm["models"] if x["kind"]=="checkpoint")
d2,m2,done2=run_chat({"sampler_path":loose,"messages":[{"role":"user","content":"Hi in 5 words"}],"n_samples":1,"max_tokens":30,"temperature":0.7,"broadcast":False},"loose n=1")
assert d2>=1 and done2, "loose n=1 streaming failed"

# n=3 discovered run -> expect NO deltas, 3 messages
d3,m3,done3=run_chat({"run_id":rid,"messages":[{"role":"user","content":"Hi in 5 words"}],"n_samples":3,"max_tokens":20,"temperature":1.0,"broadcast":False},"run n=3")
assert d3==0 and m3==3 and done3, "run n=3 should be 3 whole samples, no deltas"

print("SSE CHAT STREAMING OK")
