"""History seasons: offline analysis of finished matches and the SQLite history store.

History matches are finished, so besides the same online pipeline used live we also run
offline extras that need the whole match:
  * a plain description of what each striker / fullback / winger did in every decision
    window ("dropped 18 m deep and played a 31 m pass to C11"), used in the live narrative
    "Similar situation: in 2021-22, matchweek 14, C v H, 06:42, C09 ...";
  * the result of the whole possession, not only the next 15 s;
  * per-player match totals for the head-to-head page.

    python -m team_player.analyser.history analyse --input data/history/2019-20 --out analysis/2019-20 [--workers 8]
    python -m team_player.analyser.history build --input analysis --db data/history.db
    python -m team_player.analyser.history fit --input analysis --out data/value_models.json   # optional

The current season is never added here: it is analysed online only, while it streams.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor
import gzip
import json
import os
from pathlib import Path
import sqlite3
import numpy as np

from team_player.config.analysis import SIMILARITY_FEATURES, WINDOW
from team_player.utils.io import open_text, match_files, fingerprint
from team_player.utils.fmt import r as _r

ROLE_OF_CHOICE = {"striker_dropped": ("ST",), "fb_advanced": ("LB", "RB"), "winger_decoy": ("LW", "RW")}
DB_VERSION = 1


# ------------------------------------------------------------------ analysis ---

def clock(t):
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def describe_episode(e):
    """One short phrase for what the player did."""
    k = e["kind"]
    if k == "striker_drop":
        lp = e.get("link_pass")
        text = f"dropped {e['max_depth_m']:.0f} m deep to link play"
        if lp:
            text += f" and played a {lp['length_m']:.0f} m forward pass past {lp['packing']} opponent(s) to {lp['receiver']}"
        dl = e.get("delivery")
        if dl:
            text += "; he was in the box for the cross" if dl["striker_in_box"] else "; he was not back in the box when the ball came in"
        return text
    if k == "fullback_advance":
        text = f"pushed forward to {e['max_x']:.0f} m as an extra winger"
        if e.get("overlap_type"):
            text += f" ({e['overlap_type']})"
        c = e.get("counter")
        if c:
            text += ("; the ball was lost and the counter " + (f"produced {c['xg_against']:.2f} xG" if c["xg_against"] else "produced no shot")
                     + (" through his flank" if c["through_vacated_flank"] else ""))
        return text
    if k == "winger_run":
        kind = "decoy" if e.get("decoy") else "target"
        n = len(e.get("drawn_defenders") or [])
        rt = e["run_type"].replace("_", "-");article = "an" if rt[0] in "aeiou" else "a"
        text = f"made {article} {rt} {kind} run"
        if n:
            text += f" that drew {n} defender(s)"
        if e.get("space_used"):
            text += ", and a team-mate used the space"
        return text
    return k


NOT_DONE = {"ST": "stayed up front instead of dropping deep", "LB": "stayed back instead of pushing forward",
            "RB": "stayed back instead of pushing forward", "LW": "made no off-ball run", "RW": "made no off-ball run"}


def window_actions(window, team, lineup, episodes):
    """What each focus-role player of the attacking team did during [t, t + WINDOW)."""
    t0, t1 = window["t"], window["t"] + WINDOW
    out = []
    for role, pid in lineup.items():
        mine = [e for e in episodes if e.get("player") == pid and e.get("team") == team
                and e["t_start"] < t1 and e.get("t_end", t1) >= t0]
        if mine:
            e = max(mine, key=lambda e: e.get("t_end", t1) - e["t_start"])   # the longest episode in the window
            out.append({"player": pid, "role": role, "did": True, "kind": e["kind"], "text": describe_episode(e),
                        "own_value": (e.get("individual") or {}).get("net")})
        else:
            out.append({"player": pid, "role": role, "did": False, "kind": None, "text": NOT_DONE.get(role, "")})
    return out


def outcome_text(w, possession):
    parts = []
    if w["shots"]:
        parts.append(f"{w['shots']} shot(s) worth {w['xg']:.2f} xG within 15 s" + (" and a goal" if w["goal"] else ""))
    elif w["final_third"]:
        parts.append("the attack reached the final third but no shot came within 15 s")
    else:
        parts.append("no shot within 15 s")
    if w.get("lost_in_horizon"):
        parts.append("the ball was lost" + (f" and the opponent countered for {w['conceded_xg_12s']:.2f} xG" if w["conceded_xg_12s"] else ""))
    end = {"goal": "the possession ended in a goal", "shot": "the possession ended with a shot",
           "turnover": "the possession ended with a turnover", "keeper_claim": "the keeper claimed it"}.get(possession["end_reason"])
    if end:
        parts.append(end)
    return "; ".join(parts)


def analyse_match(path):
    """Online pipeline over one finished match plus offline extras. Returns a JSON-able dict."""
    from team_player.analyser.live import LivePipeline
    from team_player.analyser.presentation import Preferences
    from team_player.analyser.comparison import Accumulator
    path = Path(path)
    with open_text(path) as f:
        meta = json.loads(next(f))
        pipe = LivePipeline(meta);acc = Accumulator(meta)
        pref = Preferences(player=next(p["id"] for p in meta["players"] if "ST" in p["role"]))
        for line in f:
            packet = pipe.update(json.loads(line), pref)
            acc.update(packet["frame"], packet["analysis"], packet["tactics"])
        acc.update(packet["frame"], None, {"completed": pipe.finish()})
    tr = pipe.tactics
    episodes = [e for e in tr.completed_all if e["kind"] != "possession"]
    possessions = [e for e in tr.completed_all if e["kind"] == "possession"]
    windows = []
    for p in possessions:
        for w in p.get("windows", []):
            windows.append({**{k: w[k] for k in ("t", "context", "choices", "xg", "shots", "goal", "max_xt", "xt_gain",
                                                  "final_third", "lost_in_horizon", "conceded_xg_12s", "conceded_goal_12s")},
                            "team": p["team"], "opponent": p["opponent"], "lineup": p["lineup"], "t_end": p["t_end"],
                            "possession_start": p["t_start"], "end_reason": p["end_reason"],
                            "actions": window_actions(w, p["team"], p["lineup"], episodes), "outcome_text": outcome_text(w, p),
                            "possession_result": {k: p[k] for k in ("shots", "xg", "goal", "end_reason", "duration_s")}})
    info = {k: meta.get(k) for k in ("match_id", "season", "matchweek", "home", "away", "kickoff")}
    # Raw material for refitting xG / xT on the history seasons (offline only).
    shots = [{k: s[k] for k in ("angle_rad", "distance_m", "blockers", "under_pressure", "goal")} | {"cell": list(s["cell"])} for s in tr.shot_log]
    moves = [[list(a), list(b), bool(ok)] for a, b, ok in tr.moves]
    return {"version": DB_VERSION, "file": path.name, "fingerprint": fingerprint(path), **info,
            "teams": [t["id"] for t in meta["teams"]], "windows": windows, "shots": shots, "moves": moves,
            "players": list(acc.rows.values()), "episodes": len(episodes), "possessions": len(possessions)}


def _defence_samples(path):
    """Fullback positions while the opponents have the ball (positions only, via the observer)."""
    from team_player.analyser.observation import TrackingObserver
    from team_player.utils.geometry import to_local
    from team_player.config.analysis import DEF_TEMPLATE_AFTER_S, ROLE_TEMPLATE_BIN_M, COUNTER_WINDOW, DEF_SHAPE_RADIUS_M
    with open_text(path) as f:
        meta = json.loads(next(f));obs = TrackingObserver(meta)
        dirs = {t["id"]: t["attack_direction"] for t in meta["teams"]}
        fbs = [(k, p["id"], p["team"], p["role"]) for k, p in enumerate(meta["players"]) if p["role"] in ("LB", "RB")]
        spots, moves, since, prev, last = [], [], {}, None, None
        for line in f:
            fr = obs.update(json.loads(line));t = fr["t"]
            poss = last = fr["possession"] or last     # a ball in flight keeps the last team's possession
            P = np.array([[p["x"], p["y"]] for p in fr["players"]]);B = np.array([fr["ball"]["x"], fr["ball"]["y"]])
            for k, pid, team, role in fbs:
                key = (pid, team)
                if poss is None or poss == team:
                    since.pop(key, None);continue
                start = since.setdefault(key, t)
                me = to_local(P[k], dirs[team]);bl = to_local(B, dirs[team])
                b = int(min(13, max(0, bl[0] // ROLE_TEMPLATE_BIN_M)))
                if t - start >= DEF_TEMPLATE_AFTER_S:
                    spots.append((role, b, float(me[0]), float(me[1])))
                elif prev is not None and t - start <= COUNTER_WINDOW:
                    moves.append((role, b, float(me[0]), float(me[1]), *(to_local(prev[k], dirs[team]) - me).tolist(), t - prev_t))
            prev, prev_t = P, t
    return spots, moves


def _threat_vs_shots(path):
    """Uncalibrated threat forecast vs real shots in the defending windows of one match (LB of each team)."""
    from team_player.analyser.live import LivePipeline
    from team_player.analyser.presentation import Preferences
    with open_text(path) as f:
        meta = json.loads(next(f));pipe = LivePipeline(meta);pref = Preferences(player=meta["players"][0]["id"])
        eps = [e for line in f for e in pipe.update(json.loads(line), pref)["tactics"]["completed"]] + pipe.finish()
    lb = {p["id"] for p in meta["players"] if p["role"] == "LB"}
    ds = [e["value"] for e in eps if e["kind"] in ("fullback_recovery", "fullback_defence") and e["player"] in lb]
    return sum(v["net"] + v["shots_xg"] for v in ds), sum(v["shots_xg"] for v in ds)


def fit_threat_calibration(history_dir, workers=1):
    """Scale so that the opponents' forecast threat matches the xG they actually shot (history only).
    Run with THREAT_CALIBRATION = 1.0 in config/analysis.py."""
    with ProcessPoolExecutor(workers) as ex:
        r = list(ex.map(_threat_vs_shots, match_files(history_dir)))
    v, s = sum(a for a, _ in r), sum(b for _, b in r)
    return {"threat_calibration": round(s / v, 3), "forecast_xg": round(v, 2), "shot_xg": round(s, 2), "matches": len(r)}


def fit_defence(history_dir, workers=1):
    """Defensive normal positions of LB/RB by ball-x bin (medians) and the average recovery speed:
    mean speed towards that spot in the first 12 s after the opponents win the ball, while still
    more than DEF_SHAPE_RADIUS_M away. History positions only."""
    from team_player.config.analysis import DEF_SHAPE_RADIUS_M
    files = match_files(history_dir)
    with ProcessPoolExecutor(workers) as ex:
        parts = list(ex.map(_defence_samples, files))
    spots = [s for a, _ in parts for s in a];moves = [m for _, b in parts for m in b]
    tpl = {}
    for role in ("LB", "RB"):
        rows = []
        for b in range(14):
            pts = np.array([(x, y) for r, bb, x, y in spots if r == role and bb == b])
            rows.append(None if len(pts) < 20 else (round(float(np.median(pts[:, 0])), 1), round(float(np.median(pts[:, 1])), 1)))
        known = [i for i, v in enumerate(rows) if v]
        tpl[role] = [rows[i] or rows[min(known, key=lambda k: abs(k - i))] for i in range(14)]
    speeds = []
    for role, b, x, y, dx, dy, dt in moves:
        spot = np.array(tpl[role][b]);here = np.array([x, y]);gap = spot - here;d = np.linalg.norm(gap)
        if d > DEF_SHAPE_RADIUS_M and dt > 0:
            step = -np.array([dx, dy]) / dt          # velocity (prev -> now)
            speeds.append(float(step @ gap / d))
    return {"templates": tpl, "recovery_speed_m_s": round(float(np.mean(speeds)), 2), "samples": len(spots), "recovery_samples": len(speeds),
            "matches": len(files)}


def fit_value_models(analysis_dir, out_path):
    """Refit xG and xT on all analysed history matches (online use: TEAM_PLAYER_VALUE_MODELS)."""
    from team_player.analyser.models import fit_xg, fit_xt, ThreatModel, XGModel
    shots, moves = [], []
    for f in sorted(Path(analysis_dir).rglob("*.json.gz")):
        with gzip.open(f, "rt", encoding="utf-8") as h:
            m = json.load(h)
        shots += m.get("shots", []);moves += m.get("moves", [])
    xg = fit_xg(shots)
    coeff = {k: v for k, v in xg.items() if k in ("intercept", "angle", "distance", "blockers", "pressure")}
    xt = fit_xt([(tuple(a), tuple(b), ok) for a, b, ok in moves], [(tuple(s["cell"]), s["goal"]) for s in shots],
                prior=ThreatModel(xg=XGModel(coeff)).grid)
    Path(out_path).write_text(json.dumps({"xg": xg, "xt": xt.round(5).tolist(), "fitted_on": {"shots": len(shots), "moves": len(moves)}}, indent=1))
    return out_path


def analysis_name(src, root):
    """Mirror the input layout (season folders) so names never collide across seasons."""
    rel = Path(src).resolve().relative_to(Path(root).resolve())
    return rel.with_name(rel.name.replace(".jsonl.gz", "").replace(".jsonl", "") + ".json.gz")


def analyse_to_file(args):
    src, out_dir, root = args
    target = Path(out_dir) / analysis_name(src, root)
    if target.exists():
        return str(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    result = analyse_match(src)
    tmp = target.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as h:
        json.dump(result, h, separators=(",", ":"))
    os.replace(tmp, target)
    return str(target)


def analyse_folder(src, out, workers=1):
    jobs = [(str(p), out, src) for p in match_files(src)]
    if workers > 1:
        with ProcessPoolExecutor(workers) as pool:
            return list(pool.map(analyse_to_file, jobs))
    return [analyse_to_file(j) for j in jobs]


# --------------------------------------------------------------------- store ---

FEATURE_COLS = [f"f_{name}" for name, _ in SIMILARITY_FEATURES]
SCHEMA = f"""
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE matches(fixture_id TEXT PRIMARY KEY, season TEXT, matchweek INT, home TEXT, away TEXT, kickoff TEXT,
                     file TEXT, fingerprint TEXT);
CREATE TABLE windows(id INTEGER PRIMARY KEY, fixture_id TEXT, season TEXT, team TEXT, opponent TEXT, t REAL, t_end REAL,
                     {', '.join(c + ' REAL' for c in FEATURE_COLS)},
                     ch_striker_dropped INT, ch_fb_advanced INT, ch_winger_decoy INT,
                     xg REAL, goal INT, shots INT, max_xt REAL, xt_gain REAL, final_third INT, lost INT,
                     conceded_xg REAL, conceded_goal INT, end_reason TEXT, lineup TEXT, actions TEXT, outcome_text TEXT,
                     possession_result TEXT);
CREATE TABLE player_matches(player TEXT, fixture_id TEXT, season TEXT, team TEXT, role TEXT, stats TEXT);
CREATE INDEX pm_player ON player_matches(player);
"""


def build_store(analysis_dir, db_path):
    """Merge per-match analysis files into one SQLite file (rebuilt from scratch)."""
    db_path = Path(db_path);db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".building")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp);con.executescript(SCHEMA)
    n = 0
    for f in sorted(Path(analysis_dir).rglob("*.json.gz")):
        with gzip.open(f, "rt", encoding="utf-8") as h:
            m = json.load(h)
        fid = m.get("match_id") or m["file"]
        con.execute("INSERT OR REPLACE INTO matches VALUES(?,?,?,?,?,?,?,?)",
                    (fid, m.get("season"), m.get("matchweek"), m.get("home") or m["teams"][0], m.get("away") or m["teams"][1],
                     m.get("kickoff"), m["file"], m["fingerprint"]))
        for w in m["windows"]:
            c = w["choices"]
            con.execute(f"INSERT INTO windows(fixture_id,season,team,opponent,t,t_end,{','.join(FEATURE_COLS)},"
                        "ch_striker_dropped,ch_fb_advanced,ch_winger_decoy,xg,goal,shots,max_xt,xt_gain,final_third,lost,"
                        "conceded_xg,conceded_goal,end_reason,lineup,actions,outcome_text,possession_result) VALUES("
                        + ",".join("?" * (6 + len(FEATURE_COLS) + 17)) + ")",
                        (fid, m.get("season"), w["team"], w["opponent"], w["t"], w["t_end"],
                         *[float(w["context"][name]) for name, _ in SIMILARITY_FEATURES],
                         int(bool(c.get("striker_dropped"))), int(bool(c.get("fb_advanced"))), int(bool(c.get("winger_decoy"))),
                         w["xg"], int(w["goal"]), w["shots"], w["max_xt"], w["xt_gain"], int(w["final_third"]),
                         int(w["lost_in_horizon"]), w["conceded_xg_12s"], int(w["conceded_goal_12s"]), w["end_reason"],
                         json.dumps(w["lineup"]), json.dumps(w["actions"]), w["outcome_text"], json.dumps(w["possession_result"])))
        for p in m["players"]:
            con.execute("INSERT INTO player_matches VALUES(?,?,?,?,?,?)",
                        (p["id"], fid, m.get("season"), p["team"], p["role"],
                         json.dumps({k: v for k, v in p.items() if k not in ("id", "team", "role")})))
        n += 1
    con.execute("INSERT INTO meta VALUES('version',?)", (str(DB_VERSION),))
    con.execute("INSERT INTO meta VALUES('matches',?)", (str(n),))
    con.commit();con.close();os.replace(tmp, db_path)
    return db_path, n


class HistoryStore:
    """Read-only access to history.db: player history totals and the similar-situation index."""

    def __init__(self, db_path):
        self.path = str(db_path)
        self.con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        if self.con.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0] != str(DB_VERSION):
            raise ValueError("Unsupported history store version")

    def summary(self):
        q = lambda sql: self.con.execute(sql).fetchone()[0]
        return {"matches": q("SELECT COUNT(*) FROM matches"), "windows": q("SELECT COUNT(*) FROM windows"),
                "seasons": [r[0] for r in self.con.execute("SELECT DISTINCT season FROM matches ORDER BY season")]}

    def player_history(self, player):
        """Totals over all history matches, plus one row per season."""
        from team_player.analyser.comparison import FIELDS
        total = dict.fromkeys(FIELDS, 0.);seasons = {};role = team = None;n = 0
        for r in self.con.execute("SELECT season, team, role, stats FROM player_matches WHERE player=?", (player,)):
            s = json.loads(r["stats"]);role, team = r["role"], r["team"];n += 1
            row = seasons.setdefault(r["season"], dict.fromkeys(FIELDS, 0.) | {"matches": 0})
            row["matches"] += 1
            for k in FIELDS:
                total[k] += s.get(k, 0.);row[k] += s.get(k, 0.)
        return {"id": player, "team": team, "role": role, "matches": n, "totals": total, "seasons": seasons}

    def all_player_totals(self):
        """History totals for every player (rows for the head-to-head comparison)."""
        from team_player.analyser.comparison import FIELDS
        rows = {}
        for r in self.con.execute("SELECT player, team, role, stats FROM player_matches"):
            s = json.loads(r["stats"])
            row = rows.setdefault(r["player"], {"id": r["player"], "team": r["team"], "role": r["role"], "matches": 0, **dict.fromkeys(FIELDS, 0.)})
            row["matches"] += 1
            for k in FIELDS:
                row[k] += s.get(k, 0.)
        return rows

    def index(self):
        """SituationIndex over every history decision window (arrays in memory, details on demand)."""
        from team_player.analyser.situations import SituationIndex
        cols = ",".join(FEATURE_COLS)
        rows = self.con.execute(f"SELECT id,team,opponent,{cols},ch_striker_dropped,ch_fb_advanced,ch_winger_decoy,"
                                "xg,goal,final_third,conceded_xg,max_xt FROM windows ORDER BY id").fetchall()
        F = len(FEATURE_COLS)
        arr = np.array([tuple(r)[3:] for r in rows], float) if rows else np.zeros((0, F + 8))
        table = {"id": np.array([r[0] for r in rows]), "team": np.array([r[1] for r in rows]), "opp": np.array([r[2] for r in rows]),
                 "X": arr[:, :F], "choices": arr[:, F:F + 3].astype(bool), "xg": arr[:, F + 3], "goal": arr[:, F + 4],
                 "final_third": arr[:, F + 5], "conceded": arr[:, F + 6], "max_xt": arr[:, F + 7]}
        return SituationIndex(table=table, detail=self.window_detail)

    def window_detail(self, window_id):
        r = self.con.execute("SELECT w.*, m.matchweek, m.home, m.away, m.kickoff FROM windows w JOIN matches m "
                             "ON m.fixture_id=w.fixture_id WHERE w.id=?", (int(window_id),)).fetchone()
        d = dict(r)
        for k in ("lineup", "actions", "possession_result"):
            d[k] = json.loads(d[k])
        d["choices"] = {"striker_dropped": bool(d["ch_striker_dropped"]), "fb_advanced": bool(d["ch_fb_advanced"]),
                        "winger_decoy": bool(d["ch_winger_decoy"])}
        d["t_start"] = d["t"];d["clock"] = clock(d["t"]);d["conceded_xg_12s"] = d["conceded_xg"];d["conceded_goal_12s"] = d["conceded_goal"]
        d["lost_in_horizon"] = bool(d["lost"])
        return d


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyse");a.add_argument("--input", required=True);a.add_argument("--out", required=True)
    a.add_argument("--workers", type=int, default=1)
    b = sub.add_parser("build");b.add_argument("--input", required=True);b.add_argument("--db", required=True)
    f = sub.add_parser("fit");f.add_argument("--input", required=True);f.add_argument("--out", required=True)
    d = sub.add_parser("fit-defence", help="fullback defensive spots + recovery speed (paste into config/analysis.py)")
    d.add_argument("--input", required=True);d.add_argument("--workers", type=int, default=1)
    d.add_argument("--calibrate", action="store_true", help="instead: fit THREAT_CALIBRATION (runs the full pipeline)")
    args = p.parse_args()
    if args.cmd == "analyse":
        print(len(analyse_folder(args.input, args.out, args.workers)), "matches analysed")
    elif args.cmd == "fit-defence":
        print(json.dumps((fit_threat_calibration if args.calibrate else fit_defence)(args.input, args.workers)))
    elif args.cmd == "fit":
        print(fit_value_models(args.input, args.out))
    else:
        path, n = build_store(args.input, args.db);print(f"{path}: {n} matches")


if __name__ == "__main__":
    main()
