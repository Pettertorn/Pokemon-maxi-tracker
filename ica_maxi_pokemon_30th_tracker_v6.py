import argparse
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

WAIT_SECONDS = 20
STORE_DELAY = 1.0

# En separat Chrome-profil används så att ICA-inloggning/cookies sparas
# mellan körningar. Mappen skapas automatiskt bredvid scriptet.
SCRIPT_DIR = Path(__file__).resolve().parent
ICA_PROFILE_DIR = SCRIPT_DIR / "ica_chrome_profile"

# Kassakontrollen lägger aldrig någon beställning. Den går bara så långt
# att ICA kan visa om en vara är tillgänglig för vald butik/tid.
CHECKOUT_VERIFY = True


def normalize_space(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def fold_text(text):
    return (
        normalize_space(text).lower()
        .replace("é", "e")
        .replace("ö", "o")
        .replace("å", "a")
        .replace("ä", "a")
    )


def clean_store_name(name):
    name = normalize_space(name)
    name = re.sub(r"\s*Handla online\s*$", "", name, flags=re.I)
    return name


def extract_city(nearby_text, store_name):
    """Try to get the postal city from the store card, with sensible fallbacks."""
    text = (nearby_text or "").replace("\r", "\n")

    matches = re.findall(
        r"\b\d{3}\s?\d{2}\s+([A-Za-zÅÄÖåäöÉéÜüØøÆæ .'-]{2,60})",
        text,
    )
    if matches:
        city = normalize_space(matches[-1].split("\n")[0])
        city = re.split(
            r"Handla online|Visa|Öppet|Stängt|Hitta hit",
            city,
            flags=re.I,
        )[0].strip(" ,-|")
        if city:
            return city

    short = clean_store_name(store_name)
    short = re.sub(r"^Maxi ICA Stormarknad\s+", "", short, flags=re.I).strip()

    if "," in short:
        return short.rsplit(",", 1)[1].strip()

    for city in ["Uppsala", "Helsingborg", "Göteborg", "Malmö", "Örebro"]:
        if re.search(rf"\b{re.escape(city)}\b", short, flags=re.I):
            return city

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

    return short


def start_browser(headless=False, persistent_profile=True):
    chrome_options = webdriver.ChromeOptions()

    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--window-size=1700,1100")
    chrome_options.add_argument("--lang=sv-SE")

    if persistent_profile:
        ICA_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        chrome_options.add_argument(f"--user-data-dir={ICA_PROFILE_DIR}")

    if headless:
        chrome_options.add_argument("--headless=new")

    try:
        driver = webdriver.Chrome(options=chrome_options)
        print("Använder Chrome.")
        if persistent_profile:
            print(f"ICA-profil: {ICA_PROFILE_DIR}")
        return driver
    except WebDriverException as exc:
        if persistent_profile:
            raise RuntimeError(
                "Kunde inte starta Chrome med ICA-profilen. "
                "Stäng alla Chrome-fönster som använder profilen och försök igen."
            ) from exc

    raise RuntimeError("Kunde inte starta Chrome.")


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


def setup_login(driver):
    """
    Öppnar ICA i den permanenta browserprofilen.
    Användaren loggar in manuellt en gång; cookies sparas till nästa körning.
    """
    print("\nÖppnar ICA för inloggning...")
    driver.get(SHOP_BASE)
    time.sleep(2)
    accept_cookies_if_present(driver)

    print(
        "\nLogga in på ditt ICA-konto i Chrome-fönstret.\n"
        "När du är helt inloggad, gå tillbaka hit och tryck Enter."
    )
    input()
    print("Inloggningsprofilen är sparad.")
    print(f"Profilmapp: {ICA_PROFILE_DIR}")


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
            By.XPATH,
            "//button[contains(normalize-space(.), 'Visa fler butiker')]",
        )
        visible = [b for b in buttons if b.is_displayed()]

        if not visible:
            break

        before = len(
            driver.find_elements(
                By.CSS_SELECTOR,
                'a[href*="handlaprivatkund.ica.se/stores/"]',
            )
        )

        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});",
            visible[0],
        )
        driver.execute_script("arguments[0].click();", visible[0])

        try:
            WebDriverWait(driver, 6).until(
                lambda d: len(
                    d.find_elements(
                        By.CSS_SELECTOR,
                        'a[href*="handlaprivatkund.ica.se/stores/"]',
                    )
                )
                > before
            )
        except Exception:
            time.sleep(0.8)

    stores = {}

    for a in driver.find_elements(
        By.CSS_SELECTOR,
        'a[href*="handlaprivatkund.ica.se/stores/"]',
    ):
        href = a.get_attribute("href") or ""
        m = re.search(r"/stores/(\d+)", href)

        if not m:
            continue

        sid = m.group(1)

        label = normalize_space(
            a.get_attribute("aria-label")
            or a.get_attribute("title")
            or a.text
            or ""
        )
        label = re.sub(r"^Handla online\s*", "", label, flags=re.I)
        label = re.sub(r"\s*Handla online$", "", label, flags=re.I)

        nearby = ""

        try:
            nearby = (
                driver.execute_script(
                    """
                    let e = arguments[0];
                    for (let i=0; i<10 && e; i++, e=e.parentElement) {
                        let t=(e.innerText||'');
                        if (t.includes('Maxi ICA Stormarknad')) return t;
                    }
                    return '';
                    """,
                    a,
                )
                or ""
            )
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
        {
            "store_id": sid,
            "name": data["name"],
            "city": data["city"],
        }
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
    Does not depend on ICA's internal JSON shape.
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

        const lower = t.toLowerCase();

        if (
            lower.startsWith('sökresultat för')
            || lower === 'pokemon'
            || lower === 'pokémon'
        ) continue;

        if (
            lower.includes('favoriter först')
            || lower.includes('produktmärkningar')
        ) continue;

        let childSame = false;

        for (const c of Array.from(el.children)) {
            const ct = ownishText(c);
            if (ct && ct === t) {
                childSame = true;
                break;
            }
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
            const hasAction =
                /lägg till|slut|ej tillgänglig|inte tillgänglig/i.test(txt);
            const hasImg = !!card.querySelector('img');

            if (
                txt.length >= title.length
                && txt.length < 1000
                && (hasPrice || hasAction)
                && hasImg
            ) {
                chosen = card;
                break;
            }
        }

        if (!chosen) continue;

        const cardText = ownishText(chosen);

        if (title.length > 120 || /lägg till|\bkr\b/i.test(title)) {
            const candidates = Array.from(
                chosen.querySelectorAll('a, h1, h2, h3, h4, p, span, div')
            )
                .map(e => ownishText(e))
                .filter(
                    t => t
                    && t.length >= 3
                    && t.length <= 140
                    && isPokemon(t)
                )
                .filter(
                    t => !/lägg till|\bkr\b|sökresultat/i.test(t)
                );

            candidates.sort((a,b) => a.length - b.length);

            if (candidates.length) title = candidates[0];
        }

        let href = '';
        const a = titleEl.closest('a') || chosen.querySelector('a[href]');
        if (a) href = a.href || '';

        const priceMatches = [
            ...cardText.matchAll(/(\d{1,5}(?:[\.,]\d{1,2})?)\s*kr/gi)
        ];

        let price = priceMatches.length
            ? priceMatches[priceMatches.length - 1][1].replace(',', '.')
            : '';

        const low = cardText.toLowerCase();

        let inStock = null;

        if (low.includes('lägg till')) {
            inStock = true;
        } else if (
            low.includes('slut')
            || low.includes('ej tillgänglig')
            || low.includes('inte tillgänglig')
        ) {
            inStock = false;
        }

        let sku = '';
        const m = href.match(/(?:products|product)\/(\d+)/i);
        if (m) sku = m[1];

        const key = (sku || href || title).toLowerCase();

        if (seen.has(key)) continue;
        seen.add(key);

        rows.push({
            title,
            sku,
            price,
            inStock,
            href,
            cardText
        });
    }

    return rows;
    """

    raw = driver.execute_script(script) or []
    rows = []

    for item in raw:
        name = normalize_space(item.get("title", ""))

        if not name:
            continue

        rows.append(
            {
                "city": store.get("city", ""),
                "store": store["name"],
                "store_id": store["store_id"],
                "product": name,
                "sku": item.get("sku", ""),
                "price": item.get("price", ""),
                "search_stock": item.get("inStock", None),
                "checkout_stock": None,
                "checkout_note": "Ej kontrollerad",
                "in_stock": item.get("inStock", None),
                "source": "rendered_search_page",
                "url": item.get("href", "") or search_url(store["store_id"]),
            }
        )

    return rows


def looks_like_tcg(name):
    """Keep Pokémon TCG products and reject obvious non-TCG merchandise."""
    folded = fold_text(name)

    excluded = [
        "flingor",
        "kellogg",
        "tallrik",
        "servett",
        "decorata",
        "lego",
        "leta & hitta",
        "leta och hitta",
        "bok",
        "pussel",
        "mugg",
        "glas",
        "kalas",
        "party",
        "leksak",
        "gosedjur",
        "figur",
        "klader",
        "strump",
        "ryggsack",
        "matlada",
        "flaska",
        "schampo",
        "tandkram",
        "godis",
        "dryck",
    ]

    if any(x in folded for x in excluded):
        return False

    tcg_markers = [
        "tcg",
        "trading card",
        "booster",
        "blister",
        "mini tin",
        " tin",
        "elite trainer",
        "etb",
        "booster bundle",
        "illustration collection",
        "premium collection",
        "collection",
        "ex box",
        " box",
        "battle deck",
        "deck",
        "trainer toolkit",
        "ultra premium",
        "upc",
        "binder collection",
        "poster collection",
        "tech sticker",
        "checklane",
        "build & battle",
        "build and battle",
        "prerelease",
        "portfolio",
        "kortspel",
    ]

    return any(x in folded for x in tcg_markers)


def product_type(name):
    folded = fold_text(name)

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
        if any(marker in folded for marker in markers):
            return label

    return "Övrigt TCG"


def set_or_series(name):
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


def looks_like_30th(name):
    folded = fold_text(name)

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

    return any(marker in folded for marker in markers)


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


def page_text(driver):
    try:
        return normalize_space(driver.find_element(By.TAG_NAME, "body").text)
    except Exception:
        return ""


def visible_elements(driver, xpath):
    try:
        return [e for e in driver.find_elements(By.XPATH, xpath) if e.is_displayed()]
    except Exception:
        return []


def click_first(driver, elements):
    for element in elements:
        try:
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});",
                element,
            )
            time.sleep(0.15)
            driver.execute_script("arguments[0].click();", element)
            return True
        except Exception:
            continue

    return False


def find_product_card(driver, product_name):
    """
    Hitta det minsta synliga DOM-blocket som innehåller produktnamnet
    samt en köpknapp eller pris.
    """
    target = normalize_space(product_name).lower()

    script = r"""
    const target = arguments[0].toLowerCase();

    function clean(s) {
        return (s || '').replace(/\s+/g, ' ').trim();
    }

    const nodes = Array.from(document.querySelectorAll('body *'));
    let best = null;

    for (const el of nodes) {
        const text = clean(el.innerText || el.textContent || '');
        const low = text.toLowerCase();

        if (!low.includes(target)) continue;
        if (text.length > 1200) continue;

        const hasButton = !!el.querySelector('button');
        const hasPrice = /\d+[\.,]?\d*\s*kr/i.test(text);

        if (!hasButton && !hasPrice) continue;

        if (!best || text.length < clean(best.innerText || '').length) {
            best = el;
        }
    }

    return best;
    """

    try:
        return driver.execute_script(script, target)
    except Exception:
        return None


def add_product_to_cart(driver, row):
    """
    Lägg exakt den aktuella produkten i kundvagnen från butikens sökresultat.
    Returnerar (success, note).
    """
    url = search_url(row["store_id"])
    driver.get(url)
    wait_for_search_page(driver)
    accept_cookies_if_present(driver)
    time.sleep(0.8)
    scroll_search_results(driver)

    card = find_product_card(driver, row["product"])

    if not card:
        return False, "Kunde inte hitta produktkortet igen"

    card_text = fold_text(card.text)

    if (
        "slut i lager" in card_text
        or "ej tillganglig" in card_text
        or "inte tillganglig" in card_text
    ):
        return False, "Söksidan visar slut/ej tillgänglig"

    buttons = []

    try:
        buttons = [
            b
            for b in card.find_elements(By.TAG_NAME, "button")
            if b.is_displayed()
        ]
    except Exception:
        pass

    add_buttons = []

    for button in buttons:
        label = fold_text(
            (button.text or "")
            + " "
            + (button.get_attribute("aria-label") or "")
            + " "
            + (button.get_attribute("title") or "")
        )

        if (
            "lagg till" in label
            or "lägg till" in label
            or "add" in label
        ):
            add_buttons.append(button)

    if not add_buttons:
        return False, "Ingen 'Lägg till'-knapp hittades"

    if not click_first(driver, add_buttons):
        return False, "Kunde inte klicka på 'Lägg till'"

    time.sleep(0.8)
    return True, "Tillagd i kundvagn"


def _safe_click_by_text(driver, texts):
    """Click the first visible button/link whose text contains one of texts."""
    for text in texts:
        xpath = (
            "//*[self::button or self::a or @role='button' or @role='tab']["
            "contains(translate(normalize-space(.),"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZÅÄÖ','abcdefghijklmnopqrstuvwxyzåäö'),"
            f"'{text.lower()}')]"
        )
        elements = visible_elements(driver, xpath)
        if click_first(driver, elements):
            return True
    return False


def _element_is_enabled(element):
    try:
        if not element.is_enabled():
            return False
        if element.get_attribute("disabled") is not None:
            return False
        if (element.get_attribute("aria-disabled") or "").lower() == "true":
            return False
        return True
    except Exception:
        return False


def choose_pickup_location_if_needed(driver):
    """Handle ICA's 'Välj utlämningsställe' dialog if it appears."""
    text = fold_text(page_text(driver))
    if "valj utlamningsstalle" not in text and "valj den har platsen" not in text:
        return False

    buttons = visible_elements(
        driver,
        "//*[self::button or self::a][contains(translate(normalize-space(.),"
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZÅÄÖ','abcdefghijklmnopqrstuvwxyzåäö'),"
        "'välj den här platsen')]",
    )
    if click_first(driver, buttons):
        print("         🚗 Valde utlämningsställe")
        time.sleep(1.0)
        return True
    return False


def choose_pickup_tab(driver):
    """Choose 'Hämta' instead of home delivery on the delivery-time page."""
    # Prefer actual tabs/buttons, not arbitrary text nodes.
    xpath = (
        "//*[self::button or self::a or @role='tab']["
        "translate(normalize-space(.),"
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZÅÄÖ','abcdefghijklmnopqrstuvwxyzåäö')='hämta']"
    )
    elements = visible_elements(driver, xpath)
    if click_first(driver, elements):
        print("         🚗 Valde Hämta")
        time.sleep(0.8)
        return True

    # Fallback for ICA components that render the tab as another clickable element.
    try:
        elements = driver.find_elements(
            By.XPATH,
            "//*[translate(normalize-space(.),"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZÅÄÖ','abcdefghijklmnopqrstuvwxyzåäö')='hämta']",
        )
        for el in elements:
            if not el.is_displayed():
                continue
            try:
                driver.execute_script("arguments[0].click();", el)
                print("         🚗 Valde Hämta")
                time.sleep(0.8)
                return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def choose_first_available_pickup_slot(driver):
    """Choose the first enabled pickup time (the white '0 kr' cells in ICA)."""
    # First try actual buttons containing exactly 0 kr.
    candidates = []
    for xpath in [
        "//button[normalize-space(.)='0 kr']",
        "//*[@role='button' and normalize-space(.)='0 kr']",
        "//a[normalize-space(.)='0 kr']",
    ]:
        try:
            candidates.extend(driver.find_elements(By.XPATH, xpath))
        except Exception:
            pass

    # Deduplicate while preserving order.
    seen = set()
    usable = []
    for el in candidates:
        try:
            key = el.id
        except Exception:
            key = id(el)
        if key in seen:
            continue
        seen.add(key)
        try:
            if el.is_displayed() and _element_is_enabled(el):
                usable.append(el)
        except Exception:
            pass

    if not usable:
        # ICA may wrap the text in a child element. Find visible 0 kr nodes and
        # walk up to a clickable parent, while rejecting disabled cells.
        try:
            zero_nodes = driver.find_elements(
                By.XPATH,
                "//*[normalize-space(.)='0 kr']",
            )
        except Exception:
            zero_nodes = []

        for node in zero_nodes:
            try:
                if not node.is_displayed():
                    continue
                clickable = driver.execute_script(
                    """
                    let e = arguments[0];
                    for (let i=0; i<5 && e; i++, e=e.parentElement) {
                        const role=(e.getAttribute('role')||'').toLowerCase();
                        if (e.tagName==='BUTTON' || e.tagName==='A' || role==='button') return e;
                    }
                    return null;
                    """,
                    node,
                )
                if clickable and clickable.is_displayed() and _element_is_enabled(clickable):
                    usable.append(clickable)
            except Exception:
                pass

    if not usable:
        return False

    if click_first(driver, usable):
        print("         🕒 Valde första lediga hämtningstid")
        time.sleep(1.2)
        return True
    return False


def continue_after_slot_if_needed(driver):
    """Continue from delivery selection, but never click purchase/payment buttons."""
    safe_labels = [
        "fortsätt",
        "gå vidare",
        "till kundvagn",
        "till varukorg",
        "till kassan",
    ]
    dangerous = ["beställ", "slutför", "betala", "bekräfta köp"]

    for label in safe_labels:
        xpath = (
            "//*[self::button or self::a][contains(translate(normalize-space(.),"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZÅÄÖ','abcdefghijklmnopqrstuvwxyzåäö'),"
            f"'{label}')]"
        )
        for el in visible_elements(driver, xpath):
            try:
                txt = fold_text((el.text or '') + ' ' + (el.get_attribute('aria-label') or ''))
                if any(x in txt for x in dangerous):
                    continue
                driver.execute_script("arguments[0].click();", el)
                time.sleep(1.0)
                return True
            except Exception:
                pass
    return False


def ensure_pickup_slot(driver):
    """
    Make sure ICA has a pickup location + pickup time selected.

    This handles the exact flow shown by ICA:
      1. choose Hämta
      2. choose the pickup location in the modal
      3. choose the first enabled 0 kr pickup slot
    """
    for _ in range(5):
        text = fold_text(page_text(driver))
        url = driver.current_url.lower()

        changed = False

        if "valj utlamningsstalle" in text or "valj den har platsen" in text:
            changed = choose_pickup_location_if_needed(driver) or changed
            if changed:
                continue

        if "valj leveranstid" in text or "/delivery/" in url or "/slots" in url:
            # Always force pickup when this page is shown.
            if "hamta" in text:
                changed = choose_pickup_tab(driver) or changed
                time.sleep(0.4)

            # Choosing Hämta can open the pickup-location modal.
            if choose_pickup_location_if_needed(driver):
                changed = True
                time.sleep(0.5)

            # Make sure the tab is still Hämta after the modal closes.
            text = fold_text(page_text(driver))
            if "valj leveranstid" in text and "hamta" in text:
                choose_pickup_tab(driver)

            if choose_first_available_pickup_slot(driver):
                changed = True
                continue_after_slot_if_needed(driver)
                time.sleep(0.8)

                # A selected slot normally removes us from the slot chooser or
                # changes the page so that the cart/checkout can be inspected.
                new_text = fold_text(page_text(driver))
                if "valj leveranstid" not in new_text:
                    return True, "Hämta + hämtningstid vald"

                # Even if the heading remains, ICA may already have stored the slot.
                if "se lediga leveranstider" in new_text or "varukorg" in new_text or "kassa" in new_text:
                    return True, "Hämta + hämtningstid vald"

        if not changed:
            break

    # If we're no longer on the delivery selector, consider it successful.
    final_text = fold_text(page_text(driver))
    final_url = driver.current_url.lower()
    if "valj leveranstid" not in final_text and "/delivery/collection/slots" not in final_url:
        return True, "Leveransval redan klart"

    return False, "Kunde inte välja Hämta + hämtningstid"


def open_cart_or_checkout(driver):
    """
    Open cart/checkout and finish ICA's pickup-time selection when required.
    Never clicks a purchase/payment confirmation button.
    """
    candidates = visible_elements(
        driver,
        (
            "//a[contains(translate(@href,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'cart') "
            "or contains(translate(@href,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'basket') "
            "or contains(translate(@href,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'checkout')]"
        ),
    )

    opened = click_first(driver, candidates)

    if not opened:
        text_candidates = visible_elements(
            driver,
            (
                "//*[self::a or self::button]["
                "contains(translate(normalize-space(.),"
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZÅÄÖ','abcdefghijklmnopqrstuvwxyzåäö'),'kundvagn') "
                "or contains(translate(normalize-space(.),"
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZÅÄÖ','abcdefghijklmnopqrstuvwxyzåäö'),'varukorg') "
                "or contains(translate(normalize-space(.),"
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZÅÄÖ','abcdefghijklmnopqrstuvwxyzåäö'),'kassa')]"
            ),
        )
        opened = click_first(driver, text_candidates)

    if opened:
        time.sleep(1.2)
        ok, note = ensure_pickup_slot(driver)
        if not ok:
            return False
        return True

    # Direct fallbacks.
    current = driver.current_url
    base_match = re.match(r"(https://handlaprivatkund\.ica\.se/stores/\d+)", current)
    fallback_urls = []

    if base_match:
        base = base_match.group(1)
        fallback_urls.extend([
            f"{base}/cart",
            f"{base}/basket",
            f"{base}/checkout",
        ])

    for url in fallback_urls:
        try:
            driver.get(url)
            time.sleep(1.0)
            text = fold_text(page_text(driver))

            if (
                "kundvagn" in text
                or "varukorg" in text
                or "kassa" in text
                or "bestallning" in text
                or "valj leveranstid" in text
            ):
                ok, note = ensure_pickup_slot(driver)
                if ok:
                    return True
        except Exception:
            pass

    return False

def product_name_matches(haystack, product_name):
    """
    Matcha produktnamn lite tolerant så att små skillnader i ICA:s texter
    inte förstör kassakontrollen.
    """
    hay = fold_text(haystack)
    target = fold_text(product_name)

    if target and target in hay:
        return True

    words = [
        w for w in re.findall(r"[a-z0-9]+", target)
        if len(w) >= 3
    ]

    if not words:
        return False

    hits = sum(word in hay for word in words)
    required = max(2, int(len(words) * 0.65))

    return hits >= required


def inspect_checkout_stock(driver, row):
    """
    Läs kassans/kundvagnens status för en produkt.

    False = ICA placerar varan under ej tillgänglig/slut i lager.
    True  = produkten finns i kundvagn/kassa och ingen otillgänglig-markering
            hittas nära produkten.
    None  = det gick inte att avgöra säkert.
    """
    time.sleep(1.0)

    body = page_text(driver)
    folded_body = fold_text(body)

    login_markers = [
        "logga in",
        "mobilt bankid",
        "mina sidor",
    ]

    if "logga in" in folded_body and "kundvagn" not in folded_body:
        return None, "ICA verkar kräva inloggning"

    product = row["product"]

    # Försök hitta små DOM-block som både innehåller produkten och
    # lagertexten. Detta är mycket säkrare än att bara söka i hela sidan.
    script = r"""
    const target = arguments[0].toLowerCase();

    function clean(s) {
        return (s || '').replace(/\s+/g, ' ').trim();
    }

    const all = Array.from(document.querySelectorAll('body *'));
    const found = [];

    for (const el of all) {
        const text = clean(el.innerText || el.textContent || '');
        const low = text.toLowerCase();

        if (!low.includes(target)) continue;
        if (text.length > 1800) continue;

        found.push(text);
    }

    found.sort((a,b) => a.length - b.length);
    return found.slice(0, 20);
    """

    blocks = []

    try:
        blocks = driver.execute_script(
            script,
            normalize_space(product).lower(),
        ) or []
    except Exception:
        pass

    # Om exakt text inte hittades, använd större containers med "pokemon".
    if not blocks:
        try:
            generic = driver.find_elements(
                By.XPATH,
                "//*[contains("
                "translate(normalize-space(.),"
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZÅÄÖ',"
                "'abcdefghijklmnopqrstuvwxyzåäö'),"
                "'pokemon') or contains("
                "translate(normalize-space(.),"
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZÅÄÖ',"
                "'abcdefghijklmnopqrstuvwxyzåäö'),"
                "'pokémon')]",
            )

            blocks = [
                normalize_space(e.text)
                for e in generic
                if e.is_displayed()
                and len(normalize_space(e.text)) < 1800
                and product_name_matches(e.text, product)
            ]
        except Exception:
            pass

    unavailable_markers = [
        "slut i lager",
        "ej tillganglig",
        "inte tillganglig",
        "finns inte tillgangliga",
        "varor ar inte tillgangliga",
        "produkter finns inte tillgangliga",
    ]

    for block in blocks:
        folded = fold_text(block)

        if any(marker in folded for marker in unavailable_markers):
            return False, "Kassan visar slut/ej tillgänglig"

    # ICA kan ha en separat rubrik "Följande produkter finns inte tillgängliga".
    # Hitta rubriken och kontrollera dess närliggande container.
    try:
        unavailable_headers = driver.find_elements(
            By.XPATH,
            "//*[contains("
            "translate(normalize-space(.),"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZÅÄÖ',"
            "'abcdefghijklmnopqrstuvwxyzåäö'),"
            "'inte tillgängliga')]",
        )

        for header in unavailable_headers:
            if not header.is_displayed():
                continue

            container_text = normalize_space(header.text)

            try:
                ancestor = header.find_element(
                    By.XPATH,
                    "./ancestor::*[self::section or self::div][1]",
                )
                container_text += " " + normalize_space(ancestor.text)
            except Exception:
                pass

            if product_name_matches(container_text, product):
                return False, "Produkten ligger under 'inte tillgängliga'"
    except Exception:
        pass

    # Om produkten finns på kassa/kundvagnssidan och vi inte hittade någon
    # otillgänglig-markering är det en positiv verifiering.
    if product_name_matches(body, product):
        return True, "Kassan accepterar produkten"

    return None, "Kunde inte avgöra lagerstatus i kassan"


def remove_product_from_cart(driver, row):
    """
    Försök ta bort endast den produkt som nyss testades.
    Fungerar även om ICA använder ikonknapp/aria-label.
    """
    card = find_product_card(driver, row["product"])

    if card:
        try:
            buttons = [
                b for b in card.find_elements(By.TAG_NAME, "button")
                if b.is_displayed()
            ]

            remove_candidates = []

            for button in buttons:
                label = fold_text(
                    (button.text or "")
                    + " "
                    + (button.get_attribute("aria-label") or "")
                    + " "
                    + (button.get_attribute("title") or "")
                )

                if (
                    "ta bort" in label
                    or "remove" in label
                    or "radera" in label
                    or "minus" in label
                ):
                    remove_candidates.append(button)

            if click_first(driver, remove_candidates):
                time.sleep(0.6)
                return True
        except Exception:
            pass

    # Fallback: global "Ta bort"-knapp när bara vår testprodukt finns i vagnen.
    remove_global = visible_elements(
        driver,
        (
            "//button[contains(translate("
            "concat(normalize-space(.),' ',@aria-label,' ',@title),"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZÅÄÖ',"
            "'abcdefghijklmnopqrstuvwxyzåäö'),"
            "'ta bort')]"
        ),
    )

    if click_first(driver, remove_global[:1]):
        time.sleep(0.6)
        return True

    return False


def verify_product_via_checkout(driver, row):
    """
    Full kassaverifiering för EN produkt.

    1. Gå tillbaka till aktuell butiks sökning.
    2. Lägg produkten i kundvagnen.
    3. Öppna kundvagn/kassa.
    4. Läs ICA:s uppdaterade status.
    5. Ta bort testprodukten igen.

    Ingen beställning skickas.
    """
    added, note = add_product_to_cart(driver, row)

    if not added:
        if "slut" in fold_text(note) or "tillganglig" in fold_text(note):
            return False, note
        return None, note

    if not open_cart_or_checkout(driver):
        return None, "Kunde inte öppna kundvagn/kassa"

    status, note = inspect_checkout_stock(driver, row)

    removed = remove_product_from_cart(driver, row)

    if not removed:
        note += " | OBS: kunde inte bekräfta att testvaran togs bort"

    return status, note


def verify_store_rows(driver, tcg_rows, only_search_in_stock=True):
    """
    Kontrollera TCG-rader i kassan. Som standard verifieras produkter som
    söksidan visar som JA eller OKÄNT; endast tydligt NEJ hoppas över.
    """
    candidates = []

    for row in tcg_rows:
        # Kassakontrollen behövs särskilt för OKÄNT. Hoppa bara över produkter
        # som söksidan redan uttryckligen visar som slut när standardläget används.
        if only_search_in_stock and row.get("search_stock") is False:
            continue
        candidates.append(row)

    if not candidates:
        return

    print(f"    Kassakontroll: {len(candidates)} produkt(er)")

    for i, row in enumerate(candidates, 1):
        print(f"      [{i}/{len(candidates)}] {row['product']}")

        try:
            status, note = verify_product_via_checkout(driver, row)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            status = None
            note = f"Kassakontroll gav fel: {exc}"

        row["checkout_stock"] = status
        row["checkout_note"] = note

        # Kassans besked väger tyngre än söksidan när det är definitivt.
        if status is not None:
            row["in_stock"] = status

        if status is True:
            icon = "✅"
        elif status is False:
            icon = "❌"
        else:
            icon = "?"

        print(f"         {icon} {note}")


def save_csv(rows, filename):
    fields = [
        "Stad/ort",
        "Butik",
        "Butiks-ID",
        "Produkttyp",
        "Set/Serie",
        "Produkt",
        "Pris (kr)",
        "Söksida lager",
        "Kassa lager",
        "I lager",
        "Kassakontroll",
        "30th Celebration",
        "SKU",
        "Kontrollerad",
        "URL",
    ]

    checked = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    export_rows = []

    for row in rows:
        export_rows.append(
            {
                "Stad/ort": row.get("city", ""),
                "Butik": row.get("store", ""),
                "Butiks-ID": row.get("store_id", ""),
                "Produkttyp": product_type(row.get("product", "")),
                "Set/Serie": set_or_series(row.get("product", "")),
                "Produkt": row.get("product", ""),
                "Pris (kr)": swedish_price(row.get("price", "")),
                "Söksida lager": stock_text(row.get("search_stock")),
                "Kassa lager": stock_text(row.get("checkout_stock")),
                "I lager": stock_text(row.get("in_stock")),
                "Kassakontroll": row.get("checkout_note", ""),
                "30th Celebration": (
                    "JA"
                    if looks_like_30th(row.get("product", ""))
                    else "NEJ"
                ),
                "SKU": row.get("sku", ""),
                "Kontrollerad": checked,
                "URL": row.get("url", ""),
            }
        )

    def price_num(row):
        try:
            return float(
                str(row.get("Pris (kr)", "")).replace(",", ".")
            )
        except ValueError:
            return float("inf")

    export_rows.sort(
        key=lambda row: (
            row["Stad/ort"].lower(),
            row["Produkttyp"].lower(),
            price_num(row),
            row["Produkt"].lower(),
        )
    )

    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fields,
            delimiter=";",
        )
        writer.writeheader()
        writer.writerows(export_rows)


def unique_output_names():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    return (
        Path(f"ica_maxi_pokemon_30th_{stamp}.csv"),
        Path(f"ica_maxi_pokemon_tcg_{stamp}.csv"),
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Sök igenom Maxi ICA efter Pokémon TCG och verifiera lager "
            "via ICA:s kundvagn/kassa."
        )
    )

    parser.add_argument(
        "--headless",
        action="store_true",
        help="Kör Chrome utan synligt fönster.",
    )

    parser.add_argument(
        "--no-checkout",
        action="store_true",
        help="Hoppa över den extra lagerkontrollen i kundvagn/kassa.",
    )

    parser.add_argument(
        "--verify-all",
        action="store_true",
        help=(
            "Försök kassakontrollera även rader som söksidan inte uttryckligen "
            "markerar som i lager."
        ),
    )

    parser.add_argument(
        "--setup-login",
        action="store_true",
        help="Öppna ICA och spara din inloggning i den lokala Chrome-profilen.",
    )

    parser.add_argument(
        "--store",
        type=str,
        default="",
        help=(
            "Kör bara butiker vars namn eller ort innehåller texten, "
            "t.ex. --store Mora."
        ),
    )

    return parser.parse_args()


def main():
    args = parse_args()

    if args.setup_login and args.headless:
        raise SystemExit("--setup-login kan inte användas tillsammans med --headless.")

    driver = start_browser(
        headless=args.headless,
        persistent_profile=True,
    )

    try:
        if args.setup_login:
            setup_login(driver)
            return

        out_30th, out_all = unique_output_names()

        stores = collect_maxi_online_stores(driver)

        if args.store:
            needle = fold_text(args.store)

            # Prefer exact city/store matches. This makes --store Mora select
            # Mora without also matching Moraberg in Södertälje.
            exact = [
                s
                for s in stores
                if fold_text(s["name"]) == needle
                or fold_text(s.get("city", "")) == needle
                or fold_text(s["name"]).endswith(" " + needle)
            ]

            if exact:
                stores = exact
            else:
                stores = [
                    s
                    for s in stores
                    if needle in fold_text(s["name"])
                    or needle in fold_text(s.get("city", ""))
                ]

        print(f"\nHittade {len(stores)} Maxi-butiker att kontrollera.")

        if not args.store and len(stores) < 70:
            print("VARNING: butiklistan verkar ofullständig.")

        checkout_verify = CHECKOUT_VERIFY and not args.no_checkout

        if checkout_verify:
            print("Extra kassakontroll: PÅ")
        else:
            print("Extra kassakontroll: AV")

        all_rows = []
        all_30th = []
        failures = []

        for index, store in enumerate(stores, 1):
            print(
                f"\n[{index:02}/{len(stores)}] "
                f"{store['name']} | {store.get('city','')} "
                f"({store['store_id']})"
            )

            try:
                url = search_url(store["store_id"])
                driver.get(url)
                wait_for_search_page(driver)
                accept_cookies_if_present(driver)
                time.sleep(1.0)
                scroll_search_results(driver)

                rows = scrape_visible_product_cards(driver, store)

                deduped = []
                seen = set()

                for row in rows:
                    key = (
                        row["sku"]
                        or row["url"]
                        or row["product"]
                    ).lower()

                    if key in seen:
                        continue

                    seen.add(key)
                    deduped.append(row)

                tcg_rows = [
                    row
                    for row in deduped
                    if looks_like_tcg(row["product"])
                ]

                if checkout_verify:
                    verify_store_rows(
                        driver,
                        tcg_rows,
                        only_search_in_stock=not args.verify_all,
                    )

                store_30th = [
                    row
                    for row in tcg_rows
                    if looks_like_30th(row["product"])
                ]

                all_rows.extend(tcg_rows)
                all_30th.extend(store_30th)

                print(
                    f"    TCG-träffar: {len(tcg_rows)}, "
                    f"30th-träffar: {len(store_30th)}"
                )

                for row in tcg_rows[:8]:
                    status = (
                        "✅"
                        if row["in_stock"] is True
                        else "❌"
                        if row["in_stock"] is False
                        else "?"
                    )

                    price = (
                        f" | {row['price']} kr"
                        if row["price"]
                        else ""
                    )

                    checkout = ""

                    if row.get("checkout_stock") is not None:
                        checkout = (
                            " | kassa="
                            + stock_text(row["checkout_stock"])
                        )

                    print(
                        f"      {status} "
                        f"{row['product']}"
                        f"{price}"
                        f"{checkout}"
                    )

                if len(tcg_rows) > 8:
                    print(f"      ... +{len(tcg_rows) - 8} till")

                if index % 10 == 0:
                    save_csv(all_rows, out_all)
                    save_csv(all_30th, out_30th)

            except KeyboardInterrupt:
                print(
                    "\nAvbrutet av användaren. "
                    "Sparar det som hunnit hittas..."
                )
                break

            except Exception as exc:
                failures.append((store["name"], str(exc)))
                print(f"    ! FEL: {exc}")

            time.sleep(STORE_DELAY)

        all_rows.sort(
            key=lambda row: (
                row.get("city", "").lower(),
                product_type(row["product"]).lower(),
                row["store"].lower(),
                row["product"].lower(),
            )
        )

        all_30th.sort(
            key=lambda row: (
                row["in_stock"] is not True,
                row.get("city", "").lower(),
                product_type(row["product"]).lower(),
                row["store"].lower(),
                row["product"].lower(),
            )
        )

        save_csv(all_rows, out_all)
        save_csv(all_30th, out_30th)

        checkout_checked = sum(
            row.get("checkout_stock") is not None
            for row in all_rows
        )

        checkout_out = sum(
            row.get("checkout_stock") is False
            for row in all_rows
        )

        print("\n" + "=" * 72)
        print(f"Klar {datetime.now():%Y-%m-%d %H:%M:%S}")
        print(f"Maxi-butiker i listan: {len(stores)}")
        print(f"Pokémon TCG-rader: {len(all_rows)}")
        print(f"30th-rader totalt: {len(all_30th)}")
        print(
            "30th markerade i lager: "
            f"{sum(row['in_stock'] is True for row in all_30th)}"
        )

        if checkout_verify:
            print(f"Kassaverifierade rader: {checkout_checked}")
            print(f"Kassan ändrade till slut/ej tillgänglig: {checkout_out}")

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
