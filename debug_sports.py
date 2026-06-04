import requests, json, sys
sys.path.insert(0, '.')
from bot import is_sports_market, market_ends_by_tomorrow

markets = requests.get("https://gamma-api.polymarket.com/markets", params={
    "active": "true", "closed": "false", "limit": 100,
    "order": "volume24hr", "ascending": "false"
}, timeout=10).json()

for m in markets:
    if isinstance(m.get("outcomePrices"), str):
        try:
            m["outcomePrices"] = json.loads(m["outcomePrices"])
        except Exception:
            pass

eligible = []
for m in markets:
    if not market_ends_by_tomorrow(m):
        continue
    if not is_sports_market(m):
        continue
    prices = m.get("outcomePrices") or []
    if len(prices) < 2:
        continue
    y, n = float(prices[0]), float(prices[1])
    eligible.append((y, n, m.get("endDateIso", "")[:10], m.get("question", "")[:58]))

eligible.sort(key=lambda x: -max(x[0], x[1]))
print(f"{'YES':>6} | {'NO':>6} | {'FIN':>10} | MERCADO")
print("-" * 95)
for y, n, end, q in eligible:
    mark = " << EN RANGO" if (0.78 <= y <= 0.92 or 0.78 <= n <= 0.92) else ""
    print(f"{y:>6.3f} | {n:>6.3f} | {end:>10} | {q}{mark}")
