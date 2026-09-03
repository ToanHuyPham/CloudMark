from __future__ import annotations
import csv, io, secrets
from typing import Any
from .database import Database
from .distributed import DistributedError, create_task, peer_address, validate_pair, wait_task
from .profiles import DATABASE_PROFILES
from .runner import JobContext, RunStopped

REDIS_PORT=56379
REDIS_GENERATOR_CPU_LIMIT=90.0

class RedisBenchmarkError(RuntimeError):
    def __init__(self,message:str): super().__init__(message); self.partial_result=None

def parse_redis_benchmark_csv(stdout:str)->dict[str,Any]:
    row=next((r for r in csv.reader(io.StringIO(stdout)) if r and r[0].strip().upper() in {"GET","SET"}),None)
    if row is None or len(row)<8: raise RedisBenchmarkError("redis-benchmark did not return required CSV latency evidence.")
    try: v=[float(x) for x in row[1:8]]
    except ValueError as exc: raise RedisBenchmarkError("redis-benchmark returned invalid numeric evidence.") from exc
    return {"operation":row[0].strip().lower(),"requests_per_second":v[0],"latency_ms":{"average":v[1],"minimum":v[2],"p50":v[3],"p95":v[4],"p99":v[5],"maximum":v[6]}}

def redis_total_steps(name:str)->int: return len(DATABASE_PROFILES[name]["jobs"])+2
def redis_default_timeout(name:str)->int: return 180+90*len(DATABASE_PROFILES[name]["jobs"])

def validate_redis_run(db:Database,session_id:str,profile_name:str):
    p=DATABASE_PROFILES.get(profile_name)
    if not p or p.get("engine")!="redis": raise ValueError(f"Unknown Redis profile: {profile_name}")
    return validate_pair(db,session_id,target_capabilities=("redis_server","redis_cli"),generator_capabilities=("redis_benchmark","procfs_process_cpu"))

def redis_analysis(result:dict[str,Any])->dict[str,Any]:
    ms=result.get("redis_measurements") or []; cpu=[m.get("generator_cpu") or {} for m in ms]; obs=[x for x in cpu if x.get("status")=="observed"]
    peaks=[float(x["peak_process_cpu_percent_of_one_core"]) for x in obs if isinstance(x.get("peak_process_cpu_percent_of_one_core"),(int,float))]
    status="unknown" if not ms or len(obs)!=len(ms) or not peaks else "constrained" if max(peaks)>=REDIS_GENERATOR_CPU_LIMIT else "adequate"
    persistence=(result.get("server") or {}).get("persistence",{}).get("appendonly") is True; cleanup=(result.get("cleanup") or {}).get("cleanup_verified") is True
    reasons=[]
    if status!="adequate": reasons.append(f"generator-headroom-{status}")
    if not persistence: reasons.append("aof-persistence-evidence-incomplete")
    if not cleanup: reasons.append("ephemeral-cleanup-unverified")
    return {"generator_headroom":{"status":status,"peak_process_cpu_percent_of_one_core":max(peaks,default=None),"limit_percent_of_one_core":REDIS_GENERATOR_CPU_LIMIT},"persistence":{"status":"observed" if persistence else "unavailable","appendonly":persistence},"validity":{"comparison_eligible":not reasons,"reason_codes":reasons},"scored":False}

def run_redis(db:Database,run_id:str,session_id:str,profile_name:str,*,context:JobContext)->dict[str,Any]:
    session,target,generator=validate_redis_run(db,session_id,profile_name); p=DATABASE_PROFILES[profile_name]; target_address=peer_address(target); password=secrets.token_urlsafe(32)
    result={"suite":"database","engine":"redis","profile":profile_name,"profile_version":p["profile_version"],"methodology_version":p["methodology_version"],"session":{"id":session["id"],"label":session["label"],"topology":session.get("topology")},"target":{"id":target["id"],"name":target["name"],"address":target_address},"generator":{"id":generator["id"],"name":generator["name"],"address":peer_address(generator)},"policy":{"controller_in_data_path":False,"authentication":"memory-only-per-run-secret","arbitrary_command_allowed":False,"port":REDIS_PORT},"redis_measurements":[],"cleanup":{"status":"pending"}}
    server=None; cleanup_started=False
    try:
        server=create_task(db,run_id,session_id,target["id"],"redis-service-start",{"listen_address":target_address,"port":REDIS_PORT,"deadline_seconds":redis_default_timeout(profile_name),"ephemeral_secret_required":True,"run_completed_steps":0,"run_total_steps":redis_total_steps(profile_name)},ephemeral_secret={"redis_password":password})
        result["server"]=(wait_task(db,server,timeout_seconds=120,context=context).get("result") or {}); context.complete_step("redis-ready",None,partial_result=result)
        for i,job in enumerate(p["jobs"]):
            tid=create_task(db,run_id,session_id,generator["id"],"redis-client",{**job,"target_address":target_address,"port":REDIS_PORT,"ephemeral_secret_required":True,"run_completed_steps":i+1,"run_total_steps":redis_total_steps(profile_name)},ephemeral_secret={"redis_password":password})
            measurement=(wait_task(db,tid,timeout_seconds=90,context=context).get("result") or {}).get("redis_benchmark")
            if not isinstance(measurement,dict): raise RedisBenchmarkError("Redis client returned invalid evidence.")
            result["redis_measurements"].append({"name":job["name"],**measurement}); result["analysis"]=redis_analysis(result); context.complete_step("redis-measurement-complete",None,partial_result=result)
        cleanup_started=True; tid=create_task(db,run_id,session_id,target["id"],"redis-service-stop",{"server_task_id":server}); result["cleanup"]=(wait_task(db,tid,timeout_seconds=45,context=None).get("result") or {}); result["analysis"]=redis_analysis(result); context.complete_step("redis-cleanup-complete",None,partial_result=result)
    except (RunStopped,DistributedError,RedisBenchmarkError) as exc:
        if server and not cleanup_started:
            try: result["cleanup"]=(wait_task(db,create_task(db,run_id,session_id,target["id"],"redis-service-stop",{"server_task_id":server}),timeout_seconds=30,context=None).get("result") or {})
            except DistributedError: pass
        result["analysis"]=redis_analysis(result); exc.partial_result=result; raise
    return result
