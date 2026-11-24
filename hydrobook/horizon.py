"""Derive the feasible correction range for a connected cluster of datums."""
from collections import defaultdict

from .formats import require
from .store import at, refs


def available(snapshot, day):
    surveys = snapshot.active("survey")
    ties = [row for row in snapshot.active("tie").values() if at(row, day)]
    accepted, rejected = [], []
    for tie in ties:
        survey = surveys.get(tie["survey"])
        eligible = survey and survey["result"] == "certified" and at(survey, day)
        (accepted if eligible else rejected).append((tie, survey))
    return accepted, rejected


def bounds(snapshot, source, target, day, limit):
    # Bound d[v]-d[u]; simultaneous constraints share actual datum coordinates.
    certified, rejected = available(snapshot, day)
    links = defaultdict(set)
    for tie, _ in certified:
        left, right = tie["from_datum"], tie["to_datum"]
        links[left].add(right)
        links[right].add(left)
    connected, pending = {source}, [source]
    while pending:
        for neighbor in links[pending.pop()] - connected:
            connected.add(neighbor)
            pending.append(neighbor)
    edges = [(tie, survey) for tie, survey in certified if tie["from_datum"] in connected]
    proof = refs(unique(row for pair in edges for row in pair))
    ignored = refs(unique(row for pair in rejected for row in pair if row is not None))
    if target not in connected:
        return {"state": "unlinked_datums", "evidence": proof, "excluded": ignored}
    if source == target:
        return {"state": "aligned", "range": [0, 0], "evidence": [], "excluded": ignored}
    require(len(connected) <= 96, "connected datum cluster exceeds 96 points")
    vertices = sorted(connected)
    index = {key: i for i, key in enumerate(vertices)}
    dist = [[0 if a == b else float("inf") for b in vertices] for a in vertices]
    for edge, _ in edges:
        i, j = index[edge["from_datum"]], index[edge["to_datum"]]
        error, offset = edge["error"], edge["offset"]
        dist[i][j] = min(dist[i][j], offset + error)
        dist[j][i] = min(dist[j][i], -offset + error)
    for k, links_through in enumerate(dist):
        for line in dist:
            if line[k] == float("inf"):
                continue
            via = line[k]
            for j, tail in enumerate(links_through):
                if via + tail < line[j]:
                    line[j] = via + tail
    if any(dist[i][i] < 0 for i in range(len(vertices))):
        return {"state": "inconsistent_level_loop", "evidence": proof, "excluded": ignored}
    i, j = index[source], index[target]
    lo, hi = -dist[j][i], dist[i][j]
    return {"state": "aligned" if hi - lo <= limit else "error_budget_exceeded",
            "range": [lo, hi], "evidence": proof, "excluded": ignored}


def unique(rows):
    return {(row["kind"], row["id"]): row for row in rows}.values()
