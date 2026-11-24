"""ACRCloud Music Recognition API (Audio Fingerprinting), 15 s fragments. SPEC section 7.

TODO v0.1: sign request (HMAC-SHA1), send fragment, parse result into
app.risk.rules.Recognition, retry neighbouring segment on "Can't generate fingerprint".
"""
