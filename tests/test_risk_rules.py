from pathlib import Path

from app.risk.rules import MediaSignals, MetaMatch, Recognition, RiskRules, assess

RULES = RiskRules.load(Path(__file__).parents[1] / "config" / "risk_rules.example.toml")


def rec(label, score=100, **kw):
    return Recognition(status="match", title="Song", artists="Artist", label=label, score=score, **kw)


def test_commercial_label_is_high():
    a = assess(MediaSignals(has_audio_stream=True, recognitions=[rec("Columbia Records")]), RULES)
    assert a.level == "high"


def test_worst_signal_wins():
    s = MediaSignals(
        has_audio_stream=True,
        meta_matches=[MetaMatch(owner_copyright_policy="Facebook Sound Collection")],
        recognitions=[rec("Some Indie Label")],
    )
    assert assess(s, RULES).level == "medium"


def test_meta_no_match_never_lowers_risk():
    s = MediaSignals(has_audio_stream=None, meta_status="complete", meta_matches=[])
    assert assess(s, RULES).level == "unknown"


def test_meta_rights_holder_is_high_without_audio():
    s = MediaSignals(has_audio_stream=None, meta_matches=[MetaMatch(author="Big Label")])
    assert assess(s, RULES).level == "high"


def test_no_audio_track():
    s = MediaSignals(has_audio_stream=False, recognitions=[rec("Sony Music")])
    assert assess(s, RULES).level == "no_audio"


def test_cover_and_weak_score():
    assert assess(MediaSignals(True, recognitions=[rec("Indie", is_cover=True)]), RULES).level == "high"
    assert assess(MediaSignals(True, recognitions=[rec("Sony", score=40)]), RULES).level == "unknown"


def test_stock_without_and_with_licence():
    assert assess(MediaSignals(True, recognitions=[rec("Epidemic Sound")]), RULES).level == "medium"
    licensed = RiskRules(**{**RULES.__dict__, "licensed_sources": ["Epidemic Sound"]})
    assert assess(MediaSignals(True, recognitions=[rec("Epidemic Sound")]), licensed).level == "low"


def test_not_recognized_is_low_and_says_listen():
    a = assess(MediaSignals(True, recognitions=[Recognition(status="no_match")]), RULES)
    assert a.level == "low"
    assert "listen" in a.reasons[0]


def test_manual_verdict_overrides_but_keeps_automatic():
    a = assess(MediaSignals(True, recognitions=[rec("Sony")], manual_verdict="cleared"), RULES)
    assert a.level == "low" and a.automatic_level == "high"


def test_label_match_is_whole_word():
    # "EMI" is in the commercial list and inside "Epidemic"; it must not match.
    assert assess(MediaSignals(True, recognitions=[rec("Epidemic Sound")]), RULES).level == "medium"
    assert assess(MediaSignals(True, recognitions=[rec("EMI Records")]), RULES).level == "high"
