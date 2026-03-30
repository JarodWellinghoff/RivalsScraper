"""
convert_tracker_data.py

Converts tracker.gg JSON exports into compact XML files.
- Player data (heroes, ranked) → player/
- Match data (match list, individual matches) → matches/

Applies the same cleanup philosophy as update_hero_data.py:
strip presentational bloat, image URLs, cache metadata, and
flatten stats from verbose dicts down to key:value pairs.
"""

import json
import os
import re
import sys
import glob
from json2xml import json2xml
from selenium import webdriver
from bs4 import BeautifulSoup
import time
import random


# ── URL patterns to strip ──────────────────────────────────────────────
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif")

# ── Keys to remove globally ────────────────────────────────────────────
GLOBAL_KEYS_TO_REMOVE = {
    "expiryDate",  # Cache TTL from tracker.gg — not gameplay data
    "streams",  # Always null
    "paginationType",  # Pagination metadata
    "additionalParameters",  # Always null in platformInfo
}

# ── Stat fields to keep (everything else is presentation layer) ────────
# Original stat: {displayName, displayCategory, category, metadata, value, displayValue, displayType}
# We keep value + displayValue (for human-readable time/rank strings)
STAT_KEEP_FIELDS = {"value", "displayValue"}

# ── Match metadata keys to strip ──────────────────────────────────────
MATCH_META_KEYS_TO_REMOVE = {
    "fullMatchAvailable",  # tracker.gg internal
    "fullMatchFetched",  # tracker.gg internal
}

# ── Segment metadata keys to strip ────────────────────────────────────
SEGMENT_META_KEYS_TO_REMOVE = {
    "outcome",  # Redundant with "result"
    "color",  # Presentation
}


# ── Hero Stats Cleanup ─────────────────────────────────────────────────


def clean_heroes_data(data: list) -> list:
    """Clean the heroes endpoint: list of hero stat entries."""
    cleaned = []
    for entry in data:
        hero = {}

        # Flatten type
        hero["type"] = entry.get("type", "unknown")

        # Clean attributes (heroId, season, mode, role)
        attrs = entry.get("attributes", {})
        attrs.pop("season", None)  # usually null
        if attrs:
            hero["attributes"] = attrs

        # Clean metadata — keep name and role, strip images/colors
        meta = entry.get("metadata", {})
        meta = strip_image_urls(meta)
        meta.pop("color", None)
        if meta:
            hero["metadata"] = meta

        # Flatten stats
        stats = entry.get("stats", {})
        hero["stats"] = flatten_stats(stats)

        cleaned.append(hero)
    return cleaned


# ── Ranked Data Cleanup ────────────────────────────────────────────────


def clean_ranked_data(data: dict) -> dict:
    """Clean the ranked endpoint: history + leaderboard."""
    cleaned = {}

    history = data.get("history", {})
    if history:
        hist_data = history.get("data", [])
        cleaned_history = []
        for entry in hist_data:
            if isinstance(entry, list) and len(entry) == 2:
                timestamp, rank_data = entry
                rank_info = {}
                rank_info["timestamp"] = timestamp

                # Extract rank metadata — keep name/shortName, strip images
                meta = rank_data.get("metadata", {})
                meta = strip_image_urls(meta)
                meta.pop("color", None)
                meta.pop("name", None)  # Redundant with rank field below
                meta.pop("shortName", None)  # Redundant with rank field below
                rank_info.update(meta)

                # Extract value [rank_name, rating_points]
                val = rank_data.get("value", [])
                if isinstance(val, list) and len(val) == 2:
                    rank_info["rank"] = val[0]
                    rank_info["rating"] = val[1]

                cleaned_history.append(rank_info)
        cleaned["history"] = cleaned_history

    return cleaned


# ── Match List Cleanup ─────────────────────────────────────────────────


def clean_matches_list(data: dict) -> dict:
    """Clean the matches list endpoint."""
    cleaned = {}

    # Keep requesting player ID
    rpa = data.get("requestingPlayerAttributes", {})
    if rpa:
        cleaned["player"] = rpa

    # Clean each match summary
    matches = data.get("matches", [])
    cleaned_matches = []
    for match in matches:
        cleaned_matches.append(clean_match_entry(match))
    cleaned["matches"] = cleaned_matches

    return cleaned


def clean_match_entry(match: dict) -> dict:
    """Clean a single match entry (from match list or individual)."""
    cleaned = {}

    # Attributes (id, mode, mapId)
    attrs = match.get("attributes", {})
    if attrs:
        cleaned["attributes"] = attrs

    # Metadata — strip images and internal flags
    meta = dict(match.get("metadata", {}))
    meta = strip_image_urls(meta)
    for key in MATCH_META_KEYS_TO_REMOVE:
        meta.pop(key, None)
    if meta:
        cleaned["metadata"] = meta

    # Segments
    segments = match.get("segments", [])
    if segments:
        cleaned["segments"] = [clean_segment(s) for s in segments]

    return cleaned


# ── Individual Match Cleanup ───────────────────────────────────────────


def clean_individual_match(data: dict) -> dict:
    """Clean a full individual match detail."""
    return clean_match_entry(data)


# ── Segment Cleanup ────────────────────────────────────────────────────


def clean_segment(segment: dict) -> dict:
    """Clean a match segment (player overview or hero-specific)."""
    cleaned = {}

    cleaned["type"] = segment.get("type", "unknown")

    # Attributes
    attrs = segment.get("attributes", {})
    if attrs:
        cleaned["attributes"] = attrs

    # Metadata — strip images, avatars, redundant fields
    meta = dict(segment.get("metadata", {}))
    meta = strip_image_urls(meta)
    for key in SEGMENT_META_KEYS_TO_REMOVE:
        meta.pop(key, None)

    # Clean platformInfo — keep handle, strip nulls and avatar
    if "platformInfo" in meta:
        pi = meta["platformInfo"]
        meta["platformInfo"] = {
            k: v
            for k, v in pi.items()
            if v is not None and not (isinstance(v, str) and _is_image_url(v))
        }
        # If only platformUserHandle remains useful, flatten it
        pi_clean = meta["platformInfo"]
        if set(pi_clean.keys()) <= {
            "platformSlug",
            "platformUserHandle",
            "platformUserIdentifier",
        }:
            meta["playerName"] = pi_clean.get(
                "platformUserHandle", pi_clean.get("platformUserIdentifier", "")
            )
            meta["platform"] = pi_clean.get("platformSlug", "")
            del meta["platformInfo"]

    # Clean heroes list in metadata — strip image URLs
    if "heroes" in meta and isinstance(meta["heroes"], list):
        cleaned_heroes = []
        for h in meta["heroes"]:
            ch = strip_image_urls(dict(h))
            cleaned_heroes.append(ch)
        meta["heroes"] = cleaned_heroes

    if meta:
        cleaned["metadata"] = meta

    # Flatten stats
    stats = segment.get("stats", {})
    if stats:
        cleaned["stats"] = flatten_stats(stats)

    return cleaned


# ── Stats Flattening ───────────────────────────────────────────────────


def flatten_stats(stats: dict) -> dict:
    """Flatten verbose stat dicts to {key: value} or {key: {value, displayValue}}.

    If displayValue adds info beyond the raw value (e.g. "420h" vs 1513703),
    we keep both. Otherwise just the value.
    """
    flat = {}
    for key, stat in stats.items():
        if not isinstance(stat, dict):
            flat[key] = stat
            continue

        value = stat.get("value")
        display = stat.get("displayValue")

        # If value is a complex object (list/dict), keep it as-is but cleaned
        if isinstance(value, (list, dict)):
            flat[key] = strip_image_urls_recursive(value)
        elif display and display != str(value):
            flat[key] = {"value": value, "display": display}
        else:
            flat[key] = value

    return flat


# ── Image URL Stripping ────────────────────────────────────────────────


def _is_image_url(v) -> bool:
    """Check if a value is an image URL string."""
    if not isinstance(v, str):
        return False
    return any(
        v.lower().endswith(ext) or (ext + "?") in v.lower() for ext in IMAGE_EXTENSIONS
    )


def strip_image_urls(d: dict) -> dict:
    """Remove key-value pairs where the value is an image URL."""
    return {k: v for k, v in d.items() if not _is_image_url(v)}


def strip_image_urls_recursive(obj):
    """Recursively strip image URL values from nested structures."""
    if isinstance(obj, dict):
        return {
            k: strip_image_urls_recursive(v)
            for k, v in obj.items()
            if not _is_image_url(v)
        }
    elif isinstance(obj, list):
        return [strip_image_urls_recursive(item) for item in obj]
    return obj


# ── Global Key Stripping ──────────────────────────────────────────────


def strip_global_keys(obj):
    """Recursively remove keys in GLOBAL_KEYS_TO_REMOVE."""
    if isinstance(obj, dict):
        return {
            k: strip_global_keys(v)
            for k, v in obj.items()
            if k not in GLOBAL_KEYS_TO_REMOVE
        }
    elif isinstance(obj, list):
        return [strip_global_keys(item) for item in obj]
    return obj


# ── XML Writing ────────────────────────────────────────────────────────


def write_xml(data, filepath: str, root_tag: str = "all"):
    """Convert cleaned data to XML and write to file."""
    # Strip global noise keys before conversion
    data = strip_global_keys(data)

    # Rename keys that dicttoxml mangles before conversion:
    #   "name"   becomes <n>  in dicttoxml output
    #   "result" becomes <r>  in dicttoxml output
    data = fix_mangled_keys(data)

    xml_output = json2xml.Json2xml(data, attr_type=False, wrapper=root_tag).to_xml()
    # Safety net: strip any leftover type attributes
    xml_output = re.sub(r' type="[^"]*"', "", xml_output)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(xml_output)


# -- dicttoxml Key Mangling Fix --
# dicttoxml shortens certain common key names:
#   "name"   -> "n"
#   "result" -> "r"
# We rename keys before conversion to avoid this.

MANGLED_KEY_MAP = {
    "name": "hero_name",
    "result": "match_result",
}


def fix_mangled_keys(obj):
    """Recursively rename keys that dicttoxml would mangle."""
    if isinstance(obj, dict):
        return {MANGLED_KEY_MAP.get(k, k): fix_mangled_keys(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [fix_mangled_keys(item) for item in obj]
    return obj


# ── Main ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    player = "Expired Milk"
    player = "Skellinghoff"
    matches_url = (
        f"https://api.tracker.gg/api/v2/marvel-rivals/standard/matches/ign/{player}"
    )
    heroes_url = f"https://api.tracker.gg/api/v2/marvel-rivals/standard/profile/ign/{player}/segments/career?mode=all"
    ranked_url = f"https://api.tracker.gg/api/v2/marvel-rivals/standard/profile/ign/{player}/stats/overview/ranked"
    urls = [matches_url, heroes_url, ranked_url]
    url_names = ["matches", "heroes", "ranked"]
    input_dir = "."
    output_base = "."
    player_dir = os.path.join(output_base, "player", player)
    match_dir = os.path.join(output_base, "matches")
    os.makedirs(player_dir, exist_ok=True)
    os.makedirs(match_dir, exist_ok=True)

    # files = sorted(glob.glob(os.path.join(input_dir, "data_*.json")))
    # if not files:
    #     print(f"No data_*.json files found in {input_dir}")
    #     sys.exit(1)
    chrome_options = webdriver.ChromeOptions()
    # set a headless driver
    chrome_options.add_argument("--headless")
    # set the user-agent back to chrome.
    user_agent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/60.0.3112.50 Safari/537.36"
    chrome_options.add_argument(f"user-agent={user_agent}")
    driver = webdriver.Chrome(options=chrome_options)
    driver.set_window_size(1080, 800)  # set the size of the window
    for url, name in zip(urls, url_names):
        print(f"Fetching {url}...")
        driver.get(url)
        time.sleep(random.uniform(1, 10))
        soup = BeautifulSoup(driver.page_source, "html.parser")
        json_data = json.loads(soup.find("pre").text)

        data = json_data.get("data", json_data)

        if name == "heroes":
            converted = clean_heroes_data(data)
            write_xml(
                converted,
                os.path.join(player_dir, "heroes.xml"),
                root_tag="heroes",
            )
            print(f"  [PLAYER] {player} -> player/{player}/heroes.xml")

        elif name == "ranked":
            converted = clean_ranked_data(data)
            write_xml(
                converted,
                os.path.join(player_dir, "ranked.xml"),
                root_tag="ranked",
            )
            print(f"  [PLAYER] {player} -> player/{player}/ranked.xml")

        elif name == "matches":
            converted = clean_matches_list(data)
            write_xml(
                converted,
                os.path.join(player_dir, "match_history.xml"),
                root_tag="match_history",
            )
            print(f"  [MATCH]  {player} -> player/{player}/match_history.xml")
            matches = json_data["data"]["matches"]
            match_ids = [match["attributes"]["id"] for match in matches]
            for match_id in match_ids:
                filepath = os.path.join(match_dir, f"{match_id}.xml")
                if os.path.exists(filepath):
                    print(f"    [SKIP]   {match_id} (already exists)")
                    continue
                match_url = f"https://api.tracker.gg/api/v2/marvel-rivals/standard/matches/{match_id}"
                print(f"    Fetching match {match_id}...")
                driver.get(match_url)
                time.sleep(random.uniform(1, 10))
                soup = BeautifulSoup(driver.page_source, "html.parser")
                match_data = json.loads(soup.find("pre").text)
                converted_match = clean_individual_match(match_data.get("data", {}))
                write_xml(
                    converted_match,
                    filepath,
                    root_tag="match",
                )
                print(f"      [MATCH] {match_id} -> matches/{match_id}.xml")

        else:
            print(f"  [SKIP]   {name} (unrecognized pattern)")

    print("Done.")
