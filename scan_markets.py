"""Diagnóstico: muestra precios reales de los mercados más activos."""
import requests, json

markets = requests.get("https://gamma-api.polymarket.com/markets", params={
    "active": "true", "closed": "false", "limit": 30,
    "order": "volume24hr", "ascending": "false"
}, timeout=10).json()

print(f"\n{'YES':>6} | {'NO':>6} | MERCADO")
print("-" * 85)

for m in markets:
    question = m.get("question", "")[:65]
    yes_p = no_p = None
    try:
        prices = json.loads(m.get("outcomePrices", "[]"))
        if len(prices) >= 2:
            yes_p, no_p = float(prices[0]), float(prices[1])
    except (ValueError, TypeError):
        pass

    y = f"{yes_p:.3f}" if yes_p is not None else "  N/A"
    n = f"{no_p:.3f}"  if no_p  is not None else "  N/A"
    print(f"{y:>6} | {n:>6} | {question}")
