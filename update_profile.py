#!/usr/bin/env python3
"""
Dynamic profile card updater.

Features (optional heavy mode):
- Age/Uptime
- Repo count
- Star count
- Follower count
- Contributed repositories
- Commits (user-authored across repos; heavy)
- Lines of Code (add/del/net; heavy)

Environment Variables:
  ACCESS_TOKEN (optional) : Personal token. Falls back to GITHUB_TOKEN in Actions.
  USER_NAME                : GitHub login. Defaults to repository owner / actor.
  BIRTHDATE                : YYYY-MM-DD. Default 2005-01-17.
  DO_HEAVY                 : '1' => run commit & LOC scan. '0' => skip heavy parts.
  FORCE_CACHE              : '1' => rebuild LOC cache from scratch.
  EMBED_FONT              : '1' => embed Google Fonts in SVGs. '0' => skip.

SVG IDs updated:
  age_data, repo_data, star_data, commit_data, contrib_data, follower_data,
  loc_data, loc_add, loc_del

Output SVG files (dark/light): dark.svg, light.svg
"""

from __future__ import annotations
import os
import sys
import time
import hashlib
import datetime
from typing import Dict, Any, List, Tuple, Optional
from pathlib import Path
import base64
import re

import requests
from dateutil import relativedelta
from lxml import etree

# ------------------ Config & Env ------------------
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")
DEFAULT_OWNER = GITHUB_REPOSITORY.split("/")[0] if "/" in GITHUB_REPOSITORY else ""
USER_NAME = os.environ.get("USER_NAME") or os.environ.get("GITHUB_ACTOR") or DEFAULT_OWNER
if not USER_NAME:
    print("ERROR: Cannot infer USER_NAME. Set USER_NAME env variable.", file=sys.stderr)
    sys.exit(1)

BIRTHDATE_STR = os.environ.get("BIRTHDATE", "2005-01-17")
try:
    BIRTHDATE = datetime.datetime.fromisoformat(BIRTHDATE_STR)
except ValueError:
    print("Invalid BIRTHDATE format. Expected YYYY-MM-DD. Using default 2005-01-17.")
    BIRTHDATE = datetime.datetime(2005, 1, 17)

ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN") or os.environ.get("GITHUB_TOKEN")
HEADERS = {'authorization': f'token {ACCESS_TOKEN}'} if ACCESS_TOKEN else {}
# Accept header helps GitHub route appropriately
if HEADERS:
    HEADERS['Accept'] = 'application/vnd.github+json'

DO_HEAVY = os.environ.get("DO_HEAVY", "1") == "1"
FORCE_CACHE = os.environ.get("FORCE_CACHE", "0") == "1"
DEBUG = os.environ.get("DEBUG", "0") == "1"
MAX_RETRIES = int(os.environ.get("GQL_MAX_RETRIES", "3"))
RETRY_BACKOFF = float(os.environ.get("GQL_RETRY_BACKOFF", "1.5"))

REPO_ROOT = Path(__file__).resolve().parent
CACHE_DIR = str(REPO_ROOT / "cache")
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_FILE = f"{CACHE_DIR}/{hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest()}.txt"
CACHE_COMMENT_LINES = 5

QUERY_COUNT: Dict[str, int] = {
    "user_getter": 0,
    "followers": 0,
    "repos_stars": 0,
    "loc_list_repos": 0,
    "loc_repo_scan": 0
}

SVG_FILES = ["dark.svg", "light.svg"]

# Always embed by default; tests can disable with EMBED_FONT=0
EMBED_FONT = os.environ.get("EMBED_FONT", "1") == "1"
FONT_CACHE_DIR = Path(CACHE_DIR) / "fonts"
FONT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOGO_PATH = REPO_ROOT / "logo.png"

def debug(msg: str):
    if DEBUG:
        print(f"[DEBUG] {msg}")

# ------------------ Utility Functions ------------------
def rel_age(birthday: datetime.datetime) -> str:
    diff = relativedelta.relativedelta(datetime.datetime.today(), birthday)
    return f"{diff.years} year{'s' if diff.years != 1 else ''}, {diff.months} month{'s' if diff.months != 1 else ''}, {diff.days} day{'s' if diff.days != 1 else ''}" + \
           (" 🎂" if (diff.months == 0 and diff.days == 0) else "")

def gql(query: str, variables: Dict[str, Any], tag: str):
    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(
                "https://api.github.com/graphql",
                json={"query": query, "variables": variables},
                headers=HEADERS,
                timeout=40
            )
            if r.status_code == 502 and attempt < MAX_RETRIES:  # transient
                debug(f"{tag}: 502 Bad Gateway, retry {attempt}")
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            if r.status_code != 200:
                raise RuntimeError(f"{tag} failed: {r.status_code} {r.text[:300]}")
            data = r.json()
            # GraphQL errors block
            if 'errors' in data and data['errors']:
                # Rate limit detection
                messages = ' | '.join(e.get('message','') for e in data['errors'])
                if 'rate limit' in messages.lower() and attempt < MAX_RETRIES:
                    debug(f"{tag}: rate limit encountered, backoff retry {attempt}")
                    time.sleep(RETRY_BACKOFF ** attempt)
                    continue
                raise RuntimeError(f"{tag} GraphQL errors: {messages}")
            return data
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                debug(f"{tag}: network error {e}, retry {attempt}")
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            raise
        except Exception as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                debug(f"{tag}: exception {e}, retry {attempt}")
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            raise
    # Fallback (should not reach)
    if last_exc:
        raise last_exc
    raise RuntimeError(f"{tag} failed without exception")

def count_query(name: str):
    QUERY_COUNT[name] += 1

def format_int(num: int) -> str:
    return f"{num:,}"

# ------------------ Data Collection ------------------
def get_user_and_created(login: str) -> Tuple[str, str]:
    count_query("user_getter")
    query = """
    query($login: String!){
      user(login: $login){
        id
        createdAt
      }
    }"""
    data = gql(query, {"login": login}, "user_getter")
    u = data["data"]["user"]
    return u["id"], u["createdAt"]

def get_followers(login: str) -> int:
    count_query("followers")
    query = """
    query($login: String!){
      user(login: $login){
        followers { totalCount }
      }
    }"""
    data = gql(query, {"login": login}, "followers")
    return data["data"]["user"]["followers"]["totalCount"]

def get_repos_and_stars(login: str) -> Tuple[int, int, int]:
    """
    Returns (owned_repo_count, total_stars_on_owned, contributed_repo_count)
    contributed_repo_count counts OWNER + COLLABORATOR + ORGANIZATION_MEMBER
    """
    # Owned repos
    count_query("repos_stars")
    owned_query = """
    query($login: String!, $cursor: String){
      user(login: $login){
        repositories(first: 100, after: $cursor, ownerAffiliations: OWNER){
          totalCount
          edges{
            node{
              stargazers{ totalCount }
              nameWithOwner
            }
          }
          pageInfo{ endCursor hasNextPage }
        }
      }
    }"""
    stars = 0
    total_count = None
    cursor = None
    while True:
        data = gql(owned_query, {"login": login, "cursor": cursor}, "repos_stars")
        repos = data["data"]["user"]["repositories"]
        total_count = repos["totalCount"]
        for e in repos["edges"]:
            stars += e["node"]["stargazers"]["totalCount"]
        if not repos["pageInfo"]["hasNextPage"]:
            break
        cursor = repos["pageInfo"]["endCursor"]
        count_query("repos_stars")

    # Contributed set (owner + collaborator + org member)
    count_query("repos_stars")
    contrib_query = """
    query($login: String!, $cursor: String){
      user(login: $login){
        repositories(first: 100, after: $cursor, ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]){
          totalCount
          pageInfo{ endCursor hasNextPage }
        }
      }
    }"""
    cursor = None
    contributed_total = None
    while True:
        data = gql(contrib_query, {"login": login, "cursor": cursor}, "repos_stars")
        repos = data["data"]["user"]["repositories"]
        contributed_total = repos["totalCount"]
        if not repos["pageInfo"]["hasNextPage"]:
            break
        cursor = repos["pageInfo"]["endCursor"]
        count_query("repos_stars")

    return total_count, stars, contributed_total

# ------------------ Heavy Scan (Commits & LOC) ------------------
def init_cache_if_needed(repos_edges: List[str]):
    if FORCE_CACHE or not os.path.exists(CACHE_FILE):
        write_cache_header()
        with open(CACHE_FILE, "a", encoding="utf-8") as f:
            for full in repos_edges:
                f.write(f"{hashlib.sha256(full.encode()).hexdigest()} 0 0 0 0\n")
        return

    # Validate repo list length
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    repo_lines = lines[CACHE_COMMENT_LINES:]
    if len(repo_lines) != len(repos_edges):
        # Rebuild
        write_cache_header()
        with open(CACHE_FILE, "a", encoding="utf-8") as f:
            for full in repos_edges:
                f.write(f"{hashlib.sha256(full.encode()).hexdigest()} 0 0 0 0\n")

def write_cache_header():
    header = [
        "Cache File for LOC / Commit Stats\n",
        "Format: sha256(repo) totalCommits myCommits additions deletions\n",
        f"User: {USER_NAME}\n",
        f"Generated: {datetime.datetime.utcnow().isoformat()}\n",
        "---\n"
    ]
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        f.writelines(header)

def collect_repo_full_names(login: str) -> List[str]:
    # Reuse repo listing for owner affiliations only (scope for heavy scan)
    query = """
    query($login: String!, $cursor: String){
      user(login: $login){
        repositories(first: 100, after: $cursor, ownerAffiliations: OWNER){
          edges{
            node{ nameWithOwner }
          }
          pageInfo{ endCursor hasNextPage }
        }
      }
    }"""
    cursor = None
    full_names = []
    while True:
        count_query("loc_list_repos")
        data = gql(query, {"login": login, "cursor": cursor}, "loc_list_repos")
        repos = data["data"]["user"]["repositories"]
        for e in repos["edges"]:
            full_names.append(e["node"]["nameWithOwner"])
        if not repos["pageInfo"]["hasNextPage"]:
            break
        cursor = repos["pageInfo"]["endCursor"]
    return full_names

def get_repo_commit_total(owner: str, repo: str) -> int:
    count_query("loc_repo_scan")
    query = """
    query($owner: String!, $repo: String!){
      repository(owner: $owner, name: $repo){
        defaultBranchRef{
          target{
            ... on Commit {
              history(first: 0){ totalCount }
            }
          }
        }
      }
    }"""
    data = gql(query, {"owner": owner, "repo": repo}, "loc_repo_scan")
    ref = data["data"]["repository"]["defaultBranchRef"]
    if not ref:
        return 0
    return ref["target"]["history"]["totalCount"]

def scan_repo_history(owner: str, repo: str) -> Tuple[int, int, int, int]:
    """Return (total_commits, my_commits, additions, deletions) for default branch."""
    total = get_repo_commit_total(owner, repo)
    my_commits = additions = deletions = 0
    if total == 0:
        return 0, 0, 0, 0
    # page through commit history
    cursor = None
    while True:
        count_query("loc_repo_scan")
        query = """
        query($owner: String!, $repo: String!, $cursor: String){
          repository(owner: $owner, name: $repo){
            defaultBranchRef{
              target{
                ... on Commit {
                  history(first: 100, after: $cursor){
                    edges{
                      node{
                        author{ user{ login } }
                        additions
                        deletions
                      }
                    }
                    pageInfo{ hasNextPage endCursor }
                  }
                }
              }
            }
          }
        }"""
        data = gql(query, {"owner": owner, "repo": repo, "cursor": cursor}, "loc_repo_scan")
        ref = data["data"]["repository"]["defaultBranchRef"]
        if not ref:
            break
        history = ref["target"]["history"]
        for edge in history["edges"]:
            node = edge["node"]
            if node["author"]["user"] and node["author"]["user"]["login"].lower() == USER_NAME.lower():
                my_commits += 1
                additions += node["additions"]
                deletions += node["deletions"]
        if not history["pageInfo"]["hasNextPage"]:
            break
        cursor = history["pageInfo"]["endCursor"]
    return total, my_commits, additions, deletions

RECURSION_GUARD = 0

def heavy_stats(login: str) -> Tuple[int, int, int, int]:
    """Return (my_commits, loc_add, loc_del, loc_net)."""
    global RECURSION_GUARD
    repos = collect_repo_full_names(login)
    init_cache_if_needed(repos)
    # load cache lines
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    repo_lines = lines[CACHE_COMMENT_LINES:]

    # If lengths mismatch after rebuild, refetch
    if len(repo_lines) != len(repos):
        init_cache_if_needed(repos)
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        repo_lines = lines[CACHE_COMMENT_LINES:]

    updated_lines = []
    total_my_commits = total_add = total_del = 0

    for i, full in enumerate(repos):
        repo_hash, prev_total, prev_my, prev_add, prev_del = repo_lines[i].split()
        expected_hash = hashlib.sha256(full.encode()).hexdigest()
        owner, repo = full.split("/")
        if repo_hash != expected_hash:
            if RECURSION_GUARD > 1:  # prevent endless recursion
                raise RuntimeError("Cache hash mismatch recursion guard triggered")
            RECURSION_GUARD += 1
            init_cache_if_needed(repos)
            return heavy_stats(login)

        current_total = get_repo_commit_total(owner, repo)
        from_cache = current_total == int(prev_total)
        if from_cache:
            # no change
            my_commits = int(prev_my)
            add_loc = int(prev_add)
            del_loc = int(prev_del)
        else:
            _, my_commits, add_loc, del_loc = scan_repo_history(owner, repo)
        if DEBUG:
            debug(f"[HEAVY] {full}: total={current_total} my_commits={my_commits} add={add_loc} del={del_loc} from_cache={from_cache}")

        updated_lines.append(f"{expected_hash} {current_total} {my_commits} {add_loc} {del_loc}\n")
        total_my_commits += my_commits
        total_add += add_loc
        total_del += del_loc

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines[:CACHE_COMMENT_LINES] + updated_lines)

    return total_my_commits, total_add, total_del, total_add - total_del

def embed_font_if_requested(svg_text: str) -> str:
    """If EMBED_FONT is enabled, replace a Google Fonts @import with inline @font-face rules.
    Supports simple single @import line containing JetBrains Mono.
    Safe no-op on failure.
    """
    if not EMBED_FONT:
        return svg_text
    # Find the @import URL
    import_match = re.search(r"@import url\(['\"]([^'\"]+)['\"]\);", svg_text)
    if not import_match:
        return svg_text
    import_url = import_match.group(1).replace("&amp;", "&")
    try:
        # We could cache CSS too, but it's small; fetch every run.
        css_resp = requests.get(import_url, timeout=20)
        if css_resp.status_code != 200:
            return svg_text
        css_text = css_resp.text
        font_faces: List[str] = []
        for m in re.finditer(r"@font-face\s*{[^}]*}", css_text):
            block = m.group(0)
            # Only keep woff2 src lines
            src_urls = re.findall(r"https://[^)]+woff2", block)
            if not src_urls:
                continue
            # Grab font-weight if present
            weight_match = re.search(r"font-weight:\s*(\d+)", block)
            weight = weight_match.group(1) if weight_match else "400"
            style_match = re.search(r"font-style:\s*(\w+)", block)
            font_style = style_match.group(1) if style_match else "normal"
            # take first woff2
            woff2_url = src_urls[0]
            cache_name = FONT_CACHE_DIR / (hashlib.sha256(woff2_url.encode()).hexdigest() + ".woff2")
            try:
                if cache_name.exists():
                    font_bytes = cache_name.read_bytes()
                else:
                    fr = requests.get(woff2_url, timeout=30)
                    if fr.status_code != 200:
                        continue
                    font_bytes = fr.content
                    cache_name.write_bytes(font_bytes)
                b64 = base64.b64encode(font_bytes).decode('ascii')
                font_faces.append(
                    f"@font-face{{font-family:'JetBrains Mono';font-style:{font_style};font-weight:{weight};src:url(data:font/woff2;base64,{b64}) format('woff2');font-display:swap;}}"
                )
            except requests.RequestException:
                continue
        if font_faces:
            replacement = "\n".join(font_faces)
            svg_text = re.sub(r"@import url\(['\"]([^'\"]+)['\"]\);", replacement, svg_text, count=1)
    except requests.RequestException:
        return svg_text
    return svg_text

def embed_logo(svg_text: str) -> str:
    """Embed logo.png as base64 data URI if present.
    Robust method: always attempt XML parse replace; regex fast-path retained for exact matches.
    """
    if not LOGO_PATH.exists():
        debug("Logo file not found; skipping embed.")
        return svg_text
    try:
        b64 = base64.b64encode(LOGO_PATH.read_bytes()).decode('ascii')
    except Exception as e:
        debug(f"Failed reading logo: {e}")
        return svg_text
    data_uri = f'data:image/png;base64,{b64}'
    # Fast path regex (href="logo.png" or href='logo.png')
    fast_svg, count = re.subn(r'href=("|\')logo\.png\1', f'href="{data_uri}"', svg_text)
    if count > 0:
        return fast_svg
    # XML parse fallback
    try:
        tree = etree.fromstring(svg_text.encode('utf-8'))
        href_keys = ['href', '{http://www.w3.org/1999/xlink}href']
        changed = False
        for img in tree.findall('.//{*}image'):
            for hk in href_keys:
                val = img.get(hk)
                if val and val.endswith('logo.png'):
                    img.set(hk, data_uri)
                    changed = True
        if changed:
            return etree.tostring(tree, encoding='unicode')
    except Exception as e:
        debug(f"Logo embed fallback parse failed: {e}")
    return svg_text

# ------------------ SVG Update ------------------
CHAR_WIDTH_PX = 9  # approximate monospaced char width at 16px JetBrains Mono
AVAILABLE_STATS_WIDTH_PX = 570  # from x=370 to near right edge (~940)
MAX_VALUE_CHAR = {
    'age_data': 48,
    'repo_data': 8,
    'contrib_data': 8,
    'star_data': 10,
    'commit_data': 12,
    'follower_data': 10,
    'loc_data': 15,
    'loc_add': 10,
    'loc_del': 10,
}

def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return '…'
    return text[:max_chars-1] + '…'

def approx_text_px(text: str) -> int:
    return len(text) * CHAR_WIDTH_PX

def build_stats_container(tree: etree._Element, stats: Dict[str, Any]):
    container = tree.find(".//*[@id='stats_container']")
    if container is None:
        return
    # Remove existing children (we rebuild consistently)
    for child in list(container):
        container.remove(child)
    x_base = int(container.get('x', '370'))
    y_base = int(container.get('y', '580'))
    v = {
        'repos': format_int(stats.get('repos', 0)),
        'contrib': format_int(stats.get('contrib', 0)),
        'stars': format_int(stats.get('stars', 0)),
        'commits': format_int(stats.get('commits', 0)),
        'followers': format_int(stats.get('followers', 0)),
    }
    heavy_has_data = 'loc_net' in stats  # actual heavy data presence
    # Always show a LOC line so it does not disappear when DO_HEAVY=0.
    if heavy_has_data:
        v.update({
            'loc_net': format_int(stats.get('loc_net', 0)),
            'loc_add': f"+{format_int(stats.get('loc_add', 0))}",
            'loc_del': f"-{format_int(stats.get('loc_del', 0))}",
        })
    else:
        # Placeholder values when heavy scan skipped
        v.update({
            'loc_net': '--',
            'loc_add': '+0',
            'loc_del': '-0'
        })
    id_map = {
        'repo_data': 'repos',
        'contrib_data': 'contrib',
        'star_data': 'stars',
        'commit_data': 'commits',
        'follower_data': 'followers',
        'loc_data': 'loc_net',
        'loc_add': 'loc_add',
        'loc_del': 'loc_del'
    }
    # Apply truncation limits
    for key_id, max_c in MAX_VALUE_CHAR.items():
        sk = id_map.get(key_id)
        if sk and sk in v:
            v[sk] = truncate_text(v[sk], max_c)
    line_defs = [
        [('Repos', 'repo_data', 'repos'), ('Contrib', 'contrib_data', 'contrib'), ('Stars', 'star_data', 'stars')],
        [('Commits', 'commit_data', 'commits'), ('Followers', 'follower_data', 'followers')],
    ]
    for line_index, segments in enumerate(line_defs):
        segment_infos = []
        total_width = 0
        for label, id_attr, key in segments:
            label_text = f"{label}: "
            value_text = v[key]
            label_px = approx_text_px(label_text)
            value_px = approx_text_px(value_text)
            seg_width = label_px + value_px
            segment_infos.append((label_text, value_text, id_attr, seg_width))
            total_width += seg_width
        gaps = len(segments) - 1
        remaining = max(0, AVAILABLE_STATS_WIDTH_PX - total_width)
        gap_px = remaining // gaps if gaps > 0 else 0
        cursor_x = x_base
        y = y_base + (line_index * 20)
        for label_text, value_text, id_attr, seg_width in segment_infos:
            etree.SubElement(container, 'tspan', x=str(cursor_x), y=str(y), **{'class': 'keyColor'}).text = label_text.rstrip()
            etree.SubElement(container, 'tspan', **{'class': 'valueColor', 'id': id_attr}).text = value_text
            cursor_x += seg_width + gap_px
    # LOC line always rendered (third line at +40)
    loc_line_y = y_base + 40
    etree.SubElement(container, 'tspan', x=str(x_base), y=str(loc_line_y), **{'class': 'keyColor'}).text = 'Lines of Code:'
    etree.SubElement(container, 'tspan', **{'class': 'valueColor', 'id': 'loc_data'}).text = v['loc_net']
    etree.SubElement(container, 'tspan', **{'class': 'valueColor'}).text = ' ('
    etree.SubElement(container, 'tspan', **{'class': 'addColor', 'id': 'loc_add'}).text = v['loc_add']
    etree.SubElement(container, 'tspan', **{'class': 'valueColor'}).text = ', '
    etree.SubElement(container, 'tspan', **{'class': 'delColor', 'id': 'loc_del'}).text = v['loc_del']
    etree.SubElement(container, 'tspan', **{'class': 'valueColor'}).text = ')'

def wrap_tagline(tree: etree._Element):
    tag = tree.find(".//*[@id='tagline']")
    if tag is None or tag.text is None:
        return
    max_w_attr = tag.get('data-max-width') or '0'
    try:
        max_w = int(max_w_attr)
    except ValueError:
        max_w = 0
    if max_w <= 0:
        return
    original = tag.text.strip()
    if not original:
        return
    # Quick width check
    if approx_text_px(original) <= max_w:
        return  # fits
    # Word wrap (greedy)
    words = original.split()
    lines: List[str] = []
    cur = ''
    for w in words:
        test = (cur + ' ' + w).strip()
        if approx_text_px(test) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    # Replace original element content with first line and create tspans for rest
    base_x = tag.get('x', '30')
    try:
        base_y = int(tag.get('y', '380'))
    except ValueError:
        base_y = 380
    # Clear existing children (if any) and set first line
    for child in list(tag):
        tag.remove(child)
    tag.text = lines[0]
    line_height = 18
    for i, line in enumerate(lines[1:], start=1):
        etree.SubElement(tag, 'tspan', x=base_x, y=str(base_y + i * line_height)).text = line

def update_svgs(stats: Dict[str, Any]):
    for svg_file in SVG_FILES:
        if not os.path.exists(svg_file):
            print(f"[WARN] {svg_file} not found; skipping.")
            continue
        with open(svg_file, 'r', encoding='utf-8') as f:
            raw_svg = f.read()
        raw_svg = embed_font_if_requested(raw_svg)
        raw_svg = embed_logo(raw_svg)
        tree = etree.fromstring(raw_svg.encode('utf-8'))

        build_stats_container(tree, stats)
        wrap_tagline(tree)
        age_el = tree.find(".//*[@id='age_data']")
        if age_el is not None:
            age_value = stats['age']
            age_value = truncate_text(age_value, MAX_VALUE_CHAR['age_data']) if 'age_data' in MAX_VALUE_CHAR else age_value
            age_el.text = age_value

        with open(svg_file, 'wb') as f:
            f.write(etree.tostring(tree, encoding='utf-8', xml_declaration=True))

# ------------------ Main ------------------
def main():
    print("Collecting stats...")
    t0 = time.time()

    user_id, created = get_user_and_created(USER_NAME)
    age_str = rel_age(BIRTHDATE)
    owned_repos, stars, contrib_repos = get_repos_and_stars(USER_NAME)
    followers = get_followers(USER_NAME)

    commits = loc_add = loc_del = loc_net = 0
    if DO_HEAVY:
        print("Running heavy scan (commits & LOC)...")
        commits, loc_add, loc_del, loc_net = heavy_stats(USER_NAME)

    stats = {
        "age": age_str,
        "repos": owned_repos,
        "stars": stars,
        "contrib": contrib_repos,
        "followers": followers,
        "commits": commits
    }
    if DO_HEAVY:
        stats.update({
            "loc_add": loc_add,
            "loc_del": loc_del,
            "loc_net": loc_net
        })

    update_svgs(stats)

    print("Done in {:.2f}s".format(time.time() - t0))
    print("GraphQL query counts:", QUERY_COUNT)

if __name__ == "__main__":
    main()
