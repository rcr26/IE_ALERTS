#!/usr/bin/env python3
import json, os, re, sys, time, urllib.request, urllib.error
import xml.etree.ElementTree as ET

FEED_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type=4&company=&dateb=&owner=include&count=100&output=atom"
)
STATE_FILE = "seen.json"
CONFIG_FILE = "config.json"
MAX_SEEN = 500
SEC_DELAY = 0.5

def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def http_get(url, user_agent, accept="*/*"):
    time.sleep(SEC_DELAY)
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code} for {url} — {body}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error for {url} — {e.reason}") from None

def get_feed_entries(user_agent):
    raw = http_get(FEED_URL, user_agent, accept="application/atom+xml")
    raw_str = raw.decode("utf-8")
    raw_str = re.sub(r' xmlns[^"]*"[^"]*"', '', raw_str)
    root = ET.fromstring(raw_str)
    entries = []
    for entry in root.findall(".//entry"):
        title_el = entry.find("title")
        title = title_el.text if title_el is not None else ""
        link_el = entry.find("link")
        link = link_el.get("href") if link_el is not None else None
        if not link:
            continue
        m = re.search(r"(\d{10}-\d{2}-\d{6})", link)
        if not m:
            continue
        entries.append({"title": title, "link": link, "accession": m.group(1)})
    print(f"Feed returned {len(entries)} entries.")
    return entries

def find_form4_xml(index_url, user_agent):
    acc_match = re.search(r"(\d{10}-\d{2}-\d{6})", index_url)
    if not acc_match:
        return None, None
    acc = acc_match.group(1).replace("-", "")
    cik_match = re.search(r"data/(\d+)/", index_url)
    if not cik_match:
        return None, None
    cik = cik_match.group(1)
    json_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"
    try:
        raw = http_get(json_url, user_agent, accept="application/json")
        data = json.loads(raw)
    except Exception as e:
        print(f"  index.json fetch failed: {e}")
        return None, None
    items = data.get("directory", {}).get("item", [])
    base_dir = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
    for item in items:
        name = item.get("name", "")
        if not name.lower().endswith(".xml"):
            continue
        url = f"{base_dir}/{name}"
        try:
            raw_xml = http_get(url, user_agent)
            if b"ownershipDocument" in raw_xml:
                return url, raw_xml
        except Exception:
            continue
    return None, None

def parse_form4(raw_xml):
    raw_str = re.sub(rb' xmlns[^"]*"[^"]*"', b'', raw_xml)
    root = ET.fromstring(raw_str)

    def text(node, path):
        if node is None:
            return None
        el = node.find(path)
        return el.text.strip() if el is not None and el.text else None

    issuer = root.find("issuer")
    ticker = text(issuer, "issuerTradingSymbol")
    issuer_name = text(issuer, "issuerName")
    owner = root.find("reportingOwner")
    owner_name = None
    title = None
    if owner is not None:
        owner_name = text(owner.find("reportingOwnerId"), "rptOwnerName")
        rel = owner.find("reportingOwnerRelationship")
        if rel is not None:
            parts = []
            if text(rel, "isOfficer") == "1":
                parts.append(text(rel, "officerTitle") or "Officer")
            if text(rel, "isDirector") == "1":
                parts.append("Director")
            if text(rel, "isTenPercentOwner") == "1":
                parts.append("10% Owner")
            title = ", ".join(p for p in parts if p)
    transactions = []
    for tx in root.findall(".//nonDerivativeTransaction"):
        code = text(tx, "transactionCoding/transactionCode")
        shares = text(tx, "transactionAmounts/transactionShares/value")
        price = text(tx, "transactionAmounts/transactionPricePerShare/value")
        date = text(tx, "transactionDate/value")
        transactions.append({"code": code, "shares": shares, "price": price, "date": date})
    return {"ticker": ticker, "issuer_name": issuer_name, "owner_name": owner_name, "title": title, "transactions": transactions}

def matches_filters(form4, config):
    watchlist = [t.upper() for t in config.get("watchlist", [])]
    if watchlist and (form4["ticker"] or "").upper() not in watchlist:
        return False, None
    wanted = set(config.get("transaction_codes", ["P"]))
    min_val = config.get("min_value", 0)
    matched = []
    for tx in form4["transactions"]:
        if tx["code"] not in wanted:
            continue
        try:
            val = float(tx["shares"] or 0) * float(tx["price"] or 0)
        except ValueError:
            val = 0
        if val >= min_val:
            matched.append({**tx, "value": val})
    return (True, matched) if matched else (False, None)

def send_ntfy(topic, title, message, link=None):
    headers = {"Title": title, "Priority": "default"}
    if link:
        headers["Click"] = link
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=message.encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        code = resp.getcode()
        if code >= 300:
            raise RuntimeError(f"ntfy returned HTTP {code}")

def main():
    config = load_json(CONFIG_FILE, {})
    user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    ntfy_topic = os.environ.get("NTFY_TOPIC", "").strip()

    # Diagnostics: never print the actual secret values, but confirm they exist
    print(f"SEC_USER_AGENT set: {bool(user_agent)} (len={len(user_agent)})")
    print(f"NTFY_TOPIC set: {bool(ntfy_topic)} (len={len(ntfy_topic)})")

    if not user_agent:
        print("FATAL: Missing SEC_USER_AGENT. Add it under Settings > Secrets and variables > Actions in THIS repo.")
        sys.exit(1)
    if not re.match(r".+\s.+@.+\..+", user_agent):
        print("WARNING: SEC_USER_AGENT doesn't look like 'Name email@domain.com' — SEC may reject it.")
    if not ntfy_topic:
        print("FATAL: Missing NTFY_TOPIC.")
        sys.exit(1)

    try:
        entries = get_feed_entries(user_agent)
    except Exception as e:
        print(f"FATAL: Failed to fetch SEC feed: {e}")
        sys.exit(1)

    seen = set(load_json(STATE_FILE, []))
    new_seen = list(seen)
    alerts_sent = 0
    purchases_found = 0
    errors = 0

    for entry in entries:
        acc = entry["accession"]
        if acc in seen:
            continue
        try:
            xml_url, raw_xml = find_form4_xml(entry["link"], user_agent)
            if not raw_xml:
                # Confirmed no Form 4 XML exists for this filing — safe to mark seen permanently.
                new_seen.append(acc)
                continue
            form4 = parse_form4(raw_xml)
            # Only mark seen after a successful fetch + parse, so transient
            # SEC errors don't silently drop a filing forever.
            new_seen.append(acc)
        except Exception as e:
            print(f"Skipping {acc} (will retry next run): {e}")
            errors += 1
            continue

        codes = [t["code"] for t in form4["transactions"]]
        if codes:
            print(f"  {form4['ticker']} codes={codes}")
        if "P" in codes:
            purchases_found += 1
            for t in form4["transactions"]:
                if t["code"] == "P":
                    try:
                        v = float(t["shares"] or 0) * float(t["price"] or 0)
                    except ValueError:
                        v = 0
                    flag = "MATCHES" if v >= config.get("min_value", 0) else "below min_value"
                    print(f"    P buy: {form4['ticker']} ${v:,.0f} ({flag})")

        ok, matched = matches_filters(form4, config)
        if not ok:
            continue

        total = sum(t["value"] for t in matched)
        label = form4["ticker"] or form4["issuer_name"] or "Unknown"
        msg_lines = [f"{form4['owner_name'] or 'Unknown'} ({form4['title'] or 'n/a'})"]
        for t in matched:
            msg_lines.append(f"{t['code']} {float(t['shares'] or 0):,.0f} sh @ ${float(t['price'] or 0):,.2f}")
        try:
            send_ntfy(ntfy_topic, f"{label}: insider buy ${total:,.0f}", "\n".join(msg_lines), link=xml_url)
            alerts_sent += 1
            print(f"  ALERT SENT: {label} ${total:,.0f} -> ntfy.sh/{ntfy_topic}")
        except Exception as e:
            print(f"  ALERT FAILED for {acc}: {e}")
            errors += 1

    save_json(STATE_FILE, new_seen[-MAX_SEEN:])
    print(f"Purchases found: {purchases_found}")
    print(f"Checked {len(entries)} filings, sent {alerts_sent} alert(s), {errors} error(s).")

if __name__ == "__main__":
    main()
