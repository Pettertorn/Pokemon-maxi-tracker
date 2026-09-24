# Pokémon Maxi Tracker

Ett Python-script som söker igenom **Maxi ICA Stormarknads onlinebutiker** efter Pokémon TCG-produkter.

Scriptet går automatiskt igenom Maxi-butiker med onlinehandel, söker efter `pokemon` och sparar resultaten i CSV-filer som enkelt kan öppnas i Excel.

## Funktioner

* Hämtar Maxi ICA-butiker som erbjuder onlinehandel
* Söker efter Pokémon-produkter i varje butik
* Filtrerar bort produkter som inte är Pokémon TCG, exempelvis:

  * LEGO
  * böcker
  * leksaker
  * godis
  * kalasartiklar
* Identifierar olika typer av Pokémon TCG-produkter
* Försöker identifiera vilket set produkten tillhör
* Visar pris och lagerstatus
* Letar specifikt efter produkter relaterade till **Pokémon 30th Celebration**
* Exporterar resultaten till CSV
* Sparar automatiskt resultat under körningen

## Krav

* Python 3
* Google Chrome eller Microsoft Edge
* Selenium

Installera Python-paketet med:

```bash
pip install -r requirements.txt
```

`requirements.txt` kan innehålla:
