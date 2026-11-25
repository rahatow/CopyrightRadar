from app.instagram.sync import decide_sync


def post(i, permalink=True):
    return {"id": str(i), "permalink": f"https://www.instagram.com/reel/x{i}/" if permalink else None}


def test_missing_returned_and_new():
    d = decide_sync({"1", "2", "3", "4", "5"}, {"9"}, [post(i) for i in (1, 2, 3, 4, 9, 10)], complete=True)
    assert d.outcome == "apply"
    assert d.to_missing == ["5"] and d.to_in_feed == ["9"] and d.new_ids == ["10"]


def test_incomplete_response_changes_nothing():
    assert decide_sync({"1"}, set(), [], complete=False).outcome == "abort"


def test_post_without_permalink_changes_nothing():
    assert decide_sync({"1"}, set(), [post(1, permalink=False)], complete=True).outcome == "abort"


def test_large_drop_needs_exact_confirmation():
    feed = {str(i) for i in range(10)}
    fetched = [post(i) for i in range(7)]
    assert decide_sync(feed, set(), fetched, complete=True).outcome == "needs_confirmation"
    assert decide_sync(feed, set(), fetched, complete=True, confirm_count=2).outcome == "needs_confirmation"
    assert decide_sync(feed, set(), fetched, complete=True, confirm_count=3).outcome == "apply"


def test_manual_marks_do_not_count_and_are_not_returned():
    # 4 posts marked by hand are not in the feed set; only 1 of 6 feed posts is gone (17 % < 25 %).
    feed = {str(i) for i in range(6)}
    manual = {"m1", "m2", "m3", "m4"}
    fetched = [post(i) for i in range(5)] + [post("m1")]
    d = decide_sync(feed, set(), fetched, complete=True, manual_ids=manual)
    assert d.outcome == "apply" and d.to_missing == ["5"]
    assert d.to_in_feed == [] and d.still_visible_manual == ["m1"] and d.new_ids == []
