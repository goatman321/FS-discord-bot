"""Denní přehled free spinů z vyhraj.cz + dnešní Klikař dne z apollogames.cz -> Discord webhook."""

import json
import os
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

WEBHOOK_URL = (os.environ.get("DISCORD_WEBHOOK") or "").strip()
DRY_RUN = os.environ.get("DRY_RUN") == "1"

FS_URL = "https://vyhraj.cz/rubrika/freespiny-bonusy-dnes/"
KLIKAR_URL = "https://www.apollogames.cz/klikar"

# Soubor s ID zpráv, které bot poslal minule (aby je mohl smazat).
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sent_messages.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "Accept-Language": "cs-CZ,cs;q=0.9",
}

TZ = ZoneInfo("Europe/Prague")
TARGET_HOUR = 13  # místní (pražský) čas odeslání

# Štítky, které se mají ve výpisu výrazně zvýraznit
HIGHLIGHT_TAGS = ["Bez podmínek", "Bez vkladu", "Pouze za vklad"]

COLOR_HEADER = 0x5865F2     # Discord modrá
COLOR_HIGHLIGHT = 0x00FF66  # výrazná zelená
COLOR_OTHER = 0x4F545C      # šedá
COLOR_KLIKAR = 0xFFC400     # zlatá

# Limity Discordu
MAX_DESC = 4000           # max 4096 znaků v popisu embedu
CHUNK_DESC = 2850         # cílová velikost bloku – ať se 2 bloky vejdou do jedné zprávy
MAX_EMBEDS_PER_MSG = 10
MAX_CHARS_PER_MSG = 5800  # max 6000 znaků ve všech embedech zprávy

DAYS = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"]


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
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def parse_bonus(item) -> dict | None:
    name_el = item.select_one("h3.bonus-item-name .bonus-item-name--primary > span")
    if not name_el:
        return None
    title = clean(name_el.get_text(" "))

    img = item.select_one(".bonus-item-head-image img")
    casino = clean(img.get("alt")) if img else ""

    link_el = item.select_one("h3.bonus-item-name a[href]")
    link = link_el["href"] if link_el else FS_URL

    valid_el = item.select_one(".bonus-item-toolbar-item")
    valid = clean(valid_el.get_text()) if valid_el else ""

    # Štítky (na stránce jsou 2x – desktop a mobil)
    tags = []
    for tag_el in item.select(".tag-list .tag"):
        t = clean(tag_el.get_text())
        if t and t not in tags:
            tags.append(t)

    # Popis pod názvem ("Aktivuj si…", "Platí na hry: …")
    description, games = [], ""
    for li in item.select(".bonus-item-head-data-title > ul > li"):
        text = clean(li.get_text(" "))
        if text.lower().startswith("platí na hry:"):
            games = clean(text.split(":", 1)[1])
        elif text:
            description.append(text)

    # Tabulka parametrů ("Počet free spinů", "Vklad", "Protočení", …)
    details = {}
    content = item.select_one(".bonus-item-content")
    if content:
        for li in content.select("ul > li"):
            key_el = li.find("strong")
            if not key_el:
                continue
            key = clean(key_el.get_text())
            value = clean(li.get_text(" ").replace(key_el.get_text(), "", 1))
            if key and value:
                details[key] = value
    notes = [clean(p.get_text(" ")) for p in content.select("p")] if content else []
    notes = [n for n in notes if n]

    promo_el = item.select_one("[data-copy]")
    promo = clean(promo_el["data-copy"]) if promo_el else ""

    howto_el = next((a for a in item.select("a.btn[href]")
                     if "Jak získat" in a.get_text() and a["href"].startswith("http")), None)
    howto = howto_el["href"] if howto_el else ""

    return {
        "title": title,
        "casino": casino,
        "link": link,
        "valid": valid,
        "tags": tags,
        "highlight": [t for t in tags if t in HIGHLIGHT_TAGS],
        "description": description,
        "games": games,
        "details": details,
        "notes": notes,
        "promo": promo,
        "howto": howto,
    }


def scrape_free_spins() -> list[dict]:
    soup = fetch(FS_URL)
    # První seznam s denními bonusy = hlavní "Free spiny dnes"
    # (další seznamy na stránce jsou "Další akce" a "Za registraci").
    bonus_list = soup.select_one("div.bonus-list--daily")
    if not bonus_list:
        raise RuntimeError("Na vyhraj.cz nebyl nalezen seznam bonusů (změnila se struktura stránky?)")
    bonuses = [b for b in map(parse_bonus, bonus_list.select("div.bonus-item--daily")) if b]
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
# Formátování
# --------------------------------------------------------------------------- #
DETAIL_FORMAT = [  # (klíč na webu, emoji, popisek)
    ("Počet free spinů", "🎰", "FS"),
    ("Hodnota free spinu", "💎", "Hodnota FS"),
    ("Vklad", "💰", "Vklad"),
    ("Protočení", "🔄", "Protočení"),
    ("Hodnota bonusu", "🎁", "Hodnota bonusu"),
]


def format_bonus(b: dict) -> str:
    casino = f"**{b['casino']}** – " if b["casino"] else ""
    icon = "🟢" if b["highlight"] else "▫️"
    lines = [f"{icon} {casino}[{b['title']}]({b['link']})"]

    if b["highlight"]:
        others = [t for t in b["tags"] if t not in b["highlight"]]
        lines.append("✅ " + " · ".join([f"**{t}**" for t in b["highlight"]] + others))
    elif b["tags"]:
        lines.append("🏷️ " + " · ".join(b["tags"]))

    for d in b["description"]:
        lines.append(f"📝 {d}")
    if b["games"]:
        lines.append(f"🎮 {b['games']}")

    params = []
    for key, emoji, label in DETAIL_FORMAT:
        if key in b["details"]:
            val = b["details"][key]
            params.append(f"{emoji} {val} FS" if label == "FS" else f"{emoji} {label}: {val}")
    if params:
        lines.append(" · ".join(params))

    validity = b["details"].get("Platnost") or b["valid"]
    if validity:
        lines.append(f"⏰ {validity}")
    if b["promo"]:
        lines.append(f"🔑 Promokód: `{b['promo']}`")
    for n in b["notes"]:
        lines.append(f"ℹ️ {n}")
    if b["howto"]:
        lines.append(f"📖 [Jak získat bonus]({b['howto']})")
    return "\n".join(lines)


def build_list_embeds(lines: list[str], title: str, color: int) -> list[dict]:
    chunks, chunk = [], ""
    for line in lines:
        line = line[:MAX_DESC]
        if chunk and len(chunk) + len(line) + 2 > CHUNK_DESC:
            chunks.append(chunk)
            chunk = ""
        chunk += ("\n\n" if chunk else "") + line
    if chunk:
        chunks.append(chunk)
    total = len(chunks)
    return [{
        "title": title if total == 1 else f"{title} ({i}/{total})",
        "description": desc,
        "color": color,
    } for i, desc in enumerate(chunks, 1)]


def build_embeds(bonuses, klikar, errors) -> list[dict]:
    today = datetime.now(TZ)
    date_str = f"{DAYS[today.weekday()]} {today.day}. {today.month}. {today.year}"
    embeds = []

    hl = [b for b in bonuses if b["highlight"]] if bonuses else []
    other = [b for b in bonuses if not b["highlight"]] if bonuses else []

    header = {"title": f"🎰 Free spiny dnes – {date_str}", "url": FS_URL, "color": COLOR_HEADER}
    if bonuses is not None:
        header["description"] = (f"🟢 **{len(hl)}** bonusů je "
                                 f"*{' / '.join(HIGHLIGHT_TAGS)}* – jsou nahoře zeleně.")
    embeds.append(header)

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


# --------------------------------------------------------------------------- #
# Discord
# --------------------------------------------------------------------------- #
def discord_request(method: str, url: str, **kwargs) -> requests.Response:
    for _ in range(5):
        r = requests.request(method, url, timeout=30, **kwargs)
        if r.status_code == 429:  # rate limit
            time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
            continue
        return r
    return r


def load_state() -> list[str]:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return [str(i) for i in json.load(f).get("message_ids", [])]
    except (FileNotFoundError, ValueError, AttributeError):
        return []


def save_state(ids: list[str]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"message_ids": ids}, f, indent=2)
        f.write("\n")


def delete_messages(ids: list[str]) -> list[str]:
    """Smaže zprávy, které dříve poslal tento webhook. Vrací ID, která se smazat nepodařilo."""
    failed = []
    for mid in ids:
        r = discord_request("DELETE", f"{WEBHOOK_URL}/messages/{mid}")
        if r.status_code in (200, 204, 404):  # 404 = už smazaná ručně
            print(f"Smazána stará zpráva {mid}")
        else:
            print(f"Nepodařilo se smazat zprávu {mid}: {r.status_code} {r.text[:200]}")
            failed.append(mid)
        time.sleep(0.5)
    return failed


def send(messages: list[list[dict]]) -> list[str]:
    sent_ids = []
    for i, msg in enumerate(messages, 1):
        r = discord_request("POST", WEBHOOK_URL, json={"embeds": msg}, params={"wait": "true"})
        if r.status_code not in (200, 204):
            raise RuntimeError(f"Discord vrátil {r.status_code}: {r.text[:500]}", sent_ids)
        sent_ids.append(str(r.json()["id"]))
        print(f"Odeslána zpráva {i}/{len(messages)}")
        time.sleep(1)
    return sent_ids


def print_messages(messages: list[list[dict]]) -> None:
    for i, msg in enumerate(messages, 1):
        print(f"--- zpráva {i}/{len(messages)} ({sum(map(embed_size, msg))} znaků) ---")
        for e in msg:
            print(f"## {e['title']}\n{e.get('description', '')}\n")


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

    messages = pack_messages(build_embeds(bonuses, klikar, errors))
    if DRY_RUN:
        print_messages(messages)
        return 0

    old_ids = load_state()
    try:
        new_ids = send(messages)
    except RuntimeError as e:
        # Část zpráv možná odešla – staré nemažeme, ať se uklidí všechno příště.
        partial = e.args[1] if len(e.args) > 1 else []
        save_state(old_ids + partial)
        print("Chyba:", e.args[0])
        return 1

    # Nové zprávy jsou venku -> smažeme předchozí výpis.
    failed = delete_messages(old_ids)
    save_state(failed + new_ids)
    # Pokud selhal scraping, ať je běh v GitHub Actions označen červeně.
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
