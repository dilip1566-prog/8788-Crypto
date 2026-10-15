# Crypto Ledger — personal cryptocurrency portfolio dashboard

A personal investment monitor for cryptocurrency. You enter the coin, the price you paid
and how many units; the dashboard fetches everything else and works out where you stand.

It is the sibling of the **Dashboard 6 My Stocks** ledger and works the same way: your
holdings live in a Google Sheet, a script reads the sheet and the market, and the result is
one self-contained `dashboard.html`.

This is a record of your own holdings. It is not a trading exchange, and nothing here is
investment advice.

## Getting started

Clone the repository and run it. With no sheet set up it builds from the sample holdings in
`portfolio.example.csv`, so you can see what it does before entering anything of your own:

```bash
python refresh.py
```

Then point it at **your own** data, either way round:

```bash
# A Google Sheet - the link is remembered, so you only do this once
python refresh.py --sheet "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit"

# Or no sheet at all - copy the sample and edit it in any spreadsheet app
cp portfolio.example.csv portfolio.csv
```

Nothing personal ships with this repository. `source.json` (your sheet link),
`portfolio.csv` (your purchases) and everything generated from them are listed in
`.gitignore`, so your holdings stay on your machine.

Python 3.8 or newer. No dependencies - the standard library only.

## Using it

### The easy way: leave the server running

Double-click **Start Dashboard.bat** (or run `python serve.py`). It opens the dashboard and
keeps serving it at http://localhost:8788. From then on:

* Opening or reloading the page re-reads the sheet and re-fetches the market whenever the
  data is more than 10 minutes old.
* The **Refresh** button beside the snapshot time forces an immediate re-read. Edit your
  sheet, click Refresh, and the new holdings appear.

```bash
python serve.py --max-age 0     # re-read on every single page load
python serve.py --port 8789     # if 8788 is taken
```

### The one-shot way

```bash
python refresh.py
```

Or double-click **Refresh Dashboard.bat**. It reads the sheet, prints one line per coin,
writes `dashboard.html` and opens it. A copy of the sheet is saved to `portfolio.csv` each
time, so the dashboard still builds if Google is unreachable.

## Using your own Google Sheet

Put your purchases in a Google Sheet and the dashboard re-reads it **on every build** —
nothing is ever cached from a previous run, so editing the sheet is all you ever do.

**1. Let the dashboard read it.** Open your sheet, click **Share**, and set
**General access** to **Anyone with the link → Viewer**. Nothing else about the sheet
changes and it stays out of search results.

**2. Point the dashboard at it, once:**

```bash
python refresh.py --sheet "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit"
```

The link is saved to `source.json` and remembered from then on. Add `--gid 1234567` if your
holdings are on a tab other than the first. `source.example.json` shows the file's shape if
you would rather write it by hand.

> **`source.json` is gitignored on purpose.** A sheet shared as "anyone with the link" is
> readable by anyone who has that link, so publishing it would hand over your holdings.
> Keep it out of any repository you push.

**No Google account?** Skip the sheet entirely — copy `portfolio.example.csv` to
`portfolio.csv` and edit that instead. The dashboard reads it exactly the same way.

Whatever you put in `OUR REF` becomes a filter: a chip appears for each distinct value
(between two and eight of them), so grouping rows by exchange, account or batch lets you
view one group at a time.

### The columns

The sheet needs three things: which coin, what you paid, and how many units. Everything
else is optional.

| Column | Example | Needed |
| --- | --- | --- |
| Our Ref | `C001` | optional — numbered automatically if absent |
| Asset | `Bitcoin (BTC)`, `BTC`, or `Bitcoin` | **yes** |
| Purchase Price | `92000`, `$92,000.00` | **yes** |
| Units Purchased | `0.5`, `0.00042351` | **yes** |
| Purchase Date | `2025-01-15` | optional |
| Exchange | `Coinbase` | optional |
| Notes | `First tranche` | optional |

Column names are matched loosely. `Our Ref` / `Ref` / `Reference`, `Asset` / `Coin` /
`Cryptocurrency`, `Purchase Price` / `Buy Price` / `Cost`, and `Units Purchased` / `Qty` /
`Quantity` all work. Title rows above the header, blank rows and a `TOTAL` row are skipped.
`$92,000.00`, `£1,234.56`, `1,000` and the European `50,00` all parse.

**Buying the same coin more than once is expected.** Put each purchase on its own row with
its own ref. They are folded into one line on the dashboard at the **weighted average**
purchase price, and the individual purchases are listed in the detail panel with their own
profit and loss.

### Rows that cannot be read

Where a price or quantity cannot be read at all (a stray letter, a blank), the row is left
out of the totals rather than guessed at. What the sheet says is taken at face value
otherwise: whatever price is in it is the price used, and nothing is ever corrected for you.

**The dashboard stays quiet about all of this on purpose.** Unreadable rows, coins that could
not be matched, exchange quotes rejected as symbol clashes, and purchase prices far from the
market are all still worked out — printed by `refresh.py` in the terminal and recorded in
`data.json` under `problems`, `notes` and `flagged` — but none of them appear on the page.
The one exception is **Market data unavailable**, because that one changes what a number on
screen means. To surface the others again, render `DATA.problems`, `DATA.notes` or
`DATA.flagged` in `renderNotices()` in `template.html`.

So: **if a coin is missing from the table, run `python refresh.py` and read the terminal.**

**If a symbol is ambiguous or has been renamed**, add it to `coins.json`, mapping what you
type in the sheet to a CoinGecko coin id (the last part of its `coingecko.com/en/coins/…`
address). Copy `coins.example.json` to `coins.json` and add entries as needed:

```json
{ "GRT": "the-graph", "APE": "apecoin" }
```

The usual reasons a symbol needs an override are a token that has been **renamed** (the
ticker in your sheet no longer matches the one CoinGecko lists, so a search by symbol finds
nothing) or a **chain-specific ticker** that differs from the name CoinGecko files it under.
`coins.json` is yours and is not tracked by git.

## Where the numbers come from

| What | Source | Why |
| --- | --- | --- |
| Current price | **Coinbase**, then **Binance**, then CoinGecko | An exchange price is the one you could actually deal at |
| 24h / weekly / monthly / 1-year % | **CoinGecko** | The only free source carrying all four windows |
| All-time high and its date | **CoinGecko** | Same |
| Price history chart | **CoinGecko** | Daily closes, cached for six hours |
| USD→GBP rate | **Coinbase** | For the optional GBP display |
| News | CoinDesk, Cointelegraph, Decrypt, The Block, CryptoSlate, Google News | Ranked by likely price impact |

**The percentages are rebased onto the price being shown.** CoinGecko reports "+2.4% over
24 hours" relative to *its own* price. If the dashboard is showing Coinbase's price, quoting
that figure unchanged would mean the arithmetic on screen disagreed with itself. So the
historical baseline is recovered from CoinGecko and your displayed price is measured
against it. The difference is small, but the numbers now add up.

**A price is never invented.** If every source fails for a coin, it is shown as
`No price`, the row's percentages are blank, and a banner reads *"Market data unavailable —
last successful update: …"*. That coin still counts towards your total invested but is left
out of the portfolio value, so the profit and loss figures stay honest rather than silently
pretending the coin is worth what you paid.

**Symbol clashes are rejected, not shown.** If Coinbase or Binance quotes something more
than 10% away from CoinGecko, that is almost always a different asset sharing the ticker.
The quote is dropped and a note appears on the dashboard.

## What is on the dashboard

**Top of the page:** one card carries portfolio value, today's move, total invested,
profit/loss in cash and percent, the number of assets and purchases, how many are priced,
and the best and worst performer side by side. Beside it, an allocation doughnut by asset.

The table then sizes itself to whatever is left of the window, so the page itself never
scrolls — only the table does. Making the window taller shows more rows.

**The summary folds away as you scroll.** Start scrolling the table and the top cards
collapse, handing that space to the rows — about 15 at once instead of 10 on a 945px-tall
window. Scroll back to the top and they return.

**Hide totals.** The button to the right of the filter chips pins the summary shut, so it
stays out of the way wherever you scroll; that choice is remembered between refreshes.
**Show totals** brings it back and returns to the folding behaviour.

**The table:** Our ref, Asset, Current price, Purchase price, Units purchased, 24 hrs %,
Weekly %, Month %, 1 year %, ATH, Profit/loss $, Profit/loss %, Latest news.

Click any row for the detail panel: current and purchase price, units, current value,
profit and loss, all four performance windows, the all-time high with how far below it you
are, a price chart with 1M/3M/1Y ranges, every individual purchase with its own profit and
loss, and the ranked headlines.

**Sorting and filtering.** Click any column heading to sort by it — profit/loss, any of the
percentage windows, investment size, and so on. The search box matches ref, symbol and coin
name. Chips filter to *In profit* / *At a loss*, and one chip per **Our ref** appears when
your sheet uses more than one.

**Currency.** USD is primary; the USD/GBP switch top right converts the whole page. Your
choice is remembered.

**Theme.** Auto / Light / Dark, top right. Auto follows Windows.

## The news

**In the table** each coin shows one headline on a single line, with its category and a
**+N more** button underneath. The headline and the category both open the article; **+N
more** opens that coin's detail panel, where all of its headlines are listed in full with
publisher and date. Hovering the headline shows the whole thing.

Every coin gets up to three headlines, chosen by likely price impact rather than by
recency. A headline has to name the coin *and* describe a real event before it can score,
which is what keeps price predictions and "top 10 altcoins" listicles out. The categories,
heaviest first: **Hack, Regulation, ETF, Institutional, Upgrade, Unlock, Listing,
Partnership, Adoption, Price move**. Each headline shows its category and why that category
matters, and links to the original article.

## Files

| File | What it is |
| --- | --- |
| Google Sheet | **Your input.** Edit your purchases there. |
| `serve.py` | Runs the local dashboard and re-reads the sheet as you use it. |
| `refresh.py` | Reads, fetches, calculates and renders. A single build. |
| `portfolio_io.py` | Reads the Google Sheet or a CSV and makes sense of the columns. |
| `markets.py` | Coinbase, Binance and CoinGecko access, with caching and rate limiting. |
| `news.py` | Fetches headlines and ranks them by likely price impact. |
| `calc.py` | Every calculation. No network, so it can be tested directly. |
| `template.html` | The dashboard design. Edit to restyle. |
| `test_calc.py` | Proves the arithmetic against worked BTC and ETH examples. |
| `test_portfolio_io.py` | Proves the sheet reader against awkward layouts. |
| `dashboard.html` | **Generated.** The dashboard — open this. |
| `dashboard_artifact.html` | **Generated.** Same page, ready to publish as an Artifact. |
| `data.json` | **Generated.** The raw snapshot, if you want the numbers elsewhere. |
| `portfolio.csv` | **Generated.** Offline copy of the sheet, and the sample data. |
| `source.json` | **Generated, private.** Remembers which sheet to read. Gitignored. |
| `source.example.json` | The shape of `source.json`, with a placeholder link. |
| `portfolio.example.csv` | Sample holdings, used when nothing else is set up. |
| `cache/` | **Generated.** Price history and the coin list, to stay inside API limits. |
| `coins.json` | Optional. Your own symbol → CoinGecko id overrides. |

## Checking the arithmetic

```bash
python test_calc.py
```

Prints a worked example and runs 22 checks over sample Bitcoin and Ethereum transactions —
weighted average across two Bitcoin buys, investment cost, current value, profit and loss in
cash and percent, distance from the all-time high, the value-weighted portfolio day move,
allocation shares, the flagging of implausible purchase prices, and the rule that an
unreachable market never becomes a made-up price.

```bash
python test_portfolio_io.py
```

Runs 22 checks over the sheet reader: odd column names, currency symbols, European decimal
commas, title rows, total rows, bad rows and malformed sheet links.

## Options

```bash
python refresh.py --sheet <link>          # point at a different Google Sheet
python refresh.py --gid 1234567           # read a specific tab of that sheet
python refresh.py --portfolio other.csv   # ignore the sheet, use a local CSV
python refresh.py --only BTC,ETH          # refresh just these
python refresh.py --no-news               # skip the headlines, much faster
python refresh.py --no-charts             # skip the price history, much faster
python refresh.py --no-open               # write the file, do not open a browser
```

## Notes on the data

* CoinGecko's free tier allows roughly ten calls a minute. Calls are spaced six seconds
  apart and price history is cached for six hours, so the first build of the day takes about
  a minute for ten coins and later ones are quick.
* Prices are a snapshot from when the page was built, not a live tick. The snapshot time is
  printed at the top.
* The all-time high is CoinGecko's, in USD, across all the exchanges it tracks.
* News is ranked by likely impact, so a genuinely quiet week shows few items rather than
  padding.

## Designed to grow

The pieces are deliberately separate — `calc.py` never touches the network, `markets.py`
never touches the page — so these can be added without disturbing what is here: more
exchanges, a portfolio performance chart over time, price / profit / ATH alerts, a
watchlist with target buy, target sell and stop-loss levels, staking income, tax reporting,
Excel export, and a daily email summary.
