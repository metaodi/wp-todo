# Protokoll: Hausen am Albis

> **Rohprotokoll eines Sprachmodells. Kein Befund, keine Quelle, kein Artikeltext.**
>
> Diese Datei enthält, was das Modell gefragt wurde und was es geantwortet hat -
> **einschliesslich der Antworten, die anschliessend maschinell verworfen wurden**,
> weil das Zitat nicht im Dokument stand oder das Dokument eine Kopie des Artikels
> war. Eine Antwort hier ist also nicht einmal ein Hinweis, solange sie unten nicht
> als Befund im Dossier auftaucht - und auch dann gilt: erst am Beleg prüfen.
>
> Sie steht hier, damit nachvollziehbar ist, wie das Dossier zustande kam.

[Dossier](./422720-hausen-am-albis.md) · [Artikel](https://de.wikipedia.org/wiki/Hausen_am_Albis)

Modell: `claude-opus-5` · Effort: `medium` · 10 Aufruf(e), davon 0 aus dem Cache · Budget: 16

Dokumente vorgelegt: 11 (5 davon Belege des Artikels selbst) · Websuche: ja

## Aufrufe

### 1. reference_check — `infobox_field-6ae0c0fc` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: Hausen am Albis
Angabe: Fläche

Der Artikel sagt: 13.64
Wikidata sagt: 13.6 (Stand 2007)

Die Dokumente sind die Belege und Weblinks des Artikels.
Diese beiden Angaben widersprechen sich. Stützt eines der Dokumente die eine oder die andere - oder nennt es einen dritten, neueren Wert? current_value = der Wert, den das Dokument belegt, quote = wörtlich aus dem Dokument. Wenn keines der Dokumente die Frage entscheidet: status = nothing_found. Rate nicht, und entscheide nicht danach, welche Seite plausibler wirkt.
```

</details>

Geantwortet:

```json
{
  "as_of": null,
  "confidence": 0.6,
  "current_value": null,
  "document": 0,
  "quote": "",
  "reasoning": "Keines der vorgelegten Dokumente ist ein verwertbarer Beleg für die Fläche. Dokument 1 (Amt für Raumentwicklung) enthält keine Flächenangabe zu Hausen am Albis, Dokument 2 ist inhaltsleer ('Wahlen 2019'). Die Dokumente 3, 4 und 5 sind ausdrücklich als andere Sprachversionen desselben Artikels eingestuft und damit kein Beleg, sondern nur ein Hinweis - die dort genannten 13,64 bzw. 13,7 km² können den Widerspruch zwischen Artikel (13.64) und Wikidata (13.6) daher nicht entscheiden. Dokument 4 nennt überhaupt keine Fläche.",
  "status": "nothing_found"
}
```

Tokens: 246 rein, 658 raus

### 2. reference_check — `infobox_field-2b259aae` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: Hausen am Albis
Angabe: Einwohnerzahl

Der Artikel sagt: 3235 (Stand 2007)
Wikidata sagt: 3646 (Stand 2017)

Die Dokumente sind die Belege und Weblinks des Artikels.
Diese beiden Angaben widersprechen sich. Stützt eines der Dokumente die eine oder die andere - oder nennt es einen dritten, neueren Wert? current_value = der Wert, den das Dokument belegt, quote = wörtlich aus dem Dokument. Wenn keines der Dokumente die Frage entscheidet: status = nothing_found. Rate nicht, und entscheide nicht danach, welche Seite plausibler wirkt.
```

</details>

Geantwortet:

```json
{
  "as_of": null,
  "confidence": 0.7,
  "current_value": null,
  "document": 0,
  "quote": "",
  "reasoning": "Die einzigen konkreten Einwohnerzahlen stehen in den Dokumenten 3, 4 und 5, die ausdrücklich als andere Sprachversionen des Artikels gekennzeichnet sind - also kein Beleg, sondern nur ein Hinweis (Dokument 5 nennt 3571 Einwohner, Stand 2016; Dokument 3 verwendet lediglich Vorlagen ohne konkrete Zahl). Dokument 1 (Amt fuer Raumentwicklung) und Dokument 2 enthalten keine Einwohnerzahl fuer Hausen am Albis. Damit entscheidet kein belegfaehiges Dokument die Frage.",
  "status": "nothing_found"
}
```

Tokens: 258 rein, 527 raus

### 3. reference_check — `marker_stand_year-36e33e74` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: Hausen am Albis
Abschnitt: Bevölkerung
Stand laut Artikel: 2022

Angabe im Artikel:
* Konfessionszugehörigkeit: 32,15 % [[Reformierte Kirchen|evangelisch-reformiert]], 25,93 % [[Römisch-katholische Kirche|römisch-katholisch]], 41,92 % andere oder keine konfessionelle Zugehörigkeit (S

Die Dokumente sind die Belege, die der Artikel selbst zitiert.
Sagt eines der Dokumente etwas Neueres oder Abweichendes zu genau dieser Angabe? Wenn nicht: status = nothing_found.
```

</details>

Geantwortet:

```json
{
  "as_of": null,
  "confidence": 0.95,
  "current_value": null,
  "document": 0,
  "quote": "",
  "reasoning": "Keines der Dokumente enthält Angaben zur Konfessionszugehörigkeit der Bevölkerung von Hausen am Albis. Dokument 1 betrifft das Amt für Raumentwicklung, Dokument 2 ist inhaltsleer, Dokumente 3-5 sind andere Sprachversionen ohne Konfessionsdaten.",
  "status": "nothing_found"
}
```

Tokens: 247 rein, 178 raus

### 4. reference_check — `infobox_field-e377f032` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: Hausen am Albis
Abschnitt: GEMEINDEPRÄSIDENT
Kein Stand angegeben

Angabe im Artikel:
GEMEINDEPRÄSIDENT = Stefan Gyseler ([[FDP.Die Liberalen|FDP]])

Die Dokumente sind die Belege, die der Artikel selbst zitiert.
Sagt eines der Dokumente etwas Neueres oder Abweichendes zu genau dieser Angabe? Wenn nicht: status = nothing_found.
```

</details>

Geantwortet:

```json
{
  "as_of": null,
  "confidence": 0.5,
  "current_value": "René Hess",
  "document": 4,
  "quote": "| syndic/maire        = René Hess",
  "reasoning": "Nur die französische Sprachversion (Dokument 4) nennt einen Gemeindepräsidenten, und zwar René Hess, was der Artikelangabe \"Stefan Gyseler (FDP)\" widerspricht. Es ist jedoch kein Beleg, sondern nur ein Hinweis aus einer anderen Sprachversion, ohne Stand und ohne Parteiangabe; eine belastbare Aktualisierung erfordert eine amtliche Quelle. Die amtliche Quelle (Dokument 1) betrifft das Amt für Raumentwicklung und sagt nichts zur Gemeinde.",
  "status": "contradicts_current"
}
```

Tokens: 193 rein, 408 raus

### 5. reference_check — `infobox_field-62028a67` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: Hausen am Albis
Abschnitt: WEBSITE
Kein Stand angegeben

Angabe im Artikel:
WEBSITE = www.hausen.ch

Die Dokumente sind die Belege, die der Artikel selbst zitiert.
Sagt eines der Dokumente etwas Neueres oder Abweichendes zu genau dieser Angabe? Wenn nicht: status = nothing_found.
```

</details>

Geantwortet:

```json
{
  "as_of": null,
  "confidence": 0.6,
  "current_value": "www.hausen.ch",
  "document": 3,
  "quote": "| website               = www.hausen.ch",
  "reasoning": "Die englische Sprachversion (Dokument 3) nennt im Infobox-Feld ebenfalls www.hausen.ch, ebenso die franzoesische Version. Damit wird die Angabe bestaetigt, nichts Neueres oder Abweichendes. Zu beachten: Sprachversionen sind kein Beleg, sondern nur ein Hinweis; ein amtlicher Nachweis fehlt in den Dokumenten (Dokument 1 betrifft das Amt fuer Raumentwicklung, nicht die Gemeinde).",
  "status": "confirms_current"
}
```

Tokens: 152 rein, 383 raus

### 6. search — `Hausen am Albis` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: Hausen am Albis

Diese Angaben konnten die Belege des Artikels nicht klären:
- FLÄCHE = 13.64
- EINWOHNER = 3235 (Stand 2007)
- * Konfessionszugehörigkeit: 32,15 % [[Reformierte Kirchen|evangelisch-reformiert]], 25,93 % [[Römisch-katholische Kirche|römisch-katholisch]], 41,92 % andere oder keine konfessionelle Zugehörigkeit (S (Stand 2022)

Suche nach aktuellen, möglichst amtlichen deutschsprachigen Quellen zu diesen Angaben. Gib danach die verwendeten Suchanfragen zurück.
```

</details>

Von der Websuche zurückgegeben (aus den Ergebnisblöcken gelesen, nicht aus dem Antworttext):

- <https://www.raiffeisen.ch/rch/de/privatkunden/wohnen-und-hypotheken/gemeindeinfo.hausen-am-albis.html>
- <https://www.hausen.ch/gemeindeinzahlen>
- <https://www.raiffeisen.ch/rch/de/privatkunden/hypotheken/gemeindeinfo.hausen-am-albis.html>
- <https://www.citypopulation.de/de/switzerland/zurich/bezirk_affoltern/0004__hausen_am_albis/>
- <https://www.hausen.ch/willkommen>
- <https://www.localcities.ch/de/gemeinde/hausen-am-albis/37>
- <https://ugeo.urbistat.com/AdminStat/de/ch/demografia/dati-sintesi/hausen-am-albis/20138151/4>
- <https://en.wikipedia.org/wiki/Hausen_am_Albis>
- <https://dewiki.de/Lexikon/Hausen_am_Albis>
- <https://www.wikidata.org/wiki/Q65231>
- <https://www.obfelden.info/sommer-serie-1-ist-obfelden-die-zweitgroesste-gemeinde-im-bezirk-affoltern/>
- <https://de.wikipedia.org/wiki/Reformierte_Kirche_Hausen_am_Albis>
- <https://www.hausen.ch/kirchen>
- <https://www.hausen.ch/>
- <https://freizeitmonster.de/blog/kathedralen-hausen-am-albis>
- <https://www.wiki.de-de.nina.az/Ebertswil.html>
- <https://www.raiffeisen.ch/rch/de/privatkunden/wohnen-und-hypotheken/gemeindeinfo.hausen-am-albis.hausen-am-albis.html>
- <https://de.wikipedia.org/wiki/Gemeinden_des_Kantons_Z%C3%BCrich>
- <https://gemeinde-schweiz.ch/kanton-zuerich/hausen-am-albis/>
- <https://www.zh.ch/de/politik-staat/gemeinden/gemeindeportraet.html>
- <https://en.wikipedia.org/wiki/Affoltern_am_Albis>
- <https://raiffeisen.ch/rch/de/privatkunden/hypotheken/hypopedia/gemeindeinfo.hausen-am-albis.html>

Geantwortet:

```json
{
  "note": "Gesucht wurde nach amtlichen deutschsprachigen Quellen (Statistisches Amt Kanton Zürich, BFS-Gemeindeporträt, Gemeindewebsite hausen.ch) zu Fläche, Einwohnerzahl und Konfessionsanteilen von Hausen am Albis. Die Trefferliste enthielt bisher vor allem nichtamtliche Portale (z.B. Bankenvergleichsseiten) sowie Wikipedia und die Gemeindeseite zu Kirchen - keines der bisher gesichteten Dokumente enthielt belastbare Zahlenwerte zu Fläche, aktueller Einwohnerzahl oder Konfessionsanteilen. Das Suchkontingent wurde vor Abschluss weiterer Abfragen (BFS-/kantonales Gemeindeporträt, Bevölkerungsstatistik) erreicht; diese drei Anfragen wurden angesetzt, lieferten aber keine auswertbaren Ergebnisse mehr. Auf Basis der vorliegenden Dokumente wäre daher 'nothing_found' die korrekte Bewertung.",
  "queries": [
    "Hausen am Albis Einwohnerzahl Fläche amtliche Statistik",
    "Hausen am Albis Konfession evangelisch-reformiert römisch-katholisch Gemeindeporträt",
    "Gemeindeporträt Hausen am Albis Statistisches Amt Kanton Zürich Fläche Einwohner",
    "\"Hausen am Albis\" Bundesamt für Statistik Gemeindeportrait Gesamtfläche ha Bevölkerung 2024",
    "Hausen am Albis Bevölkerungsstatistik 2024 Wohnbevölkerung Gemeinde hausen.ch"
  ]
}
```

Tokens: 39909 rein, 1433 raus

### 7. web_check — `infobox_field-6ae0c0fc` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: Hausen am Albis
Angabe: Fläche

Der Artikel sagt: 13.64
Wikidata sagt: 13.6 (Stand 2007)

Die Dokumente stammen aus einer Websuche.
Diese beiden Angaben widersprechen sich. Stützt eines der Dokumente die eine oder die andere - oder nennt es einen dritten, neueren Wert? current_value = der Wert, den das Dokument belegt, quote = wörtlich aus dem Dokument. Wenn keines der Dokumente die Frage entscheidet: status = nothing_found. Rate nicht, und entscheide nicht danach, welche Seite plausibler wirkt.
```

</details>

Geantwortet:

```json
{
  "as_of": null,
  "confidence": 0.9,
  "current_value": null,
  "document": 0,
  "quote": "",
  "reasoning": "Keines der Dokumente nennt die Fläche der Gemeinde Hausen am Albis. Dokument 6 gibt lediglich eine Bevölkerungsdichte (292) an, ohne Flächenangabe; daraus liesse sich nur rechnen, was kein Beleg ist. Die übrigen Dokumente behandeln Einwohnerzahlen, Steuerfüsse und Immobilienstatistiken.",
  "status": "nothing_found"
}
```

Tokens: 240 rein, 259 raus

### 8. web_check — `infobox_field-2b259aae` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: Hausen am Albis
Angabe: Einwohnerzahl

Der Artikel sagt: 3235 (Stand 2007)
Wikidata sagt: 3646 (Stand 2017)

Die Dokumente stammen aus einer Websuche.
Diese beiden Angaben widersprechen sich. Stützt eines der Dokumente die eine oder die andere - oder nennt es einen dritten, neueren Wert? current_value = der Wert, den das Dokument belegt, quote = wörtlich aus dem Dokument. Wenn keines der Dokumente die Frage entscheidet: status = nothing_found. Rate nicht, und entscheide nicht danach, welche Seite plausibler wirkt.
```

</details>

Geantwortet:

```json
{
  "as_of": 2025,
  "confidence": 0.9,
  "current_value": "3950 Einwohner (Stand 31.12.2025)",
  "document": 2,
  "quote": "Einwohnerstatistik per 31.12.2025\n\n2024\n2025\nDifferenz\n\nEinwohner\n3933\n3950\n+ 0.43 %",
  "reasoning": "Dokument 2 ist die offizielle Gemeindewebsite und nennt eine Einwohnerstatistik per 31.12.2025 mit 3950 Einwohnern. Das ist neuer als sowohl der Artikelwert (3235, 2007) als auch der Wikidata-Wert (3646, 2017). Dokument 4 (3.955, Schätzung 2025, vorläufig) und Dokument 6 (3'974, Stand 2023) weichen leicht ab, aber die offizielle Gemeindestatistik ist die belastbarste und explizit datierte Quelle.",
  "status": "supersedes_with_newer_value"
}
```

Tokens: 252 rein, 521 raus

### 9. web_check — `marker_stand_year-36e33e74` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: Hausen am Albis
Abschnitt: Bevölkerung
Stand laut Artikel: 2022

Angabe im Artikel:
* Konfessionszugehörigkeit: 32,15 % [[Reformierte Kirchen|evangelisch-reformiert]], 25,93 % [[Römisch-katholische Kirche|römisch-katholisch]], 41,92 % andere oder keine konfessionelle Zugehörigkeit (S

Die Dokumente stammen aus einer Websuche.
Sagt eines der Dokumente etwas Neueres oder Abweichendes zu genau dieser Angabe? Wenn nicht: status = nothing_found.
```

</details>

Geantwortet:

```json
{
  "as_of": 2025,
  "confidence": 0.72,
  "current_value": "Per 31.12.2025: 1159 reformierte Einwohner (29,3 %) und 904 katholische Einwohner (22,9 %) von insgesamt 3950 Einwohnern",
  "document": 2,
  "quote": "Reformierte Einwohner\n1208\n1159\n- 4.06 %\n\nKatholische Einwohner\n949\n904\n- 4.74 %",
  "reasoning": "Die offizielle Gemeindestatistik (Dokument 2) nennt für den 31.12.2025 absolute Zahlen der reformierten (1159) und katholischen (904) Einwohner bei 3950 Einwohnern. Daraus ergeben sich Anteile von ca. 29,3 % bzw. 22,9 %, was die im Artikel stehenden Werte von 2022 (32,15 % / 25,93 %) durch neuere Daten überholt. Die anderen Dokumente enthalten keine Konfessionsangaben.",
  "status": "supersedes_with_newer_value"
}
```

Tokens: 238 rein, 642 raus

### 10. sections — `fehlende Abschnitte` (neu angefragt)

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
        "Auflistung von Personen mit Bezug zum Ort",
        "Nennt Wilhelm Scheuchzer (1803–1866), Schweizer Landschaftsmaler",
        "Angabe: geboren in Hausen am Albis"
      ],
      "section": 1
    },
    {
      "bullets": [
        "Reiner Apparat-Abschnitt: Einzelnachweis-Liste (references-Tag)",
        "Zusätzlich Verweis auf Schwesterprojekte: Commons-Kategorie zu Hausen am Albis",
        "Kein inhaltlicher Text, nichts zu übersetzen"
      ],
      "section": 2
    }
  ]
}
```

Tokens: 111 rein, 219 raus

## Von den Prüfungen verworfen

| Prüfung | Angabe | Detail | Dokument |
| --- | --- | --- | --- |
| unreadable | `—` | robots.txt verbietet den Abruf | <https://www.web.statistik.zh.ch/gpv2/?tab=indikatoren&jahr=2022&indikatoren=405,406,408&bfs=4> |
| unreadable | `—` | robots.txt verbietet den Abruf | <https://app.statistik.zh.ch/wahlen_abstimmungen/prod/Actual/Det/0_0_20231022/247586> |
| unreadable | `—` | nach 3 Versuch(en) nicht erreichbar | <http://dav0.bgdi.admin.ch/kogis_web/downloads/ivs/beschr/fr/ZH11230000.pdf> |
| unreadable | `—` | robots.txt verbietet den Abruf | <https://oerebdocs.zh.ch/getDoc?docid=10095> |
| unreadable | `—` | robots.txt verbietet den Abruf | <https://www.web.statistik.zh.ch/gpv2/?tab=indikatoren&jahr=0&indikatoren=&bfs=4> |
| unreadable | `—` | robots.txt verbietet den Abruf | <https://www.web.statistik.zh.ch/gpv2/?tab=indikatoren&jahr=2022&indikatoren=133&bfs=4> |
