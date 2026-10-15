"""Check that real-world sheet layouts are read correctly.

Run it with:  python test_portfolio_io.py

The point of these is that your sheet should not have to be written any
particular way - column names, currency symbols and how you spell the coin can
all vary, and the dashboard should still find the three things it needs.
"""

import unittest

import portfolio_io as pio


def parse(text):
    return pio.parse_rows(text, "test sheet")


class Numbers(unittest.TestCase):
    def test_currency_symbols_and_separators(self):
        self.assertEqual(pio.parse_number("$92,000.00"), 92000.0)
        self.assertEqual(pio.parse_number("£1,234.56"), 1234.56)
        self.assertEqual(pio.parse_number("78 994.63"), 78994.63)
        self.assertEqual(pio.parse_number("0.00042351"), 0.00042351)

    def test_european_decimal_comma(self):
        self.assertEqual(pio.parse_number("50,00"), 50.0)
        self.assertEqual(pio.parse_number("1.234,56"), 1234.56)

    def test_thousands_comma(self):
        self.assertEqual(pio.parse_number("1,000"), 1000.0)
        self.assertEqual(pio.parse_number("1,234,567"), 1234567.0)

    def test_nothing_usable(self):
        for value in ("", "   ", "-", "n/a", None):
            self.assertIsNone(pio.parse_number(value))


class AssetNames(unittest.TestCase):
    def test_name_with_bracketed_symbol(self):
        self.assertEqual(pio.split_asset("Bitcoin (BTC)"), ("BTC", "Bitcoin"))

    def test_bare_symbol(self):
        self.assertEqual(pio.split_asset("BTC"), ("BTC", None))

    def test_plain_name(self):
        self.assertEqual(pio.split_asset("Bitcoin"), (None, "Bitcoin"))

    def test_trading_pairs(self):
        self.assertEqual(pio.split_asset("BTC-USD")[0], "BTC")
        self.assertEqual(pio.split_asset("BTCUSDT")[0], "BTC")
        self.assertEqual(pio.split_asset("ETH/GBP")[0], "ETH")


class SheetLayouts(unittest.TestCase):
    def test_the_documented_layout(self):
        rows, problems = parse(
            "our ref,asset,purchase price,units purchased,purchase date\n"
            "C001,Bitcoin (BTC),92000,0.5,2025-01-15\n"
            "C002,Ethereum (ETH),2400,4,2025-02-02\n")
        self.assertEqual(problems, [])
        self.assertEqual([r["symbol"] for r in rows], ["BTC", "ETH"])
        self.assertEqual(rows[0]["purchase_price"], 92000.0)
        self.assertEqual(rows[0]["units"], 0.5)
        self.assertEqual(rows[0]["ref"], "C001")

    def test_alternative_column_names(self):
        rows, _ = parse(
            "Ref,Coin,Buy Price,Qty,Date Bought,Platform\n"
            "1,BTC,$92000,0.5,15/01/2025,Coinbase\n")
        self.assertEqual(rows[0]["symbol"], "BTC")
        self.assertEqual(rows[0]["purchase_price"], 92000.0)
        self.assertEqual(rows[0]["units"], 0.5)
        self.assertEqual(rows[0]["exchange"], "Coinbase")

    def test_separate_symbol_and_name_columns(self):
        rows, _ = parse(
            "our ref,asset,symbol,purchase price,units\n"
            "C001,Bitcoin,BTC,92000,0.5\n")
        self.assertEqual(rows[0]["symbol"], "BTC")
        self.assertEqual(rows[0]["name"], "Bitcoin")

    def test_title_rows_above_the_header_are_skipped(self):
        rows, _ = parse(
            "MY CRYPTO PORTFOLIO,,,\n"
            "Updated August 2026,,,\n"
            ",,,\n"
            "our ref,asset,purchase price,units purchased\n"
            "C001,BTC,92000,0.5\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "BTC")

    def test_total_rows_and_blanks_are_ignored(self):
        rows, problems = parse(
            "our ref,asset,purchase price,units purchased\n"
            "C001,BTC,92000,0.5\n"
            ",,,\n"
            ",TOTAL,,\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(problems, [])

    def test_missing_ref_is_numbered_automatically(self):
        rows, _ = parse("asset,purchase price,units purchased\n"
                        "BTC,92000,0.5\nETH,2400,4\n")
        self.assertEqual([r["ref"] for r in rows], ["C001", "C002"])

    def test_bad_rows_are_reported_not_dropped_silently(self):
        rows, problems = parse(
            "our ref,asset,purchase price,units purchased\n"
            "C001,BTC,92000,0.5\n"
            "C002,ETH,not a number,4\n"
            "C003,SOL,148.5,-3\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(problems), 2)
        self.assertIn("could not be read", problems[0]["reason"])
        self.assertIn("above zero", problems[1]["reason"])

    def test_a_sheet_with_no_asset_column_says_so(self):
        with self.assertRaises(pio.PortfolioError) as caught:
            parse("date,price,quantity\n2025-01-15,92000,0.5\n")
        self.assertIn("no asset column", str(caught.exception))

    def test_a_sheet_missing_price_or_units_says_which(self):
        with self.assertRaises(pio.PortfolioError) as caught:
            parse("our ref,asset,purchase date\nC001,BTC,2025-01-15\n")
        message = str(caught.exception)
        self.assertIn("purchase price", message)
        self.assertIn("units purchased", message)


class SheetLinks(unittest.TestCase):
    LINK = ("https://docs.google.com/spreadsheets/d/"
            "1EXAMPLEsheetId_0123456789abcdefghijKLM/edit#gid=123")

    def test_a_full_link_becomes_a_csv_export(self):
        url = pio.sheet_csv_url(self.LINK)
        self.assertIn("/1EXAMPLEsheetId_0123456789abcdefghijKLM/export?format=csv", url)
        self.assertTrue(url.endswith("&gid=123"))

    def test_a_bare_file_id_works_too(self):
        url = pio.sheet_csv_url("1EXAMPLEsheetId_0123456789abcdefghijKLM")
        self.assertIn("export?format=csv", url)
        self.assertNotIn("gid=", url)

    def test_an_explicit_gid_beats_the_one_in_the_link(self):
        self.assertTrue(pio.sheet_csv_url(self.LINK, gid="999").endswith("&gid=999"))

    def test_nonsense_is_rejected(self):
        with self.assertRaises(pio.PortfolioError):
            pio.sheet_csv_url("not a sheet")

    def test_a_login_page_is_not_mistaken_for_data(self):
        with self.assertRaises(pio.PortfolioError) as caught:
            pio.fetch_sheet(lambda url: "<!DOCTYPE html><html>Sign in</html>", self.LINK)
        self.assertIn("Share", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
