"""Where the app reads matches and the history store from: a local folder or Azure Blob Storage.

Local (default):  TEAM_PLAYER_DATA=data  ->  data/current/<season>/*.jsonl.gz, data/history.db
Azure Blob:       TEAM_PLAYER_BLOB_ACCOUNT_URL=https://<account>.blob.core.windows.net
                  TEAM_PLAYER_BLOB_CONTAINER=tracking   (current/<season>/... blobs)
                  TEAM_PLAYER_ANALYSIS_CONTAINER=analysis (history.db blob)
Blobs are downloaded on first use into TEAM_PLAYER_CACHE (default /tmp/team_player_cache),
using the app's managed identity (or `az login` locally). No account keys are used.
"""
from __future__ import annotations
import os
import re
from pathlib import Path

from team_player.config.league import CURRENT_SEASON

NAME = re.compile(r"mw(\d+)_([A-Z])-([A-Z])\.jsonl(\.gz)?$")


def describe(name):
    m = NAME.search(name)
    if not m:
        return {"file": name, "label": name}
    mw, home, away = int(m.group(1)), m.group(2), m.group(3)
    return {"file": name, "matchweek": mw, "home": home, "away": away, "label": f"MW{mw:02d} · {home} v {away}"}


class DataSource:
    def __init__(self):
        self.root = Path(os.environ.get("TEAM_PLAYER_DATA", "data"))
        self.season = os.environ.get("TEAM_PLAYER_SEASON", CURRENT_SEASON)
        self.account = os.environ.get("TEAM_PLAYER_BLOB_ACCOUNT_URL", "")
        self.container = os.environ.get("TEAM_PLAYER_BLOB_CONTAINER", "tracking")
        self.analysis_container = os.environ.get("TEAM_PLAYER_ANALYSIS_CONTAINER", "analysis")
        self.cache = Path(os.environ.get("TEAM_PLAYER_CACHE", "/tmp/team_player_cache"))
        self._service = None

    # ---------------------------------------------------------------- blob ---
    def service(self):
        if self._service is None:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient
            credential = DefaultAzureCredential(managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID"))
            self._service = BlobServiceClient(self.account, credential=credential)
        return self._service

    def _download(self, container, blob, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        with tmp.open("wb") as handle:
            self.service().get_container_client(container).download_blob(blob).readinto(handle)
        os.replace(tmp, target)
        return target

    # ------------------------------------------------------------- matches ---
    def current_prefix(self):
        return f"current/{self.season}/"

    def list_current(self):
        """Current-season matches available to stream, sorted by matchweek."""
        if self.account:
            names = [b.name.rsplit("/", 1)[-1] for b in self.service().get_container_client(self.container)
                     .list_blobs(name_starts_with=self.current_prefix())]
        else:
            folder = self.root / "current" / self.season
            names = [p.name for p in folder.glob("*.jsonl*")] if folder.is_dir() else []
        return sorted((describe(n) for n in names if n.endswith((".jsonl", ".jsonl.gz"))), key=lambda d: (d.get("matchweek", 0), d["file"]))

    def match_path(self, name):
        """Local path of a current-season match (downloaded from Blob if needed)."""
        if "/" in name or "\\" in name or ".." in name or not name.endswith((".jsonl", ".jsonl.gz")):
            raise ValueError("Invalid match name")
        if self.account:
            target = self.cache / "current" / self.season / name
            return target if target.exists() else self._download(self.container, self.current_prefix() + name, target)
        path = self.root / "current" / self.season / name
        if not path.is_file():
            raise ValueError("Unknown match")
        return path

    # --------------------------------------------------------------- history ---
    def history_db(self):
        """Path to history.db, or None if it has not been built yet."""
        explicit = os.environ.get("TEAM_PLAYER_HISTORY_DB")
        if explicit is not None:
            return Path(explicit) if explicit and Path(explicit).is_file() else None
        if self.account:
            target = self.cache / "history.db"
            if not target.exists():
                try:
                    self._download(self.analysis_container, "history.db", target)
                except Exception:
                    return None
            return target
        path = self.root / "history.db"
        return path if path.is_file() else None
