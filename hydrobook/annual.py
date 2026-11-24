"""Build and publish an edition without substituting estimates for missing days."""
import json
from contextlib import closing

from . import archive, flow, horizon
from .formats import encoded, require
from .store import Snapshot, connect, digest, initialize, reference, replay_receipts, transaction


def for_day(snapshot, station, reach, edition, day):
    result = {"day": day, "archive": archive.stage(snapshot, station, reach, day)}
    raw = result["archive"]
    if raw["state"] != "measured":
        return result
    datum = horizon.bounds(snapshot, raw["datum"], edition["root"], day, edition["max_error"])
    result["datum"] = datum
    if datum["state"] == "aligned":
        result["stage_mm"] = [raw["stage"] + offset for offset in datum["range"]]
    # The profile is kept in its own observed local datum; volume can be proved
    # while the basin-wide common-datum stage is still unavailable.
    result["discharge"] = flow.curve(snapshot, raw["profile"], raw["datum"], day, raw["stage"], raw["stage"])
    return result


def report(snapshot, edition_id):
    edition = snapshot.active("edition").get(edition_id)
    require(edition is not None, f"unknown or withdrawn edition {edition_id}")
    assignments = [row for row in snapshot.active("series").values() if row["edition"] == edition_id]
    require(0 < len(assignments) <= 60, "edition must have 1..60 station assignments")
    seen = set()
    series = {}
    for allocation in assignments:
        station, reach = allocation["station"], allocation["reach"]
        require(station not in seen, f"conflicting series assignment for {station}")
        seen.add(station)
        days = [for_day(snapshot, station, reach, edition, day) for day in range(edition["start"], edition["end"])]
        stage_gaps = [row["day"] for row in days if "stage_mm" not in row]
        volume_gaps = [row["day"] for row in days if row.get("discharge", {}).get("state") != "rated"]
        peak = [max(row["stage_mm"][end] for row in days) for end in range(2)] if not stage_gaps else None
        volume = [86400 * sum(row["discharge"]["litres_per_second"][end] for row in days)
                  for end in range(2)] if not volume_gaps else None
        series[station] = {"reach": reach, "assignment": reference(allocation), "days": days,
                           "stage_gaps": stage_gaps, "volume_gaps": volume_gaps,
                           "peak_mm": peak, "volume_l": volume}
    # Compare in the same declared catchment even when flow has no complete series.
    hulls = {station: row["peak_mm"] for station, row in series.items() if row["peak_mm"] is not None}
    strictly_highest = [station for station, limits in hulls.items()
                        if all(limits[0] > other[1] for name, other in hulls.items() if name != station)]
    if len(hulls) != len(series):
        comparison = {"state": "incomplete_stage"}
    elif len(series) < 2:
        comparison = {"state": "single_series"}
    elif strictly_highest:
        comparison = {"state": "separable_peak", "station": strictly_highest[0]}
    else:
        comparison = {"state": "peak_order_uncertain"}
    return {"edition": edition_id, "contract": reference(edition), "cut": snapshot.cut,
            "basin": edition["basin"], "series": series, "comparison": comparison,
            "decision": "complete" if all(row["peak_mm"] is not None and row["volume_l"] is not None
                                          for row in series.values()) else "partial"}


def publication(db, edition, cut=None):
    snapshot = Snapshot(db, cut)
    result = report(snapshot, edition)
    latest = db.execute("SELECT * FROM issue WHERE edition=? ORDER BY cut DESC LIMIT 1", (edition,)).fetchone()
    if latest and latest["cut"] == snapshot.cut:
        require(latest["body"] == encoded(result) and latest["digest"] == digest(encoded(result).encode()),
                "stored issue does not match its evidence")
        return {"issue": latest["id"], "prior": latest["prior"], "report": result}
    require(latest is None or latest["cut"] < snapshot.cut, "cannot issue before current edition")
    body, prior = encoded(result), latest["id"] if latest else None
    key = db.execute("INSERT INTO issue(edition,cut,prior,digest,body) VALUES(?,?,?,?,?)",
                     (edition, snapshot.cut, prior, digest(body.encode()), body)).lastrowid
    return {"issue": key, "prior": prior, "report": result}


def audit(db):
    with closing(connect(":memory:")) as rebuilt:
        initialize(rebuilt)
        with transaction(rebuilt, writing=True):
            count = replay_receipts(db, rebuilt)
            issues = 0
            for row in db.execute("SELECT * FROM issue ORDER BY id"):
                checked = publication(rebuilt, row["edition"], row["cut"])
                body = encoded(checked["report"])
                require((checked["issue"], checked["prior"], body, digest(body.encode())) ==
                        (row["id"], row["prior"], row["body"], row["digest"]), f"issue {row['id']} replay mismatch")
                issues += 1
    return {"verified": True, "records": count, "issues": issues}


def read_issue(db, key):
    row = db.execute("SELECT * FROM issue WHERE id=?", (key,)).fetchone()
    require(row is not None, f"unknown issue {key}")
    require(digest(row["body"].encode()) == row["digest"], f"issue {key} digest mismatch")
    return {"issue": key, "prior": row["prior"], "report": json.loads(row["body"])}
