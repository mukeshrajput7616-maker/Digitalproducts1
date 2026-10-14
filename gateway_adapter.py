"""
Payment gateway integration template.

Important:
- Do not credit wallet balance from a browser redirect or a user-supplied screenshot.
- Use your provider's official server API and signed webhook.
- Verify webhook signature using the provider's documented algorithm.
- Fetch payment/order status server-to-server and confirm amount, currency, merchant order ID,
  and successful final status before crediting.
- Enforce idempotency: each provider payment/order ID can credit a wallet only once.
- Store API secrets only in environment variables; never put them in bot.py sent to users.

This file is intentionally provider-neutral. Share your gateway's official API documentation
and webhook signature rules to implement the exact verified integration.
"""

def verify_webhook_signature(raw_body: bytes, signature_header: str, secret: str) -> bool:
    """Example ONLY for providers that explicitly document HMAC-SHA256 over raw request body.
    Do not use this unless it matches the payment provider's official documentation.
    """
    expected = hmac_sha256_hex(raw_body, secret)
    return bool(signature_header) and secrets_compare(expected, signature_header)

def hmac_sha256_hex(raw_body: bytes, secret: str) -> str:
    import hmac, hashlib
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

def secrets_compare(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a, b)
  
