"""Offline operations, each with a consistent receipt cut and explicit bounds."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from . import annual, archive, capture_plan, flow, horizon, sensor_log
from .formats import day, integer, require, text
from .store import Snapshot, connect, ingest, initialize, transaction

SAMPLER_ACTIONS = {'program': capture_plan.program, 'capture': sensor_log.capture, 'progress': sensor_log.progress}

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def demo(db):
    require(Snapshot(db).cut == 0, "demo requires an empty archive")
    summaries = {}
    for label, file in [("original", "season.json"), ("late_tie", "contradiction.json"), ("restored", "withdraw.json")]:
        ingest(db, (FIXTURES / file).read_bytes())
        issued = annual.publication(db, "BASIN-26")
        report = issued["report"]
        northern = report["series"]["NORTH"]
        summaries[label] = {"issue": issued["issue"], "decision": report["decision"],
                            "comparison": report["comparison"], "stage_gaps": northern["stage_gaps"],
                            "northern_volume_l": northern["volume_l"]}
    return summaries


def parser():
    root = argparse.ArgumentParser(description="Offline hydrometric field archive and yearbook")
    root.add_argument("--db", required=True)
    sub = root.add_subparsers(dest="command", required=True)
    for name in ("init", "demo", "audit"):
        sub.add_parser(name)
    sub.add_parser("capture").add_argument("file", type=Path)
    sampler = sub.add_parser('sampler')
    sampler.add_argument('action', choices=SAMPLER_ACTIONS)
    sampler.add_argument('arguments')
    daily = sub.add_parser("chart-day")
    daily.add_argument("station", type=text)
    daily.add_argument("reach", type=text)
    align = sub.add_parser("alignment")
    align.add_argument("source", type=text)
    align.add_argument("target", type=text)
    align.add_argument("max_error", type=int)
    rating = sub.add_parser("rating")
    rating.add_argument("profile", type=text)
    rating.add_argument("datum", type=text)
    rating.add_argument("low", type=int)
    rating.add_argument("high", type=int)
    for operation in (daily, align, rating):
        operation.add_argument("day", type=int)
        operation.add_argument("--cut", type=int)
    for name in ("yearbook", "issue"):
        command = sub.add_parser(name)
        command.add_argument("edition", type=text)
        command.add_argument("--cut", type=int)
    sub.add_parser("issued").add_argument("number", type=int)
    return root


def execute(db, args):
    if args.command == "capture":
        return ingest(db, args.file.read_bytes())
    if args.command == "issue":
        return annual.publication(db, args.edition, args.cut)
    if args.command == "issued":
        return annual.read_issue(db, args.number)
    if args.command == "audit":
        return annual.audit(db)
    if args.command == "demo":
        return demo(db)
    if args.command == 'sampler':
        arguments = json.loads(args.arguments)
        require(isinstance(arguments, dict), 'expected sampler argument object')
        capture_plan.initialize(db)
        return SAMPLER_ACTIONS[args.action](db, **arguments)
    snapshot = Snapshot(db, args.cut)
    if args.command == "chart-day":
        return archive.stage(snapshot, args.station, args.reach, day(args.day))
    if args.command == "alignment":
        return horizon.bounds(snapshot, args.source, args.target, day(args.day), integer(args.max_error, 0, 10_000))
    if args.command == "rating":
        low, high = [integer(value, -200_000, 200_000) for value in (args.low, args.high)]
        require(low <= high, "inverted stage bound")
        return flow.curve(snapshot, args.profile, args.datum, day(args.day), low, high)
    return annual.report(snapshot, args.edition)


def main(argv=None):
    handle = parser()
    args = handle.parse_args(argv)
    try:
        if args.command not in ("init", "demo"):
            require(Path(args.db).is_file(), "initialize a database first")
        with closing(connect(args.db)) as db:
            if args.command in ("init", "demo"):
                initialize(db)
            require(db.execute("PRAGMA user_version").fetchone()[0] == 1, "unsupported database schema")
            if args.command == "init":
                result = {"schema": 1}
            else:
                with transaction(db, writing=args.command in ("capture", "issue", "demo", "sampler")):
                    result = execute(db, args)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (ValueError, TypeError, OSError, sqlite3.Error) as error:
        handle.exit(2, f"hydrobook: {error}\n")

