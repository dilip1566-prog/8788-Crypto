"""Crypto headlines, ranked by how much they could actually move a price.

Two sources feed this: a handful of crypto newsroom RSS feeds, fetched once and
shared by every coin, and a per-coin Google News search for anything the
newsrooms missed. A headline then has to *name the coin* and *describe a real
event* before it can score at all, which is what keeps price-prediction filler
and "top 10 altcoins" listicles off the dashboard.
"""

import re
import time
import urllib.parse
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

# Newsrooms worth reading, fetched once for the whole portfolio.
FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("The Block", "https://www.theblock.co/rss.xml"),
    ("CryptoSlate", "https://cryptoslate.com/feed/"),
]

# Each category carries a weight and the reason it can move a price. The
# ordering is the user's own list of what matters, heaviest first.
CATEGORIES = [
    ("Hack", 10,
     r"\b(hack(ed|s|ing)?|exploit(ed|s)?|breach(ed|es)?|stolen|theft|drained?|"
     r"rug ?pull|attack(ed)?|51%|vulnerabilit(y|ies)|compromised?|siphoned)\b",
     "Funds lost to an exploit hit confidence and the price straight away."),

    ("Regulation", 9,
     r"\b(regulat(or|ors|ory|ion)|sec |cftc|fca |mica|lawsuit|sues?|sued|"
     r"court|judge|ruling|settle(ment|s|d)?|charges?|enforcement|ban(ned|s)?|"
     r"crackdown|subpoena|legislation|bill|senate|congress|treasury|sanction(s|ed)?|"
     r"tax(ation)?|licen[cs]e[sd]?|compliance)\b",
     "Rules decide who is allowed to buy it, and at what cost."),

    ("ETF", 9,
     r"\b(etf|etp|etn|spot (bitcoin|ether|crypto)|exchange[- ]traded|"
     r"blackrock|ishares|fidelity|grayscale|vaneck|bitwise|ark ?invest|"
     r"inflows?|outflows?|s-1|19b-4|approval|filing)\b",
     "ETF flows are the main channel for institutional money."),

    ("Institutional", 8,
     r"\b(institutional|treasury (holding|strategy|company|reserve)|"
     r"microstrategy|strategy inc|metaplanet|adds? \d[\d,.]* (btc|bitcoin|eth)|"
     r"buys? \$?\d[\d,.]*(m|bn|b| million| billion)?|accumulat(e|es|ing|ion)|"
     r"whales?|pension|sovereign|hedge fund|corporate (buy|purchase|holding))\b",
     "Large balance-sheet buying takes supply off the market."),

    ("Upgrade", 8,
     r"\b(upgrade|hard ?fork|soft ?fork|mainnet|testnet|halving|merge|"
     r"pectra|dencun|fusaka|shapella|layer[- ]?2|rollup|staking|validator|"
     r"protocol (change|update)|roadmap|scaling|throughput|eip[- ]?\d+|"
     r"bip[- ]?\d+|activat(e|es|ed|ion))\b",
     "Network changes alter what the chain can do and what it costs to use."),

    ("Unlock", 8,
     r"\b(token unlock|unlocks?|vesting|cliff|emission(s)?|inflation schedule|"
     r"burn(ed|s|ing)?|buyback|supply (increase|shock|cut)|dilution|"
     r"circulating supply|new tokens? released)\b",
     "New supply hitting the market is a direct weight on the price."),

    ("Listing", 7,
     r"\b(list(s|ed|ing)? on|delist(s|ed|ing)?|coinbase|binance|kraken|"
     r"upbit|okx|bybit|bitstamp|gemini|now available|trading pairs?|perpetual)\b",
     "A major listing widens who can buy it; a delisting narrows it."),

    ("Partnership", 7,
     r"\b(partner(s|ship|ed|ing)?|integrat(e|es|ed|ion)|collaborat(e|es|ion)|"
     r"tie[- ]up|deal with|joins? forces|adopt(s|ed|ion) by|powers?|"
     r"acqui(re|res|red|sition)|merger|invests? in|funding round|raises? \$)\b",
     "Real usage and distribution deals change the demand picture."),

    ("Adoption", 6,
     r"\b(accepts? (crypto|bitcoin|payment)|payment(s)? (rail|option|support)|"
     r"merchants?|legal tender|central bank|cbdc|custody|bank(s|ing)? (offer|launch)|"
     r"visa|mastercard|paypal|stripe|swift)\b",
     "Payment and banking access broadens the buyer base."),

    ("Price move", 5,
     r"\b(surge[sd]?|soar[sd]?|plunge[sd]?|crash(es|ed)?|tumbl(e|es|ed)|"
     r"rall(y|ies|ied)|record high|all[- ]time high|slumps?|jumps?|"
     r"liquidat(e|ed|ions?)|sell[- ]?off|breaks? (above|below)|"
     r"\d+% (gain|drop|rise|fall))\b",
     "A move already under way often carries on for a few sessions."),
]

# Chatter that is not news, however often it is published.
NOISE = re.compile(
    r"\b(price prediction|forecast for 20|could hit|might hit|will hit|"
    r"here[' ]?s why|top \d+|best \d+|\d+ (coins?|altcoins?|tokens?) to|"
    r"to buy now|next (bull run|100x|1000x)|explained|what is|beginners? guide|"
    r"how to buy|sponsored|press release|giveaway|airdrop guide|"
    r"technical analysis|chart (shows|signals?)|analyst says|expert (says|predicts)|"
    r"weekly (wrap|recap)|market wrap|daily digest|what to (watch|know))\b", re.I)

OPINION = re.compile(r"^(opinion|op-ed|analysis|explainer|guide|review)\b[:\s]", re.I)

REPUTABLE = re.compile(
    r"(coindesk|cointelegraph|the block|decrypt|reuters|bloomberg|financial times|"
    r"ft\.com|wall street journal|wsj|cnbc|forbes|barron|axios|the information|"
    r"protos|blockworks|dl ?news|cryptoslate|bitcoin magazine|yahoo finance|"
    r"associated press|guardian|telegraph|times)", re.I)

# Names a coin also goes by, so a headline saying "Ether" still matches ETH.
ALIASES = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "ether", "eth"],
    "XRP": ["xrp", "ripple"],
    "SOL": ["solana", "sol"],
    "BNB": ["bnb", "binance coin"],
    "ADA": ["cardano", "ada"],
    "DOGE": ["dogecoin", "doge"],
    "AVAX": ["avalanche", "avax"],
    "DOT": ["polkadot", "dot"],
    "MATIC": ["polygon", "matic"],
    "POL": ["polygon", "pol"],
    "LTC": ["litecoin", "ltc"],
    "LINK": ["chainlink", "link"],
    "TRX": ["tron", "trx"],
    "SHIB": ["shiba inu", "shib", "shiba"],
    "XLM": ["stellar", "xlm", "lumens"],
    "ATOM": ["cosmos", "atom"],
    "UNI": ["uniswap", "uni"],
    "NEAR": ["near protocol", "near"],
    "HBAR": ["hedera", "hbar"],
    "SUI": ["sui"],
    "APT": ["aptos", "apt"],
    "ARB": ["arbitrum", "arb"],
    "OP": ["optimism"],
    "TON": ["toncoin", "ton"],
    "BCH": ["bitcoin cash", "bch"],
}

# Words too common to prove a headline is about that coin.
WEAK_TOKENS = {"the", "coin", "token", "network", "protocol", "chain", "finance",
               "labs", "foundation", "dao", "inu", "cash", "classic", "usd",
               "on", "one", "near", "link", "sui", "op", "ton", "sand", "gas"}


def identity_tokens(symbol, name):
    """Everything a headline could call this coin."""
    tokens = set()
    symbol = (symbol or "").upper()
    if symbol in ALIASES:
        tokens.update(ALIASES[symbol])
    if symbol and len(symbol) >= 3:
        tokens.add(symbol.lower())
    for part in re.split(r"[^a-z0-9]+", (name or "").lower()):
        if len(part) >= 4 and part not in WEAK_TOKENS:
            tokens.add(part)
    if name:
        tokens.add(name.strip().lower())
    return {t for t in tokens if t}


def mentions(title, tokens):
    """Whole-word match, so SOL does not fire on "solution"."""
    low = " " + re.sub(r"[^a-z0-9]+", " ", title.lower()) + " "
    for token in tokens:
        if " " + token + " " in low:
            return True
    return False


def classify(title):
    """The heaviest category this headline belongs to."""
    for name, weight, pattern, why in CATEGORIES:
        if re.search(pattern, title, re.I):
            return {"category": name, "weight": weight, "why": why}
    return None


# ------------------------------------------------------------------ fetching
def _stamp(text):
    if not text:
        return 0
    try:
        return parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError):
        return 0


def _strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'")
                .replace("&quot;", '"').replace("&#8217;", "'"))
    return " ".join(text.split())


def parse_feed(xml_text, default_publisher=""):
    """Read an RSS or Atom feed into plain dictionaries."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []

    items = []
    nodes = list(root.iter("item")) or list(
        root.iter("{http://www.w3.org/2005/Atom}entry"))
    for node in nodes:
        def get(tag):
            found = node.findtext(tag)
            if found is None:
                found = node.findtext("{http://www.w3.org/2005/Atom}" + tag)
            return found

        title = _strip_html(get("title"))
        if not title:
            continue

        link = get("link") or ""
        if not link:
            anchor = node.find("{http://www.w3.org/2005/Atom}link")
            if anchor is not None:
                link = anchor.get("href") or ""

        publisher = default_publisher
        source = node.find("source")
        if source is not None and (source.text or "").strip():
            publisher = source.text.strip()

        # Google News appends " - Publisher" to every headline.
        trailing = re.search(r"\s+-\s+([^-]{2,40})$", title)
        if trailing and not default_publisher:
            publisher = publisher or trailing.group(1).strip()
            title = title[:trailing.start()].strip()

        items.append({
            "title": title,
            "link": link.strip(),
            "publisher": publisher,
            "summary": _strip_html(get("description") or get("summary") or "")[:400],
            "timestamp": _stamp(get("pubDate") or get("published") or get("updated")),
        })
    return items


def newsroom_items(api):
    """The shared crypto newsroom feeds. Failures are skipped, not fatal."""
    items = []
    for publisher, url in FEEDS:
        try:
            items.extend(parse_feed(api.text(url, tries=2), publisher))
        except Exception:
            continue
    return items


def google_news(api, query, days=21):
    """A targeted search, for coins the newsrooms did not cover."""
    url = ("https://news.google.com/rss/search?q=%s+when:%dd&hl=en-GB&gl=GB&ceid=GB:en"
           % (urllib.parse.quote(query), days))
    try:
        return parse_feed(api.text(url, tries=2))
    except Exception:
        return []


# ------------------------------------------------------------------- ranking
def rank(items, symbol, name, limit=3, max_age_days=30):
    """Up to `limit` headlines for one coin, the ones that matter first."""
    now = time.time()
    tokens = identity_tokens(symbol, name)
    events, plain, seen = [], [], set()

    for item in items:
        title = item.get("title") or ""
        if not title or not item.get("link"):
            continue

        key = re.sub(r"[^a-z0-9]+", "", title.lower())[:60]
        if key in seen:
            continue
        seen.add(key)

        stamp = item.get("timestamp") or 0
        age_days = (now - stamp) / 86400 if stamp else 999
        if stamp and age_days > max_age_days:
            continue

        if not mentions(title, tokens):
            continue

        publisher = item.get("publisher") or ""
        if NOISE.search(title) or OPINION.match(title):
            continue

        hit = classify(title)
        score = hit["weight"] if hit else 0.0
        score += 4 if age_days <= 2 else 2 if age_days <= 7 else 0
        score += 2 if REPUTABLE.search(publisher) else -1
        # A headline that leads with the coin is usually about the coin.
        if re.match(r"^\W*(%s)\b" % "|".join(re.escape(t) for t in tokens), title, re.I):
            score += 1.5

        row = {
            "title": title,
            "link": item["link"],
            "publisher": publisher or "",
            "when": time.strftime("%d %b %Y", time.localtime(stamp)) if stamp else "",
            "timestamp": stamp,
            "category": hit["category"] if hit else None,
            "why": hit["why"] if hit else None,
            "score": round(score, 2),
        }
        (events if hit else plain).append(row)

    events.sort(key=lambda r: (-r["score"], -r["timestamp"]))
    plain.sort(key=lambda r: (-r["score"], -r["timestamp"]))
    return (events + plain)[:limit]
