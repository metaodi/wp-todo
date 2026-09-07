# Protokoll: St. Josef (Horgen)

> **Rohprotokoll eines Sprachmodells. Kein Befund, keine Quelle, kein Artikeltext.**
>
> Diese Datei enthält, was das Modell gefragt wurde und was es geantwortet hat -
> **einschliesslich der Antworten, die anschliessend maschinell verworfen wurden**,
> weil das Zitat nicht im Dokument stand oder das Dokument eine Kopie des Artikels
> war. Eine Antwort hier ist also nicht einmal ein Hinweis, solange sie unten nicht
> als Befund im Dossier auftaucht - und auch dann gilt: erst am Beleg prüfen.
>
> Sie steht hier, damit nachvollziehbar ist, wie das Dossier zustande kam.

[Dossier](./8418392-st-josef-horgen.md) · [Artikel](https://de.wikipedia.org/wiki/St._Josef_(Horgen))

Modell: `claude-opus-5` · Effort: `medium` · 4 Aufruf(e), davon 0 aus dem Cache · Budget: 16

Dokumente vorgelegt: 12 (6 davon Belege des Artikels selbst) · Websuche: ja

## Aufrufe

### 1. reference_check — `marker_derzeit-634359b4` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: St. Josef (Horgen)
Abschnitt: Entstehungs- und Baugeschichte
Stand laut Artikel: 2016

Angabe im Artikel:
An der Kirchgemeindeversammlung vom 9.&nbsp;Juni 2005 wurde ein Planungskredit für einen Neubau des Pfarreizentrums genehmigt, wobei die ''Stiftung St.&nbsp;Josef'' die Hälfte übernahm. Nach eingehend

Die Dokumente sind die Belege, die der Artikel selbst zitiert.
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
  "reasoning": "Keines der Dokumente äussert sich zum Planungskredit von 2005 bzw. zum Neubau des Pfarreizentrums. Die Orgel-Dokumente behandeln Orgelbau, die Pfarrei- und Zeitungsseiten enthalten nur aktuelle Meldungen ohne Bezug zu dieser Angabe.",
  "status": "nothing_found"
}
```

Tokens: 261 rein, 175 raus

### 2. reference_check — `marker_stand_year-a733d72b` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: St. Josef (Horgen)
Abschnitt: Entstehungs- und Baugeschichte
Stand laut Artikel: 2021

Angabe im Artikel:
Seit dem 1. Januar 2020 ist die Kirchgemeinde Horgen auch für die Kirche [[St. Antonius (Hirzel)]] zuständig, nach dem Anschluss von Hirzel an die politische Gemeinde Horgen auch die Auflösung der Kir

Die Dokumente sind die Belege, die der Artikel selbst zitiert.
Sagt eines der Dokumente etwas Neueres oder Abweichendes zu genau dieser Angabe? Wenn nicht: status = nothing_found.
```

</details>

Geantwortet:

```json
{
  "as_of": 2026,
  "confidence": 0.6,
  "current_value": "Die Kirchgemeinde Horgen ist (weiterhin) auch für die Pfarrei/Kirche St. Antonius Hirzel zuständig; beide Pfarreien werden gemeinsam geführt.",
  "document": 2,
  "quote": "Mit grosser Freude durften wir am 27. Juni 2026 acht neue Minis in die Ministrantenscharen von St. Josef Horgen und St. Antonius Hirzel im Familiengottesdienst aufnehmen und ihnen ihre Diplome überreichen, die sie voller Stolz entgegennahmen.",
  "reasoning": "Dokument 2 (Website der katholischen Kirche Horgen) führt St. Antonius Hirzel als eigenen Pfarreibereich innerhalb der Kirchgemeinde Horgen und belegt für 2026 die gemeinsame Führung beider Pfarreien. Damit wird die Artikelangabe zur Zuständigkeit bestätigt; ein abweichendes oder neueres Datum zur Auflösung der Kirchgemeinde Hirzel bzw. zum 1. Januar 2020 findet sich in keinem der Dokumente.",
  "status": "confirms_current"
}
```

Tokens: 254 rein, 741 raus

### 3. search — `St. Josef (Horgen)` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: St. Josef (Horgen)

Diese Angaben konnten die Belege des Artikels nicht klären:
- An der Kirchgemeindeversammlung vom 9.&nbsp;Juni 2005 wurde ein Planungskredit für einen Neubau des Pfarreizentrums genehmigt, wobei die ''Stiftung St.&nbsp;Josef'' die Hälfte übernahm. Nach eingehend (Stand 2016)

Suche nach aktuellen, möglichst amtlichen deutschsprachigen Quellen zu diesen Angaben. Gib danach die verwendeten Suchanfragen zurück.
```

</details>

Von der Websuche zurückgegeben (aus den Ergebnisblöcken gelesen, nicht aus dem Antworttext):

- <https://de.wikipedia.org/wiki/St._Josef_(Horgen)>
- <https://competitions.espazium.ch/de/wettbewerbe/entschieden/neugestaltung-kirchenraum-katholische-kirche-st-josef-horgen>
- <https://www.kath-horgen.ch/>
- <https://www.kath-horgen.ch/pfarreizentrum/>
- <https://zukunft-inklusion.ch/zh/partner/pfarrei-st-josef-horgen/>
- <https://konkurado.ch/de/neugestaltung_kirchenraum_kath_kirche_st_josef_horgen>
- <https://www.kirchbau.de/400_kirchenlistemobil.php?wunsch=landSchweiz&name=keiner>
- <https://150-jahre-st-josef.ch/Agenda/>
- <https://coarchitektur.com/>
- <https://stiftungen.stiftungschweiz.ch/organisation/stiftung-st-josef-horgen>
- <https://www.fundraiso.com/en/organisations/stiftung-st-josef-horgen>
- <https://www.kath-horgen.ch/wp-content/uploads/2023/01/MB145_V3_HP.pdf>
- <https://www.kath-horgen.ch/kirchgemeindeversammlungen/>
- <https://search.ch/tel/horgen/burghaldenstrasse-5/roem-kath-2>
- <https://zuerioberland24.ch/articles/151510-pfarrhaus-kirchgemeinde-versammlung-fuer-neubau>
- <https://www.kath-oberrieden.ch/events/venues/kath-pfarreizentrum-horgen/>
- <https://refhorgen.ch/agenda/>
- <https://refhorgen.ch/>
- <https://www.kath-horgen.ch/turmausstellung/>
- <http://relaunch-rh2.kath-horgen.ch/adresse.php>
- <https://refhorgen.ch/ueber-uns/gebaeude-und-raeume/kirche-horgen/>
- <https://search.ch/tel/horgen/burghaldenstrasse-5/horgen>

Geantwortet:

```json
{
  "note": "Die Suche brachte bisher keine amtliche oder unabhaengige Quelle, die den Planungskredit der Kirchgemeindeversammlung vom 9. Juni 2005 und die Haelfte-Uebernahme durch die Stiftung St. Josef belegt oder den Stand von 2016 aktualisiert. Gefunden wurden nur der Wikipedia-Artikel selbst (Zirkelschluss, nicht verwendbar), Stiftungsverzeichnis-Eintraege zur Stiftung St. Josef Horgen, eine Wettbewerbsseite zur Neugestaltung des Kirchenraums sowie die allgemeine Pfarrei-Website mit Raumvermietungs-Informationen zum Pfarreizentrum - keines dieser Dokumente nennt den Kredit, das Datum oder den Kostenteiler. Das Suchbudget fuer weitere Abfragen war ausgeschoepft. Nach den Regeln waere hier daher 'nothing_found' die korrekte Antwort; empfehlenswert waere eine Recherche in den Protokollen bzw. Jahresberichten der Katholischen Kirchgemeinde Horgen oder im Archiv der 'Zuerichsee-Zeitung'.",
  "queries": [
    "St. Josef Horgen Pfarreizentrum Neubau Planungskredit 2005",
    "Stiftung St. Josef Horgen Pfarreizentrum Kirchgemeindeversammlung",
    "katholische Kirchgemeinde Horgen Neubau Pfarreizentrum Baukredit Abstimmung",
    "Horgen Pfarreizentrum Burghaldenstrasse Neubau Einweihung",
    "kath-horgen.ch Pfarreizentrum Geschichte Neubau"
  ]
}
```

Tokens: 29538 rein, 1351 raus

### 4. web_check — `marker_derzeit-634359b4` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: St. Josef (Horgen)
Abschnitt: Entstehungs- und Baugeschichte
Stand laut Artikel: 2016

Angabe im Artikel:
An der Kirchgemeindeversammlung vom 9.&nbsp;Juni 2005 wurde ein Planungskredit für einen Neubau des Pfarreizentrums genehmigt, wobei die ''Stiftung St.&nbsp;Josef'' die Hälfte übernahm. Nach eingehend

Die Dokumente stammen aus einer Websuche.
Sagt eines der Dokumente etwas Neueres oder Abweichendes zu genau dieser Angabe? Wenn nicht: status = nothing_found.
```

</details>

Geantwortet:

```json
{
  "as_of": null,
  "confidence": 0.72,
  "current_value": null,
  "document": 0,
  "quote": "",
  "reasoning": "Die Artikelangabe betrifft den an der Kirchgemeindeversammlung vom 9. Juni 2005 genehmigten Planungskredit für den Neubau des Pfarreizentrums und die Mitfinanzierung durch die Stiftung St. Josef. Keines der Dokumente äussert sich dazu: Dok. 1 ist der Artikel selbst, Dok. 2 und 6 betreffen den Wettbewerb zur Neugestaltung des Kirchenraums (2014/2015), Dok. 3 und 4 sind Pfarrei-Webseiten ohne Angaben zum Planungskredit, Dok. 5 nennt nur die Mitgliederzahl. Damit liegt keine neuere oder abweichende Information zu genau dieser Angabe vor.",
  "status": "nothing_found"
}
```

Tokens: 252 rein, 440 raus

## Von den Prüfungen verworfen

| Prüfung | Angabe | Detail | Dokument |
| --- | --- | --- | --- |
| unreadable | `—` | HTTP 404 | <http://www.kath-horgen.ch/aktuell/einzelansicht/news/altar-und-kirchweihe-st-josef.html> |
| unreadable | `—` | HTTP 404 | <https://www.sakralbauten.ch/horgen-st-josef/> |
