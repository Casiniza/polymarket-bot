"""
Ejecuta este script UNA SOLA VEZ para generar tus credenciales CLOB de Polymarket.
Luego guárdalas en GitHub Secrets.

Uso:
    pip install py-clob-client
    python generate_credentials.py
"""
import os
from py_clob_client_v2 import ClobClient
from py_clob_client_v2.constants import POLYGON

private_key = input("Pega tu PRIVATE KEY de Polygon (sin 0x si la tiene): ").strip()
if private_key.startswith("0x"):
    private_key = private_key[2:]

client = ClobClient(
    host="https://clob.polymarket.com",
    key=private_key,
    chain_id=POLYGON,
)

print("\nGenerando credenciales CLOB...")
creds = client.create_or_derive_api_creds()

print("\n✅ Credenciales generadas. Cópialas en GitHub Secrets:\n")
print(f"  PRIVATE_KEY      = {private_key}")
print(f"  CLOB_API_KEY     = {creds.api_key}")
print(f"  CLOB_API_SECRET  = {creds.api_secret}")
print(f"  CLOB_API_PASSPHRASE = {creds.api_passphrase}")
print("\nNO compartas estas claves con nadie.")
