# Protokoll: Jenny-Schloss

> **Rohprotokoll eines Sprachmodells. Kein Befund, keine Quelle, kein Artikeltext.**
>
> Diese Datei enthält, was das Modell gefragt wurde und was es geantwortet hat -
> **einschliesslich der Antworten, die anschliessend maschinell verworfen wurden**,
> weil das Zitat nicht im Dokument stand oder das Dokument eine Kopie des Artikels
> war. Eine Antwort hier ist also nicht einmal ein Hinweis, solange sie unten nicht
> als Befund im Dossier auftaucht - und auch dann gilt: erst am Beleg prüfen.
>
> Sie steht hier, damit nachvollziehbar ist, wie das Dossier zustande kam.

[Dossier](./10057196-jenny-schloss.md) · [Artikel](https://de.wikipedia.org/wiki/Jenny-Schloss)

Modell: `claude-opus-5` · Effort: `medium` · 1 Aufruf(e), davon 0 aus dem Cache · Budget: 16

Dokumente vorgelegt: 3 (3 davon Belege des Artikels selbst) · Websuche: nein

## Aufrufe

### 1. sections — `fehlende Abschnitte` (neu angefragt)

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
        "Überblick über weitere bekannte Mitglieder der Familie Jenny neben Hans Jenny",
        "Schwester Tina Keller-Jenny: Psychotherapeutin, frühe Klientin von C. G. Jung, verheiratet mit dem Theologen Adolf Keller (Freund Sigmund Freuds)",
        "Schwester Elisabeth Alice Jenny: verheiratet mit Politiker Paul Lachenal (1884–1955), Mitbegründer des Orchestre de la Suisse Romande",
        "Sohn Caspar Konrad Jenny-Caflisch (1888–1944): Schweizer Botschafter in Buenos Aires"
      ],
      "section": 1
    }
  ]
}
```

Tokens: 111 rein, 252 raus

## Von den Prüfungen verworfen

| Prüfung | Angabe | Detail | Dokument |
| --- | --- | --- | --- |
| unreadable | `—` | HTTP 410 | <https://www.tagesanzeiger.ch/zuerich/JennySchloss-wird-saniert/story/15512856> |
| unreadable | `—` | robots.txt verbietet den Abruf | <https://books.google.ch/books?id=AOUXAQAAIAAJ&q=jenny+schloss+thalwil&dq=jenny+schloss+thalwil&hl=es&sa=X&ved=0ahUKEwiGmNbIxMLWAhVJKlAKHVONBDkQ6AEIMzAC> |
| unreadable | `—` | robots.txt verbietet den Abruf | <https://books.google.ch/books?id=J9RxAAAAIAAJ&q=jenny+schloss+thalwil&dq=jenny+schloss+thalwil&hl=es&sa=X&ved=0ahUKEwiGmNbIxMLWAhVJKlAKHVONBDkQ6AEIJzAA> |
| unreadable | `—` | robots.txt verbietet den Abruf | <https://books.google.ch/books?id=LdXGaOdkJpYC&pg=PA76&dq=jenny+schloss+thalwil&hl=es&sa=X&ved=0ahUKEwiGmNbIxMLWAhVJKlAKHVONBDkQ6AEIPzAE#v=onepage&q=jenny%20schloss%20thalwil&f=false> |
| unreadable | `—` | nach 3 Versuch(en) nicht erreichbar | <https://psicologialenguaje.wikispaces.com/file/view/JungAI.pdf> |
| unreadable | `—` | HTTP 404 | <https://www.daimon.ch/catalog/product_info.php?products_id=5749&language=de&tellRegion=1> |
