"""Offline tests for profile stats and SVG mutation."""
import importlib
import json
import os
import pathlib
import sys
from unittest.mock import patch

from lxml import etree


USER_JSON = {
    "data": {"user": {"id": "MOCKUSERID", "createdAt": "2020-01-01T00:00:00Z"}}
}
FOLLOWERS_JSON = {
    "data": {"user": {"followers": {"totalCount": 42}}}
}
OWNED_COUNT_JSON = {
    "data": {"user": {"repositories": {"totalCount": 2}}}
}
PUBLIC_STARS_JSON = {
    "data": {
        "user": {
            "repositories": {
                "edges": [
                    {"node": {"stargazers": {"totalCount": 5}}},
                    {"node": {"stargazers": {"totalCount": 3}}},
                ],
                "pageInfo": {"endCursor": None, "hasNextPage": False},
            }
        }
    }
}
CONTRIB_COUNT_JSON = {
    "data": {"user": {"repositories": {"totalCount": 5}}}
}


class FakeResp:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200
        self.text = json.dumps(payload)

    def json(self):
        return self.payload


def fake_post(url, json=None, headers=None, timeout=40):
    q = (json or {}).get("query", "")

    if "createdAt" in q and "user(login" in q and "followers" not in q:
        return FakeResp(USER_JSON)
    if "followers" in q:
        return FakeResp(FOLLOWERS_JSON)
    if "privacy: PUBLIC" in q and "stargazers" in q:
        return FakeResp(PUBLIC_STARS_JSON)
    if "ORGANIZATION_MEMBER" in q:
        return FakeResp(CONTRIB_COUNT_JSON)
    if "repositories(first: 1, ownerAffiliations: OWNER)" in q:
        return FakeResp(OWNED_COUNT_JSON)

    return FakeResp({"data": {}})


def load_module(monkeypatch, *, heavy="0"):
    monkeypatch.setenv("USER_NAME", "HimuCodes")
    monkeypatch.setenv("BIRTHDATE", "2005-01-17")
    monkeypatch.setenv("DO_HEAVY", heavy)
    monkeypatch.setenv("EMBED_FONT", "0")

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    module = importlib.import_module("update_profile")
    return importlib.reload(module)


@patch("requests.post", side_effect=fake_post)
def test_offline_basic(mock_post, monkeypatch, tmp_path):
    update_profile = load_module(monkeypatch)

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    for svg in update_profile.SVG_FILES:
        src = repo_root / svg
        (tmp_path / svg).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        update_profile.main()
    finally:
        os.chdir(old_cwd)

    dark = (tmp_path / "dark.svg").read_text(encoding="utf-8")
    assert ">42<" in dark
    assert ">8<" in dark

    root = etree.fromstring(dark.encode("utf-8"))
    for stat_id in [
        "age_data",
        "repo_data",
        "star_data",
        "commit_data",
        "contrib_data",
        "follower_data",
    ]:
        el = root.find(f".//*[@id='{stat_id}']")
        assert el is not None
        assert el.text and el.text.strip() != "--"


@patch("requests.post", side_effect=fake_post)
def test_repo_count_and_stars_use_minimal_queries(mock_post, monkeypatch):
    update_profile = load_module(monkeypatch)

    repos, stars, contrib = update_profile.get_repos_and_stars("HimuCodes")

    assert repos == 2
    assert stars == 8
    assert contrib == 5

    queries = [
        call.kwargs["json"]["query"]
        for call in mock_post.call_args_list
    ]

    public_star_query = next(q for q in queries if "stargazers" in q)
    assert "privacy: PUBLIC" in public_star_query

    owned_count_query = next(
        q
        for q in queries
        if "repositories(first: 1, ownerAffiliations: OWNER)" in q
    )
    assert "edges" not in owned_count_query
