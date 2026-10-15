"""Portfolio arithmetic for the crypto dashboard.

Kept apart from the fetching code so every number can be tested without
touching the network. Nothing here knows what an API is.

A *lot* is one line from your sheet - one purchase. A *position* is every lot
for the same coin added together, which is where the weighted average purchase
price comes from.
"""


def weighted_average_price(lots):
    """The price you effectively paid across several purchases.

    Weighted by units, not a plain average of the prices: buying 0.5 BTC at
    92,000 and 0.25 BTC at 105,000 averages 96,333.33, not 98,500.
    """
    units = sum(lot["units"] for lot in lots)
    if units <= 0:
        return None
    return sum(lot["purchase_price"] * lot["units"] for lot in lots) / units


def invested_cost(lots):
    """What left your pocket: purchase price x units, over every purchase."""
    return sum(lot["purchase_price"] * lot["units"] for lot in lots)


def pct_change(new, old):
    """Percentage move from old to new, or None if it cannot be worked out."""
    if new is None or old is None or old == 0:
        return None
    return (new - old) / old * 100.0


def rebase(price, reference_price, reference_pct):
    """Restate a percentage change against a different current price.

    CoinGecko reports "+2.4% over 24h" relative to *its* price. If we are
    showing Coinbase's price we would be quoting a move the numbers do not
    support, so we recover the historical baseline and measure our own price
    against it instead.
    """
    if reference_pct is None or reference_price is None or price is None:
        return reference_pct
    baseline = reference_price / (1.0 + reference_pct / 100.0)
    if baseline <= 0:
        return reference_pct
    return pct_change(price, baseline)


# A purchase price this far from the market is almost always a typo, a wrong
# column or a total cost entered as a unit price - not a spectacular trade. We
# say so and leave the number alone, because a missing digit and a ten-bagger
# look identical from here.
TYPO_ABOVE = 5.0     # paid five times today's price or more
TYPO_BELOW = 0.15    # paid less than 15% of today's price


def implausible(purchase_price, price):
    """Explain why a purchase price looks wrong, or None if it looks fine."""
    if not price or not purchase_price or price <= 0 or purchase_price <= 0:
        return None
    ratio = purchase_price / price
    if ratio >= TYPO_ABOVE:
        return ("the purchase price is %s times the current price - check the "
                "sheet has a price per unit, not a total cost"
                % ("%.0f" % ratio if ratio >= 10 else "%.1f" % ratio))
    if ratio <= TYPO_BELOW:
        return ("the purchase price is only %.1f%% of the current price - check "
                "the sheet has a price per unit, not a quantity"
                % (ratio * 100))
    return None


def build_position(symbol, lots, market=None):
    """Fold one coin's purchases and its market data into a dashboard row."""
    market = market or {}
    units = sum(lot["units"] for lot in lots)
    invested = invested_cost(lots)
    avg_price = weighted_average_price(lots)
    price = market.get("price")

    current_value = price * units if price is not None else None
    profit = current_value - invested if current_value is not None else None
    profit_pct = pct_change(current_value, invested) if current_value is not None else None

    reference = market.get("reference_price")
    moves = {}
    for window in ("24h", "7d", "30d", "1y"):
        moves[window] = rebase(price, reference, market.get("pct_" + window))

    ath = market.get("ath")
    from_ath = pct_change(price, ath) if (ath and price is not None) else None

    refs = []
    for lot in lots:
        ref = str(lot.get("ref") or "").strip()
        if ref and ref not in refs:
            refs.append(ref)

    return {
        "warning": implausible(avg_price, price),
        "symbol": symbol,
        "name": market.get("name") or symbol,
        "coin_id": market.get("coin_id"),
        "image": market.get("image"),
        "ref": ", ".join(refs),
        "refs": refs,
        "lots": sorted(lots, key=lambda l: l.get("purchase_date") or ""),
        "lot_count": len(lots),
        "units": units,
        "invested": invested,
        "purchase_price": avg_price,
        "price": price,
        "price_source": market.get("source"),
        "priced_at": market.get("as_of"),
        "stale": price is None,
        "current_value": current_value,
        "pl": profit,
        "pl_pct": profit_pct,
        "pct_24h": moves["24h"],
        "pct_7d": moves["7d"],
        "pct_30d": moves["30d"],
        "pct_1y": moves["1y"],
        "ath": ath,
        "ath_date": market.get("ath_date"),
        "from_ath_pct": from_ath,
        "market_cap": market.get("market_cap"),
        "series": market.get("series") or [],
        "news": market.get("news") or [],
        "exchanges": sorted({(l.get("exchange") or "").strip()
                             for l in lots if (l.get("exchange") or "").strip()}),
        "notes": [l["notes"] for l in lots if (l.get("notes") or "").strip()],
    }


def group_lots(rows):
    """Bucket sheet rows by coin, preserving the order they were first seen."""
    grouped, order = {}, []
    for row in rows:
        key = row["symbol"]
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(row)
    return [(key, grouped[key]) for key in order]


def portfolio_totals(positions):
    """The headline numbers across everything you hold.

    Only priced positions can contribute to the current value, so a coin we
    could not price is counted in the total invested and reported separately
    rather than being silently valued at cost - that would invent a 0% move.
    """
    invested = sum(p["invested"] for p in positions)
    priced = [p for p in positions if p["current_value"] is not None]
    unpriced = [p for p in positions if p["current_value"] is None]

    value = sum(p["current_value"] for p in priced)
    invested_priced = sum(p["invested"] for p in priced)
    profit = value - invested_priced if priced else None
    profit_pct = pct_change(value, invested_priced) if priced else None

    ranked = sorted([p for p in priced if p["pl_pct"] is not None],
                    key=lambda p: p["pl_pct"])

    day_before = sum(p["current_value"] / (1.0 + p["pct_24h"] / 100.0)
                     for p in priced
                     if p["pct_24h"] is not None and p["pct_24h"] != -100)
    covered = sum(p["current_value"] for p in priced if p["pct_24h"] is not None)
    day_pct = pct_change(covered, day_before) if day_before else None

    allocation = sorted(
        [{"symbol": p["symbol"], "name": p["name"], "value": p["current_value"],
          "share": p["current_value"] / value * 100.0 if value else 0.0}
         for p in priced],
        key=lambda a: a["value"], reverse=True)

    return {
        "invested": invested,
        "invested_priced": invested_priced,
        "value": value,
        "pl": profit,
        "pl_pct": profit_pct,
        "day_pct": day_pct,
        "assets": len(positions),
        "lots": sum(p["lot_count"] for p in positions),
        "unpriced": [p["symbol"] for p in unpriced],
        "best": ranked[-1]["symbol"] if ranked else None,
        "best_pct": ranked[-1]["pl_pct"] if ranked else None,
        "worst": ranked[0]["symbol"] if ranked else None,
        "worst_pct": ranked[0]["pl_pct"] if ranked else None,
        "allocation": allocation,
    }
