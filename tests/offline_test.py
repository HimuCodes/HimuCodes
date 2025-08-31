"""
Offline test: Mocks GitHub GraphQL responses so we can verify SVG mutation
without real network calls.

Run:  pytest -q
"""
import json
from unittest.mock import patch
import pathlib
import os
import importlib
import sys
from lxml import etree

# Minimal canned responses
USER_JSON = {
    "data": {"user": {"id": "MOCKUSERID", "createdAt": "2020-01-01T00:00:00Z"}}
}
FOLLOWERS_JSON = {
    "data": {"user": {"followers": {"totalCount": 42}}}
}
REPOS_JSON = {
    "data": {
        "user": {
            "repositories": {
                "totalCount": 2,
                "edges": [
                    {"node": {"stargazers": {"totalCount": 5}, "nameWithOwner": "HimuCodes/repo1"}},
                    {"node": {"stargazers": {"totalCount": 3}, "nameWithOwner": "HimuCodes/repo2"}}
                ],
                "pageInfo": {"endCursor": None, "hasNextPage": False}
            }
        }
    }
}

CONTRIB_REPOS_JSON = REPOS_JSON  # same structure, different query usage

EMPTY_TOTAL_COMMITS = {
    "data": {
        "repository": {
            "defaultBranchRef": {
                "target": {
                    "history": {
                        "totalCount": 0
                    }
                }
            }
        }
    }
}

def fake_post(url, json=None, headers=None, timeout=40):
    q = (json or {}).get("query", "")
    # order of checks matters
    if "createdAt" in q and "user(login" in q and "id" in q and "followers" not in q:
        return FakeResp(USER_JSON)
    if "followers" in q:
        return FakeResp(FOLLOWERS_JSON)
    if "repositories(first: 100" in q and "ownerAffiliations: OWNER)" in q:
        return FakeResp(REPOS_JSON)
    if "ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]" in q:
        return FakeResp(CONTRIB_REPOS_JSON)
    if "history(first: 0)" in q:
        return FakeResp(EMPTY_TOTAL_COMMITS)
    # fallback default
    return FakeResp({"data": {}})

class FakeResp:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200
        self.text = json.dumps(payload)
    def json(self):
        return self.payload

@patch("requests.post", side_effect=fake_post)
def test_offline_basic(mock_post, monkeypatch, tmp_path):
    # Ensure env variables exist before importing update_profile
    monkeypatch.setenv("USER_NAME", "HimuCodes")
    monkeypatch.setenv("BIRTHDATE", "2005-01-17")
    monkeypatch.setenv("DO_HEAVY", "0")
    monkeypatch.setenv("EMBED_FONT", "0")

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    update_profile = importlib.import_module("update_profile")
    importlib.reload(update_profile)

    # Use new svg names
    update_profile.SVG_FILES[:] = ["dark.svg", "light.svg"]
    update_profile.DO_HEAVY = False

    # Copy current SVG templates into temp workspace
    for svg in update_profile.SVG_FILES:
        src = repo_root / svg
        assert src.exists(), f"Source SVG missing: {src}"
        (tmp_path / svg).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    (tmp_path / "cache").mkdir(exist_ok=True)
    (tmp_path / "cache" / "requirements.txt").write_text("requests\npython-dateutil\nlxml\n", encoding="utf-8")

    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        update_profile.main()
    finally:
        os.chdir(old_cwd)

    dark = (tmp_path / "dark.svg").read_text(encoding="utf-8")
    assert ">42<" in dark, "Followers not injected"
    assert ">8<" in dark, "Stars (5+3) not injected"

    root = etree.fromstring(dark.encode('utf-8'))
    for stat_id in ["age_data","repo_data","star_data","commit_data","contrib_data","follower_data"]:
        el = root.find(f".//*[@id='{stat_id}']")
        assert el is not None, f"Missing element id={stat_id}"
        assert el.text and el.text.strip() != "--", f"Placeholder not replaced for {stat_id}"
