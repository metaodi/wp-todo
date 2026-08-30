# What a research dossier is, and what it is not

`wp-todo research` produces a file under `research/`. This document says what
that file may be used for. It exists because the difference between a research
aid and an unwelcome automation is entirely a matter of what happens next, and
that part is not enforced by any code.

## The short version

A dossier is **notes for a human**. It is a list of things worth checking, with
pointers to where to check them. It is not an edit, not a draft, and not a
source.

## What the tool does

- Reads the article's wikitext out of the fetched corpus and works out which of
  its statements can go stale: infobox values, `Stand: YYYY` markers, dated
  maintenance templates.
- Compares the ones it can compare mechanically against Wikidata.
- Reports which sections other language editions have that this one does not.
- Summarises how old the article's own sourcing is.

## What the tool does not do

- **It does not edit.** No login, no OAuth, no write tokens, no `action=edit`.
  The client refuses any action outside a read-only allowlist, and the
  open-web client implements no verb but GET.
- **It does not draft article text.** Deliberately. A ready-to-paste sentence
  invites pasting without checking, which is the exact failure this whole
  design is arranged against — and unmarked machine-written prose is not
  welcome on German Wikipedia.
- **It does not post anywhere.** Not to talk pages, not to noticeboards, not to
  maintenance lists. If something in a dossier is worth raising, a person
  raises it, in their own words, having checked it.
- **It does not decide who is right.** A Wikidata delta is a pair of values and
  two links. Wikidata is frequently the one that is out of date or wrong.

## On the source list

There is no allowlist. The tool classifies sources and sorts them, but it does
not decide which ones you are allowed to see — you check every source anyway, so
an allowlist would only cost you findings you never learn about.

What it does have is a blocklist you build yourself, one line at a time, from
sources you have already looked at and rejected. Three rules govern it:

1. **Every entry carries a reason, and the reason is mandatory.** This is not
   bureaucracy. A blocklist encodes its author's judgement, and *"this source is
   unreliable"* and *"I disagree with this source"* are easy to conflate —
   especially on a topic you care about. Writing down which one it was is what
   makes the difference checkable afterwards.
2. **Every exclusion is printed in the dossier**, with its reason and the date
   you decided. Nothing disappears silently. If a dossier is thinner than it
   should be, the file says why.
3. **`trust` is not a bypass.** A domain you trust is sorted higher and nothing
   else. It does not skip the citation check, and it does not skip
   Wikipedia-mirror detection, because a trusted mirror is still a mirror.

If you find yourself blocking a source because of what it says rather than
because of what it is, that is worth noticing. The reason field is where you
will notice it.

## Rules for using it

1. **Check every finding at its source before acting on it.** The dossier is a
   pointer. Following the pointer is the work, and it is not optional.
2. **Never cite the dossier.** Cite the source it points at, having read it.
3. **Write your own prose.** Do not paraphrase the dossier into the article.
4. **A finding you cannot verify does not go in.** Not with a hedge, not with a
   "laut" — it does not go in.
5. **Nothing here is a reason to edit faster.** The point of the tool is to cut
   the time spent *finding* what needs work, not the time spent getting it
   right.

## Why this is not the thing the community objects to

The objection to automated editing is an objection to unreviewed changes
appearing in article space at machine speed and machine volume. None of those
properties are present here: nothing reaches article space except through a
person who has read the sources, and the throughput ceiling is that person.

This is closer in kind to a worklist, a category intersection, or a search
result than it is to a bot. The tool that generated it never had the ability to
edit, and adding that ability is out of scope for the project — see rule 1 in
`CLAUDE.md`.

## On the model layer

The stage is **off by default**. Without `--agent` no model is consulted, no
money is spent, and everything above is regex, template parsing and structured
API comparison. With it, the order of work is deliberate and is where most of
the value comes from:

1. **The article's own references are read first.** A page whose population
   figure says "Stand 2018" very often already cites the statistical office
   that has since published 2025. That is cheaper than searching, it produces a
   finding an editor can act on immediately, and it cannot drag in a source
   nobody has vetted.
2. **The open web is asked only about what the references could not answer** -
   one discovery call for the whole article, not one per claim.
3. **Sections other editions have and this one does not** get a few bullet
   points summarising what the other edition's section actually says, with a
   link to it. Not what a model knows about the subject: what the linked text
   says, so the summary can be checked like everything else here.

Two of those three used to be computed and then thrown away. A Wikidata
disagreement — the sharpest question the free stage produces, two values that
cannot both be right — was rendered in the dossier and never put to the model.
An undated infobox value ("the mayor is X") was excluded from the agenda
altogether, which is right for a web search and wrong for the article's own
official website, already fetched by the time the question is asked. Neither
change moves the line on deciding: the contradiction prompt asks which value a
*document* supports, and `nothing_found` stays the right answer when none does.

It is subject to one non-negotiable rule: **the model never emits a URL, and
every quoted sentence is mechanically checked to appear verbatim in a document
that was actually fetched and stored.** URLs come out of the structured
search-result blocks, never out of the model's prose. A quote that fails the
check is dropped and counted, and the count is shown. This makes a fabricated
citation structurally impossible rather than merely discouraged, which is the
only version of this worth shipping - a plausible-looking citation to a page
that does not say what is claimed is worse than no dossier at all.

Being exact about what that rule buys matters as much as having it. The quote
gate proves a sentence is **on the page**. It does not prove the page is
honest, and it does not prove the sentence supports the value printed beside
it — a live run reported "mindestens 31 Titel" under a quote containing no
number at all. So:

- **A figure the quote does not carry is demoted**, not printed as *"Laut
  Quelle"*. The inference may well be right, which is why it is kept and
  labelled rather than dropped.
- **Fetched pages are sent as user content, never as part of the system
  prompt.** A page nobody vetted does not belong in the highest-trust channel
  of the request. This is not a claim that prompt injection is solved: a
  hostile page can carry both an instruction and a verbatim sentence that
  passes the quote gate. What the gate guarantees is that the quote is really
  at the URL shown, which is precisely what makes rule 1 above — check every
  finding at its source — something a reader can actually carry out. That rule
  is the defence. The gates only make it possible to follow.

Three more things follow from the same reasoning:

- **A `trust` verdict cannot override the circularity check.** Trusting a
  Wikipedia mirror is always an error, and a perfect quote from a copy of the
  article is the one failure a human check does not catch, because the text
  looks right - it is the article's own text.
- **Running out of budget is announced, never absorbed.** A short findings
  list because the ceiling was hit is a different fact from a short findings
  list because there was little to find, and the dossier names every claim it
  never got to. It goes one step further than that now: a claim the model
  answered `nothing_found`, a claim whose answer a gate refused, and a claim
  never asked at all are three facts, and each gets its own line. The middle
  one used to print as *"keine Quelle sagte etwas dazu"*, which is the opposite
  of what happened — a source had spoken and the machine had thrown the answer
  away. The same rule applies to documents: one that 404s, is refused by
  robots.txt, or arrives in a format we cannot read is reported with its
  reason, not quietly missing from the count.
- **The transcript is committed next to the dossier.** A findings section is a
  summary of a conversation nobody else saw. `research/<id>-<slug>.transcript.md`
  holds what was asked, what came back, and which gate refused what - including
  the answers that were thrown away. It carries a louder header than the
  dossier for exactly that reason: a rejected answer sitting in a file is not
  a finding, and somebody who finds the file on its own has to be told so
  before they read a word of it.

Nothing the model produces is a source. The findings section says so inside
itself, not only in the file header, because the header is read once by whoever
opens the file and the section is the part that gets scrolled to and pasted.
