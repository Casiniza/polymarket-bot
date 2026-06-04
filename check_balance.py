import requests

wallet = '0x410e17df2babc04f97a6f8f01afc241d9d0664a7'

def get_usdc(contract):
    payload = {
        'jsonrpc': '2.0', 'method': 'eth_call',
        'params': [{'to': contract, 'data': '0x70a08231000000000000000000000000' + wallet[2:]}, 'latest'],
        'id': 1
    }
    r = requests.post('https://polygon-rpc.com', json=payload, timeout=10)
    result = r.json().get('result', '0x0')
    return int(result, 16) / 1e6

native  = get_usdc('0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359')
bridged = get_usdc('0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174')
print(f"USDC native:  ${native:.2f}")
print(f"USDC bridged: ${bridged:.2f}")
print(f"Total:        ${native + bridged:.2f}")
