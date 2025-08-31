"""Heavy mode test: validates commit & LOC stats population using mocked GraphQL.
Run: pytest -q
"""
from unittest.mock import patch
import importlib
import pathlib
import os
import sys

USER = "HimuCodesHeavy"  # isolate cache file

# Mock payload builders

def user_payload():
    return {"data": {"user": {"id": "MOCKID", "createdAt": "2020-01-01T00:00:00Z"}}}

def followers_payload():
    return {"data": {"user": {"followers": {"totalCount": 7}}}}

# Repo listing for stars (with stargazers)
STAR_REPOS = {
    "data": {
        "user": {
            "repositories": {
                "totalCount": 1,
                "edges": [
                    {"node": {"stargazers": {"totalCount": 4}, "nameWithOwner": f"{USER}/repo1"}}
                ],
                "pageInfo": {"endCursor": None, "hasNextPage": False}
            }
        }
    }
}
# Contributed repos query only needs totalCount
CONTRIB_REPOS = {
    "data": {
        "user": {
            "repositories": {
                "totalCount": 1,
                "pageInfo": {"endCursor": None, "hasNextPage": False}
            }
        }
    }
}
# Repo listing for heavy scan (names only)
HEAVY_LIST = {
    "data": {
        "user": {
            "repositories": {
                "edges": [
                    {"node": {"nameWithOwner": f"{USER}/repo1"}}
                ],
                "pageInfo": {"endCursor": None, "hasNextPage": False}
            }
        }
    }
}

# Commit total query (history(first:0))
COMMIT_TOTAL = {
    "data": {
        "repository": {
            "defaultBranchRef": {
                "target": {"history": {"totalCount": 1}}
            }
        }
    }
}
# Commit history query (history(first:100))
COMMIT_HISTORY = {
    "data": {
        "repository": {
            "defaultBranchRef": {
                "target": {
                    "history": {
                        "edges": [
                            {"node": {"author": {"user": {"login": USER}}, "additions": 10, "deletions": 2}}
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None}
                    }
                }
            }
        }
    }
}


def fake_post(url, json=None, headers=None, timeout=40):
    q = (json or {}).get("query", "")
    if "user(login" in q and "id" in q and "createdAt" in q and "followers" not in q:
        return FakeResp(user_payload())
    if "followers" in q:
        return FakeResp(followers_payload())
    if "ownerAffiliations: OWNER)" in q and "stargazers" in q:
        return FakeResp(STAR_REPOS)
    if "ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]" in q:
        return FakeResp(CONTRIB_REPOS)
    if "ownerAffiliations: OWNER)" in q and "stargazers" not in q:
        return FakeResp(HEAVY_LIST)
    if "history(first: 0)" in q:
        return FakeResp(COMMIT_TOTAL)
    if "history(first: 100" in q and "additions" in q:
        return FakeResp(COMMIT_HISTORY)
    return FakeResp({"data": {}})

class FakeResp:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200
        self.text = str(payload)
    def json(self):
        return self.payload

@patch("requests.post", side_effect=fake_post)
def test_heavy_mode(mock_post, tmp_path, monkeypatch):
    monkeypatch.setenv("USER_NAME", USER)
    monkeypatch.setenv("BIRTHDATE", "2005-01-17")
    monkeypatch.setenv("DO_HEAVY", "1")
    monkeypatch.setenv("FORCE_CACHE", "1")  # deterministic
    monkeypatch.setenv("DEBUG", "0")
    monkeypatch.setenv("EMBED_FONT", "0")

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    update_profile = importlib.import_module("update_profile")
    importlib.reload(update_profile)

    # Copy SVG templates
    for svg in update_profile.SVG_FILES:
        src = repo_root / svg
        (tmp_path / svg).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    os.chdir(tmp_path)
    try:
        update_profile.main()
    finally:
        os.chdir(repo_root)

    out = (tmp_path / "dark.svg").read_text(encoding="utf-8")
    # Validate heavy stats injected
    assert ">1<" in out, "Commit count missing"
    assert ">10<" in out or "+10" in out, "Additions missing"
    assert "-2" in out, "Deletions missing"
    assert "loc_data" in out
