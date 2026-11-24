"""Locate the original observation within a versioned station and chart archive."""
from .store import at, refs


def stage(snapshot, station, reach, day):
    candidates = [row for row in snapshot.active("section").values()
                  if row["station"] == station and at(row, day)]
    if len(candidates) != 1:
        return {"state": "unassigned_station" if not candidates else "overlapping_sections",
                "evidence": refs(candidates)}
    section, = candidates
    if section["reach"] != reach:
        return {"state": "wrong_reach", "evidence": refs(candidates)}
    strips = {row["id"]: row for row in snapshot.active("strip").values()
              if row["station"] == station and row["section"] == section["id"] and at(row, day)}
    observations = [row for row in snapshot.active("reading").values()
                    if row["strip"] in strips and row["day"] == day]
    evidence = refs([section, *strips.values(), *observations])
    if not observations:
        return {"state": "missing_chart_mark", "evidence": evidence}
    witnessed = [row for row in observations if row["source"] == "witness"]
    preferred = witnessed or observations
    if any(not strips[row["strip"]]["scale_lo"] <= row["mark"] <= strips[row["strip"]]["scale_hi"]
           for row in preferred):
        return {"state": "chart_scale_exceeded", "evidence": evidence}
    marks = {row["mark"] for row in preferred}
    if len(marks) != 1:
        return {"state": "competing_chart_marks", "evidence": evidence}
    return {"state": "measured", "datum": section["datum"], "profile": section["profile"],
            "stage": marks.pop() + section["zero"], "authority": preferred[0]["source"],
            "section": section["id"], "evidence": evidence}
