"""Crypto market data, from Coinbase, Binance and CoinGecko.

Only the Python standard library is used.

Which source does what, and why:

* **Coinbase** and **Binance** give a live spot price and nothing else. They are
  tried first because that is the order asked for, and because an exchange
  price is the one you could actually deal at.
* **CoinGecko** is the only free source that carries the 7-day, 30-day and
  1-year moves plus the all-time high, so the history always comes from there.
  One `/coins/markets` call covers the whole top 500 by market cap, which also
  doubles as the table used to turn "BTC" into "bitcoin".

Because the price and the history can come from different places, the caller
rebases the percentages onto whichever price is shown - see `calc.rebase`.
Nothing here ever invents a price: if every source fails the coin comes back
unpriced and the dashboard says so.
"""

import gzip
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip",
}

COINGECKO = "https://api.coingecko.com/api/v3"
COINBASE = "https://api.coinbase.com/v2"
BINANCE = "https://api.binance.com/api/v3"

# CoinGecko's free tier allows roughly ten calls a minute and answers 429 when
# pushed, so every call to that host goes through one lock and this spacing.
# Price history is cached for six hours, which is what keeps the count down.
GECKO_INTERVAL = 6.0

# What to do about a 429 from anyone: wait out the window rather than retry
# into it.
RATE_LIMIT_PAUSE = 20.0

# Symbols whose Coinbase or Binance listing is a different asset from the coin
# you almost certainly mean, or which are known to collide.
QUOTE_BLOCKLIST = {"ETHW", "LUNA", "LUNC", "USTC"}


class MarketError(RuntimeError):
    pass


class Markets:
    def __init__(self, timeout=25, retries=3, cache_dir=None):
        self.timeout = timeout
        self.retries = retries
        self.cache_dir = cache_dir
        if cache_dir and not os.path.isdir(cache_dir):
            os.makedirs(cache_dir, exist_ok=True)
        self._gecko_lock = threading.Lock()
        self._gecko_last = 0.0
        self._universe = None
        self.notes = []

    # ---------------------------------------------------------------- plumbing
    def _raw(self, url, tries=None):
        """One GET, with retries. 429 and 5xx back off; 404 gives up at once."""
        tries = tries or self.retries
        last = None
        for attempt in range(tries):
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip":
                        body = gzip.decompress(body)
                    return body.decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code in (400, 401, 403, 404):
                    break
                # A 429 window is measured in tens of seconds, so retrying
                # straight away just burns another request.
                time.sleep((RATE_LIMIT_PAUSE if exc.code == 429 else 0.6)
                           * (attempt + 1))
            except Exception as exc:                     # timeouts, DNS, resets
                last = exc
                time.sleep(0.5 * (attempt + 1))
        raise MarketError("%s: %s" % (urllib.parse.urlsplit(url).netloc, last))

    def json(self, url, tries=None):
        try:
            return json.loads(self._raw(url, tries))
        except ValueError as exc:
            raise MarketError("unreadable reply from %s (%s)"
                              % (urllib.parse.urlsplit(url).netloc, exc))

    def text(self, url, tries=None):
        return self._raw(url, tries)

    def gecko(self, path, tries=None):
        """Every CoinGecko call, spaced out and serialised."""
        with self._gecko_lock:
            wait = GECKO_INTERVAL - (time.time() - self._gecko_last)
            if wait > 0:
                time.sleep(wait)
            try:
                return self.json(COINGECKO + path, tries)
            finally:
                self._gecko_last = time.time()

    # ------------------------------------------------------------------ cache
    def _cache_path(self, key):
        if not self.cache_dir:
            return None
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
        return os.path.join(self.cache_dir, safe + ".json")

    def cached(self, key, max_age, produce):
        """Return a cached answer if it is fresh, otherwise fetch and store it.

        If the fetch fails, stale cache is better than nothing - but the caller
        is told, so the dashboard can date the number honestly.
        """
        path = self._cache_path(key)
        if path and os.path.exists(path):
            age = time.time() - os.path.getmtime(path)
            if age < max_age:
                try:
                    with open(path, encoding="utf-8") as fh:
                        return json.load(fh), False
                except (OSError, ValueError):
                    pass
        try:
            value = produce()
        except MarketError:
            if path and os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as fh:
                        return json.load(fh), True      # stale, but real
                except (OSError, ValueError):
                    pass
            raise
        if path:
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(value, fh)
            except OSError:
                pass
        return value, False

    # -------------------------------------------------------------- coingecko
    def universe(self, pages=2):
        """Top coins by market cap - the resolution table and most of the data.

        Two calls give the top 500, which covers anything a personal portfolio
        is likely to hold, and means adding coins costs no extra requests.
        """
        if self._universe is not None:
            return self._universe

        def produce():
            out = []
            for page in range(1, pages + 1):
                out.extend(self.gecko(
                    "/coins/markets?vs_currency=usd&order=market_cap_desc"
                    "&per_page=250&page=%d&sparkline=false"
                    "&price_change_percentage=24h,7d,30d,1y" % page))
            if not out:
                raise MarketError("CoinGecko returned an empty market list")
            return out

        rows, stale = self.cached("universe", 3600, produce)
        if stale:
            self.notes.append("CoinGecko was unreachable - using the market list "
                              "from the last successful run")
        self._universe = rows
        return rows

    def resolve(self, symbol, name=None, overrides=None):
        """Turn what you typed into a CoinGecko coin id.

        Ranked by market cap, so "BTC" finds Bitcoin rather than one of the
        dozens of copycat tokens that share the symbol.
        """
        overrides = overrides or {}
        key = (symbol or name or "").upper()
        if key in overrides:
            return overrides[key], "your coins.json override"

        rows = self.universe()
        if symbol:
            hits = [r for r in rows if (r.get("symbol") or "").upper() == symbol.upper()]
            if hits:
                best = max(hits, key=lambda r: r.get("market_cap") or 0)
                return best["id"], "CoinGecko"
        if name:
            low = name.strip().lower()
            hits = [r for r in rows if (r.get("name") or "").lower() == low]
            if not hits:
                hits = [r for r in rows if (r.get("name") or "").lower().startswith(low)]
            if hits:
                best = max(hits, key=lambda r: r.get("market_cap") or 0)
                return best["id"], "CoinGecko"

        # Outside the top 500 - ask CoinGecko's own search, which is also
        # ordered by market cap.
        query = name or symbol
        if not query:
            return None, None
        try:
            found, _ = self.cached(
                "search-" + query.lower(), 86400,
                lambda: self.gecko("/search?query=" + urllib.parse.quote(query)))
        except MarketError:
            return None, None
        coins = (found or {}).get("coins") or []
        if symbol:
            exact = [c for c in coins if (c.get("symbol") or "").upper() == symbol.upper()]
            if exact:
                return exact[0]["id"], "CoinGecko search"
        return (coins[0]["id"], "CoinGecko search") if coins else (None, None)

    def market_data(self, coin_ids):
        """Price, the four percentage windows and the all-time high, per coin."""
        known = {r["id"]: r for r in self.universe()}
        out = {cid: known[cid] for cid in coin_ids if cid in known}

        missing = [cid for cid in coin_ids if cid not in out]
        if missing:
            try:
                extra = self.gecko(
                    "/coins/markets?vs_currency=usd&ids=%s&sparkline=false"
                    "&price_change_percentage=24h,7d,30d,1y"
                    % urllib.parse.quote(",".join(missing)))
                for row in extra:
                    out[row["id"]] = row
            except MarketError as exc:
                self.notes.append("could not price %s (%s)"
                                  % (", ".join(missing), exc))
        return out

    def chart(self, coin_id, days=365):
        """Daily closes for the detail page chart. Cached - it moves slowly."""
        def produce():
            data = self.gecko("/coins/%s/market_chart?vs_currency=usd&days=%d"
                              "&interval=daily" % (urllib.parse.quote(coin_id), days))
            return [[int(p[0]), float(p[1])] for p in (data.get("prices") or [])]
        try:
            series, _ = self.cached("chart-%s-%d" % (coin_id, days), 21600, produce)
            return series
        except MarketError:
            return []

    # ---------------------------------------------------------- exchange spot
    def coinbase_spot(self, symbol):
        data = self.json("%s/prices/%s-USD/spot" % (COINBASE, urllib.parse.quote(symbol)),
                         tries=2)
        amount = ((data or {}).get("data") or {}).get("amount")
        if amount is None:
            raise MarketError("Coinbase does not list %s" % symbol)
        return float(amount)

    def binance_spot(self, symbol):
        data = self.json("%s/ticker/price?symbol=%sUSDT"
                         % (BINANCE, urllib.parse.quote(symbol.upper())), tries=2)
        price = (data or {}).get("price")
        if price is None:
            raise MarketError("Binance does not list %s" % symbol)
        return float(price)

    def spot_price(self, symbol, reference=None):
        """Coinbase first, then Binance. Returns (price, source) or (None, None).

        A quote wildly away from CoinGecko almost always means the exchange
        ticker is a different asset that happens to share the symbol, so it is
        rejected rather than shown.
        """
        if not symbol or symbol.upper() in QUOTE_BLOCKLIST:
            return None, None

        for name, fetch in (("Coinbase", self.coinbase_spot),
                            ("Binance", self.binance_spot)):
            try:
                price = fetch(symbol.upper())
            except (MarketError, ValueError, TypeError):
                continue
            if price is None or price <= 0:
                continue
            if reference and reference > 0:
                drift = abs(price - reference) / reference
                if drift > 0.10:
                    self.notes.append(
                        "%s on %s (%s) is %.0f%% away from the CoinGecko price - "
                        "ignored as a probable symbol clash"
                        % (symbol.upper(), name, fmt(price), drift * 100))
                    continue
            return price, name
        return None, None

    # -------------------------------------------------------------------- fx
    def usd_to_gbp(self):
        """One rate, from Coinbase, for the optional second currency."""
        try:
            data, _ = self.cached(
                "fx-usd", 3600,
                lambda: self.json(COINBASE + "/exchange-rates?currency=USD"))
            return float(data["data"]["rates"]["GBP"])
        except (MarketError, KeyError, ValueError, TypeError):
            return None


def fmt(value):
    return format(value, ",.2f") if value is not None else "n/a"


def normalise(row, coin_id):
    """Reshape a CoinGecko markets row into what `calc` expects."""
    row = row or {}

    def num(key):
        value = row.get(key)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "coin_id": coin_id,
        "name": row.get("name"),
        "symbol": (row.get("symbol") or "").upper(),
        "image": row.get("image"),
        "reference_price": num("current_price"),
        "price": num("current_price"),
        "source": "CoinGecko",
        "pct_24h": num("price_change_percentage_24h_in_currency"),
        "pct_7d": num("price_change_percentage_7d_in_currency"),
        "pct_30d": num("price_change_percentage_30d_in_currency"),
        "pct_1y": num("price_change_percentage_1y_in_currency"),
        "ath": num("ath"),
        "ath_date": (row.get("ath_date") or "")[:10] or None,
        "market_cap": num("market_cap"),
        "rank": row.get("market_cap_rank"),
    }
