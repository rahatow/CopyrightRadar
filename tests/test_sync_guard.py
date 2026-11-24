from app.instagram.sync import decide_sync


def post(i, permalink=True):
    return {"id": str(i), "permalink": f"https://www.instagram.com/reel/x{i}/" if permalink else None}


def test_missing_and_returned():
    d = decide_sync({"1", "2", "3", "4", "5"}, {"9"}, [post(1), post(2), post(3), post(4), post(9)], complete=True)
    assert d.outcome == "apply" and d.to_missing == ["5"] and d.to_in_feed == ["9"]


def test_incomplete_response_changes_nothing():
    assert decide_sync({"1"}, set(), [], complete=False).outcome == "abort"


def test_post_without_permalink_changes_nothing():
    assert decide_sync({"1"}, set(), [post(1, permalink=False)], complete=True).outcome == "abort"


def test_large_drop_needs_confirmation():
    feed = {str(i) for i in range(10)}
    d = decide_sync(feed, set(), [post(i) for i in range(7)], complete=True)
    assert d.outcome == "needs_confirmation"
    assert decide_sync(feed, set(), [post(i) for i in range(7)], complete=True, confirmed=True).outcome == "apply"
