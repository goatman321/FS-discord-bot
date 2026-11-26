# FS Discord bot

Každý den ve **13:00 (pražského času)** pošle na Discord přes webhook:

1. **Hlavičku** s dnešním datem a počtem zvýrazněných bonusů.
2. **Dnešního Klikaře dne** z [apollogames.cz/klikar](https://www.apollogames.cz/klikar).
3. **Přehled dnešních free spinů** z [vyhraj.cz](https://vyhraj.cz/rubrika/freespiny-bonusy-dnes/)
   s detaily (popis, hry, počet a hodnota FS, vklad, protočení, platnost, promokód, návod).
   Bonusy se štítkem **Bez podmínek**, **Bez vkladu** nebo **Pouze za vklad** jsou nahoře, výrazně zeleně (🟢).

Před každým novým výpisem bot **smaže svůj předchozí výpis**. ID odeslaných zpráv si ukládá
do `sent_messages.json` (workflow ho po běhu commitne zpět do repa).
Webhook umí mazat jen zprávy, které sám poslal – zprávy jiných lidí v kanálu zůstanou.

Běží přes GitHub Actions (`.github/workflows/scrape.yml`).

## Nastavení

- Repo → *Settings → Secrets and variables → Actions* → secret `DISCORD_WEBHOOK` = URL webhooku.
- Ruční spuštění: *Actions → Free spiny dnes → Run workflow*.

> GitHub cron běží v UTC, proto jsou ve workflow dva crony (11:00 a 12:00 UTC) a skript
> sám pustí jen ten, který odpovídá 13:00 v Praze (letní/zimní čas).

## Lokální spuštění

```bash
pip install -r requirements.txt
DRY_RUN=1 python scraper.py                                # jen vypíše zprávy, nic neposílá
DISCORD_WEBHOOK="https://discord.com/api/webhooks/..." python scraper.py
```
