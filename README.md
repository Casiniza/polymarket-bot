# Polymarket Trading Bot

Bot automatizado para Polymarket con 3 estrategias y despliegue en GitHub Actions.

## Estrategias disponibles

| Estrategia | Descripción |
|---|---|
| `THRESHOLD` | Compra YES/NO cuando el precio cae por debajo de un umbral configurable |
| `MOMENTUM` | Sigue la dirección dominante cuando el spread es alto |
| `CONTRARIAN` | Va contra el mercado cuando el consenso es extremo (>85% o <15%) |

## Setup local

```bash
git clone https://github.com/TU_USUARIO/polymarket-bot
cd polymarket-bot
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Editar .env con tus credenciales
python bot.py
```

## Setup en GitHub Actions

1. Haz fork/push del repo a tu cuenta de GitHub.
2. Ve a **Settings → Secrets and variables → Actions**.
3. Agrega estos **Secrets** (datos sensibles):
   - `PRIVATE_KEY` — clave privada de tu wallet Polygon
   - `CLOB_API_KEY`, `CLOB_API_SECRET`, `CLOB_API_PASSPHRASE` — credenciales CLOB
4. Agrega estas **Variables** (configuración):
   - `DRY_RUN=true` (cambia a `false` para trading real)
   - `MAX_BET_USDC=10.0`
   - `STRATEGY=THRESHOLD`
   - `THRESHOLD_BUY_YES=0.30`
5. El bot corre automáticamente cada hora. También puedes dispararlo manualmente en **Actions → Polymarket Bot → Run workflow**.

## Obtener credenciales CLOB

1. Ve a [polymarket.com](https://polymarket.com) y conecta tu wallet.
2. Ejecuta en Python:
   ```python
   from py_clob_client.client import ClobClient
   from py_clob_client.constants import POLYGON
   client = ClobClient("https://clob.polymarket.com", key="TU_PRIVATE_KEY", chain_id=POLYGON)
   creds = client.create_or_derive_api_creds()
   print(creds)
   ```

## Estructura

```
polymarket-bot/
├── bot.py          # Punto de entrada — ciclo principal
├── markets.py      # Obtiene mercados y precios via API
├── strategy.py     # Lógica de señales (THRESHOLD / MOMENTUM / CONTRARIAN)
├── trader.py       # Ejecuta órdenes via py-clob-client
├── config.py       # Configuración desde variables de entorno
├── requirements.txt
└── .github/
    └── workflows/
        └── bot.yml # GitHub Actions (cron + manual)
```

## Advertencia

Este bot es experimental. Úsalo primero con `DRY_RUN=true` para verificar las señales antes de arriesgar fondos reales. El trading en mercados de predicción conlleva riesgo de pérdida total del capital invertido.
