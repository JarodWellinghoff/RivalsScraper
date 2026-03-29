import os
import re
from json2xml import json2xml
from selenium import webdriver
from bs4 import BeautifulSoup
import json


# ── Ability-level filters ──────────────────────────────────────────────
# Abilities whose *only* additional_fields keys are health/movement_speed
# duplicates of the transformations block. These are ghost stat-blocks
# injected by the API that carry zero gameplay information.
STAT_BLOCK_KEYS = {"health", "movement_speed"}

# Internal dev/debug descriptions that slipped through the API.
DEV_NOTE_PATTERNS = [
    "Logic Management Abilities",
    "Not displayed on F1",
    "Not shown in F1",
    "no localization needed",
    "no description is required",
]

# Trivial sub-ability names with no description — reload animations,
# melee fallback attacks, weapon-swap stubs. These add noise without
# providing any analytical value.
TRIVIAL_ABILITY_NAMES = {
    "reload",
    "melee normal attack",
    "switch arrows",
}

# ── Hero-level field removals ──────────────────────────────────────────
# Fields removed from every hero dict before XML conversion.
HERO_FIELDS_TO_REMOVE = {"en_name", "slug"}


def update_hero_data():
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--headless")
    user_agent = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/60.0.3112.50 Safari/537.36"
    )
    chrome_options.add_argument(f"user-agent={user_agent}")
    url = "https://api.dotgg.gg/cgfw/getgacha?game=rivals&type=heroes"
    driver = webdriver.Chrome(options=chrome_options)
    driver.set_window_size(1080, 800)
    driver.get(url)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    data = json.loads(soup.find("pre").text)

    data = clean_json(data)

    # ── Extract slugs for filenames, then strip _slug from data ──────
    slugs = []
    for hero_data in data:
        slugs.append(hero_data.pop("_slug", hero_data.get("name", "unknown")))

    # ── Write individual hero files ────────────────────────────────────
    os.makedirs("heroes", exist_ok=True)
    for hero_data, slug in zip(data, slugs):
        slug = slug.lower().replace(" ", "-")
        file_name = f"heroes/{slug}.xml"
        xml_output = json2xml.Json2xml(hero_data, attr_type=False).to_xml()
        xml_output = strip_xml_noise(xml_output)
        with open(file_name, "w", encoding="utf-8") as f:
            f.write(xml_output)

    print(f"Wrote {len(data)} hero files to heroes/")


def clean_json(obj):
    """Clean the raw API response at the top level (list of hero dicts)."""
    if isinstance(obj, list):
        for hero in obj:
            clean_hero(hero)
    return obj


def clean_hero(hero: dict):
    """Apply all hero-level and ability-level cleanup to a single hero dict."""

    # ── Strip skins and image URLs (original behavior) ─────────────────
    _strip_skins_and_images(hero)

    # ── Strip parentheses from keys (original behavior) ────────────────
    _strip_parens_from_keys(hero)

    # ── Preserve slug for filename before removing it ──────────────────
    if "slug" in hero:
        hero["_slug"] = hero["slug"]

    # ── Remove redundant hero-level fields ─────────────────────────────
    for field in HERO_FIELDS_TO_REMOVE:
        hero.pop(field, None)

    # ── Deduplicate transformation names ───────────────────────────────
    # When a hero has multiple transformations with the same name,
    # it's impossible to tell them apart. This isn't removed, but we
    # could add a suffix here in the future if needed.

    # ── Clean abilities ────────────────────────────────────────────────
    if "abilities" in hero and isinstance(hero["abilities"], list):
        hero["abilities"] = [
            _clean_ability(a) for a in hero["abilities"] if _should_keep_ability(a)
        ]


def _clean_ability(ability: dict) -> dict:
    """Remove noise fields from a single ability dict."""
    # Remove isCollab — unreliable, almost always false
    ability.pop("isCollab", None)
    return ability


def _should_keep_ability(ability: dict) -> bool:
    """Return False for abilities that should be stripped entirely."""
    name = (ability.get("name") or "").strip()
    desc = (ability.get("description") or "").strip()
    additional = ability.get("additional_fields", {})
    if not isinstance(additional, dict):
        additional = {}

    # ── Ghost stat-blocks: no name, additional_fields only has Health/Movement_Speed
    if not name:
        af_keys = {k.lower().replace(" ", "_") for k in additional.keys()}
        if af_keys <= STAT_BLOCK_KEYS:
            return False

    # ── Internal dev notes ─────────────────────────────────────────────
    if any(pattern in desc for pattern in DEV_NOTE_PATTERNS):
        return False

    # ── Non-ASCII ability names (untranslated Chinese strings) ─────────
    if name and all(ord(c) > 127 or c in " -_" for c in name):
        return False

    # ── Trivial sub-abilities: known junk names with no description ────
    if name.lower().rstrip(" -_0123456789") in TRIVIAL_ABILITY_NAMES and not desc:
        # Also catch variants like "Squirrel Girl Reload", "Moon Knight Reload"
        return False
    # Broader pattern: if the name ends with a known trivial suffix and has no description
    name_lower = name.lower()
    if not desc:
        for trivial in TRIVIAL_ABILITY_NAMES:
            if name_lower.endswith(trivial):
                return False

    return True


def _strip_skins_and_images(obj):
    """Remove 'skins' keys and any string value ending in .webp (recursive)."""
    if isinstance(obj, dict):
        keys_to_delete = [
            k
            for k, v in obj.items()
            if k == "skins" or (isinstance(v, str) and v.endswith(".webp"))
        ]
        for k in keys_to_delete:
            del obj[k]
        for v in obj.values():
            _strip_skins_and_images(v)
    elif isinstance(obj, list):
        for item in obj:
            _strip_skins_and_images(item)


def _strip_parens_from_keys(obj):
    """Strip parentheses from dictionary keys (recursive)."""
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            new_key = k.replace("(", "").replace(")", "")
            if new_key != k:
                obj[new_key] = obj.pop(k)
        for v in obj.values():
            _strip_parens_from_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            _strip_parens_from_keys(item)


def strip_xml_noise(xml_string: str) -> str:
    """Post-process XML string to remove remaining json2xml artifacts.

    Even with attr_type=False, json2xml may leave empty-looking tags
    or other formatting quirks. This does a light cleanup pass.
    """
    # Remove any leftover type="" attributes if json2xml version regresses
    xml_string = re.sub(r' type="[^"]*"', "", xml_string)
    return xml_string


if __name__ == "__main__":
    update_hero_data()
