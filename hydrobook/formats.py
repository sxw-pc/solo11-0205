"""Validated schemas and strict, dependency-free offline receipt decoding."""
import json
import re


class EvidenceError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def text(value):
    require(isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", value)),
            f"invalid identifier: {value!r}")
    return value


def integer(value, low, high):
    require(type(value) is int and low <= value <= high, f"expected integer in {low}..{high}")
    return value


def day(value):
    return integer(value, 0, 100_000)


def field(value, options):
    require(value in options, f"expected one of {options!r}")
    return value


def span(record):
    require(day(record["start"]) < day(record["end"]), "interval must be increasing")


def encoded(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def unique_keys(pairs):
    value = dict(pairs)
    require(len(value) == len(pairs), "duplicate JSON key")
    return value


def invalid_constant(value):
    raise EvidenceError(f"unsupported JSON numeric constant: {value}")


FIELDS = {
    "section": "station reach datum profile start end zero",
    "strip": "station section start end scale_lo scale_hi",
    "reading": "strip day mark source",
    "tie": "from_datum to_datum start end offset error survey",
    "survey": "start end reviewer result",
    "campaign": "profile datum start end result",
    "gauging": "campaign day stage flow",
    "edition": "start end root max_error basin",
    "series": "edition station reach",
}


def validate(row):
    require(isinstance(row, dict), "record must be an object")
    kind = row.get("kind")
    require(isinstance(kind, str) and kind in FIELDS, "unknown receipt record kind")
    names = FIELDS[kind].split()
    require(set(row) == {"kind", "id", "rev", "state", *names}, f"unexpected fields for {kind}")
    text(row["id"])
    integer(row["rev"], 1, 1_000_000)
    field(row["state"], ("active", "void"))
    if "start" in names:
        span(row)
    for key in names:
        value = row[key]
        if key in {"start", "end"}:
            continue
        if key == "day":
            day(value)
        elif key in {"zero", "mark", "scale_lo", "scale_hi", "offset", "stage"}:
            integer(value, -100_000, 100_000)
        elif key in {"error", "max_error"}:
            integer(value, 0, 10_000)
        elif key == "flow":
            integer(value, 0, 10_000_000)
        elif key == "result":
            field(value, ("certified", "rejected"))
        elif key == "source":
            field(value, ("witness", "digitizer"))
        else:
            text(value)
    if kind == "tie":
        require(row["from_datum"] != row["to_datum"], "datum cannot be tied to itself")
    elif kind == "strip":
        require(row["scale_lo"] < row["scale_hi"], "strip scale is inverted")
    elif kind == "edition":
        require(row["end"] - row["start"] <= 366, "edition cannot exceed 366 days")
    return row


def parse(raw):
    require(len(raw) <= 2_000_000, "receipt exceeds 2 MB")
    items = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_keys,
                       parse_constant=invalid_constant)
    require(isinstance(items, list) and 0 < len(items) <= 5_000, "expected 1..5000 records")
    return [validate(row) for row in items]
