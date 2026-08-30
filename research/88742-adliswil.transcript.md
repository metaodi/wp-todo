# Protokoll: Adliswil

> **Rohprotokoll eines Sprachmodells. Kein Befund, keine Quelle, kein Artikeltext.**
>
> Diese Datei enthält, was das Modell gefragt wurde und was es geantwortet hat -
> **einschliesslich der Antworten, die anschliessend maschinell verworfen wurden**,
> weil das Zitat nicht im Dokument stand oder das Dokument eine Kopie des Artikels
> war. Eine Antwort hier ist also nicht einmal ein Hinweis, solange sie unten nicht
> als Befund im Dossier auftaucht - und auch dann gilt: erst am Beleg prüfen.
>
> Sie steht hier, damit nachvollziehbar ist, wie das Dossier zustande kam.

[Dossier](./88742-adliswil.md) · [Artikel](https://de.wikipedia.org/wiki/Adliswil)

Modell: `claude-opus-5` · Effort: `medium` · 4 Aufruf(e), davon 0 aus dem Cache · Budget: 10

Dokumente vorgelegt: 9 (4 davon Belege des Artikels selbst) · Websuche: ja

## Aufrufe

### 1. reference_check — `marker_stand_year-5e6444d3` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: Adliswil
Abschnitt: Bevölkerung
Stand laut Artikel: 2022

Angabe im Artikel:
* Konfessionszugehörigkeit: 18,02 % [[Reformierte Kirchen|evangelisch-reformiert]], 25,84 % [[Römisch-katholische Kirche|römisch-katholisch]], 56,14 % andere oder keine konfessionelle Zugehörigkeit (S

Die Dokumente sind die Belege, die der Artikel selbst zitiert.
Sagt eines der Dokumente etwas Neueres oder Abweichendes zu genau dieser Angabe? Wenn nicht: status = nothing_found.
```

</details>

Geantwortet:

```json
{
  "as_of": null,
  "confidence": 0.97,
  "current_value": null,
  "document": 0,
  "quote": "",
  "reasoning": "Keines der vier Dokumente enthält Angaben zur Konfessionszugehörigkeit der Bevölkerung von Adliswil. Es handelt sich um Navigationsmenüs, News-Meldungen und einen Hinweis auf Wahlen 2019.",
  "status": "nothing_found"
}
```

Tokens: 246 rein, 145 raus

### 2. search — `Adliswil` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: Adliswil

Diese Angaben konnten die Belege des Artikels nicht klären:
- * Konfessionszugehörigkeit: 18,02 % [[Reformierte Kirchen|evangelisch-reformiert]], 25,84 % [[Römisch-katholische Kirche|römisch-katholisch]], 56,14 % andere oder keine konfessionelle Zugehörigkeit (S (Stand 2022)

Suche nach aktuellen, möglichst amtlichen deutschsprachigen Quellen zu diesen Angaben. Gib danach die verwendeten Suchanfragen zurück.
```

</details>

Von der Websuche zurückgegeben (aus den Ergebnisblöcken gelesen, nicht aus dem Antworttext):

- <https://fowid.de/meldung/religionen-schweiz>
- <https://www.eda.admin.ch/aboutswitzerland/de/home/gesellschaft/religionen/religionen---fakten-und-zahlen.html>
- <https://de.wikipedia.org/wiki/Religionen_in_der_Schweiz>
- <https://kirchenstatistik.spi-sg.ch/religionslandschaft-schweiz/>
- <https://adliswil-kath.ch/ueber-uns/geschichte/>
- <https://www.census1850.bfs.admin.ch/de/religionslandschaft.html>
- <https://www.kath-adliswil.ch/>
- <https://dewiki.de/Lexikon/Adliswil>
- <https://www.web.statistik.zh.ch/ogd/daten/ressourcen/KTZH_00000737_00001877.pdf>
- <https://zgz.statistik.zh.ch/publikation/bevoelkerung-konfession?gc=11&gtc=3>
- <https://www.wikidata.org/wiki/Q68210>
- <https://de.statista.com/statistik/daten/studie/1235141/umfrage/bevoelkerung-des-kantons-zuerich-nach-bezirken>
- <https://de.statista.com/themen/8082/zuerich>
- <https://de.statista.com/statistik/daten/studie/1244027/umfrage/religionszugehoerigkeit-in-der-stadt-zuerich>
- <https://de.wikipedia.org/wiki/de:Adliswil?uselang=en>
- <https://www.zh.ch/de/politik-staat/gemeinden/gemeindeportraet.html>
- <https://www.adliswil.ch/gemeindeinzahlen>
- <https://gemeinde-schweiz.ch/kanton-zuerich/adliswil/>
- <https://www.kirchgemeindeplus.ch/arbeitshilfen/statistiken/>
- <https://zgz.statistik.zh.ch/publikation/bevoelkerung-konfession>
- <https://www.zh.ch/de/news-uebersicht/medienmitteilungen/2013/02/034_bevoelkerungserhebung2012.html>
- <https://www.adliswil.ch/geschichte/6989>
- <https://zgz.statistik.zh.ch/?amp=&gc=121&gtc=3>
- <https://www.raiffeisen.ch/rch/de/privatkunden/wohnen-und-hypotheken/gemeindeinfo.adliswil.html>
- <https://www.web.statistik.zh.ch/gpv2/>
- <https://www.citypopulation.de/de/switzerland/zurich/>
- <https://de.statista.com/statistik/daten/studie/770744/umfrage/bevoelkerungsanteil-der-konfessionslosen-in-der-schweiz>
- <https://www.bischoefe.ch/kirchenstatistik-2024/>
- <https://www.zugerkb.ch/docs/default-source/die-zgkb/zug-in-zahlen-2024.pdf?sfvrsn=c5c08c8c_4>
- <https://www.lustat.ch/analysen/politik-gesellschaft/2024/religionen>
- <https://fowid.de/meldung/schweiz-kirchenmitglieder-kasualien-2017-2024>
- <https://de.statista.com/statistik/stichworte/letter/p/572>

Geantwortet:

```json
{
  "note": "Die Suche zielte auf amtliche Quellen des Statistischen Amts des Kantons Zürich (Gemeindeporträt bzw. dessen Nachfolger «Zürcher Gemeinden in Zahlen», Publikation «Bevölkerung nach Konfession»), da die Angabe im Artikel genau von dort stammt. Die Trefferliste führte zwar zur zuständigen amtlichen Publikationsseite, lieferte in den Suchergebnissen aber keine konkreten, gemeindespezifischen Prozentwerte für Adliswil (die Werte werden dort dynamisch je Gemeinde ausgegeben). Gefundene Zahlen betrafen nur die Schweiz oder andere Kantone und sind für die Aussage nicht verwertbar. Ohne ein Dokument mit wörtlich zitierbaren Adliswil-Werten wäre die korrekte Bewertung 'nothing_found' – geraten wird nicht.",
  "queries": [
    "Adliswil Konfession evangelisch-reformiert römisch-katholisch Anteil",
    "Adliswil Bevölkerung Religion Statistik Kanton Zürich",
    "Gemeindeporträt Adliswil Statistisches Amt Kanton Zürich Konfession",
    "Zürcher Gemeinden in Zahlen Adliswil Bevölkerung nach Konfession",
    "Adliswil evangelisch-reformiert Prozent römisch-katholisch 2024 Statistik"
  ]
}
```

Tokens: 27611 rein, 1101 raus

### 3. web_check — `marker_stand_year-5e6444d3` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: Adliswil
Abschnitt: Bevölkerung
Stand laut Artikel: 2022

Angabe im Artikel:
* Konfessionszugehörigkeit: 18,02 % [[Reformierte Kirchen|evangelisch-reformiert]], 25,84 % [[Römisch-katholische Kirche|römisch-katholisch]], 56,14 % andere oder keine konfessionelle Zugehörigkeit (S

Die Dokumente stammen aus einer Websuche.
Sagt eines der Dokumente etwas Neueres oder Abweichendes zu genau dieser Angabe? Wenn nicht: status = nothing_found.
```

</details>

Geantwortet:

```json
{
  "as_of": null,
  "confidence": 0.93,
  "current_value": null,
  "document": 0,
  "quote": "",
  "reasoning": "Die Angabe betrifft die Konfessionszugehörigkeit der Bevölkerung von Adliswil. Alle Dokumente behandeln jedoch nur die Religionslandschaft der Schweiz gesamt (Dok. 1-4) bzw. die Baugeschichte der katholischen Kirche Adliswil (Dok. 5, mit Zahlen von 1904). Keines nennt aktuelle Konfessionsanteile für die Gemeinde Adliswil.",
  "status": "nothing_found"
}
```

Tokens: 237 rein, 266 raus

### 4. sections — `fehlende Abschnitte` (neu angefragt)

<details><summary>Gefragt</summary>

```
Fasse jeden Abschnitt in zwei bis vier Stichpunkten zusammen: worüber er handelt, welche Zahlen oder Ereignisse darin vorkommen. Kein Fliesstext, keine Wertung. Gib zu jedem Abschnitt seine Nummer aus der Liste an.
```

</details>

Geantwortet:

```json
{
  "sections": [
    {
      "bullets": [
        "Zuständigkeit für die öffentlichen Schulen (Primar- und Sekundarstufe) liegt bei der Schulpflege der Gemeinde, bestehend aus neun gewählten Mitgliedern",
        "Tabelle mit den öffentlichen Schulen Adliswils: Name, Schultyp (u.a. Sekundar A, Sekundar B/C, Aufnahmeklasse), Schulhaus/Standort, Zahl der Klassen, Lehrpersonen und Schüler",
        "Beispiele: Hofern (4 bzw. 5 Klassen, 9 Klassenlehrpersonen, 13 Fachlehrpersonen, 2 Logopäden, ca. 170 Schüler) sowie Zentrum Kronenwiese",
        "Belege überwiegend aus Webseiten der Stadt Adliswil und Kartenlinks (search.ch)"
      ],
      "section": 1
    },
    {
      "bullets": [
        "Die Zurich International School (ZIS) betreibt ihren Oberstufen-Campus (Upper School) in Adliswil",
        "Privatschule mit englischsprachigem Unterricht; kantonale Genehmigung bis zum Ende der Schulpflicht",
        "Programm für Schüler von 3 bis 18 Jahren, akkreditiert durch die Commission on International Education (NEASC); IB-Diplom von der International Baccalaureate Organisation anerkannt"
      ],
      "section": 2
    }
  ]
}
```

Tokens: 113 rein, 503 raus

## Von den Prüfungen verworfen

_Nichts verworfen._
