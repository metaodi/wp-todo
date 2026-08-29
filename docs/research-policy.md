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

Milestone 1 uses no language model at all: everything above is regex, template
parsing and structured API comparison. The planned M2 adds open-web retrieval
with a model reading the retrieved documents.

If and when that lands, it is subject to one non-negotiable rule: **the model
never emits a URL, and every quoted sentence is mechanically checked to appear
verbatim in a document that was actually fetched and stored.** A quote that
fails the check is dropped and counted, and the count is shown. This makes a
fabricated citation structurally impossible rather than merely discouraged,
which is the only version of this worth shipping — a plausible-looking citation
to a page that does not say what is claimed is worse than no dossier at all.
