# Protokoll: Küsnachter Dorfbach

> **Rohprotokoll eines Sprachmodells. Kein Befund, keine Quelle, kein Artikeltext.**
>
> Diese Datei enthält, was das Modell gefragt wurde und was es geantwortet hat -
> **einschliesslich der Antworten, die anschliessend maschinell verworfen wurden**,
> weil das Zitat nicht im Dokument stand oder das Dokument eine Kopie des Artikels
> war. Eine Antwort hier ist also nicht einmal ein Hinweis, solange sie unten nicht
> als Befund im Dossier auftaucht - und auch dann gilt: erst am Beleg prüfen.
>
> Sie steht hier, damit nachvollziehbar ist, wie das Dossier zustande kam.

[Dossier](./7649444-kuesnachter-dorfbach.md) · [Artikel](https://de.wikipedia.org/wiki/Küsnachter_Dorfbach)

Modell: `claude-opus-5` · Effort: `medium` · 6 Aufruf(e), davon 0 aus dem Cache · Budget: 10

Dokumente vorgelegt: 8 (4 davon Belege des Artikels selbst) · Websuche: ja

## Aufrufe

### 1. reference_check — `zukunft_template-6cba6779` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: Küsnachter Dorfbach
Abschnitt: —
Stand laut Artikel: 2025

Angabe im Artikel:
{{Zukunft}}: dieser Abschnitt sollte seit 2025-05-01 überprüft werden

Die Dokumente sind die Belege, die der Artikel selbst zitiert.
Sagt eines der Dokumente etwas Neueres oder Abweichendes zu genau dieser Angabe? Wenn nicht: status = nothing_found.
```

</details>

Geantwortet:

```json
{
  "as_of": null,
  "confidence": 0.85,
  "current_value": null,
  "document": 0,
  "quote": "",
  "reasoning": "Die Angabe im Artikel ist ein Wartungsbaustein ({{Zukunft}}), der auf eine zu überprüfende Zukunftsaussage hinweist. Keines der vorgelegten Dokumente enthält eine neuere oder abweichende Information dazu: Dokument 1 und 3 sind reine Navigations-/Amtsseiten des Kantons Zürich ohne Bezug zum Dorfbach, Dokument 2 liefert nur Einzugsgebietskennzahlen, Dokument 4 ist ein NZZ-Artikel von 2011 über den Fischpass, also älter und ohne Aktualisierung des Stands.\n",
  "status": "nothing_found"
}
```

Tokens: 179 rein, 288 raus

### 2. reference_check — `marker_derzeit-014245ea` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: Küsnachter Dorfbach
Abschnitt: Entstehung
Stand laut Artikel: 1960

Angabe im Artikel:
Die Entstehungsgeschichte des Tobels lässt sich zunächst an zahlreichen fossilen Funden ablesen: Muscheln, Schnecken, versteinerte Blätter von Zimt- und Lorbeergewächsen, ein als Palmast interpretiert

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
  "reasoning": "Keines der vier Dokumente behandelt die Entstehungsgeschichte des Tobels oder fossile Funde. Dokument 1 und 3 sind allgemeine Amtsseiten, Dokument 2 enthält Einzugsgebietsdaten, Dokument 4 behandelt Fischpass und Seeforellen.",
  "status": "nothing_found"
}
```

Tokens: 242 rein, 167 raus

### 3. search — `Küsnachter Dorfbach` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: Küsnachter Dorfbach

Diese Angaben konnten die Belege des Artikels nicht klären:
- {{Zukunft}}: dieser Abschnitt sollte seit 2025-05-01 überprüft werden (Stand 2025)
- Die Entstehungsgeschichte des Tobels lässt sich zunächst an zahlreichen fossilen Funden ablesen: Muscheln, Schnecken, versteinerte Blätter von Zimt- und Lorbeergewächsen, ein als Palmast interpretiert (Stand 1960)

Suche nach aktuellen, möglichst amtlichen deutschsprachigen Quellen zu diesen Angaben. Gib danach die verwendeten Suchanfragen zurück.
```

</details>

Von der Websuche zurückgegeben (aus den Ergebnisblöcken gelesen, nicht aus dem Antworttext):

- <https://de.wikipedia.org/wiki/K%C3%BCsnachter_Dorfbach>
- <https://www.baselland.ch/politik-und-behorden/direktionen/bau-und-umweltschutzdirektion/tiefbauamt/projekte-baustellen/projekte/muttenz-hochwasserschutz-dorf>
- <https://www.luzernerzeitung.ch/zentralschweiz/schwyz/schwyz-hochwasserschutz-arbeiten-im-dorfbach-beginnen-im-herbst-ld.2591265>
- <https://www.bote.ch/nachrichten/schwyz/aeusserst-komplex-der-schutz-vor-hochwasser-am-dorfbach-art-1612123>
- <https://www.ag.ch/de/themen/staat-politik/anhoerungen-vernehmlassungen/laufende-anhoerungen?dc=d07bf23a-3f44-4f7e-bb45-127eda1f1ef6_de>
- <https://gemeinde-burgberg.de/hochwasserschutz-dorfbach/>
- <https://www.gemeindeschwyz.ch/umweltmobilitaet/55699>
- <https://bezirk-schwyz.ch/hochwasserschutz-dorfbach-schwyz/>
- <https://dewiki.de/Lexikon/K%C3%BCsnachter_Dorfbach>
- <https://ec.europa.eu/regional_policy/whats-new/newsroom/30-08-2023-eu-funds-flood-protection-in-dobeln-with-eur27-million_de>
- <https://en.wikipedia.org/wiki/K%C3%BCsnachter_Tobel>
- <https://erlebnis-geologie.ch/geoweg/geologischer-lehrpfad-kuesnachter-tobel/>
- <https://www.kuesnacht.ch/gemeinde/aktuelles.page/847/news/13971>
- <https://www.myswitzerland.com/en-us/experiences/kuesnachter-tobel/>
- <https://www.steinfreuden.de/deko/fossilien/>
- <https://www.edelsteine-mineralien.de/fossilien/>
- <https://www.fossnet.de/>
- <https://www.fossilienmarkt.de/>
- <https://www.kuesnacht.ch/gemeinde/aktuelles.page/847/news/14465>
- <https://www.kuesnacht.ch/page/312/news/12065>
- <https://www.badenertagblatt.ch/aargau/baden/wettingen-gefahr-oder-vorzeigeprojekt-am-entlastungsstollen-fuer-den-dorfbach-scheiden-sich-die-geister-ld.2724243>
- <https://www.nau.ch/ort/kusnacht-zh/der-dorfbach-in-kusnacht-zh-wird-saniert-66525395>
- <https://zukunft-wettingen.ch/projekte-gemeinde/hochwasserschutz>
- <https://geotouren-schwarzwald.de/kuesnacht-geologischer-lehrpfad-kuesnachter-tobel/>
- <https://geotouren-schwarzwald.de/category/schweiz/>
- <https://ngzh.ch/wp-content/uploads/2024/09/Neujahrsblatt_NGZH_2023.pdf>
- <https://www.vv-k.ch/taetigkeiten/drucksachen>
- <https://www.verschoenerungsverein-kuesnacht.ch/taetigkeiten/lehrpfade>
- <https://www.zh.ch/de/politik-staat/kanton/kantonale-verwaltung/beschaffung-einkaeufe/nachhaltige-beschaffung/beschaffungsleitlinien/wasserbau.html>
- <https://www.zh.ch/content/dam/zhweb/bilder-dokumente/themen/umwelt-tiere/wasser-gewaesser/gewaesserqualitaet/untersuchungsprogramm_seen_fliessgewaesser.pdf>
- <https://www.zh.ch/de/planen-bauen/wasserbau/wasserbauprojekte/hochwasserschutz-revitalisierungsprojekte-gemeinden.html>
- <https://www.zh.ch/de/planen-bauen/wasserbau/wasserbauprojekte/hallowasser.html>
- <https://www.zh.ch/de/planen-bauen/wasserbau/wasserbauprojekte.html>
- <https://www.zh.ch/de/planen-bauen/wasserbau/planungsgrundlagen-wasserbau.html>
- <https://www.zh.ch/de/planen-bauen/wasserbau.html>

Geantwortet:

```json
{
  "note": "Gesucht wurde zu zwei Punkten: (1) dem als überprüfungsbedürftig markierten Zukunfts-/Planungsabschnitt und (2) den Fossilfunden im Küsnachter Tobel (Stand 1960). Treffer gab es v. a. bei der Gemeinde Küsnacht (Ufermauer-Instandsetzung, Sohlensanierung/Fischrinne, Lehrpfade) sowie beim Kanton Zürich (AWEL, kommunale Wasserbauprojekte); zur Geologie führen der Geologische Lehrpfad Küsnachter Tobel und dessen Tafelbeschreibungen. Ob diese Dokumente die Artikelangaben tatsächlich überholen, lässt sich erst anhand der nummerierten Dokumente beurteilen - liegt keines vor, das die Angabe klar beantwortet, ist 'nothing_found' korrekt.",
  "queries": [
    "Küsnachter Dorfbach Hochwasserschutz Projekt 2025",
    "Küsnachter Tobel Fossilien Palmast Versteinerungen",
    "Küsnacht Dorfbach Hochwasserschutz Gemeinde Küsnacht Zukunft Bachöffnung",
    "Küsnachter Tobel Molasse Fossilien Blätter Zimtgewächse Lorbeer Palme Geologischer Lehrpfad",
    "Küsnacht Dorfbach Wasserbauprojekt Kanton Zürich AWEL"
  ]
}
```

Tokens: 23721 rein, 1125 raus

### 4. web_check — `zukunft_template-6cba6779` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: Küsnachter Dorfbach
Abschnitt: —
Stand laut Artikel: 2025

Angabe im Artikel:
{{Zukunft}}: dieser Abschnitt sollte seit 2025-05-01 überprüft werden

Die Dokumente stammen aus einer Websuche.
Sagt eines der Dokumente etwas Neueres oder Abweichendes zu genau dieser Angabe? Wenn nicht: status = nothing_found.
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
  "reasoning": "Die Dokumente 2, 3 und 4 betreffen andere Orte (Schwyz, Aargau, Burgberg im Allgäu) und nicht den Küsnachter Dorfbach. Dokument 1 ist der Wikipedia-Artikel selbst und enthält keine neuere Angabe zum mit {{Zukunft}} markierten Sachverhalt. Somit liefert keines der Dokumente eine aktualisierte Information.",
  "status": "nothing_found"
}
```

Tokens: 170 rein, 207 raus

### 5. web_check — `marker_derzeit-014245ea` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: Küsnachter Dorfbach
Abschnitt: Entstehung
Stand laut Artikel: 1960

Angabe im Artikel:
Die Entstehungsgeschichte des Tobels lässt sich zunächst an zahlreichen fossilen Funden ablesen: Muscheln, Schnecken, versteinerte Blätter von Zimt- und Lorbeergewächsen, ein als Palmast interpretiert

Die Dokumente stammen aus einer Websuche.
Sagt eines der Dokumente etwas Neueres oder Abweichendes zu genau dieser Angabe? Wenn nicht: status = nothing_found.
```

</details>

Geantwortet:

```json
{
  "as_of": 1960,
  "confidence": 0.72,
  "current_value": "Erdölprospektion 1960 auf dem Limberg, fossile Nashornknochen zwischen 1961 und 1993 entdeckt; Molassemächtigkeit total 2'500 m",
  "document": 1,
  "quote": "Sie werden durch die Erkenntnisse einer 1960 durchgeführten Erdöl prospektion auf dem Limberg gestützt, welche die ganze Schichtreihe der total 2'500 m mächtigen Molasse dokumentierte.",
  "reasoning": "Dokument 1 ist der Wikipedia-Artikel selbst und enthält exakt die im Artikel angegebene Aussage inklusive der Jahreszahl 1960. Die übrigen Dokumente (Schwyz, Aargau, Burgberg) betreffen andere Dorfbäche bzw. Hochwasserschutz und sagen nichts zur Entstehungsgeschichte des Küsnachter Tobels. Es liegt also keine neuere oder abweichende Angabe vor; die bestehende Angabe wird bestätigt.",
  "status": "confirms_current"
}
```

Tokens: 233 rein, 462 raus

### 6. sections — `fehlende Abschnitte` (neu angefragt)

<details><summary>Gefragt</summary>

```
Fasse jeden Abschnitt in zwei bis vier Stichpunkten zusammen: worüber er handelt, welche Zahlen oder Ereignisse darin vorkommen. Kein Fliesstext, keine Wertung.
```

</details>

Geantwortet:

```json
{
  "sections": [
    {
      "bullets": [
        "Der Küsnachter Dorfbach im Küsnachter Tobel: heute unscheinbar, früher gefährlich",
        "Erste urkundliche Erwähnung eines grossen Hochwassers mit Todesopfern 1678",
        "1778: Nach starken Regenfällen Überschwemmung in Küsnacht–Goldbach, 63 Todesopfer und grosse Sachschäden; danach Begradigung des Bachlaufs durch das Dorf",
        "Rund 100 Jahre später erneutes Hochwasser durch Aufstauung an einer zu tief liegenden Brücke, hohe Schäden, keine Todesopfer",
        "Quelle: Ortsmuseum Küsnacht (Webseite, archiviert)"
      ],
      "heading": "Historische Hochwasser",
      "lang": "en"
    },
    {
      "bullets": [
        "Anbindung von Forch durch die Forchbahn ab Zürich-Stadelhofen (Sechseläutenplatz) bzw. Esslingen",
        "Küsnacht mit S-Bahn-Linien S6 und S16, Buslinie 910 sowie Ortsbus",
        "Im Sommer etwa halbstündliche Schiffskurse von/nach Zürich und entlang des Sees nach Rapperswil (Betreiberin ZSG)"
      ],
      "heading": "Verkehr",
      "lang": "en"
    }
  ]
}
```

Tokens: 85 rein, 471 raus

## Von den Prüfungen verworfen

| Prüfung | Angabe | Detail | Dokument |
| --- | --- | --- | --- |
| Zirkelbezug: das Dokument ist eine Kopie des Artikels | `marker_derzeit-014245ea` | nennt Wikipedia bzw. CC-BY-SA als Quelle | <https://de.wikipedia.org/wiki/K%C3%BCsnachter_Dorfbach> |
