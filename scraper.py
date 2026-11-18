import os
import json
import requests
from bs4 import BeautifulSoup

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK")
CACHE_FILE = "cache.json"

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_cache(cache_data):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)

def main():
    if not WEBHOOK_URL:
        print("Chyba: DISCORD_WEBHOOK není nastaven.")
        return

    # Stažení stránky
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    try:
        response = requests.get("https://vyhraj.cz/rubrika/freespiny-bonusy-dnes/", headers=headers, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Chyba při stahování: {e}")
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    cache = load_cache()
    has_new_items = False

    # Zde je potřeba upravit CSS selektory podle aktuálního DOMu na vyhraj.cz
    # Toto je ukázkový předpoklad struktury
    bonus_items = soup.find_all('div', class_='relative flex flex-col') # Příklad třídy, nutno ověřit v DevTools

    for item in bonus_items:
        # Extrakce dat (nutno upravit selektory)
        title_element = item.find('h3')
        if not title_element:
            continue
            
        title = title_element.text.strip()
        
        # Unikátní identifikátor (např. název + dnešní datum ze stránky, nebo jen název, pokud se mění)
        item_id = title 

        if item_id not in cache:
            # Sestavení zprávy pro Discord
            discord_payload = {
                "embeds": [{
                    "title": f"🎁 Nový bonus: {title}",
                    "description": "Byl nalezen nový bonus na Vyhraj.cz",
                    "url": "https://vyhraj.cz/rubrika/freespiny-bonusy-dnes/",
                    "color": 16734296
                }]
            }
            
            # Odeslání webhooku
            res = requests.post(WEBHOOK_URL, json=discord_payload)
            if res.status_code == 204:
                print(f"Odesláno: {title}")
                cache.append(item_id)
                has_new_items = True
            else:
                print(f"Chyba odesílání na Discord: {res.status_code}")

    # Uložení cache, pokud je něco nového
    if has_new_items:
        # Udržujeme jen posledních např. 100 záznamů, aby soubor nekynul
        save_cache(cache[-100:])
        print("Cache aktualizována.")
    else:
        print("Žádné nové bonusy.")

if __name__ == "__main__":
    main()
