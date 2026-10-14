"""Heavy-mode tests for author-filtered scans and incremental caching."""
import importlib
import json
import os
import pathlib
import sys
from unittest.mock import patch


USER = "HimuCodesHeavy"

USER_JSON = {
    "data": {"user": {"id": "MOCKID", "createdAt": "2020-01-01T00:00:00Z"}}
}
FOLLOWERS_JSON = {
    "data": {"user": {"followers": {"totalCount": 7}}}
}
OWNED_COUNT_JSON = {
    "data": {"user": {"repositories": {"totalCount": 1}}}
}
PUBLIC_STARS_JSON = {
    "data": {
        "user": {
            "repositories": {
                "edges": [{"node": {"stargazers": {"totalCount": 4}}}],
                "pageInfo": {"endCursor": None, "hasNextPage": False},
            }
        }
    }
}
CONTRIB_COUNT_JSON = {
    "data": {"user": {"repositories": {"totalCount": 1}}}
}
REPO_STATES_JSON = {
    "data": {
        "user": {
            "repositories": {
                "edges": [
                    {
                        "node": {
                            "nameWithOwner": f"{USER}/repo1",
                            "defaultBranchRef": {
                                "target": {
                                    "oid": "abc123",
                                    "history": {"totalCount": 1},
                                }
                            },
                        }
                    }
                ],
                "pageInfo": {"endCursor": None, "hasNextPage": False},
            }
        }
    }
}
COMMIT_HISTORY_JSON = {
    "data": {
        "repository": {
            "defaultBranchRef": {
                "target": {
                    "history": {
                        "edges": [
                            {"node": {"additions": 10, "deletions": 2}}
                        ],
                        "pageInfo": {
                            "hasNextPage": False,
                            "endCursor": None,
                        },
                    }
                }
            }
        }
    }
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
    if "defaultBranchRef" in q and "nameWithOwner" in q and "history(first: 0)" in q:
        return FakeResp(REPO_STATES_JSON)
    if "author: {id: $userId}" in q and "additions" in q:
        return FakeResp(COMMIT_HISTORY_JSON)

    return FakeResp({"data": {}})


def load_module(monkeypatch, tmp_path, *, force_cache):
    monkeypatch.setenv("USER_NAME", USER)
    monkeypatch.setenv("BIRTHDATE", "2005-01-17")
    monkeypatch.setenv("DO_HEAVY", "1")
    monkeypatch.setenv("FORCE_CACHE", "1" if force_cache else "0")
    monkeypatch.setenv("DEBUG", "0")
    monkeypatch.setenv("EMBED_FONT", "0")

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    module = importlib.import_module("update_profile")
    module = importlib.reload(module)
    module.CACHE_FILE = str(tmp_path / "heavy-cache.txt")
    return module


@patch("requests.post", side_effect=fake_post)
def test_heavy_mode_populates_cache_and_svg(mock_post, tmp_path, monkeypatch):
    update_profile = load_module(monkeypatch, tmp_path, force_cache=True)
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

    out = (tmp_path / "dark.svg").read_text(encoding="utf-8")
    assert ">1<" in out
    assert ">10<" in out or "+10" in out
    assert "-2" in out

    cache = pathlib.Path(update_profile.CACHE_FILE).read_text(encoding="utf-8")
    assert "abc123 1 1 10 2" in cache

    history_queries = [
        call.kwargs["json"]["query"]
        for call in mock_post.call_args_list
        if "author: {id: $userId}" in call.kwargs["json"]["query"]
    ]
    assert len(history_queries) == 1


@patch("requests.post", side_effect=fake_post)
def test_unchanged_repo_reuses_cache(mock_post, tmp_path, monkeypatch):
    update_profile = load_module(monkeypatch, tmp_path, force_cache=False)

    repo_hash = update_profile.hashlib.sha256(
        f"{USER}/repo1".encode()
    ).hexdigest()
    pathlib.Path(update_profile.CACHE_FILE).write_text(
        "Cache File for LOC / Commit Stats\n"
        "Format: sha256(repo) headOid totalCommits myCommits additions deletions\n"
        f"User: {USER}\n"
        "Generated: 2026-10-14T00:00:00+00:00\n"
        "---\n"
        f"{repo_hash} abc123 1 1 10 2\n",
        encoding="utf-8",
    )

    stats = update_profile.heavy_stats(USER, "MOCKID")
    assert stats == (1, 10, 2, 8)

    history_calls = [
        call
        for call in mock_post.call_args_list
        if "author: {id: $userId}" in call.kwargs["json"]["query"]
    ]
    assert history_calls == []


@patch("requests.post", side_effect=fake_post)
def test_new_repo_does_not_discard_existing_cache(mock_post, tmp_path, monkeypatch):
    update_profile = load_module(monkeypatch, tmp_path, force_cache=False)

    existing_hash = update_profile.hashlib.sha256(
        f"{USER}/repo1".encode()
    ).hexdigest()
    pathlib.Path(update_profile.CACHE_FILE).write_text(
        "Cache File for LOC / Commit Stats\n"
        "Format: sha256(repo) headOid totalCommits myCommits additions deletions\n"
        f"User: {USER}\n"
        "Generated: 2026-10-14T00:00:00+00:00\n"
        "---\n"
        f"{existing_hash} abc123 1 1 10 2\n",
        encoding="utf-8",
    )

    original_collect = update_profile.collect_repo_states
    update_profile.collect_repo_states = lambda login: [
        (f"{USER}/repo1", "abc123", 1),
        (f"{USER}/repo2", "new456", 1),
    ]

    try:
        update_profile.heavy_stats(USER, "MOCKID")
    finally:
        update_profile.collect_repo_states = original_collect

    history_calls = [
        call
        for call in mock_post.call_args_list
        if "author: {id: $userId}" in call.kwargs["json"]["query"]
    ]

    # Existing repo remains cached; only the newly discovered repo is scanned.
    assert len(history_calls) == 1
