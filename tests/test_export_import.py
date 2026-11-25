import json
import zipfile

from app.importer.export_zip import match_items, read_export, repair_text


def make_zip(path):
    reels = {"ig_reels_media": [{"media": [{"uri": "media/reels/202409/a.mp4", "creation_timestamp": 1725000000,
                                            "title": "RÃ¼ckenhaare"}]},
                                {"media": [{"uri": "media/reels/202409/b.mp4", "creation_timestamp": 1726000000, "title": ""}]}]}
    posts = [{"media": [{"uri": "media/posts/202409/c.jpg", "creation_timestamp": 1725500000, "title": ""}],
              "title": "Photo post", "creation_timestamp": 1725500000}]
    archived = {"ig_archived_post_media": [{"media": [{"uri": "media/archived_posts/202301/d.jpg", "creation_timestamp": 1673000000}]}]}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("your_instagram_activity/content/reels.json", json.dumps(reels))
        z.writestr("your_instagram_activity/content/posts_1.json", json.dumps(posts))
        z.writestr("your_instagram_activity/content/archived_posts.json", json.dumps(archived))
        for f in ("media/reels/202409/a.mp4", "media/reels/202409/b.mp4", "media/posts/202409/c.jpg", "media/archived_posts/202301/d.jpg"):
            z.writestr(f, b"x")


def test_repair_text():
    assert repair_text("RÃ¼ckenhaare") == "Rückenhaare"
    assert repair_text("Ok text") == "Ok text"


def test_read_and_match(tmp_path):
    zp = tmp_path / "export.zip"
    make_zip(zp)
    res = read_export(zp, tmp_path / "out")
    by_uri = {i.uri: i for i in res.items}
    assert set(by_uri) == {"media/reels/202409/a.mp4", "media/reels/202409/b.mp4", "media/posts/202409/c.jpg",
                           "media/archived_posts/202301/d.jpg"}
    assert by_uri["media/reels/202409/a.mp4"].caption == "Rückenhaare"
    assert by_uri["media/posts/202409/c.jpg"].caption == "Photo post"
    assert by_uri["media/archived_posts/202301/d.jpg"].source == "archived"
    assert all(i.local_path and i.local_path.exists() for i in res.items)
    api = [{"id": "111", "kind": "video", "timestamp": "2024-08-30T06:41:00+0000"},   # a.mp4 + 60 s
           {"id": "222", "kind": "photo", "timestamp": "2024-09-05T01:33:20+0000"}]   # c.jpg exact
    m = match_items(res.items, api)
    matched = {res.items[i].uri: mid for i, mid in m.items()}
    assert matched == {"media/reels/202409/a.mp4": "111", "media/posts/202409/c.jpg": "222"}
