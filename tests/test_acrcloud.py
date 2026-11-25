import base64
import hashlib
import hmac

import httpx
import pytest

from app.recognition.acrcloud import ACRCloud, ACRCloudError, parse, sign


def test_signature_matches_documented_formula():
    expected = base64.b64encode(hmac.new(b"sec", b"POST\n/v1/identify\nkey\naudio\n1\n1700000000", hashlib.sha1).digest()).decode()
    assert sign("sec", "key", "1700000000") == expected


def test_parse_match_and_no_match():
    body = {"status": {"code": 0, "msg": "Success"}, "metadata": {"music": [
        {"title": "Song", "artists": [{"name": "A"}, {"name": "B"}], "label": "Label X", "score": 100,
         "external_ids": {"isrc": "XX0000000001"}}]}}
    r = parse(body)
    assert r.recognition.status == "match" and r.recognition.artists == "A, B" and r.isrc == "XX0000000001"
    assert parse({"status": {"code": 1001}}).recognition.status == "no_match"
    assert parse({"status": {"code": 2004}}).no_fingerprint
    with pytest.raises(ACRCloudError) as e:
        parse({"status": {"code": 3001, "msg": "Missing/Invalid Access Key"}})
    assert e.value.code == 3001


def test_identify_sends_documented_fields():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["body"] = req.content
        return httpx.Response(200, json={"status": {"code": 1001, "msg": "No result"}})

    c = ACRCloud("identify-eu-west-1.acrcloud.com", "key", "sec", transport=httpx.MockTransport(handler))
    assert c.identify(b"abc").recognition.status == "no_match"
    assert seen["url"] == "https://identify-eu-west-1.acrcloud.com/v1/identify"
    for field in (b'name="access_key"', b'name="sample"', b'name="sample_bytes"', b'name="signature"',
                  b'name="data_type"', b'name="signature_version"', b'name="timestamp"'):
        assert field in seen["body"]


def test_host_must_be_acrcloud():
    with pytest.raises(ValueError):
        ACRCloud("evil.example.com", "k", "s")
