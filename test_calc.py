"""Check every calculation the dashboard makes, using worked BTC and ETH trades.

Run it with:  python test_calc.py

Bitcoin is bought twice at different prices, so it also proves the weighted
average. Every expected number below is worked out by hand in the comments.
"""

import unittest

import calc

# Three purchases. Bitcoin twice, Ethereum once.
LOTS = [
    {"ref": "C001", "symbol": "BTC", "asset": "Bitcoin", "purchase_price": 92000.0,
     "units": 0.5, "purchase_date": "2025-01-15", "exchange": "Coinbase", "notes": ""},
    {"ref": "C002", "symbol": "ETH", "asset": "Ethereum", "purchase_price": 2400.0,
     "units": 4.0, "purchase_date": "2025-02-02", "exchange": "Binance", "notes": ""},
    {"ref": "C003", "symbol": "BTC", "asset": "Bitcoin", "purchase_price": 105000.0,
     "units": 0.25, "purchase_date": "2025-06-10", "exchange": "Coinbase", "notes": ""},
]

MARKET = {
    "BTC": {"name": "Bitcoin", "price": 115000.0, "reference_price": 115000.0,
            "pct_24h": 2.4, "pct_7d": 8.1, "pct_30d": 14.5, "pct_1y": 76.0,
            "ath": 126000.0, "ath_date": "2025-10-06", "source": "Coinbase"},
    "ETH": {"name": "Ethereum", "price": 3000.0, "reference_price": 3000.0,
            "pct_24h": -1.2, "pct_7d": 3.4, "pct_30d": -5.6, "pct_1y": 21.0,
            "ath": 4878.26, "ath_date": "2021-11-10", "source": "Coinbase"},
}


def positions(market=None):
    market = MARKET if market is None else market
    return [calc.build_position(sym, lots, market.get(sym))
            for sym, lots in calc.group_lots(LOTS)]


class WeightedAverage(unittest.TestCase):
    def test_two_bitcoin_purchases(self):
        # (92,000 x 0.5) + (105,000 x 0.25) = 46,000 + 26,250 = 72,250
        # 72,250 / 0.75 units = 96,333.33 -- NOT the plain average of 98,500.
        btc = [l for l in LOTS if l["symbol"] == "BTC"]
        self.assertAlmostEqual(calc.weighted_average_price(btc), 96333.333333, places=6)
        self.assertNotAlmostEqual(calc.weighted_average_price(btc), 98500.0, places=2)

    def test_single_purchase_is_just_the_price(self):
        eth = [l for l in LOTS if l["symbol"] == "ETH"]
        self.assertAlmostEqual(calc.weighted_average_price(eth), 2400.0)

    def test_invested_cost(self):
        # 46,000 + 9,600 + 26,250
        self.assertAlmostEqual(calc.invested_cost(LOTS), 81850.0)


class PositionMaths(unittest.TestCase):
    def setUp(self):
        self.pos = {p["symbol"]: p for p in positions()}

    def test_bitcoin(self):
        btc = self.pos["BTC"]
        self.assertAlmostEqual(btc["units"], 0.75)
        self.assertAlmostEqual(btc["invested"], 72250.0)
        self.assertAlmostEqual(btc["purchase_price"], 96333.333333, places=6)
        # 115,000 x 0.75 = 86,250
        self.assertAlmostEqual(btc["current_value"], 86250.0)
        # 86,250 - 72,250 = 14,000
        self.assertAlmostEqual(btc["pl"], 14000.0)
        # 14,000 / 72,250 = 19.3772%
        self.assertAlmostEqual(btc["pl_pct"], 19.377162629, places=6)
        self.assertEqual(btc["lot_count"], 2)
        self.assertEqual(btc["ref"], "C001, C003")

    def test_ethereum(self):
        eth = self.pos["ETH"]
        self.assertAlmostEqual(eth["units"], 4.0)
        self.assertAlmostEqual(eth["invested"], 9600.0)        # 2,400 x 4
        self.assertAlmostEqual(eth["current_value"], 12000.0)  # 3,000 x 4
        self.assertAlmostEqual(eth["pl"], 2400.0)
        self.assertAlmostEqual(eth["pl_pct"], 25.0)

    def test_distance_from_all_time_high(self):
        # 115,000 against 126,000 = -8.73%
        self.assertAlmostEqual(self.pos["BTC"]["from_ath_pct"], -8.730158730, places=6)
        # 3,000 against 4,878.26 = -38.50%
        self.assertAlmostEqual(self.pos["ETH"]["from_ath_pct"], -38.502663, places=5)

    def test_percentage_windows_pass_through_when_source_agrees(self):
        btc = self.pos["BTC"]
        self.assertAlmostEqual(btc["pct_24h"], 2.4, places=9)
        self.assertAlmostEqual(btc["pct_7d"], 8.1, places=9)
        self.assertAlmostEqual(btc["pct_30d"], 14.5, places=9)
        self.assertAlmostEqual(btc["pct_1y"], 76.0, places=9)


class MixedPriceSources(unittest.TestCase):
    """Coinbase gives the price, CoinGecko gives the history. Keep them honest."""

    def test_percentages_rebase_onto_the_live_price(self):
        # CoinGecko says 114,500 and +2.0% over 24h, so 24h ago it was
        # 114,500 / 1.02 = 112,254.90. Coinbase says we are at 115,000 now,
        # which against that baseline is +2.4454%, not +2.0%.
        moved = calc.rebase(115000.0, 114500.0, 2.0)
        self.assertAlmostEqual(moved, (115000.0 / (114500.0 / 1.02) - 1) * 100, places=9)
        self.assertAlmostEqual(moved, 2.445415, places=5)

    def test_rebase_is_a_no_op_when_prices_match(self):
        self.assertAlmostEqual(calc.rebase(115000.0, 115000.0, 8.1), 8.1, places=9)

    def test_missing_inputs_do_not_invent_a_number(self):
        self.assertIsNone(calc.rebase(115000.0, 114500.0, None))
        self.assertIsNone(calc.pct_change(100.0, 0))
        self.assertIsNone(calc.pct_change(None, 50.0))


class ImplausiblePurchasePrices(unittest.TestCase):
    """A missing digit and a ten-bagger look identical, so we flag, not fix."""

    def test_a_total_cost_entered_as_a_unit_price(self):
        # ALGO has never traded near $660; that is what was spent, not the rate.
        message = calc.implausible(660.0, 0.0898)
        self.assertIsNotNone(message)
        self.assertIn("times the current price", message)

    def test_a_quantity_entered_as_a_unit_price(self):
        message = calc.implausible(10.0, 2459.75)
        self.assertIsNotNone(message)
        self.assertIn("% of the current price", message)

    def test_real_trades_are_left_alone(self):
        self.assertIsNone(calc.implausible(92000.0, 78871.0))   # BTC, down a bit
        self.assertIsNone(calc.implausible(2400.0, 3000.0))     # ETH, up a bit
        self.assertIsNone(calc.implausible(10.0, 11.37))        # LINK, flat
        self.assertIsNone(calc.implausible(148.5, 96.76))       # SOL, down a third

    def test_a_genuine_four_bagger_is_not_flagged(self):
        # Up 4x is inside the threshold: unusual, but perfectly possible.
        self.assertIsNone(calc.implausible(25.0, 100.0))

    def test_nothing_to_compare_against(self):
        self.assertIsNone(calc.implausible(100.0, None))
        self.assertIsNone(calc.implausible(None, 100.0))
        self.assertIsNone(calc.implausible(100.0, 0))

    def test_the_flag_rides_along_with_the_position(self):
        flagged = calc.build_position(
            "ALGO", [{"ref": "BINANCE", "purchase_price": 660.0, "units": 5.0}],
            {"price": 0.0898, "reference_price": 0.0898})
        self.assertIsNotNone(flagged["warning"])
        healthy = {p["symbol"]: p for p in positions()}
        self.assertIsNone(healthy["BTC"]["warning"])


class Totals(unittest.TestCase):
    def setUp(self):
        self.totals = calc.portfolio_totals(positions())

    def test_headline_numbers(self):
        self.assertAlmostEqual(self.totals["invested"], 81850.0)   # 72,250 + 9,600
        self.assertAlmostEqual(self.totals["value"], 98250.0)      # 86,250 + 12,000
        self.assertAlmostEqual(self.totals["pl"], 16400.0)
        # 16,400 / 81,850 = 20.0367%
        self.assertAlmostEqual(self.totals["pl_pct"], 20.036652413, places=6)
        self.assertEqual(self.totals["assets"], 2)
        self.assertEqual(self.totals["lots"], 3)

    def test_best_and_worst_performer(self):
        # ETH +25.00% beats BTC +19.38%, even though BTC made more cash.
        self.assertEqual(self.totals["best"], "ETH")
        self.assertEqual(self.totals["worst"], "BTC")
        self.assertAlmostEqual(self.totals["best_pct"], 25.0)

    def test_portfolio_day_move_is_weighted_by_value(self):
        # BTC 86,250 at +2.4% and ETH 12,000 at -1.2%.
        before = 86250.0 / 1.024 + 12000.0 / 0.988
        self.assertAlmostEqual(self.totals["day_pct"],
                               (98250.0 - before) / before * 100, places=9)
        self.assertAlmostEqual(self.totals["day_pct"], 1.946303, places=5)

    def test_allocation_shares_sum_to_100(self):
        shares = [a["share"] for a in self.totals["allocation"]]
        self.assertAlmostEqual(sum(shares), 100.0, places=9)
        self.assertEqual(self.totals["allocation"][0]["symbol"], "BTC")
        self.assertAlmostEqual(shares[0], 86250.0 / 98250.0 * 100, places=9)


class WhenTheMarketIsUnreachable(unittest.TestCase):
    """No price must ever mean an invented price."""

    def setUp(self):
        self.pos = positions({"BTC": MARKET["BTC"]})   # ETH deliberately missing

    def test_unpriced_position_reports_nothing_rather_than_zero(self):
        eth = [p for p in self.pos if p["symbol"] == "ETH"][0]
        self.assertIsNone(eth["price"])
        self.assertIsNone(eth["current_value"])
        self.assertIsNone(eth["pl"])
        self.assertIsNone(eth["pl_pct"])
        self.assertTrue(eth["stale"])

    def test_totals_exclude_it_instead_of_valuing_it_at_cost(self):
        totals = calc.portfolio_totals(self.pos)
        # Still your full outlay ...
        self.assertAlmostEqual(totals["invested"], 81850.0)
        # ... but only Bitcoin can be marked to market.
        self.assertAlmostEqual(totals["invested_priced"], 72250.0)
        self.assertAlmostEqual(totals["value"], 86250.0)
        self.assertAlmostEqual(totals["pl"], 14000.0)   # not 86,250 - 81,850
        self.assertEqual(totals["unpriced"], ["ETH"])


def demonstrate():
    """Print the worked example, so the arithmetic can be eyeballed."""
    rows = positions()
    totals = calc.portfolio_totals(rows)
    line = "-" * 92
    print("\n" + line)
    print("WORKED EXAMPLE - sample Bitcoin and Ethereum transactions")
    print(line)
    print("%-12s %-6s %12s %9s %12s %11s %12s %8s" % (
        "REF", "ASSET", "AVG BUY", "UNITS", "INVESTED", "PRICE", "VALUE", "P/L %"))
    for p in rows:
        print("%-12s %-6s %12s %9.4f %12s %11s %12s %7.2f%%" % (
            p["ref"], p["symbol"], format(p["purchase_price"], ",.2f"), p["units"],
            format(p["invested"], ",.2f"), format(p["price"], ",.2f"),
            format(p["current_value"], ",.2f"), p["pl_pct"]))
    print(line)
    print("Total invested       %14s" % format(totals["invested"], ",.2f"))
    print("Portfolio value      %14s" % format(totals["value"], ",.2f"))
    print("Profit / loss        %14s   (%+.2f%%)" % (
        format(totals["pl"], ",.2f"), totals["pl_pct"]))
    print("Assets / purchases   %10d / %d" % (totals["assets"], totals["lots"]))
    print("Best  performer      %14s   (%+.2f%%)" % (totals["best"], totals["best_pct"]))
    print("Worst performer      %14s   (%+.2f%%)" % (totals["worst"], totals["worst_pct"]))
    print(line + "\n")


if __name__ == "__main__":
    demonstrate()
    unittest.main(verbosity=2)
