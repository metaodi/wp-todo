# Protokoll: Sanatorium Kilchberg

> **Rohprotokoll eines Sprachmodells. Kein Befund, keine Quelle, kein Artikeltext.**
>
> Diese Datei enthält, was das Modell gefragt wurde und was es geantwortet hat -
> **einschliesslich der Antworten, die anschliessend maschinell verworfen wurden**,
> weil das Zitat nicht im Dokument stand oder das Dokument eine Kopie des Artikels
> war. Eine Antwort hier ist also nicht einmal ein Hinweis, solange sie unten nicht
> als Befund im Dossier auftaucht - und auch dann gilt: erst am Beleg prüfen.
>
> Sie steht hier, damit nachvollziehbar ist, wie das Dossier zustande kam.

[Dossier](./11926686-sanatorium-kilchberg.md) · [Artikel](https://de.wikipedia.org/wiki/Sanatorium_Kilchberg)

Modell: `claude-opus-5` · Effort: `medium` · 1 Aufruf(e), davon 0 aus dem Cache · Budget: 16

Dokumente vorgelegt: 1 (1 davon Belege des Artikels selbst) · Websuche: nein

## Aufrufe

### 1. reference_check — `belege_fehlen-314b7853` (neu angefragt)

<details><summary>Gefragt</summary>

```
Artikel: Sanatorium Kilchberg
Abschnitt: —

Wartungshinweis im Artikel:
{{Belege fehlen}}: als unzureichend belegt markiert - Teilweise: Nur spärliche Einzelnachweise, die dann nur Pirmärquellen für Zitate angeben

Die Dokumente sind die Belege und Weblinks des Artikels.
Belegt eines der Dokumente eine konkrete, überprüfbare Aussage über das Thema des Artikels - etwas, das sich als Einzelnachweis verwenden liesse? Wenn ja: status = confirms_current, current_value = die belegte Aussage, quote = wörtlich aus dem Dokument. Wenn keines der Dokumente etwas Belegbares hergibt: status = nothing_found.
```

</details>

Geantwortet:

```json
{
  "as_of": 2026,
  "confidence": 0.72,
  "current_value": "Das Sanatorium Kilchberg ist eine der ältesten psychiatrischen Privatkliniken der Schweiz mit über 150-jähriger Geschichte; als Listenspital und Grundversorger steht es allen allgemeinversicherten Patientinnen und Patienten offen, der Premiumbereich umfasst drei Privatstationen. Ambulante Behandlungen erfolgen im Zentrum für Psychosomatik Zürich City und im Ambulanten Zentrum Kilchberg. Sitz: Alte Landstrasse 70, CH-8802 Kilchberg.",
  "document": 1,
  "quote": "Das Sanatorium Kilchberg ist mit seiner über 150jährigen Geschichte eine der ältesten psychiatrischen Privatkliniken in der Schweiz.",
  "reasoning": "Dokument 1 ist die offizielle Website der Klinik und enthält konkrete, überprüfbare Angaben (Alter/Status als eine der ältesten Privatkliniken, Listenspital/Grundversorger, drei Privatstationen, ambulante Standorte, Adresse). Diese lassen sich als Primärbeleg für entsprechende Artikelaussagen verwenden. Eine Änderung gegenüber dem Artikelstand ist nicht erkennbar, daher bestätigend.",
  "status": "confirms_current"
}
```

Tokens: 281 rein, 606 raus

## Von den Prüfungen verworfen

| Prüfung | Angabe | Detail | Dokument |
| --- | --- | --- | --- |
| unreadable | `—` | PDF nicht lesbar (PdfStreamError) | <https://www.kilchberg.ch/fileadmin/user_upload/gemeinde/Neujahrsblatt_Nr_058_2017_optimiert.pdf> |
