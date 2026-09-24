import csv
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

MAXI_ONLINE_PAGE = "https://www.ica.se/butiker/maxi/handla-online/"
SHOP_BASE = "https://handlaprivatkund.ica.se"
SEARCH_TERM = "pokemon"
HEADLESS = False
WAIT_SECONDS = 20
STORE_DELAY = 1.0


def normalize_space(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def clean_store_name(name):
    name = normalize_space(name)
    name = re.sub(r"\s*Handla online\s*$", "", name, flags=re.I)
    return name


def extract_city(nearby_text, store_name):
    """Try to get the postal city from the store card, with sensible fallbacks."""
    text = (nearby_text or "").replace("\r", "\n")

    # Swedish addresses usually contain: 123 45 CITY
    matches = re.findall(
        r"\b\d{3}\s?\d{2}\s+([A-Za-zÅÄÖåäöÉéÜüØøÆæ .'-]{2,60})",
        text,
    )
    if matches:
        city = normalize_space(matches[-1].split("\n")[0])
        # Stop if other UI text follows on the same line.
        city = re.split(r"Handla online|Visa|Öppet|Stängt|Hitta hit", city, flags=re.I)[0].strip(" ,-|")
        if city:
            return city

    short = clean_store_name(store_name)
    short = re.sub(r"^Maxi ICA Stormarknad\s+", "", short, flags=re.I).strip()

    # Names where the city is explicitly included.
    if "," in short:
        return short.rsplit(",", 1)[1].strip()
    for city in ["Uppsala", "Helsingborg", "Göteborg", "Malmö", "Örebro"]:
        if re.search(rf"\b{re.escape(city)}\b", short, flags=re.I):
            return city

    # Fallbacks for shopping districts / neighbourhood store names.
    overrides = {
        "barkarbystaden": "Järfälla",
        "brynäs": "Gävle",
        "erikslund": "Västerås",
        "flygstaden": "Halmstad",
        "häggvik": "Sollentuna",
        "halla": "Västerås",
        "hälla": "Västerås",
        "högskolan": "Halmstad",
        "lindhagen": "Stockholm",
        "moraberg": "Södertälje",
        "universitetet": "Örebro",
        "vasa handelsplats": "Södertälje",
        "välsviken": "Karlstad",
        "västra hamnen": "Malmö",
        "österåker": "Åkersberga",
    }
    key = short.lower()
    if key in overrides:
        return overrides[key]

    # Last resort: store's short name. Still useful as a sortable location column.
    return short


def start_browser():
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--window-size=1700,1100")
    chrome_options.add_argument("--lang=sv-SE")
    if HEADLESS:
        chrome_options.add_argument("--headless=new")
    try:
        driver = webdriver.Chrome(options=chrome_options)
        print("Använder Chrome.")
        return driver
    except WebDriverException:
        pass

    edge_options = webdriver.EdgeOptions()
    edge_options.add_argument("--disable-blink-features=AutomationControlled")
    edge_options.add_argument("--window-size=1700,1100")
    edge_options.add_argument("--lang=sv-SE")
    if HEADLESS:
        edge_options.add_argument("--headless=new")
    try:
        driver = webdriver.Edge(options=edge_options)
        print("Använder Edge.")
        return driver
    except WebDriverException as exc:
        raise RuntimeError("Kunde inte starta Chrome eller Edge.") from exc


def accept_cookies_if_present(driver):
    labels = ["Acceptera alla", "Godkänn alla", "Tillåt alla", "Acceptera"]
    for label in labels:
        try:
            buttons = driver.find_elements(
                By.XPATH,
                f"//button[contains(translate(normalize-space(.), "
                f"'ABCDEFGHIJKLMNOPQRSTUVWXYZÅÄÖ', 'abcdefghijklmnopqrstuvwxyzåäö'), "
                f"'{label.lower()}')]",
            )
            for button in buttons:
                if button.is_displayed():
                    driver.execute_script("arguments[0].click();", button)
                    time.sleep(0.4)
                    return
        except Exception:
            pass


def collect_maxi_online_stores(driver):
    print("Öppnar ICA:s lista över Maxi-butiker med onlinehandel...")
    driver.get(MAXI_ONLINE_PAGE)
    WebDriverWait(driver, WAIT_SECONDS).until(
        lambda d: len(d.find_elements(By.TAG_NAME, "body")) > 0
    )
    time.sleep(1.5)
    accept_cookies_if_present(driver)

    for _ in range(35):
        buttons = driver.find_elements(
            By.XPATH, "//button[contains(normalize-space(.), 'Visa fler butiker')]"
        )
        visible = [b for b in buttons if b.is_displayed()]
        if not visible:
            break
        before = len(driver.find_elements(By.CSS_SELECTOR, 'a[href*="handlaprivatkund.ica.se/stores/"]'))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", visible[0])
        driver.execute_script("arguments[0].click();", visible[0])
        try:
            WebDriverWait(driver, 6).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, 'a[href*="handlaprivatkund.ica.se/stores/"]')) > before
            )
        except Exception:
            time.sleep(0.8)

    stores = {}
    for a in driver.find_elements(By.CSS_SELECTOR, 'a[href*="handlaprivatkund.ica.se/stores/"]'):
        href = a.get_attribute("href") or ""
        m = re.search(r"/stores/(\d+)", href)
        if not m:
            continue
        sid = m.group(1)
        label = normalize_space(a.get_attribute("aria-label") or a.get_attribute("title") or a.text or "")
        label = re.sub(r"^Handla online\s*", "", label, flags=re.I)
        label = re.sub(r"\s*Handla online$", "", label, flags=re.I)

        nearby = ""
        try:
            nearby = driver.execute_script(
                """
                let e = arguments[0];
                for (let i=0; i<10 && e; i++, e=e.parentElement) {
                    let t=(e.innerText||'');
                    if (t.includes('Maxi ICA Stormarknad')) return t;
                }
                return '';
                """,
                a,
            ) or ""
        except Exception:
            pass

        if "Maxi" not in label:
            mm = re.search(r"Maxi ICA Stormarknad[^\n]*", nearby or "")
            if mm:
                label = normalize_space(mm.group(0))

        if "Maxi" in label:
            label = clean_store_name(label)
            stores[sid] = {
                "name": label,
                "city": extract_city(nearby, label),
            }

    result = [
        {"store_id": sid, "name": data["name"], "city": data["city"]}
        for sid, data in stores.items()
    ]
    result.sort(key=lambda x: (x["city"].lower(), x["name"].lower()))
    return result


def search_url(store_id, query=SEARCH_TERM):
    return f"{SHOP_BASE}/stores/{store_id}/search?q={quote(query)}"


def wait_for_search_page(driver):
    def loaded(d):
        try:
            text = (d.find_element(By.TAG_NAME, "body").text or "").lower()
            url = d.current_url.lower()
            return (
                "/search" in url
                and (
                    "sökresultat" in text
                    or "pokemon" in text
                    or "pokémon" in text
                    or "inga träffar" in text
                )
            )
        except Exception:
            return False

    try:
        WebDriverWait(driver, WAIT_SECONDS).until(loaded)
    except Exception:
        pass


def scroll_search_results(driver):
    last_height = 0
    stable = 0
    for _ in range(10):
        height = driver.execute_script("return document.body.scrollHeight") or 0
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.55)
        new_height = driver.execute_script("return document.body.scrollHeight") or 0
        if new_height == last_height == height:
            stable += 1
        else:
            stable = 0
        last_height = new_height
        if stable >= 2:
            break
    driver.execute_script("window.scrollTo(0, 0);")


def scrape_visible_product_cards(driver, store):
    """
    Extract product cards from ICA's rendered /search?q=pokemon page.
    Does NOT depend on ICA's internal JSON shape or a particular product-link URL.
    """
    script = r"""
    function clean(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
    function isPokemon(s) {
        s = clean(s).toLowerCase();
        return s.includes('pokémon') || s.includes('pokemon');
    }
    function ownishText(el) {
        return clean(el.innerText || el.textContent || '');
    }

    const all = Array.from(document.querySelectorAll('body *'));
    const titles = [];

    for (const el of all) {
        if (!el || el.children.length > 8) continue;
        const t = ownishText(el);
        if (!t || t.length < 3 || t.length > 180 || !isPokemon(t)) continue;

        // Reject huge section/header strings and search UI labels.
        const lower = t.toLowerCase();
        if (lower.startsWith('sökresultat för') || lower === 'pokemon' || lower === 'pokémon') continue;
        if (lower.includes('favoriter först') || lower.includes('produktmärkningar')) continue;

        // Prefer an element where child elements do not already contain the exact same product-like text.
        let childSame = false;
        for (const c of Array.from(el.children)) {
            const ct = ownishText(c);
            if (ct && ct === t) { childSame = true; break; }
        }
        if (!childSame) titles.push(el);
    }

    const rows = [];
    const seen = new Set();

    for (const titleEl of titles) {
        let title = ownishText(titleEl);
        if (!title || title.length > 180) continue;

        let card = titleEl;
        let chosen = null;
        for (let i = 0; i < 9 && card; i++, card = card.parentElement) {
            const txt = ownishText(card);
            const hasPrice = /\d+[\.,]?\d*\s*kr/i.test(txt);
            const hasAction = /lägg till|slut|ej tillgänglig|inte tillgänglig/i.test(txt);
            const hasImg = !!card.querySelector('img');
            if (txt.length >= title.length && txt.length < 1000 && (hasPrice || hasAction) && hasImg) {
                chosen = card;
                break;
            }
        }
        if (!chosen) continue;

        const cardText = ownishText(chosen);

        // If title element accidentally contains card text, derive title from short descendant texts.
        if (title.length > 120 || /lägg till|\bkr\b/i.test(title)) {
            const candidates = Array.from(chosen.querySelectorAll('a, h1, h2, h3, h4, p, span, div'))
                .map(e => ownishText(e))
                .filter(t => t && t.length >= 3 && t.length <= 140 && isPokemon(t))
                .filter(t => !/lägg till|\bkr\b|sökresultat/i.test(t));
            candidates.sort((a,b) => a.length - b.length);
            if (candidates.length) title = candidates[0];
        }

        let href = '';
        const a = titleEl.closest('a') || chosen.querySelector('a[href]');
        if (a) href = a.href || '';

        const priceMatches = [...cardText.matchAll(/(\d{1,5}(?:[\.,]\d{1,2})?)\s*kr/gi)];
        let price = priceMatches.length ? priceMatches[priceMatches.length - 1][1].replace(',', '.') : '';

        const low = cardText.toLowerCase();
        let inStock = null;
        if (low.includes('lägg till')) inStock = true;
        else if (low.includes('slut') || low.includes('ej tillgänglig') || low.includes('inte tillgänglig')) inStock = false;

        let sku = '';
        const m = href.match(/(?:products|product)\/(\d+)/i);
        if (m) sku = m[1];

        const key = (sku || href || title).toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);

        rows.push({title, sku, price, inStock, href, cardText});
    }

    return rows;
    """

    raw = driver.execute_script(script) or []
    rows = []
    for item in raw:
        name = normalize_space(item.get("title", ""))
        if not name:
            continue
        rows.append({
            "city": store.get("city", ""),
            "store": store["name"],
            "store_id": store["store_id"],
            "product": name,
            "sku": item.get("sku", ""),
            "price": item.get("price", ""),
            "in_stock": item.get("inStock", None),
            "source": "rendered_search_page",
            "url": item.get("href", "") or search_url(store["store_id"]),
        })
    return rows



def looks_like_tcg(name):
    """Keep Pokémon Trading Card Game products and reject cereal, LEGO, books, party goods, etc."""
    n = (name or "").lower()
    folded = (
        n.replace("é", "e")
         .replace("ö", "o")
         .replace("å", "a")
         .replace("ä", "a")
    )

    # Strong exclusions for obvious non-TCG Pokémon merchandise.
    excluded = [
        "flingor", "kellogg", "tallrik", "servett", "decorata",
        "lego", "leta & hitta", "leta och hitta", "bok", "pussel",
        "mugg", "glas", "kalas", "party", "leksak", "gosedjur",
        "figur", "klader", "strump", "ryggsack", "matlada",
        "flaska", "schampo", "tandkram", "godis", "dryck"
    ]
    if any(x in folded for x in excluded):
        return False

    # Terms commonly used in Pokémon TCG product names.
    tcg_markers = [
        "tcg", "trading card", "booster", "blister", "mini tin",
        " tin", "elite trainer", "etb", "booster bundle",
        "illustration collection", "premium collection", "collection",
        "ex box", " box", "battle deck", "deck", "trainer toolkit",
        "ultra premium", "upc", "binder collection", "poster collection",
        "tech sticker", "checklane", "build & battle", "build and battle",
        "prerelease", "portfolio", "kortspel"
    ]
    return any(x in folded for x in tcg_markers)

def product_type(name):
    """Classify a TCG item into a useful sortable product type."""
    n = (name or "").lower()
    folded = (
        n.replace("é", "e").replace("ö", "o").replace("å", "a").replace("ä", "a")
    )

    checks = [
        ("Ultra Premium Collection (UPC)", ["ultra premium", " upc"]),
        ("Elite Trainer Box (ETB)", ["elite trainer", " etb"]),
        ("Booster Bundle", ["booster bundle"]),
        ("Build & Battle", ["build & battle", "build and battle", "prerelease"]),
        ("Binder Collection", ["binder collection"]),
        ("Poster Collection", ["poster collection"]),
        ("Tech Sticker Collection", ["tech sticker"]),
        ("Premium Collection", ["premium collection"]),
        ("Illustration Collection", ["illustration collection"]),
        ("3-pack Blister", ["3-p", "3 pack", "3-pack", "3pk"]),
        ("Blister", ["blister", "checklane"]),
        ("Mini Tin", ["mini tin"]),
        ("Tin", [" tin"]),
        ("Battle Deck", ["battle deck"]),
        ("Deck", [" deck"]),
        ("Trainer Toolkit", ["trainer toolkit"]),
        ("Booster Pack", ["booster pack", " booster"]),
        ("Box", [" box", "ex box"]),
        ("Collection", ["collection"]),
        ("Portfolio", ["portfolio"]),
    ]
    for label, markers in checks:
        if any(m in folded for m in markers):
            return label
    return "Övrigt TCG"


def set_or_series(name):
    """Best-effort extraction of common set/series names for filtering."""
    n = (name or "").lower()
    known = [
        "30th Celebration",
        "Mega Evolution",
        "Phantasmal Flames",
        "Destined Rivals",
        "Journey Together",
        "Prismatic Evolutions",
        "Surging Sparks",
        "Stellar Crown",
        "Shrouded Fable",
        "Twilight Masquerade",
        "Temporal Forces",
        "Paldean Fates",
        "Paradox Rift",
        "151",
        "Obsidian Flames",
        "Paldea Evolved",
        "Scarlet & Violet",
        "Crown Zenith",
        "Silver Tempest",
        "Lost Origin",
        "Astral Radiance",
        "Brilliant Stars",
        "Fusion Strike",
    ]
    for set_name in known:
        if set_name.lower() in n:
            return set_name
    if "30th" in n or "celebration" in n:
        return "30th Celebration"
    return ""


def stock_text(value):
    if value is True:
        return "JA"
    if value is False:
        return "NEJ"
    return "OKÄNT"


def swedish_price(value):
    if value in (None, ""):
        return ""
    try:
        return f"{float(str(value).replace(',', '.')):.2f}".replace(".", ",")
    except (TypeError, ValueError):
        return str(value).replace(".", ",")


def looks_like_30th(name):
    n = (name or "").lower()
    folded = (
        n.replace("é", "e")
        .replace("ö", "o")
        .replace("å", "a")
        .replace("ä", "a")
    )
    markers = [
        "30th",
        "30 th",
        "30th celebration",
        "30 celebration",
        "30 ar",
        "30-ar",
        "30ars",
        "30 ars",
        "30 arsjubileum",
        "celebration",
    ]
    return any(m in folded for m in markers)


def save_csv(rows, filename):
    # Swedish Excel normally handles semicolon-delimited CSV + decimal comma best.
    fields = [
        "Stad/ort",
        "Butik",
        "Butiks-ID",
        "Produkttyp",
        "Set/Serie",
        "Produkt",
        "Pris (kr)",
        "I lager",
        "30th Celebration",
        "SKU",
        "Kontrollerad",
        "URL",
    ]
    checked = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    export_rows = []
    for r in rows:
        export_rows.append({
            "Stad/ort": r.get("city", ""),
            "Butik": r.get("store", ""),
            "Butiks-ID": r.get("store_id", ""),
            "Produkttyp": product_type(r.get("product", "")),
            "Set/Serie": set_or_series(r.get("product", "")),
            "Produkt": r.get("product", ""),
            "Pris (kr)": swedish_price(r.get("price", "")),
            "I lager": stock_text(r.get("in_stock")),
            "30th Celebration": "JA" if looks_like_30th(r.get("product", "")) else "NEJ",
            "SKU": r.get("sku", ""),
            "Kontrollerad": checked,
            "URL": r.get("url", ""),
        })

    # Default order: city -> product type -> numeric price -> product.
    def price_num(row):
        try:
            return float(str(row.get("Pris (kr)", "")).replace(",", "."))
        except ValueError:
            return float("inf")

    export_rows.sort(key=lambda r: (
        r["Stad/ort"].lower(),
        r["Produkttyp"].lower(),
        price_num(r),
        r["Produkt"].lower(),
    ))

    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(export_rows)


def unique_output_names():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (
        Path(f"ica_maxi_pokemon_30th_{stamp}.csv"),
        Path(f"ica_maxi_pokemon_tcg_{stamp}.csv"),
    )


def main():
    out_30th, out_all = unique_output_names()
    driver = start_browser()

    try:
        stores = collect_maxi_online_stores(driver)
        print(f"\nHittade {len(stores)} Maxi-butiker med onlinehandel.")
        if len(stores) < 70:
            print("VARNING: butiklistan verkar ofullständig.")

        all_rows = []
        all_30th = []
        failures = []

        for index, store in enumerate(stores, 1):
            print(f"[{index:02}/{len(stores)}] {store['name']} | {store.get('city','')} ({store['store_id']})")
            try:
                url = search_url(store["store_id"])
                driver.get(url)
                wait_for_search_page(driver)
                accept_cookies_if_present(driver)
                time.sleep(1.0)
                scroll_search_results(driver)
                rows = scrape_visible_product_cards(driver, store)

                # Deduplicate product names/sku within the store.
                deduped = []
                seen = set()
                for row in rows:
                    key = (row["sku"] or row["url"] or row["product"]).lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append(row)

                # Keep only Pokémon TCG products; remove cereal, LEGO, books, party goods, etc.
                tcg_rows = [r for r in deduped if looks_like_tcg(r["product"])]
                store_30th = [r for r in tcg_rows if looks_like_30th(r["product"])]
                all_rows.extend(tcg_rows)
                all_30th.extend(store_30th)

                print(f"    TCG-träffar: {len(tcg_rows)}, 30th-träffar: {len(store_30th)}")
                for row in tcg_rows[:8]:
                    status = "✅" if row["in_stock"] is True else "❌" if row["in_stock"] is False else "?"
                    price = f" | {row['price']} kr" if row["price"] else ""
                    print(f"      {status} {row['product']}{price}")
                if len(tcg_rows) > 8:
                    print(f"      ... +{len(tcg_rows)-8} till")

                if index % 10 == 0:
                    save_csv(all_rows, out_all)
                    save_csv(all_30th, out_30th)

            except KeyboardInterrupt:
                print("\nAvbrutet av användaren. Sparar det som hunnit hittas...")
                break
            except Exception as exc:
                failures.append((store["name"], str(exc)))
                print(f"    ! FEL: {exc}")

            time.sleep(STORE_DELAY)

        all_rows.sort(key=lambda r: (r.get("city", "").lower(), product_type(r["product"]).lower(), r["store"].lower(), r["product"].lower()))
        all_30th.sort(key=lambda r: (r["in_stock"] is not True, r.get("city", "").lower(), product_type(r["product"]).lower(), r["store"].lower(), r["product"].lower()))
        save_csv(all_rows, out_all)
        save_csv(all_30th, out_30th)

        print("\n" + "=" * 72)
        print(f"Klar {datetime.now():%Y-%m-%d %H:%M:%S}")
        print(f"Maxi-butiker i listan: {len(stores)}")
        print(f"Pokémon TCG-rader: {len(all_rows)}")
        print(f"30th-rader totalt: {len(all_30th)}")
        print(f"30th markerade i lager: {sum(r['in_stock'] is True for r in all_30th)}")
        print(f"Sparat: {out_all}")
        print(f"Sparat: {out_30th}")

        if failures:
            print(f"Butiker med fel: {len(failures)}")
            for name, err in failures[:15]:
                print(f"- {name}: {err}")

    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
