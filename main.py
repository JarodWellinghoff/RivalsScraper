from selenium import webdriver
from bs4 import BeautifulSoup
import json
import time
import random

player = "Skellinghoff"
matches_url = (
    f"https://api.tracker.gg/api/v2/marvel-rivals/standard/matches/ign/{player}"
)
heroes_url = f"https://api.tracker.gg/api/v2/marvel-rivals/standard/profile/ign/{player}/segments/career?mode=all"
ranked_url = f"https://api.tracker.gg/api/v2/marvel-rivals/standard/profile/ign/{player}/stats/overview/ranked"
urls = [matches_url, heroes_url, ranked_url]
url_names = ["matches", "heroes", "ranked"]


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
    time.sleep(random.uniform(1, 3))
    soup = BeautifulSoup(driver.page_source, "html.parser")
    json_data = json.loads(soup.find("pre").text)
    with open(f"data_{name}.json", "w") as f:
        json.dump(json_data, f, indent=2)

driver.get(matches_url)
soup = BeautifulSoup(driver.page_source, "html.parser")
json_data = json.loads(soup.find("pre").text)
with open("data.json", "w") as f:
    json.dump(json_data, f)
matches = json_data["data"]["matches"]


def sv(stats, key):
    v = stats.get(key, {}).get("value", None)
    return v if v is not None else 0


results = []
for i, m in enumerate(matches):
    meta = m["metadata"]
    seg = m["segments"][0]
    smeta = seg["metadata"]
    stats = seg["stats"]

    heroes = [h["name"] for h in smeta.get("heroes", [])]
    hero_ids = [h["heroId"] for h in smeta.get("heroes", [])]

    kills = sv(stats, "kills")
    deaths = sv(stats, "deaths")
    assists = sv(stats, "assists")
    kda = sv(stats, "kdaRatio")
    damage = sv(stats, "totalHeroDamage")
    dmg_min = sv(stats, "totalHeroDamagePerMinute")
    healing = sv(stats, "totalHeroHeal")
    heal_min = sv(stats, "totalHeroHealPerMinute")
    time_played = stats.get("timePlayed", {}).get("displayValue", "N/A")
    time_ms = sv(stats, "timePlayed")

    main_attacks = sv(stats, "mainAttacks")
    main_hits = sv(stats, "mainAttackHits")
    acc = (main_hits / main_attacks * 100) if main_attacks and main_attacks > 0 else 0

    results.append(
        {
            "idx": i + 1,
            "heroes": heroes,
            "result": smeta.get("result", "unknown"),
            "mvp": smeta.get("isMvp", False),
            "svp": smeta.get("isSvp", False),
            "kills": kills,
            "deaths": deaths,
            "assists": assists,
            "kda": kda,
            "damage": round(damage),
            "dmg_min": round(dmg_min),
            "healing": round(healing),
            "heal_min": round(heal_min),
            "time": time_played,
            "time_ms": time_ms,
            "acc": round(acc, 1),
            "map": meta.get("mapName", "?"),
            "mode": meta.get("mapModeName", "?"),
            "timestamp": meta.get("timestamp", ""),
            "duration": meta.get("duration", 0),
            "main_attacks": main_attacks,
            "main_hits": main_hits,
        }
    )

print(
    f"{'#':<3} {'W/L':<4} {'Hero':<25} {'K/D/A':<10} {'KDA':<6} {'DMG':<7} {'D/m':<6} {'Heal':<6} {'Acc%':<6} {'Time':<8} {'Map':<18} {'Mode':<14} {'Note'}"
)
print("-" * 140)

wins = losses = total_k = total_d = total_a = total_dmg = total_heal = total_time = 0
hero_data = {}

for r in results:
    hs = ", ".join(r["heroes"])[:24]
    kda_s = f"{r['kills']}/{r['deaths']}/{r['assists']}"
    note = "MVP" if r["mvp"] else ("SVP" if r["svp"] else "")

    try:
        print(
            f"{r['idx']:<3} {'W' if r['result']=='win' else 'L':<4} {hs:<25} {kda_s:<10} {r['kda']:<6.2f} {r['damage']:<7} {r['dmg_min']:<6} {r['healing']:<6} {r['acc']:<6} {r['time']:<8} {r['map']:<18} {r['mode']:<14} {note}"
        )
    except Exception as e:
        continue

    if r["result"] == "win":
        wins += 1
    else:
        losses += 1
    total_k += r["kills"]
    total_d += r["deaths"]
    total_a += r["assists"]
    total_dmg += r["damage"]
    total_heal += r["healing"]
    total_time += r["time_ms"]

    for h in r["heroes"]:
        if h not in hero_data:
            hero_data[h] = {
                "games": 0,
                "wins": 0,
                "k": 0,
                "d": 0,
                "a": 0,
                "dmg": 0,
                "heal": 0,
                "time": 0,
                "attacks": 0,
                "hits": 0,
            }
        hd = hero_data[h]
        hd["games"] += 1
        if r["result"] == "win":
            hd["wins"] += 1
        hd["k"] += r["kills"]
        hd["d"] += r["deaths"]
        hd["a"] += r["assists"]
        hd["dmg"] += r["damage"]
        hd["heal"] += r["healing"]
        hd["time"] += r["time_ms"]
        hd["attacks"] += r["main_attacks"]
        hd["hits"] += r["main_hits"]

total_min = total_time / 60000
print(f"\n=== AGGREGATE ===")
print(f"Record: {wins}W-{losses}L ({wins/(wins+losses)*100:.1f}% WR)")
print(f"K/D/A Total: {total_k}/{total_d}/{total_a}")
print(f"Overall KDA: {(total_k+total_a)/max(total_d,1):.2f}")
print(f"Total Damage: {total_dmg:,} | Avg DMG/Min: {total_dmg/total_min:.0f}")
print(f"Total Healing: {total_heal:,}")
print(f"Total Playtime: {total_min:.1f} min")

print(f"\n=== HERO BREAKDOWN ===")
for h, d in sorted(hero_data.items(), key=lambda x: -x[1]["games"]):
    mins = d["time"] / 60000
    acc = (d["hits"] / d["attacks"] * 100) if d["attacks"] > 0 else 0
    avg_kda = (d["k"] + d["a"]) / max(d["d"], 1)
    print(
        f"  {h}: {d['games']}G {d['wins']}W ({d['wins']/d['games']*100:.0f}%WR) | K/D/A: {d['k']}/{d['d']}/{d['a']} (KDA {avg_kda:.2f}) | DMG: {d['dmg']:,} ({d['dmg']/mins:.0f}/min) | Heal: {d['heal']:,} | Acc: {acc:.1f}%"
    )

# Win/Loss streaks
print(f"\n=== WIN/LOSS PATTERN ===")
pattern = ["W" if r["result"] == "win" else "L" for r in results]
print(f"  Recent -> Old: {' '.join(pattern)}")

# Map analysis
print(f"\n=== MAP ANALYSIS ===")
map_data = {}
for r in results:
    mk = f"{r['map']} ({r['mode']})"
    if mk not in map_data:
        map_data[mk] = {"games": 0, "wins": 0}
    map_data[mk]["games"] += 1
    if r["result"] == "win":
        map_data[mk]["wins"] += 1
for mk, d in sorted(map_data.items(), key=lambda x: -x[1]["games"]):
    print(f"  {mk}: {d['games']}G {d['wins']}W ({d['wins']/d['games']*100:.0f}%WR)")

# Deaths analysis - high death games
print(f"\n=== HIGH DEATH GAMES (6+ deaths) ===")
for r in results:
    if r["deaths"] >= 6:
        hs = ", ".join(r["heroes"])[:24]
        print(
            f"  Match {r['idx']}: {r['deaths']} deaths as {hs} on {r['map']} ({r['mode']}) - {'W' if r['result']=='win' else 'L'} - KDA {r['kda']:.2f} - {r['time']}"
        )
