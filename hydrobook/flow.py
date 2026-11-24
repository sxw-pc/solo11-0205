"""Qualify discharge campaigns and bound flow by monotone local interpolation."""
from bisect import bisect_right
from fractions import Fraction
from math import ceil, floor

from .store import at, refs


def curve(snapshot, profile, datum, day, low, high):
    rows = [row for row in snapshot.active("campaign").values() if row["profile"] == profile and row["datum"] == datum and at(row, day)]
    recognized = [row for row in rows if row["result"] == "certified"]
    if len(recognized) != 1:
        detail = "overlapping_campaigns" if recognized else "no_certified_campaign"
        return {"state": detail, "evidence": refs(rows)}
    campaign, = recognized
    points = [row for row in snapshot.active("gauging").values()
              if row["campaign"] == campaign["id"] and at(campaign, row["day"]) and row["day"] <= day]
    proof = refs([campaign, *points])
    stage_points = {}
    for row in points:
        flows = stage_points.setdefault(row["stage"], set())
        flows.add(row["flow"])
    if any(len(flows) != 1 for flows in stage_points.values()):
        return {"state": "contested_gauging", "evidence": proof}
    rated = sorted((stage, next(iter(flows))) for stage, flows in stage_points.items())
    if any(previous[1] > current[1] for previous, current in zip(rated, rated[1:])):
        return {"state": "non_monotone_rating", "evidence": proof}
    if len(rated) < 2 or low < rated[0][0] or high > rated[-1][0]:
        return {"state": "unbracketed_stage", "evidence": proof}
    stage_axis = [item[0] for item in rated]

    def interpolate(stage):
        index = bisect_right(stage_axis, stage) - 1
        a, q = rated[index]
        if stage == a:
            return Fraction(q)
        b, r = rated[index + 1]
        return q + Fraction((r - q) * (stage - a), b - a)

    return {"state": "rated", "litres_per_second": [floor(interpolate(low)), ceil(interpolate(high))],
            "evidence": proof}
