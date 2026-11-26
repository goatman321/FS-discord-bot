"""Denní přehled free spinů z vyhraj.cz + dnešní Klikař dne z apollogames.cz -> Discord webhook."""

import os
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK")
DRY_RUN = os.environ.get("DRY_RUN") == "1"

FS_URL = "https://vyhraj.cz/rubrika/freespiny-bonusy-dnes/"
KLIKAR_URL = "https://www.apollogames.cz/klikar"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "Accept-Language": "cs-CZ,cs;q=0.9",
}

TZ = ZoneInfo("Europe/Prague")
TARGET_HOUR = 13  # místní (pražský) čas odeslání

# Štítky, které se mají ve výpisu výrazně zvýraznit
HIGHLIGHT_TAGS = ["Bez podmínek", "Bez vkladu", "Pouze za vklad"]

COLOR_HEADER = 0x5865F2   # Discord modrá
COLOR_HIGHLIGHT = 0x00FF66  # výrazná zelená
COLOR_OTHER = 0x4F545C    # šedá
COLOR_KLIKAR = 0xFFC400   # zlatá

# Limity Discordu
MAX_DESC = 4000           # max 4096 znaků v popisu embedu
MAX_EMBEDS_PER_MSG = 10
MAX_CHARS_PER_MSG = 5800  # max 6000 znaků ve všech embedech zprávy


# --------------------------------------------------------------------------- #
# Plánování (GitHub cron běží v UTC, hlídáme letní/zimní čas)
# --------------------------------------------------------------------------- #
def should_run_now() -> bool:
    """Workflow má dva crony (11:00 a 12:00 UTC). Pustíme jen ten, který
    odpovídá 13:00 pražského času – podle toho, jestli je letní nebo zimní čas."""
    schedule = os.environ.get("GITHUB_SCHEDULE", "").strip()
    if not schedule:  # ruční spuštění / lokální běh
        return True
    cron_hour = int(schedule.split()[1])
    offset_h = int(datetime.now(TZ).utcoffset().total_seconds() // 3600)
    expected_utc_hour = (TARGET_HOUR - offset_h) % 24
    if cron_hour != expected_utc_hour:
        print(f"Cron '{schedule}' neodpovídá {TARGET_HOUR}:00 pražského času "
              f"(UTC+{offset_h}), přeskakuji.")
        return False
    return True


# --------------------------------------------------------------------------- #
# Scraping
# --------------------------------------------------------------------------- #
def fetch(url: str) -> BeautifulSoup:
    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return BeautifulSoup(r.text, "html.parser")
        except requests.RequestException as e:
            last_err = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Nepodařilo se stáhnout {url}: {last_err}")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def scrape_free_spins() -> list[dict]:
    soup = fetch(FS_URL)
    # První seznam s denními bonusy = hlavní "Free spiny dnes"
    # (další seznamy na stránce jsou "Další akce" a "Za registraci").
    bonus_list = soup.select_one("div.bonus-list--daily")
    if not bonus_list:
        raise RuntimeError("Na vyhraj.cz nebyl nalezen seznam bonusů (změnila se struktura stránky?)")

    bonuses = []
    for item in bonus_list.select("div.bonus-item--daily"):
        name_el = item.select_one("h3.bonus-item-name .bonus-item-name--primary > span")
        if not name_el:
            continue
        title = clean(name_el.get_text(" "))

        img = item.select_one(".bonus-item-head-image img")
        casino = clean(img.get("alt")) if img else ""

        link_el = item.select_one("h3.bonus-item-name a[href]")
        link = link_el["href"] if link_el else FS_URL

        valid_el = item.select_one(".bonus-item-toolbar-item")
        valid = clean(valid_el.get_text()) if valid_el else ""

        tags = []
        for tag_el in item.select(".tag-list .tag"):
            t = clean(tag_el.get_text())
            if t and t not in tags:
                tags.append(t)

        highlight = [t for t in tags if t in HIGHLIGHT_TAGS]
        bonuses.append({
            "title": title,
            "casino": casino,
            "link": link,
            "valid": valid,
            "tags": tags,
            "highlight": highlight,
            "spins": int(item.get("data-spins") or 0),
        })

    if not bonuses:
        raise RuntimeError("Seznam bonusů na vyhraj.cz je prázdný (změnila se struktura stránky?)")
    return bonuses


def scrape_klikar() -> dict:
    soup = fetch(KLIKAR_URL)
    box = soup.select_one('app-widget-snippet[snippetname="content/luckyday/lucky_day_actual"]')
    if not box or not box.select_one("h2"):
        raise RuntimeError("Na apollogames.cz nebyl nalezen Klikař dne (změnila se struktura stránky?)")

    headline = clean(box.select_one("h2").get_text(" "))   # "c***hmanek dnes Může získat 800 FS"
    detail_el = box.select_one("p")
    detail = clean(detail_el.get_text(" ")) if detail_el else ""

    m_name = re.match(r"(.+?)\s+dnes\b", headline, re.I)
    m_fs = re.search(r"(\d[\d\s]*)\s*FS", headline)
    return {
        "name": m_name.group(1) if m_name else headline,
        "fs": m_fs.group(1).replace(" ", "") if m_fs else None,
        "headline": headline,
        "detail": detail,
    }


# --------------------------------------------------------------------------- #
# Discord
# --------------------------------------------------------------------------- #
def format_bonus(b: dict) -> str:
    casino = f"**{b['casino']}** – " if b["casino"] else ""
    if b["highlight"]:
        tags = " · ".join(f"**{t}**" for t in b["highlight"])
        line = f"🟢 {casino}[{b['title']}]({b['link']})\n┗ ✅ {tags}"
    else:
        tags = " · ".join(b["tags"][:2])
        line = f"▫️ {casino}[{b['title']}]({b['link']})"
        if tags:
            line += f"\n┗ {tags}"
    if b["valid"]:
        line += f" · ⏰ {b['valid']}"
    return line


def build_list_embeds(lines: list[str], title: str, color: int) -> list[dict]:
    embeds, chunk = [], ""
    for line in lines:
        if chunk and len(chunk) + len(line) + 2 > MAX_DESC:
            embeds.append(chunk)
            chunk = ""
        chunk += ("\n\n" if chunk else "") + line
    if chunk:
        embeds.append(chunk)
    total = len(embeds)
    return [{
        "title": title if total == 1 else f"{title} ({i}/{total})",
        "description": desc,
        "color": color,
    } for i, desc in enumerate(embeds, 1)]


def build_embeds(bonuses, klikar, errors) -> list[dict]:
    today = datetime.now(TZ)
    date_str = f"{today.day}. {today.month}. {today.year}"
    embeds = []

    if bonuses is not None:
        hl = [b for b in bonuses if b["highlight"]]
        other = [b for b in bonuses if not b["highlight"]]
        total_fs = sum(b["spins"] for b in bonuses)
        embeds.append({
            "title": f"🎰 Free spiny dnes – {date_str}",
            "url": FS_URL,
            "description": (
                f"Dnes je na vyhraj.cz **{len(bonuses)} bonusů** (celkem **{total_fs} FS**).\n"
                f"🟢 **{len(hl)}** z nich je *{' / '.join(HIGHLIGHT_TAGS)}* – jsou nahoře zeleně."
            ),
            "color": COLOR_HEADER,
        })

    if klikar is not None:
        desc = f"👤 **{klikar['name']}**"
        if klikar["fs"]:
            desc += f"\n🎁 Může získat **{klikar['fs']} FS**"
        if klikar["detail"]:
            desc += f"\n\n{klikar['detail']}"
        embeds.append({
            "title": "🏆 Apollo Games – Klikař dne",
            "url": KLIKAR_URL,
            "description": desc,
            "color": COLOR_KLIKAR,
        })

    if bonuses is not None:
        if hl:
            embeds += build_list_embeds([format_bonus(b) for b in hl],
                                        "🟢 " + " · ".join(HIGHLIGHT_TAGS), COLOR_HIGHLIGHT)
        if other:
            embeds += build_list_embeds([format_bonus(b) for b in other],
                                        "Ostatní free spiny a bonusy", COLOR_OTHER)

    for err in errors:
        embeds.append({"title": "⚠️ Chyba", "description": err[:MAX_DESC], "color": 0xFF0000})
    return embeds


def embed_size(e: dict) -> int:
    return len(e.get("title", "")) + len(e.get("description", ""))


def pack_messages(embeds: list[dict]) -> list[list[dict]]:
    messages, cur, cur_len = [], [], 0
    for e in embeds:
        size = embed_size(e)
        if cur and (len(cur) >= MAX_EMBEDS_PER_MSG or cur_len + size > MAX_CHARS_PER_MSG):
            messages.append(cur)
            cur, cur_len = [], 0
        cur.append(e)
        cur_len += size
    if cur:
        messages.append(cur)
    return messages


def send(messages: list[list[dict]]) -> None:
    for i, msg in enumerate(messages, 1):
        payload = {"embeds": msg}
        if DRY_RUN:
            print(f"--- zpráva {i}/{len(messages)} ({sum(map(embed_size, msg))} znaků) ---")
            for e in msg:
                print(f"## {e['title']}\n{e['description']}\n")
            continue
        for _ in range(5):
            r = requests.post(WEBHOOK_URL, json=payload, params={"wait": "true"}, timeout=30)
            if r.status_code == 429:  # rate limit
                time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
                continue
            break
        if r.status_code not in (200, 204):
            raise RuntimeError(f"Discord vrátil {r.status_code}: {r.text[:500]}")
        print(f"Odeslána zpráva {i}/{len(messages)}")
        time.sleep(1)


def main() -> int:
    if not DRY_RUN and not WEBHOOK_URL:
        print("Chyba: proměnná DISCORD_WEBHOOK není nastavena.")
        return 1
    if not should_run_now():
        return 0

    errors = []
    bonuses = klikar = None
    try:
        bonuses = scrape_free_spins()
        print(f"vyhraj.cz: {len(bonuses)} bonusů, "
              f"{sum(1 for b in bonuses if b['highlight'])} zvýrazněných")
    except Exception as e:
        errors.append(f"Free spiny ({FS_URL}): {e}")
    try:
        klikar = scrape_klikar()
        print(f"Klikař dne: {klikar['headline']}")
    except Exception as e:
        errors.append(f"Klikař dne ({KLIKAR_URL}): {e}")

    for err in errors:
        print("Chyba:", err)

    send(pack_messages(build_embeds(bonuses, klikar, errors)))
    # Pokud selhal scraping, ať je běh v GitHub Actions označen červeně.
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
