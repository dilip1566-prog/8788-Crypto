"""Build the crypto portfolio dashboard.

    python refresh.py                      # read the sheet, fetch, render, open
    python refresh.py --sheet <link>       # point at a Google Sheet (remembered)
    python refresh.py --gid 1234567        # a specific tab of that sheet
    python refresh.py --portfolio my.csv   # ignore the sheet, use a local CSV
    python refresh.py --only BTC,ETH       # limit the build to these coins
    python refresh.py --no-news            # skip the headlines (much faster)
    python refresh.py --no-charts          # skip the price history
    python refresh.py --no-open            # write the file, do not open a browser

Reads your purchases, fetches the market, does the arithmetic and writes
`dashboard.html`, `data.json` and an offline copy of the sheet.
"""

import argparse
import datetime as dt
import json
import os
import sys
import webbrowser
from concurrent.futures import ThreadPoolExecutor

import calc
import news
import portfolio_io
from markets import Markets, MarketError, normalise

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "template.html")
PAGE = os.path.join(HERE, "dashboard.html")
ARTIFACT = os.path.join(HERE, "dashboard_artifact.html")
DATA = os.path.join(HERE, "data.json")
PORTFOLIO_CSV = os.path.join(HERE, "portfolio.csv")
EXAMPLE_CSV = os.path.join(HERE, "portfolio.example.csv")
SOURCE = os.path.join(HERE, "source.json")
OVERRIDES = os.path.join(HERE, "coins.json")
CACHE = os.path.join(HERE, "cache")


# ----------------------------------------------------------------- small bits
def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, payload):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)


def remember_sheet(link, gid):
    write_json(SOURCE, {"sheet": link, "gid": gid})


def now_stamp():
    local = dt.datetime.now().astimezone()
    return local.isoformat(timespec="seconds"), local.strftime("%d %b %Y, %H:%M %Z")


# --------------------------------------------------------------------- stages
def load_portfolio(api, args):
    """The sheet if we can reach it, the offline copy if we cannot."""
    remembered = read_json(SOURCE, {})
    sheet = args.sheet or remembered.get("sheet")
    gid = args.gid if args.gid is not None else remembered.get("gid")

    if args.portfolio:
        rows, problems = portfolio_io.load(api.text, path=args.portfolio)
        return rows, problems, {"kind": "csv", "label": os.path.basename(args.portfolio)}

    if sheet:
        try:
            rows, problems = portfolio_io.load(api.text, sheet=sheet, gid=gid)
            if args.sheet:
                remember_sheet(args.sheet, gid)
            portfolio_io.write_csv(rows, PORTFOLIO_CSV)
            return rows, problems, {"kind": "sheet", "label": "Google Sheet",
                                    "link": sheet}
        except (portfolio_io.PortfolioError, MarketError) as exc:
            print("  ! the Google Sheet could not be read: %s" % exc)
            if os.path.exists(PORTFOLIO_CSV):
                print("    falling back to the offline copy in portfolio.csv")
                rows, problems = portfolio_io.load(api.text, path=PORTFOLIO_CSV)
                problems = list(problems) + [
                    {"asset": "", "reason": "the Google Sheet could not be read, so "
                                            "these are the holdings from the last "
                                            "successful run"}]
                return rows, problems, {"kind": "csv-fallback",
                                        "label": "portfolio.csv (sheet unreachable)"}
            raise

    if os.path.exists(PORTFOLIO_CSV):
        rows, problems = portfolio_io.load(api.text, path=PORTFOLIO_CSV)
        return rows, problems, {"kind": "csv", "label": "portfolio.csv"}

    # Nothing set up yet - a fresh clone. Build from the sample so the
    # dashboard works immediately, and say plainly how to use your own data.
    if os.path.exists(EXAMPLE_CSV):
        print()
        print("  No Google Sheet is configured and there is no portfolio.csv,")
        print("  so this build uses the sample holdings in portfolio.example.csv.")
        print()
        print("  To use your own, either:")
        print("    python refresh.py --sheet \"<your Google Sheet link>\"")
        print("  or copy portfolio.example.csv to portfolio.csv and edit it.")
        print()
        rows, problems = portfolio_io.load(api.text, path=EXAMPLE_CSV)
        return rows, problems, {"kind": "example",
                                "label": "portfolio.example.csv (sample data)"}

    raise portfolio_io.PortfolioError(
        "no holdings found. Run:  python refresh.py --sheet \"<your sheet link>\"  "
        "or copy portfolio.example.csv to portfolio.csv")


def resolve_all(api, rows, problems):
    """Attach a CoinGecko id to every purchase, grouping by coin as we go."""
    overrides = read_json(OVERRIDES, {})
    overrides = {k.upper(): v for k, v in overrides.items()} if overrides else {}

    resolved, unknown = [], []
    memo = {}
    for row in rows:
        key = (row.get("symbol") or "", (row.get("name") or "").lower())
        if key not in memo:
            memo[key] = api.resolve(row.get("symbol"), row.get("name"), overrides)
        coin_id, how = memo[key]
        if not coin_id:
            label = row.get("symbol") or row.get("name") or row.get("asset_text")
            unknown.append(label)
            problems.append({
                "asset": label,
                "reason": "no coin on CoinGecko matches this. Check the spelling, or "
                          "add a line to coins.json mapping it to a CoinGecko id."})
            continue
        row = dict(row)
        row["coin_id"] = coin_id
        row["resolved_by"] = how
        if not row.get("symbol"):
            row["symbol"] = (row.get("name") or coin_id).upper()[:10]
        resolved.append(row)
    return resolved, unknown


def gather_market(api, rows, want_charts=True):
    """Prices, percentage windows, all-time highs and the 1-year series."""
    coin_ids = []
    for row in rows:
        if row["coin_id"] not in coin_ids:
            coin_ids.append(row["coin_id"])

    raw = api.market_data(coin_ids)
    market = {}
    for coin_id in coin_ids:
        if coin_id in raw:
            market[coin_id] = normalise(raw[coin_id], coin_id)
        else:
            market[coin_id] = {"coin_id": coin_id, "price": None, "source": None}

    # Exchange spot prices, in parallel - these are quick and unthrottled.
    symbols = {}
    for row in rows:
        symbols.setdefault(row["coin_id"], row["symbol"])

    def spot(coin_id):
        entry = market[coin_id]
        price, source = api.spot_price(symbols.get(coin_id),
                                       reference=entry.get("reference_price"))
        return coin_id, price, source

    with ThreadPoolExecutor(max_workers=6) as pool:
        for coin_id, price, source in pool.map(spot, coin_ids):
            entry = market[coin_id]
            if price is not None:
                entry["price"] = price
                entry["source"] = source
            elif entry.get("price") is not None:
                entry["source"] = "CoinGecko"

    if want_charts:
        # One CoinGecko call per coin, spaced out to stay inside the free tier.
        # Cached for six hours, so this is only slow on the first run of a day.
        wanted = [c for c in coin_ids if market[c].get("price") is not None]
        for index, coin_id in enumerate(wanted, start=1):
            series = api.chart(coin_id, 365)
            market[coin_id]["series"] = series
            print("    history %d/%d  %-16s %s"
                  % (index, len(wanted), coin_id,
                     "%d days" % len(series) if series else "unavailable"),
                  flush=True)

    stamp, _ = now_stamp()
    for entry in market.values():
        if entry.get("price") is not None:
            entry["as_of"] = stamp
    return market


def gather_news(api, rows, market, limit=3):
    """Shared newsroom feeds plus one targeted search per coin."""
    shared = news.newsroom_items(api)
    print("    %d headlines from the crypto newsrooms" % len(shared))

    targets = {}
    for row in rows:
        entry = market.get(row["coin_id"]) or {}
        targets.setdefault(row["coin_id"],
                           (row["symbol"], entry.get("name") or row.get("name")
                            or row["symbol"]))

    def per_coin(item):
        coin_id, (symbol, name) = item
        found = news.google_news(api, "%s crypto" % (name or symbol))
        ranked = news.rank(shared + found, symbol, name, limit=limit)
        return coin_id, ranked

    out = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        for coin_id, ranked in pool.map(per_coin, list(targets.items())):
            out[coin_id] = ranked
    return out


def build(rows, market, headlines):
    """Fold everything into the rows the dashboard draws."""
    positions = []
    for symbol, lots in calc.group_lots(rows):
        coin_id = lots[0]["coin_id"]
        entry = dict(market.get(coin_id) or {})
        entry["news"] = headlines.get(coin_id, [])
        if not entry.get("name"):
            entry["name"] = lots[0].get("name") or symbol
        position = calc.build_position(symbol, lots, entry)
        position["resolved_by"] = lots[0].get("resolved_by")
        position["rank"] = entry.get("rank")
        positions.append(position)

    positions.sort(key=lambda p: (p["current_value"] is None,
                                  -(p["current_value"] or p["invested"] or 0)))
    return positions


# ----------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Build the crypto portfolio dashboard.")
    ap.add_argument("--sheet", help="Google Sheets link or file id (remembered)")
    ap.add_argument("--gid", help="a specific tab of that sheet")
    ap.add_argument("--portfolio", help="use a local CSV instead of the sheet")
    ap.add_argument("--only", help="comma separated symbols to limit the build to")
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--no-charts", action="store_true")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    api = Markets(cache_dir=CACHE)

    print("Crypto Ledger")
    print("  reading your purchases ...")
    try:
        rows, problems, source = load_portfolio(api, args)
    except (portfolio_io.PortfolioError, MarketError) as exc:
        print("\n  Could not read your portfolio: %s" % exc)
        return 1

    if args.only:
        wanted = {s.strip().upper() for s in args.only.split(",") if s.strip()}
        rows = [r for r in rows
                if (r.get("symbol") or "").upper() in wanted
                or (r.get("name") or "").upper() in wanted]

    print("    %d purchase%s from %s"
          % (len(rows), "" if len(rows) == 1 else "s", source["label"]))
    if not rows:
        print("\n  Nothing to show. Add a purchase to your sheet and run again.")
        return 1

    print("  matching each asset to a coin ...")
    rows, unknown = resolve_all(api, rows, problems)
    if unknown:
        print("    ! not recognised: %s" % ", ".join(sorted(set(unknown))))
    if not rows:
        print("\n  None of the assets could be matched to a coin.")
        return 1

    print("  fetching prices, highs and history ...")
    try:
        market = gather_market(api, rows, want_charts=not args.no_charts)
    except MarketError as exc:
        print("  ! market data unavailable: %s" % exc)
        market = {row["coin_id"]: {"coin_id": row["coin_id"], "price": None}
                  for row in rows}

    headlines = {}
    if not args.no_news:
        print("  ranking the headlines ...")
        try:
            headlines = gather_news(api, rows, market)
        except Exception as exc:                          # news is never fatal
            api.notes.append("headlines could not be fetched (%s)" % exc)
            print("  ! headlines unavailable: %s" % exc)

    positions = build(rows, market, headlines)
    totals = calc.portfolio_totals(positions)
    fx = api.usd_to_gbp()

    previous = read_json(DATA, {})
    priced = [p for p in positions if p["price"] is not None]
    stamp, display = now_stamp()
    last_good = stamp if priced else previous.get("last_good")

    sources = sorted({p["price_source"] for p in priced if p["price_source"]})
    flagged = [{"asset": p["symbol"], "reason": p["warning"]}
               for p in positions if p.get("warning")]

    payload = {
        "generated": stamp,
        "generated_display": display,
        "last_good": last_good,
        "last_good_display": (display if priced
                              else previous.get("last_good_display")),
        "source": source,
        "price_sources": sources,
        "fx_gbp": fx,
        "positions": positions,
        "totals": totals,
        "problems": problems,
        "flagged": flagged,
        "notes": api.notes,
        "counts": {"purchases": len(rows), "assets": len(positions),
                   "priced": len(priced)},
    }
    write_json(DATA, payload)

    if not os.path.exists(TEMPLATE):
        print("\n  template.html is missing - cannot render the page.")
        return 1
    with open(TEMPLATE, encoding="utf-8") as fh:
        template = fh.read()
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    page = template.replace('"__PORTFOLIO_DATA__"', blob)
    with open(PAGE, "w", encoding="utf-8") as fh:
        fh.write(page)
    with open(ARTIFACT, "w", encoding="utf-8") as fh:
        fh.write(page)

    print("\n  %-6s %-11s %13s %13s %12s %10s"
          % ("ASSET", "PRICE", "INVESTED", "VALUE", "P/L", "P/L %"))
    for p in positions:
        if p["price"] is None:
            print("  %-6s %-11s %13s %13s %12s %10s"
                  % (p["symbol"], "no price", format(p["invested"], ",.2f"),
                     "-", "-", "-"))
            continue
        print("  %-6s %-11s %13s %13s %12s %9.2f%%"
              % (p["symbol"], format(p["price"], ",.2f"),
                 format(p["invested"], ",.2f"), format(p["current_value"], ",.2f"),
                 format(p["pl"], ",.2f"), p["pl_pct"]))

    if totals["pl"] is not None:
        print("\n  Invested %s   Value %s   P/L %s (%+.2f%%)"
              % (format(totals["invested"], ",.2f"), format(totals["value"], ",.2f"),
                 format(totals["pl"], ",.2f"), totals["pl_pct"]))
    for note in api.notes:
        print("  note: %s" % note)
    for problem in problems:
        print("  ! %s %s" % (problem.get("asset") or "", problem["reason"]))
    for flag in flagged:
        print("  ? %s %s" % (flag["asset"], flag["reason"]))
    print("\n  written to dashboard.html (%s)" % display)

    if not args.no_open:
        webbrowser.open("file://" + PAGE.replace("\\", "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
