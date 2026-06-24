#!/usr/bin/env python3
"""
Backfill the structured geo-restriction model (docs/GEO_REFACTOR_SCOPE.md).

Sets `effect`, `effect_value`, and `conditions` on every row of
`map_geographic_restrictions` from a deterministic classification of the
distinct `restriction_detail` strings (28 patterns as of this writing).

- Matches by a distinctive *signature* substring (or exact text) so the backfill
  survives minor wording drift, scoped by state where a detail is reused across
  states with different sub-locations.
- Idempotent and re-runnable: each rule is an UPDATE; running twice is a no-op.
- Dry-run by default; pass --apply to commit.

Usage:
    python -m ingest.tools.backfill_geo_conditions            # dry run
    python -m ingest.tools.backfill_geo_conditions --apply     # write to MySQL

Reads MySQL connection from .env (MYSQL_HOST/PORT/USER/PASSWORD/DATABASE).
Run migration 019 first.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# --- occupancy / location codes match backend/eligibility.py's normalized form.
# occupancy:  primary | second | investment
# location.kind: state | territory | county | city | zip
# property_type uses the engine's property_type_code values (e.g. two_to_four_family).
# `unevaluable` entries demote the effect to a note (app cannot assess the dimension).

INVEST = ["investment"]

# Each rule: states (filter), match ("contains"|"exact"), sig (lowercased),
# effect, effect_value (dict|None), conditions (dict).
RULES: list[dict] = [
    # 1 — DC investment
    {"states": ["DC"], "match": "contains", "sig": "investment properties are ineligible",
     "effect": "ineligible",
     "conditions": {"location": {"kind": "state", "any": ["DC"]}, "occupancy": INVEST}},
    # 2 — FL restricted counties, investment
    {"states": ["FL"], "match": "contains", "sig": "charlotte, lee, hendry",
     "effect": "ineligible",
     "conditions": {"location": {"kind": "county", "any": ["charlotte", "lee", "hendry", "glades"]},
                    "occupancy": INVEST}},
    # 3 — FL: PRC non-permanent resident aliens (nationality unevaluable → note)
    {"states": ["FL"], "match": "contains", "sig": "peoples republic of china",
     "effect": "ineligible",
     "conditions": {"location": {"kind": "state", "any": ["FL"]},
                    "borrower": {"citizenship": ["non_permanent_resident"], "country_of_origin": ["china"]}}},
    # 4 — IL Cook County, all occupancies
    {"states": ["IL"], "match": "contains", "sig": "cook county",
     "effect": "ineligible",
     "conditions": {"location": {"kind": "county", "any": ["cook"]}}},
    # 5 — IL Kane/Peoria/Will: TRID, non-correspondent (unevaluable → note)
    {"states": ["IL"], "match": "contains", "sig": "kane, peoria and will",
     "effect": "ineligible",
     "conditions": {"location": {"kind": "county", "any": ["kane", "peoria", "will"]},
                    "unevaluable": ["loan_program:trid", "channel:non_correspondent"]}},
    # 6 — IN Indianapolis, investment
    {"states": ["IN"], "match": "contains", "sig": "indianapolis:",
     "effect": "ineligible",
     "conditions": {"location": {"kind": "city", "any": ["indianapolis"]}, "occupancy": INVEST}},
    # 7 — MD Baltimore City, all occupancies
    {"states": ["MD"], "match": "contains", "sig": "baltimore city: all occupancies",
     "effect": "ineligible",
     "conditions": {"location": {"kind": "city", "any": ["baltimore"]}}},
    # 8 — NJ Paterson, investment (data spelling "Patterson")
    {"states": ["NJ"], "match": "contains", "sig": "patterson:",
     "effect": "ineligible",
     "conditions": {"location": {"kind": "city", "any": ["paterson", "patterson"]}, "occupancy": INVEST}},
    # 9 — PA suspended ZIPs, all occupancies
    {"states": ["PA"], "match": "contains", "sig": "zip codes 19121",
     "effect": "ineligible",
     "conditions": {"location": {"kind": "zip", "any": ["19121", "19132"]}}},
    # 10 — PA DSCR row homes in Philadelphia
    {"states": ["PA"], "match": "contains", "sig": "dscr row homes in philadelphia",
     "effect": "ineligible",
     "conditions": {"location": {"kind": "city", "any": ["philadelphia"]},
                    "product": ["dscr"], "property_type": ["townhouse"]}},
    # 11 — TX Lubbock, investment
    {"states": ["TX"], "match": "contains", "sig": "lubbock:",
     "effect": "ineligible",
     "conditions": {"location": {"kind": "city", "any": ["lubbock"]}, "occupancy": INVEST}},
    # 12 — "Investor occupancy in Baltimore City, MD, and Philadelphia County, PA"
    #      per-state sub-location; the IN copy can never match → dormant note.
    {"states": ["MD"], "match": "contains", "sig": "investor occupancy in baltimore city",
     "effect": "ineligible",
     "conditions": {"location": {"kind": "city", "any": ["baltimore"]}, "occupancy": INVEST}},
    {"states": ["PA"], "match": "contains", "sig": "investor occupancy in baltimore city",
     "effect": "ineligible",
     "conditions": {"location": {"kind": "county", "any": ["philadelphia"]}, "occupancy": INVEST}},
    {"states": ["IN"], "match": "contains", "sig": "investor occupancy in baltimore city",
     "effect": "note",
     "conditions": {"unevaluable": ["data:mistagged_to_IN"]}},
    # 13/14 — Territories (rows carry state 'ZZ'); engine also fetches ZZ rows
    {"states": ["ZZ"], "match": "contains", "sig": "puerto rico, guam",
     "effect": "ineligible",
     "conditions": {"location": {"kind": "territory", "any": ["PR", "GU", "VI"]}}},
    # 15 — NY sub-prime definition (loan-characteristic unevaluable → note; scoped to NY)
    {"states": ["ZZ"], "match": "contains", "sig": "new york sub-prime definition",
     "effect": "ineligible",
     "conditions": {"location": {"kind": "state", "any": ["NY"]},
                    "unevaluable": ["loan_characteristic:ny_subprime"]}},
    # 16 — TX state-wide ineligible
    {"states": ["TX"], "match": "contains", "sig": "ineligible states: tx",
     "effect": "ineligible",
     "conditions": {"location": {"kind": "state", "any": ["TX"]}}},
    # 17 — IL/NY 2-4 unit not eligible
    {"states": ["IL", "NY"], "match": "contains", "sig": "2-4 unit not eligible",
     "effect": "ineligible",
     "conditions": {"location": {"kind": "state", "any": ["IL", "NY"]},
                    "property_type": ["two_to_four_family"]}},
    # 18 — bare "Baltimore City, MD" label row (exact match avoids #7/#12 collision)
    {"states": ["MD"], "match": "exact", "sig": "baltimore city, md",
     "effect": "ineligible",
     "conditions": {"location": {"kind": "city", "any": ["baltimore"]}}},
    # 19 — bare "Philadelphia County, PA" label row (exact match)
    {"states": ["PA"], "match": "exact", "sig": "philadelphia county, pa",
     "effect": "ineligible",
     "conditions": {"location": {"kind": "county", "any": ["philadelphia"]}}},
    # 20 — second liens in wholesale/retail (lien + channel → note)
    {"states": ["NJ", "NY", "TX"], "match": "contains", "sig": "not eligible for 2nd liens",
     "effect": "note",
     "conditions": {"lien": ["second_lien"], "unevaluable": ["channel:wholesale", "channel:retail"]}},
    # 21 — HI delegated correspondents only (channel → note)
    {"states": ["HI"], "match": "contains", "sig": "delegated correspondents only",
     "effect": "note",
     "conditions": {"unevaluable": ["channel:non_delegated", "channel:wholesale"]}},
    # 22 — NY NQMF wholesale TRID halt (channel/trid → note)
    {"states": ["NY"], "match": "contains", "sig": "nqmf has temporarily halted",
     "effect": "note",
     "conditions": {"unevaluable": ["channel:wholesale", "loan_program:trid"]}},
    # 23 — TN Memphis appraisal transfers (channel → note)
    {"states": ["TN"], "match": "contains", "sig": "appraisal transfers not permitted",
     "effect": "note",
     "conditions": {"location": {"kind": "city", "any": ["memphis"]},
                    "unevaluable": ["channel:wholesale"]}},
    # 24 — State overlay: 75/70 purchase/refi, loan ≤ $2.0MM
    {"states": ["CT", "FL", "IL", "NJ", "NY"], "match": "contains",
     "sig": "75% for purchase and 70% for refinance",
     "effect": "cap",
     "effect_value": {"ltv": {"purchase": 75, "refinance": 70},
                      "cltv": {"purchase": 75, "refinance": 70}, "loan_amount": 2000000},
     "conditions": {"location": {"kind": "state", "any": ["CT", "FL", "IL", "NJ", "NY"]}}},
    # 25 — State overlay: 85/80 purchase/refi, loan ≤ $2.0MM
    {"states": ["CT", "FL", "IL", "NJ", "NY"], "match": "contains",
     "sig": "85% for purchase and 80% for refinance",
     "effect": "cap",
     "effect_value": {"ltv": {"purchase": 85, "refinance": 80},
                      "cltv": {"purchase": 85, "refinance": 80}, "loan_amount": 2000000},
     "conditions": {"location": {"kind": "state", "any": ["CT", "FL", "IL", "NJ", "NY"]}}},
    # 26 — State overlay: 70% refinances only, loan ≤ $2.0MM
    {"states": ["CT", "FL", "IL", "NJ", "NY"], "match": "contains",
     "sig": "70% for refinances only",
     "effect": "cap",
     "effect_value": {"ltv": {"refinance": 70}, "cltv": {"refinance": 70}, "loan_amount": 2000000},
     "conditions": {"location": {"kind": "state", "any": ["CT", "FL", "IL", "NJ", "NY"]}}},
    # 27 — State overlay: HCLTV 80%, min FICO 720
    {"states": ["CT", "FL", "IL", "NJ", "NY"], "match": "contains",
     "sig": "max hcltv 80%, min credit score 720",
     "effect": "cap",
     "effect_value": {"hcltv": 80, "min_fico": 720},
     "conditions": {"location": {"kind": "state", "any": ["CT", "FL", "IL", "NJ", "NY"]}}},
    # 28 — TN max total loan term 15 years
    {"states": ["TN"], "match": "contains", "sig": "maximum total loan term is 15 years",
     "effect": "cap",
     "effect_value": {"max_term_years": 15},
     "conditions": {"location": {"kind": "state", "any": ["TN"]}}},
]


def _load_env() -> dict:
    env = {}
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(root, ".env")
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k] = v.strip().strip('"').strip("'")
    for k in ("MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"):
        env.setdefault(k, os.environ.get(k, ""))
    return env


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="commit changes (default: dry run)")
    args = ap.parse_args()

    try:
        import pymysql
    except Exception:
        print("pymysql required: pip install pymysql", file=sys.stderr)
        return 2

    env = _load_env()
    conn = pymysql.connect(
        host=env["MYSQL_HOST"], port=int(env.get("MYSQL_PORT") or 3306),
        user=env["MYSQL_USER"], password=env["MYSQL_PASSWORD"],
        database=env["MYSQL_DATABASE"], connect_timeout=10, read_timeout=30,
        autocommit=False,
    )
    cur = conn.cursor()
    total = 0
    for rule in RULES:
        states = rule["states"]
        ev = json.dumps(rule.get("effect_value")) if rule.get("effect_value") is not None else None
        cond = json.dumps(rule.get("conditions") or {})
        st_ph = ",".join(["%s"] * len(states))
        if rule["match"] == "exact":
            where = f"LOWER(TRIM(restriction_detail)) = %s AND state IN ({st_ph})"
            params = [rule["sig"]] + states
        else:
            where = f"LOWER(restriction_detail) LIKE %s AND state IN ({st_ph})"
            params = [f"%{rule['sig']}%"] + states
        cur.execute(f"SELECT COUNT(*) FROM map_geographic_restrictions WHERE {where}", params)
        n = cur.fetchone()[0]
        total += n
        print(f"[{rule['effect']:11}] {n:3} rows  ⟵ {rule['sig'][:48]}  ({','.join(states)})")
        if args.apply and n:
            cur.execute(
                f"UPDATE map_geographic_restrictions "
                f"SET effect=%s, effect_value=CAST(%s AS JSON), conditions=CAST(%s AS JSON) "
                f"WHERE {where}",
                [rule["effect"], ev, cond] + params,
            )

    # eligible_state rows keep their own effect.
    cur.execute("SELECT COUNT(*) FROM map_geographic_restrictions WHERE restriction_type='eligible_state'")
    n_elig = cur.fetchone()[0]
    print(f"\neligible_state rows: {n_elig} (effect set to 'eligible_state')")
    if args.apply:
        cur.execute("UPDATE map_geographic_restrictions SET effect='eligible_state' "
                    "WHERE restriction_type='eligible_state'")

    # Report any rows the backfill did not classify (engine falls back to legacy matcher).
    cur.execute("SELECT COUNT(*) FROM map_geographic_restrictions "
                "WHERE effect IS NULL AND restriction_type <> 'eligible_state'")
    uncovered = cur.fetchone()[0]
    print(f"matched non-eligible rows: {total} · uncovered (effect NULL → legacy fallback): {uncovered}")

    if args.apply:
        conn.commit()
        print("\nCOMMITTED.")
    else:
        conn.rollback()
        print("\nDRY RUN — re-run with --apply to commit.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
