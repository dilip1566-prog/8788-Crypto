"""Load crypto purchases from a Google Sheet or a local CSV.

The sheet only has to carry three things - which coin, what you paid and how
many units - under any of the usual column names. Everything else on the
dashboard is fetched or calculated.
"""

import csv
import io
import os
import re

# Column names people actually use, mapped to what the dashboard needs.
# Order matters: the first field that claims a header keeps it, so "Symbol"
# lands on `symbol` rather than being swallowed by `asset`.
ALIASES = {
    "ref": ["our ref", "ref", "reference", "our reference", "id", "sr no", "s no",
            "sl no", "serial", "no"],
    "symbol": ["symbol", "ticker", "code", "coin symbol", "asset symbol",
               "ticker symbol", "pair"],
    "asset": ["asset", "coin", "crypto", "cryptocurrency", "currency", "token",
              "asset name", "coin name", "name", "crypto name"],
    "purchase_price": ["purchase price", "buy price", "price paid", "cost",
                       "cost price", "average price", "avg price", "entry price",
                       "purchase", "price per coin", "price per unit", "rate",
                       "price"],
    "units": ["units purchased", "units", "quantity", "qty", "coins", "tokens",
              "amount", "no of units", "number of units", "no of coins", "holding",
              "units bought", "quantity purchased"],
    "purchase_date": ["purchase date", "date", "buy date", "date bought",
                      "entry date", "date purchased", "bought on"],
    "exchange": ["exchange", "platform", "wallet", "broker", "held with",
                 "held on", "venue"],
    "notes": ["notes", "note", "comments", "comment", "remarks", "remark"],
}

SHEET_ID = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]{20,})")
SHEET_GID = re.compile(r"[#&?]gid=(\d+)")

# "Bitcoin (BTC)" - the bracketed part is the symbol.
BRACKETED = re.compile(r"\(\s*([A-Za-z0-9]{2,12})\s*\)")
# A bare symbol: short, no spaces, not obviously a word like "Bitcoin".
BARE_SYMBOL = re.compile(r"^[A-Z0-9]{2,10}$")

SKIP_ROWS = {"total", "totals", "grand total", "sum", "subtotal"}


class PortfolioError(RuntimeError):
    pass


def parse_number(raw):
    """Handle $92,000.00, 0.00042351, 1,000 and the European style 50,00."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    text = re.sub(r"[^\d.,\-]", "", text)
    if not text or text in ("-", ".", ","):
        return None

    has_dot, has_comma = "." in text, "," in text
    if has_dot and has_comma:
        # Whichever separator comes last is the decimal point.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif has_comma:
        # "50,00" is a decimal comma; "1,000" and "1,234,567" are thousands.
        if re.fullmatch(r"-?\d+,\d{1,2}", text):
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def split_asset(text):
    """Pull a symbol and a display name out of whatever was typed.

    "Bitcoin (BTC)" gives both. "BTC" gives a symbol. "Bitcoin" gives a name,
    and the market lookup resolves the symbol from it later.
    """
    text = (text or "").strip()
    if not text:
        return None, None

    match = BRACKETED.search(text)
    if match:
        name = BRACKETED.sub("", text).strip(" -–")
        return match.group(1).upper(), (name or None)

    # Trading pairs, as they come off an exchange: BTC-USD, BTCUSDT, BTC/GBP.
    pair = re.fullmatch(r"([A-Za-z0-9]{2,10})[-/ ](?:USDT?|GBP|EUR|USDC|BUSD)", text, re.I)
    if pair:
        return pair.group(1).upper(), None
    pair = re.fullmatch(r"([A-Z0-9]{3,10})(USDT|USDC|BUSD)", text.upper())
    if pair:
        return pair.group(1), None

    compact = re.sub(r"\s+", "", text)
    if BARE_SYMBOL.fullmatch(compact.upper()) and (
            compact.isupper() or compact.isdigit() or len(compact) <= 5):
        return compact.upper(), None

    return None, text


def _header_map(fieldnames):
    """Match the sheet's column names to the fields we need."""
    found = {}
    for raw in fieldnames or []:
        key = re.sub(r"[^a-z0-9 ]", " ", (raw or "").strip().lower())
        key = " ".join(key.split())
        if not key:
            continue
        for field, names in ALIASES.items():
            if field in found:
                continue
            if key in names:
                found[field] = raw
                break
    return found


def _identifies_a_coin(cols):
    return "asset" in cols or "symbol" in cols


def parse_rows(text, origin="portfolio"):
    """Turn CSV text into purchase rows, reporting anything unusable."""
    text = text.lstrip("﻿")
    lines = [ln for ln in text.splitlines() if ln.strip().strip(",")]
    if not lines:
        raise PortfolioError("%s is empty" % origin)

    # Sheets often carry a title row or blank columns above the real header.
    start = None
    for i, line in enumerate(lines[:15]):
        try:
            probe = next(csv.reader([line]))
        except StopIteration:
            continue
        if _identifies_a_coin(_header_map(probe)):
            start = i
            break
    if start is None:
        raise PortfolioError(
            "%s has no asset column. Name one column 'Asset' (or 'Coin', 'Symbol') "
            "and add 'Purchase Price' and 'Units Purchased' beside it." % origin)

    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    cols = _header_map(reader.fieldnames)
    missing = [label for field, label in
               (("purchase_price", "purchase price"), ("units", "units purchased"))
               if field not in cols]
    if missing:
        raise PortfolioError(
            "%s is missing a column for: %s" % (origin, ", ".join(missing)))

    rows, problems = [], []
    for lineno, raw in enumerate(reader, start=start + 2):
        asset_text = (raw.get(cols["asset"]) or "").strip() if "asset" in cols else ""
        symbol_text = (raw.get(cols["symbol"]) or "").strip() if "symbol" in cols else ""

        if not asset_text and not symbol_text:
            continue
        if (asset_text or symbol_text).lower() in SKIP_ROWS:
            continue
        if (asset_text or symbol_text).startswith("#"):
            continue

        symbol, name = split_asset(symbol_text or asset_text)
        if symbol_text and asset_text:
            # Both columns present: the symbol column wins, the asset column
            # is the readable name.
            symbol = split_asset(symbol_text)[0] or symbol
            name = split_asset(asset_text)[1] or asset_text

        label = symbol or name or symbol_text or asset_text
        price = parse_number(raw.get(cols["purchase_price"]))
        units = parse_number(raw.get(cols["units"]))

        if price is None or units is None:
            problems.append({"asset": label, "reason":
                             "row %d: purchase price or units could not be read"
                             % lineno})
            continue
        if price <= 0 or units <= 0:
            problems.append({"asset": label, "reason":
                             "row %d: purchase price and units must both be above "
                             "zero" % lineno})
            continue

        def col(field):
            return (raw.get(cols[field]) or "").strip() if field in cols else ""

        rows.append({
            "ref": col("ref") or ("C%03d" % (len(rows) + 1)),
            "asset_text": asset_text or symbol_text,
            "symbol": symbol,
            "name": name,
            "purchase_price": price,
            "units": units,
            "purchase_date": col("purchase_date") or None,
            "exchange": col("exchange"),
            "notes": col("notes"),
            "row": lineno,
        })
    if not rows and not problems:
        raise PortfolioError("%s has a header but no purchases under it" % origin)
    return rows, problems


# ------------------------------------------------------------- google sheets
def sheet_csv_url(reference, gid=None):
    """Accept a full Sheets URL or a bare file id and return its CSV export URL."""
    reference = (reference or "").strip()
    match = SHEET_ID.search(reference)
    file_id = match.group(1) if match else reference
    if not re.fullmatch(r"[a-zA-Z0-9_-]{20,}", file_id):
        raise PortfolioError("that does not look like a Google Sheets link or file id")
    if gid is None:
        gid_match = SHEET_GID.search(reference)
        gid = gid_match.group(1) if gid_match else None
    # Without a gid Google exports the first tab, which is what we want by
    # default - guessing "gid=0" fails on sheets whose first tab has another id.
    url = "https://docs.google.com/spreadsheets/d/%s/export?format=csv" % file_id
    return url + ("&gid=%s" % gid if gid else "")


def fetch_sheet(http, reference, gid=None):
    """Download the sheet as CSV. Needs link sharing to be switched on."""
    url = sheet_csv_url(reference, gid)
    body = http(url)
    head = body.lstrip()[:400].lower()
    if head.startswith("<!doctype") or "<html" in head:
        raise PortfolioError(
            "Google would not hand over that sheet. Open it, click Share, and set "
            "'General access' to 'Anyone with the link - Viewer', then run this "
            "again. (Nothing else about the sheet changes, and it stays out of "
            "search results.)")
    return body


def load(http, sheet=None, path=None, gid=None):
    """Prefer the live sheet; fall back to the local CSV."""
    if sheet:
        return parse_rows(fetch_sheet(http, sheet, gid), "the Google Sheet")
    if not path or not os.path.exists(path):
        raise PortfolioError("no portfolio source found - pass --sheet <link> or "
                             "create portfolio.csv")
    with open(path, encoding="utf-8-sig") as fh:
        return parse_rows(fh.read(), os.path.basename(path))


FIELDNAMES = ["our ref", "asset", "symbol", "purchase price", "units purchased",
              "purchase date", "exchange", "notes"]


def write_csv(rows, path):
    """Keep a local copy so the dashboard still builds if the sheet is offline."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(FIELDNAMES)
        for row in rows:
            writer.writerow([
                row.get("ref") or "",
                row.get("name") or row.get("asset_text") or "",
                row.get("symbol") or "",
                row["purchase_price"],
                row["units"],
                row.get("purchase_date") or "",
                row.get("exchange") or "",
                row.get("notes") or "",
            ])
