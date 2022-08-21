from typing import Dict, List, Any


def get_headers(extra: Dict[str, Any] = {}) -> Dict[str, str]:
    headers = {
        "accept-language": "en-GB,en;q=0.9,ja-JP;q=0.8,ja;q=0.7,en-US;q=0.6",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.0.0 Safari/537.36",
    }
    for key, val in extra.items():
        headers[key] = val
    return headers
