"""Azure batch worker: simulate seasons, analyse history matches, build history.db.

Runs as an Azure Container Apps Job (or anywhere with `az login`). Start N replicas of the
same command; each replica (and each CPU inside it) claims tasks one at a time by creating
a small "claim" blob that can only be created once. Finished outputs are skipped, so a job
can be re-run safely after a failure.

Blob containers (one storage account):
  tracking      public positions:  history/<season>/mwNN_H-A.jsonl.gz, current/<season>/...
  ground-truth  PRIVATE profiles:  <season>/<fixture>.json   (simulator side only)
  analysis      derived results:   matches/<season>/mwNN_H-A.json.gz, history.db, value_models.json
  work          claim markers

    python -m team_player.cloud.worker simulate --seasons history|current|2019-20,2020-21
    python -m team_player.cloud.worker analyse  --seasons history
    python -m team_player.cloud.worker build-store [--fit-models]

Environment: TEAM_PLAYER_BLOB_ACCOUNT_URL (required), AZURE_CLIENT_ID (user-assigned identity),
TEAM_PLAYER_WORKERS (processes per replica, default = CPU count). Without CLI arguments the
task comes from TEAM_PLAYER_TASK (simulate | analyse | build-store), TEAM_PLAYER_SEASONS,
TEAM_PLAYER_LIMIT and TEAM_PLAYER_FIT_MODELS=1 -- this is how the Container Apps Jobs run it.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import sys
import tempfile

from team_player.config.league import HISTORY_SEASONS, CURRENT_SEASON

TRACKING, PRIVATE, ANALYSIS, WORK = (os.environ.get("TEAM_PLAYER_BLOB_CONTAINER", "tracking"), "ground-truth",
                                     os.environ.get("TEAM_PLAYER_ANALYSIS_CONTAINER", "analysis"), "work")
RECLAIM_AFTER_S = 3 * 3600   # a claim older than this without output is assumed to be from a crashed replica


def service():
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient
    url = os.environ["TEAM_PLAYER_BLOB_ACCOUNT_URL"]
    return BlobServiceClient(url, credential=DefaultAzureCredential(managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID")))


def exists(svc, container, name):
    return svc.get_blob_client(container, name).exists()


def claim(svc, key):
    """True if this caller now owns `key`. Atomic: blob creation fails if it already exists."""
    from azure.core.exceptions import ResourceExistsError
    blob = svc.get_blob_client(WORK, f"claims/{key}")
    stamp = json.dumps({"by": socket.gethostname(), "pid": os.getpid(), "at": datetime.now(timezone.utc).isoformat()})
    try:
        blob.upload_blob(stamp, overwrite=False);return True
    except ResourceExistsError:
        age = (datetime.now(timezone.utc) - blob.get_blob_properties().last_modified).total_seconds()
        if age > RECLAIM_AFTER_S:
            blob.upload_blob(stamp, overwrite=True);return True
        return False


def parse_seasons(text):
    if text == "history":
        return list(HISTORY_SEASONS)
    if text == "current":
        return [CURRENT_SEASON]
    if text == "all":
        return list(HISTORY_SEASONS) + [CURRENT_SEASON]
    return [s.strip() for s in text.split(",") if s.strip()]


# ------------------------------------------------------------------ simulate ---

def simulate_task(fx):
    from team_player.simulator.season import write_fixture, relative_path
    svc = service();name = relative_path(fx).as_posix()
    if exists(svc, TRACKING, name) or not claim(svc, f"simulate/{fx['fixture_id']}"):
        return 0
    with tempfile.TemporaryDirectory() as d:
        private = write_fixture(fx, d)
        with (Path(d) / name).open("rb") as h:
            svc.get_blob_client(TRACKING, name).upload_blob(h, overwrite=True)
    svc.get_blob_client(PRIVATE, f"{fx['season']}/{fx['fixture_id']}.json").upload_blob(json.dumps(private), overwrite=True)
    return 1


# ------------------------------------------------------------------- analyse ---

def analyse_task(blob_name):
    from team_player.analyser.history import analyse_match
    import gzip
    svc = service()
    season = blob_name.split("/")[1];stem = blob_name.rsplit("/", 1)[-1].replace(".jsonl.gz", "")
    target = f"matches/{season}/{stem}.json.gz"
    if exists(svc, ANALYSIS, target) or not claim(svc, f"analyse/{season}/{stem}"):
        return 0
    with tempfile.TemporaryDirectory() as d:
        local = Path(d) / blob_name.rsplit("/", 1)[-1]
        with local.open("wb") as h:
            svc.get_blob_client(TRACKING, blob_name).download_blob().readinto(h)
        result = analyse_match(local)
    svc.get_blob_client(ANALYSIS, target).upload_blob(gzip.compress(json.dumps(result, separators=(",", ":")).encode()), overwrite=True)
    return 1


# ---------------------------------------------------------------- build store ---

def build_store_remote(fit_models=False):
    from team_player.analyser.history import build_store, fit_value_models
    svc = service();container = svc.get_container_client(ANALYSIS)
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "matches";n = 0
        for blob in container.list_blobs(name_starts_with="matches/"):
            path = Path(d) / blob.name;path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as h:
                container.download_blob(blob.name).readinto(h)
            n += 1
        db, matches = build_store(root, Path(d) / "history.db")
        with open(db, "rb") as h:
            container.upload_blob("history.db", h, overwrite=True)
        if fit_models:
            out = fit_value_models(root, Path(d) / "value_models.json")
            with open(out, "rb") as h:
                container.upload_blob("value_models.json", h, overwrite=True)
    print(f"history.db built from {matches} matches ({n} analysis files) and uploaded to '{ANALYSIS}'")


def run_pool(fn, tasks):
    workers = int(os.environ.get("TEAM_PLAYER_WORKERS", os.cpu_count() or 1))
    if workers > 1:
        with ProcessPoolExecutor(workers) as pool:
            done = sum(pool.map(fn, tasks, chunksize=1))
    else:
        done = sum(map(fn, tasks))
    print(f"{done} task(s) completed by this replica; {len(tasks)} in the queue")


def ensure_containers():
    from azure.core.exceptions import ResourceExistsError
    svc = service()
    for c in (TRACKING, PRIVATE, ANALYSIS, WORK):
        try:
            svc.create_container(c)
        except ResourceExistsError:
            pass


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("simulate");s.add_argument("--seasons", default="history")
    s.add_argument("--limit", type=int, default=0, help="only the first N fixtures per season (trial runs)")
    a = sub.add_parser("analyse");a.add_argument("--seasons", default="history")
    b = sub.add_parser("build-store");b.add_argument("--fit-models", action="store_true")
    argv = sys.argv[1:]
    if not argv:   # Container Apps Jobs: configure through environment variables (no CLI flags needed)
        argv = [os.environ.get("TEAM_PLAYER_TASK", "simulate")]
        if argv[0] in ("simulate", "analyse"):
            argv += ["--seasons", os.environ.get("TEAM_PLAYER_SEASONS", "history")]
        if argv[0] == "simulate" and os.environ.get("TEAM_PLAYER_LIMIT"):
            argv += ["--limit", os.environ["TEAM_PLAYER_LIMIT"]]
        if argv[0] == "build-store" and os.environ.get("TEAM_PLAYER_FIT_MODELS") == "1":
            argv += ["--fit-models"]
    args = p.parse_args(argv)
    ensure_containers()
    if args.cmd == "simulate":
        from team_player.simulator.season import fixtures
        tasks = [fx for season in parse_seasons(args.seasons) for fx in (fixtures(season)[:args.limit] if args.limit else fixtures(season))]
        run_pool(simulate_task, tasks)
    elif args.cmd == "analyse":
        seasons = parse_seasons(args.seasons)
        if CURRENT_SEASON in seasons:
            raise SystemExit("The current season is analysed online only; do not add it to the history store.")
        container = service().get_container_client(TRACKING)
        tasks = [b.name for season in seasons for b in container.list_blobs(name_starts_with=f"history/{season}/")]
        run_pool(analyse_task, tasks)
    else:
        build_store_remote(args.fit_models)


if __name__ == "__main__":
    main()
