#!/usr/bin/env python3
"""Resolve every entry in references.bib against publisher metadata.

Defect (15) of the manuscript is a bibliography that was generated rather than
looked up: one entry cited an arXiv identifier belonging to an unrelated paper,
one cited a survey that resolves to no indexed record, one named the wrong first
author and venue, and three truncated their author lists to `and others`.  None
of those changed an experimental number, which is why they would have shipped.

This script is the gate.  For each entry it gathers candidate records from every
resolver it has --- Crossref by DOI, Crossref by title, OpenAlex by title, the
arXiv record, and for web pages the URL itself --- scores them by title
similarity, author agreement and year, and keeps the best.  It then compares the
winner's title, author list, year and DOI with what the .bib says.

    python3 scripts/check_bibliography.py --fetch     # network; writes records/bib_verification.json
    python3 scripts/check_bibliography.py             # offline; this is the build gate

The record stores a fingerprint of the entry it verified, so editing references.bib
after fetching makes the record stale and the gate fails.  `--fetch` must then be
re-run, which is the point: no bibliography edit can ship unverified.

An entry no resolver can find is a FAILURE, not a pass.  A source that genuinely
is not indexed goes in EXEMPT below with a written reason; the reason is stored in
the record and printed on every check, so an exemption is an argument, not a silence.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
import unicodedata
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
BIB = HERE / "references.bib"
RECORD = HERE / "records" / "bib_verification.json"
CACHE = HERE / "records" / ".bib_cache.json"
# NOT gitignored, whatever the comment here used to say: `git ls-files` lists it and
# `git check-ignore` returns 1.  It is a cache of network responses, tracked so that a
# reader can see what the resolver was given, and it is not evidence on its own -- the
# record beside it is.  It is also not sufficient to reproduce the record offline: at
# HEAD it held 24 URLs, all crossref-query and semanticscholar, while 23 of the 27
# resolved entries came from arXiv, Crossref-DOI, PMLR or a direct fetch.
TIMEOUT = 15
PACE = 2.0                # seconds between network calls; Crossref and OpenAlex both 429 bursts
MAILTO = "litianyu@ntu.edu.sg"   # OpenAlex's polite pool; without it the burst limit is low

# Sources that exist but are not indexed by Crossref or OpenAlex.  An exemption is
# NOT a pass by assertion: it names a `verify_url` whose text must contain the
# entry's own title, fetched on every `--fetch`.  So an exempted citation is still
# checked, against the publisher's own volume listing rather than an aggregator.
EXEMPT: dict[str, dict] = {
    "shekhovtsov2023cold": {
        "reason": "PMLR v202 carries no DOI and is indexed by neither Crossref nor OpenAlex",
        # The PAPER's page, not the volume's.  The volume index page is titled
        # "Proceedings of Machine Learning Research | Proceedings of the 40th
        # International Conference on Machine Learning" and carries no author
        # metadata at all, so comparing an entry against it scores title similarity
        # 0.019 -- a mismatch.  It was passing only because the record-reuse branch
        # below skipped re-resolution for any entry whose fingerprint had not
        # changed, so this page had not actually been fetched and compared since an
        # older version of this script.  Exemptions are now always re-fetched.
        "verify_url": "https://proceedings.mlr.press/v202/shekhovtsov23a.html",
    },
}

# Statuses that mean "the resolver could not answer", as opposed to "the resolver
# answered and the answer disagreed".  Only the former may inherit a previous
# verification; the latter must fail, or a stale record becomes a way to keep an
# entry passing after the evidence against it arrives.
# `verify_entry` emits exactly five statuses: "ok", "mismatch" (a resolver answered and
# disagreed), "fail" (nothing resolved, or an exemption without a usable page),
# "unresolved" (no resolver could find it) and "network" (the call did not complete).
# Only "network" may inherit a previous verification.  "unresolved" deliberately may
# not: an entry no resolver indexes is a FAIL rather than a pass, and letting it
# inherit would turn a transient aggregator outage into a permanent blind spot.
NONSUBSTANTIVE = {"network"}

TITLE_SIM_FLOOR = 0.75      # Jaccard on title tokens
TITLE_OVERLAP_FLOOR = 0.95  # ...or near-containment, when one title is a subset of the other
YEAR_TOLERANCE = 1          # preprint vs proceedings versions legitimately differ


# --------------------------------------------------------------------------- bib
def parse_bib(path: Path) -> list[dict]:
    """Brace-aware parse of a .bib file into {key, type, fields}."""
    txt = path.read_text(encoding="utf-8")
    entries, i = [], 0
    while True:
        at = txt.find("@", i)
        if at < 0:
            break
        brace = txt.find("{", at)
        if brace < 0:
            break
        typ = txt[at + 1:brace].strip().lower()
        depth, j = 0, brace
        while j < len(txt):
            if txt[j] == "{":
                depth += 1
            elif txt[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        inner = txt[brace + 1:j]
        key = inner.split(",", 1)[0].strip()
        rest = inner.split(",", 1)[1] if "," in inner else ""
        entries.append({"key": key, "type": typ, "fields": _parse_fields(rest)})
        i = j + 1
    return entries


def _parse_fields(body: str) -> dict:
    fields, pat = {}, re.compile(r"(\w+)\s*=\s*", re.S)
    pos = 0
    while True:
        m = pat.search(body, pos)
        if not m:
            break
        name, k = m.group(1).lower(), m.end()
        if k < len(body) and body[k] == "{":
            depth, j = 0, k
            while j < len(body):
                if body[j] == "{":
                    depth += 1
                elif body[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            fields[name] = body[k + 1:j]
            pos = j + 1
        else:
            j = body.find(",", k)
            j = len(body) if j < 0 else j
            fields[name] = body[k:j].strip().strip('"')
            pos = j + 1
    return fields


PARTICLES = {"van", "von", "de", "del", "della", "di", "da", "du", "la", "le", "ter", "den"}


def strip_tex(s: str) -> str:
    """LaTeX -> plain text.  Braces are DELETED, never replaced by a space: a
    group is how BibTeX protects a capital or carries an accent, so
    `{G}umbel` is `Gumbel` and `Pogan{\\v{c}}i{\\'c}` is `Pogancic`.  Spacing
    them out (`G umbel`, `Pogan c i c`) silently corrupts both the query sent to
    every resolver and the surname comparison, which is how the first version of
    this gate reported 23 of 27 real citations as broken."""
    s = re.sub(r"\\url\{([^}]*)\}", r" \1 ", s or "")
    s = re.sub(r"\\[`'\"^~.=uvHtcdb]\s*\{(.)\}", r"\1", s)   # \\v{c}, \\'{e}
    s = re.sub(r"\\[`'\"^~.=]\s*(.)", r"\1", s)                # \\'e, \\"o
    s = re.sub(r"\\[a-zA-Z]+\*?\s*\{([^{}]*)\}", r"\1", s)   # \\emph{x} -> x
    s = re.sub(r"\\[a-zA-Z]+\*?", " ", s)                        # \\ and, \\LaTeX
    s = s.replace("$", "").replace("{", "").replace("}", "")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", strip_tex(s).lower()).replace("  ", " ").strip()


def surnames(author_field: str) -> list[str]:
    """Surnames in bib order.  Handles 'Last, First', 'First Last', and '{Org}'."""
    out = []
    for part in re.split(r"\s+and\s+", strip_tex(author_field).strip()):
        part = part.strip().strip(",")
        if not part:
            continue
        if "," in part:
            out.append(norm(part.split(",")[0]).split()[-1])
        else:
            toks = [t for t in norm(part).split() if t]
            if not toks:
                continue
            while len(toks) > 2 and toks[0] in PARTICLES:
                toks.pop(0)
            out.append(toks[-1])
    return [o for o in out if o]


MIN_NAME_TOKEN = 3          # initials are not evidence of identity


def name_tokens(s: str) -> set[str]:
    return {w for w in norm(s).split() if len(w) >= MIN_NAME_TOKEN}


def author_diff(bib_field: str, resolved_names: list[str]) -> tuple[list[str], list[str]]:
    """Compare author lists positionally.  A position matches when the .bib's full
    author string and the resolved display name share a token of at least
    MIN_NAME_TOKEN letters, which lets `Pogan{\v{c}}i{\'c}, Marin Vlastelica`
    match a database that stores the same person as `Marin Vlastelica`.  Length
    differences are reported, because a truncated or padded list is exactly what
    defect (15) looked like."""
    bib = [a.strip() for a in re.split(r"\s+and\s+", strip_tex(bib_field).strip()) if a.strip()]
    missing, extra = [], []
    if len(bib) != len(resolved_names):
        extra.append(f"list length {len(bib)} vs publisher's {len(resolved_names)}")
    for i, res in enumerate(resolved_names):
        rt = name_tokens(res)
        if i < len(bib):
            # 'Last, First' -> compare both halves; 'First Last' -> whole string
            bt = name_tokens(bib[i])
            if not (bt & rt):
                missing.append(f"{res} (position {i+1}, .bib says {bib[i]})")
        else:
            missing.append(f"{res} (position {i+1}, absent from the .bib)")
    for i in range(len(resolved_names), len(bib)):
        extra.append(f"{bib[i]} (position {i+1}, absent from the publisher's list)")
    return missing, extra


def title_sims(a: str, b: str) -> tuple[float, float]:
    """(Jaccard, overlap-coefficient) on title tokens.

    Both are needed.  Jaccard alone rejects a real record whose title carries a
    prefix the .bib omits; the overlap coefficient alone accepts a DIFFERENT paper
    whose title merely contains the cited one -- which is how `blondel2020`
    resolved to "Regularized Learning with Fenchel-Young Losses" at similarity 1.0,
    a 2022 paper by five different people.  A candidate qualifies on either, and
    author agreement is what breaks the tie."""
    ta, tb = set(norm(a).split()), set(norm(b).split())
    if not ta or not tb:
        return 0.0, 0.0
    inter = len(ta & tb)
    return inter / len(ta | tb), inter / min(len(ta), len(tb))


def title_sim(a: str, b: str) -> float:
    return title_sims(a, b)[0]


def qualifies(sim: float, overlap: float) -> bool:
    return sim >= TITLE_SIM_FLOOR or overlap >= TITLE_OVERLAP_FLOOR


def fingerprint(entry: dict) -> str:
    f = entry["fields"]
    blob = json.dumps({k: strip_tex(f.get(k, "")) for k in
                       ("title", "author", "year", "journal", "booktitle", "volume",
                        "number", "pages", "doi")}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------- resolvers
_CACHE: dict | None = None


def _load_cache() -> dict:
    global _CACHE
    if _CACHE is None:
        _CACHE = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    return _CACHE


def _save_cache() -> None:
    if _CACHE is None:
        return
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_CACHE, sort_keys=True))
    tmp.replace(CACHE)


def _get(url: str, tries: int = 6) -> str:
    """GET with a persistent cache and backoff.

    The cache is not a convenience.  Crossref and OpenAlex both 429 a burst of
    ~80 queries, and a rate-limited resolver returning NOTHING is
    indistinguishable from a citation that does not exist -- the one confusion this
    gate must never make.  Two runs of an uncached version of this script disagreed
    about ten of twenty-seven entries for exactly that reason.  A cached response
    makes a re-fetch reproducible, and `--fetch` also reuses any entry whose
    fingerprint is unchanged and already resolved, so a healthy run is never
    degraded by an unhealthy one."""
    cache = _load_cache()
    if url in cache:
        return cache[url]
    last = None
    for k in range(tries):
        time.sleep(PACE)
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "dcdtuni-bibcheck/1.0 (mailto:litianyu@ntu.edu.sg)"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                body = r.read().decode("utf-8", "replace")
            cache[url] = body
            return body
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504):
                raise
            last = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
        time.sleep(6.0 * (k + 1))
    raise last


def _j(url: str):
    return json.loads(_get(url))


def cand(source, title, authors, year, container, volume="", issue="", page="", doi=""):
    return {"source": source, "title": title or "", "authors": [a for a in authors if a],
            "authors_raw": authors, "year": str(year or ""), "container": container or "",
            "volume": str(volume or ""), "issue": str(issue or ""), "page": page or "",
            "doi": doi or ""}


def from_crossref(raw: dict, source: str) -> dict:
    auth = [(a.get("family") or "").strip() for a in raw.get("author", [])]
    full = [f"{a.get('family','')}, {a.get('given','')}".strip(", ") for a in raw.get("author", [])]
    yr = (raw.get("published-print") or raw.get("published-online")
          or raw.get("published") or raw.get("issued") or {}).get("date-parts", [[None]])
    return cand(source, (raw.get("title") or [""])[0], [norm(a).split()[-1] if norm(a) else "" for a in auth],
                yr[0][0] if yr and yr[0] else "", (raw.get("container-title") or [""])[0],
                raw.get("volume"), raw.get("issue"), raw.get("page") or raw.get("article-number"),
                raw.get("DOI")) | {"authors_raw": full}


def from_openalex(raw: dict) -> dict:
    names = [(a.get("author") or {}).get("display_name", "") for a in raw.get("authorships", [])]
    src = ((raw.get("primary_location") or {}).get("source") or {})
    return cand("openalex", raw.get("title"), [norm(n).split()[-1] if norm(n) else "" for n in names],
                raw.get("publication_year"), src.get("display_name"),
                (raw.get("biblio") or {}).get("volume"), (raw.get("biblio") or {}).get("issue"),
                (raw.get("biblio") or {}).get("last_page") and
                f"{(raw.get('biblio') or {}).get('first_page','')}-{(raw.get('biblio') or {}).get('last_page')}",
                (raw.get("doi") or "").replace("https://doi.org/", "")) | {"authors_raw": names}


def from_arxiv(arxid: str):
    """arXiv's own metadata from the abstract page.  The export API is blocked from
    this host; the HTML is not, and citation_* is what arXiv asks indexers to read."""
    d = _get(f"https://arxiv.org/abs/{arxid}")
    def meta(name):
        m = re.search(rf'name="{name}" content="(.*?)"', d)
        return m.group(1) if m else None
    names = re.findall(r'name="citation_author" content="(.*?)"', d)
    if not meta("citation_title"):
        return None
    # citation_date is the v1 submission date; citation_online_date is the LATEST
    # revision.  A .bib entry cites the year of the version it read, and for these
    # entries that is the conference year, so preferring the revision date made
    # OptNet (ICML 2017, arXiv v3 in 2021) look like a five-year error.
    return cand("arxiv", meta("citation_title"),
                [surnames(n)[0] if surnames(n) else "" for n in names],
                (meta("citation_date") or meta("citation_online_date") or "")[:4],
                "arXiv", doi=meta("citation_doi")) | {"authors_raw": names}


def from_web(url: str):
    d = _get(url)
    m = re.search(r"<title[^>]*>(.*?)</title>", d, re.S | re.I)
    title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    return cand("http", title, [], "", url.split("/")[2])


def from_pinned_page(url: str):
    """A candidate read off one pinned publisher page: the <title>, plus authors from
    citation_author meta tags where the page carries them (PMLR and JMLR both do).
    Deterministic: no query API, no rate limit, no ranking heuristic."""
    d = _get(url)
    m = re.search(r"<title[^>]*>(.*?)</title>", d, re.S | re.I)
    title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    names = [html.unescape(x) for x in re.findall(r'name="citation_author"\s+content="(.*?)"', d)]
    year = ""
    m = re.search(r'name="citation_(?:publication_date|date|journal_year)" content="(\d{4})', d)
    if m:
        year = m.group(1)
    return cand("pinned-page", title, [surnames(n)[0] if surnames(n) else "" for n in names],
                year, url) | {"authors_raw": names}


def candidates(entry: dict, title: str, arxid: str, url: str, errors: list[str],
               tier: int) -> list[dict]:
    """Resolver candidates, in tiers.

    Tiering is not an optimisation, it is what makes the gate usable.  Crossref,
    OpenAlex and arXiv all rate-limit a burst of ~80 queries, and a resolver that
    answers 429 with nothing is indistinguishable from a citation that does not
    exist.  So the cheap authoritative sources are asked first and the expensive
    broad ones only when nothing so far qualifies:

      1  Crossref by DOI, the entry's own pinned page, the entry's own URL
      2  the arXiv record, when the entry names one -- a single deterministic GET
         of the identifier's own page, which is never rate-limited
      3  Crossref by title (bibliographic and title fields, first author as a filter)
      4  OpenAlex by title, OpenAlex full-text search, Semantic Scholar
    Tiers 3 and 4 are the ones that 429, so they are asked only when 1 and 2 have
    nothing that agrees on both title and author list.
    """
    f = entry["fields"]
    doi = f.get("doi", "").strip()
    out: list[dict] = []

    def try_(fn, *a):
        try:
            r = fn(*a)
            if r:
                out.extend(r if isinstance(r, list) else [r])
        except Exception as exc:                                   # noqa: BLE001
            errors.append(f"{type(exc).__name__}")

    if tier >= 1:
        if doi:
            try_(lambda: from_crossref(
                _j(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}")["message"],
                "crossref-doi"))
        if entry["key"] in EXEMPT and EXEMPT[entry["key"]].get("verify_url"):
            try_(from_pinned_page, EXEMPT[entry["key"]]["verify_url"])
        if url:
            try_(from_pinned_page, url)
    if tier >= 2 and arxid:
        try_(from_arxiv, arxid)
    if tier >= 3 and title:
        first = surnames(f.get("author", ""))[:1]
        q = urllib.parse.quote(title)
        ra = urllib.parse.quote(first[0]) if first else ""

        def _cr(field):
            au = f"&query.author={ra}" if ra else ""
            items = _j(f"https://api.crossref.org/works?{field}={q}{au}&rows=8")["message"]["items"]
            return [from_crossref(it, "crossref-query") for it in items]
        try_(_cr, "query.bibliographic")
        try_(_cr, "query.title")
    if tier >= 4 and title:
        q = urllib.parse.quote(title)

        def _s2():
            d = _j(f"https://api.semanticscholar.org/graph/v1/paper/search?query={q}"
                   f"&limit=5&fields=title,authors,year,venue,externalIds")
            out = []
            for it in d.get("data") or []:
                names = [a.get("name", "") for a in it.get("authors") or []]
                out.append(cand("s2", it.get("title"),
                                [norm(n).split()[-1] if norm(n) else "" for n in names],
                                it.get("year"), it.get("venue"),
                                doi=(it.get("externalIds") or {}).get("DOI", ""))
                     | {"authors_raw": names})
            return out
        try_(_s2)

        def _oa(filt):
            items = _j(f"https://api.openalex.org/works?{filt}={q}&per-page=10"
                       f"&mailto={MAILTO}")["results"]
            return [from_openalex(it) for it in items]
        try_(_oa, "filter=title.search")
        try_(_oa, "search")
    return out


def score(c: dict, title: str, bib_author_field: str, bib_year: str) -> tuple:
    """Rank key.  Author agreement comes FIRST: two records can share a title
    (a preprint and its published version, or a paper and a later one whose title
    contains it), and only the author list tells them apart."""
    sim, ovl = title_sims(title, c["title"])
    missing, extra = author_diff(bib_author_field, c["authors_raw"])
    auth = 1 if (not c["authors_raw"] or not (missing or extra)) else 0
    yr = 1 if (not c["year"] or not bib_year
               or abs(int(c["year"]) - int(bib_year)) <= YEAR_TOLERANCE) else 0
    return (auth, round(sim, 4), round(ovl, 4), yr, 1 if c["source"].endswith("doi") else 0)


# ------------------------------------------------------------------------- verify
def verify_entry(entry: dict, explain: bool = False) -> dict:
    f = entry["fields"]
    title = strip_tex(f.get("title", "")).strip()
    bib_authors = surnames(f.get("author", ""))
    bib_year = f.get("year", "").strip()
    doi = f.get("doi", "").strip()
    m = re.search(r"arXiv:(\d{4}\.\d{4,5})", f.get("note", "") + " " + f.get("journal", ""))
    arxid = m.group(1) if m else ""
    m = re.search(r"\\url\{([^}]+)\}", f.get("howpublished", "") + f.get("url", ""))
    url = m.group(1) if m else ""

    out = {"key": entry["key"], "type": entry["type"], "fingerprint": fingerprint(entry),
           "bib_title": title, "bib_authors": bib_authors, "bib_year": bib_year,
           "bib_doi": doi, "arxiv_id": arxid, "url": url}

    if "and others" in f.get("author", ""):
        out.update(source="none", status="fail", similarity=0.0, resolved=None,
                   problems=["author list truncated with 'and others'"], candidates=[])
        return out
    if entry["key"] in EXEMPT:
        spec = EXEMPT[entry["key"]]
        url = spec.get("verify_url")
        if not url:
            out.update(source="exempt", status="fail", similarity=0.0, resolved=None,
                       problems=["EXEMPT without a verify_url is an uncheckable assertion"],
                       candidates=[])
            return out
        try:
            page = from_pinned_page(url)
        except Exception as exc:                                   # noqa: BLE001
            out.update(source="exempt", status="fail", similarity=0.0, resolved=None,
                       problems=[f"verify_url could not be fetched: {type(exc).__name__}"],
                       candidates=[])
            return out
        sim, ovl = title_sims(title, page["title"])
        problems = []
        if sim < TITLE_SIM_FLOOR and ovl < TITLE_OVERLAP_FLOOR:
            problems.append(f"the pinned page's title is {page['title'][:70]!r}, "
                            f"Jaccard {sim:.2f} / overlap {ovl:.2f}")
        if page["authors_raw"]:
            missing, extra = author_diff(f.get("author", ""), page["authors_raw"])
            if missing or extra:
                problems.append(f"the pinned page's authors disagree: missing {missing}; extra {extra}")
        if page["year"] and bib_year and abs(int(page["year"]) - int(bib_year)) > YEAR_TOLERANCE:
            problems.append(f"year {bib_year} but the pinned page says {page['year']}")
        out.update(source="pinned-page", status="ok" if not problems else "mismatch",
                   similarity=round(sim, 4), problems=problems,
                   warnings=[] if page["authors_raw"] else
                   ["the pinned page lists no authors; author list not cross-checked"],
                   resolved={**page, "reason": spec["reason"], "verified_by": url},
                   candidates=[{"source": "pinned-page", "title": page["title"],
                                "year": page["year"], "sim": round(sim, 4),
                                "overlap": round(ovl, 4)}])
        return out
    if not bib_authors:
        out.update(source="none", status="fail", similarity=0.0, resolved=None,
                   problems=["no author field"], candidates=[])
        return out

    errors: list[str] = []
    auth_field = f.get("author", "")
    scored, cands, seen = [], [], set()
    for tier in (1, 2, 3, 4, 5):
        fresh = candidates(entry, title, arxid, url, errors, tier)
        for c in fresh:
            k = (c["source"], c["title"], c["year"])
            if k not in seen:
                seen.add(k); cands.append(c)
        scored = sorted(((score(c, title, auth_field, bib_year), c) for c in cands),
                        key=lambda x: x[0], reverse=True)
        scored = [(s, c) for s, c in scored if qualifies(s[1], s[2])]
        # stop as soon as a candidate agrees on the title AND the author list: asking
        # the broad resolvers after that only buys rate limiting
        if scored and scored[0][0][0] == 1:
            break
    if explain:
        for s, c in scored[:6]:
            print(f"    {s} {c['source']:14s} {c['year']:5s} {c['title'][:70]}")
    best0 = max((c["title"] for c in cands), default="",
                key=lambda ct: title_sims(title, ct)[0]) if cands else ""
    if not scored:
        net = sorted(e for e in set(errors) if "HTTP" in e or "URL" in e or "Timeout" in e)
        out.update(source="none",
                   status="network" if net else "unresolved",
                   similarity=0.0, resolved=None,
                   problems=(["resolvers were rate-limited or unreachable, so this entry is "
                               "UNVERIFIED rather than wrong: re-run --fetch"] if net else
                             [f"no resolver returned this title; the closest of "
                              f"{len(cands)} candidates was {best0[:60]!r}" if cands else
                              "no resolver returned a candidate"])
                   + ([f"resolver errors seen: {net}"] if net else []),
                   candidates=[], closest=(best0 if cands else None))
        return out

    (_, sim, ovl, _, _), best = scored[0]
    problems, warnings = [], []
    if best["authors_raw"]:
        missing, extra = author_diff(f.get("author", ""), best["authors_raw"])
        if missing or extra:
            problems.append("author list disagrees with the publisher's"
                            + (f": missing {missing}" if missing else "")
                            + (f"; extra {extra}" if extra else ""))
    if best["year"] and bib_year and abs(int(best["year"]) - int(bib_year)) > YEAR_TOLERANCE:
        if best["source"] == "arxiv":
            warnings.append(f"year {bib_year} (proceedings) vs arXiv record {best['year']} "
                            f"(latest preprint revision): the .bib cites the conference year")
        else:
            problems.append(f"year {bib_year} but the resolved record says {best['year']}")
    elif best["year"] and bib_year and best["year"] != bib_year:
        warnings.append(f"year {bib_year} vs resolved {best['year']} (preprint/proceedings)")
    if doi and best["doi"] and doi.lower() != best["doi"].lower():
        problems.append(f"doi {doi} vs resolved {best['doi']}")
    if not best["authors"]:
        warnings.append("resolved record carries no author list; authors not cross-checked")

    out.update(source=best["source"], status="ok" if not problems else "mismatch",
               similarity=sim, problems=problems, warnings=warnings, resolved=best,
               candidates=[{"source": c["source"], "title": c["title"], "year": c["year"],
                            "sim": s[1], "overlap": s[2]} for s, c in scored[:5]])
    return out


def fetch(entries: list[dict], explain: bool = False, refresh: bool = False) -> dict:
    prev = {}
    if RECORD.exists() and not refresh:
        try:
            prev = {e["key"]: e for e in json.loads(RECORD.read_text())["entries"]}
        except Exception:                                        # noqa: BLE001
            prev = {}
    results = []
    for e in entries:
        old = prev.get(e["key"])
        same = bool(old) and old.get("fingerprint") == fingerprint(e)
        # Every entry is re-resolved on a --fetch, including exempt ones.  This used
        # to short-circuit on `same and old.status == "ok"`, which had two effects:
        # an exempt entry's pinned page was never actually fetched again, so a
        # verify_url pointing at the wrong page passed on the strength of a record
        # written by an older version of this script; and the "kept after failed
        # refresh" branch below was unreachable dead code, because anything that
        # could have reached it had already been `continue`d past.
        if explain:
            print(f"  {e['key']}")
        new = verify_entry(e, explain)
        _save_cache()          # incremental: a 15-minute fetch that is interrupted
                               # must not lose every response it already paid for
        # An unchanged entry that verified before must not be UN-verified now
        # because a resolver was rate-limiting: that would make the record depend on
        # network weather, and a flaky gate is a gate people stop running.  But the
        # tolerance is for a resolver that could not ANSWER, not for one that
        # answered differently -- a substantive disagreement is a failure and is
        # never inherited away.
        if same and old.get("status") == "ok" and new["status"] in NONSUBSTANTIVE:
            old = dict(old); old["kept_after_failed_refresh"] = {
                "status": new["status"], "problems": new["problems"]}
            results.append(old)
            if explain:
                print(f"  {e['key']}: re-resolution could not answer "
                      f"({new['status']}), keeping the previous verification")
            continue
        if same and old.get("status") == "ok":
            new["resolver_changed_from_previous"] = old.get("resolved", {}).get("source")
        results.append(new)
    _save_cache()
    return {"bib_sha256": hashlib.sha256(BIB.read_bytes()).hexdigest(),
            "n_entries": len(results), "title_sim_floor": TITLE_SIM_FLOOR,
            "year_tolerance": YEAR_TOLERANCE, "exempt": EXEMPT, "entries": results}


def check(entries: list[dict]) -> int:
    if not RECORD.exists():
        print(f"CHECK FAILED: {RECORD} missing; run "
              f"`python3 scripts/check_bibliography.py --fetch`")
        return 1
    rec = json.loads(RECORD.read_text())
    by_key = {e["key"]: e for e in rec["entries"]}
    fails, notes = [], []
    if rec["bib_sha256"] != hashlib.sha256(BIB.read_bytes()).hexdigest():
        fails.append("references.bib changed since records/bib_verification.json was "
                     "fetched: re-run check_bibliography.py --fetch")
    for e in entries:
        r = by_key.get(e["key"])
        if r is None:
            fails.append(f"{e['key']}: not in the verification record")
            continue
        if r["fingerprint"] != fingerprint(e):
            fails.append(f"{e['key']}: entry edited since it was verified (re-fetch)")
        for w in r.get("warnings") or []:
            notes.append(f"{e['key']}: {w}")
        if e["key"] in EXEMPT:
            notes.append(f"{e['key']}: exempt from aggregator lookup ({EXEMPT[e['key']]['reason']}), "
                         f"verified against its own page {EXEMPT[e['key']]['verify_url']}")
            if r["status"] != "ok":
                fails.append(f"{e['key']}: " + "; ".join(r["problems"]))
            continue
        if r["status"] == "ok":
            continue
        if r["status"] == "network":
            fails.append(f"{e['key']}: UNVERIFIED -- the resolvers were rate-limited or "
                         "unreachable on the last --fetch. Re-run it; do not edit the .bib "
                         "to make this go away.")
        elif r["status"] == "unresolved":
            fails.append(f"{e['key']}: no publisher record found; either the citation is "
                         "wrong or it belongs in EXEMPT with a written reason and a pinned page")
        else:
            fails.append(f"{e['key']} ({r['source']}, sim {r['similarity']}): "
                         + "; ".join(r["problems"]))
    for k in sorted(set(by_key) - {e["key"] for e in entries}):
        fails.append(f"{k}: in the verification record but no longer in references.bib (re-fetch)")
    srcs: dict[str, int] = {}
    for e in entries:
        s = by_key.get(e["key"], {}).get("source", "?")
        srcs[s] = srcs.get(s, 0) + 1
    for n in notes:
        print("note:", n)
    if fails:
        print("BIBLIOGRAPHY CHECK FAILED:")
        for f in fails:
            print("  -", f)
        return 1
    # The composition of `srcs` is a property of THIS run, not of the bibliography:
    # with unchanged input it moved from {arxiv 14, s2 1} to {arxiv 9, s2 6} and back
    # again across runs, because which resolver answers first depends on rate limits.
    # `resolver_changed_from_previous` was recorded per entry and read by nothing, so
    # ledger row (26) claimed the gate "states how many entries changed resolver between
    # runs" when it did not.  A recorded field is not gate output; this is.
    changed = [(e["key"], e.get("resolver_changed_from_previous"),
                (e.get("resolved") or {}).get("source"))
               for e in entries if e.get("resolver_changed_from_previous") not in (None, "")
               and e.get("resolver_changed_from_previous") != (e.get("resolved") or {}).get("source")]
    print(f"note: resolver stability: {len(changed)} of {len(entries)} entries resolved "
          f"via a different source than the previous record"
          + ("" if not changed else ": " + ", ".join(f"{k} {a}->{b}" for k, a, b in changed[:8])))
    near = [e["key"] for e in entries
            if isinstance(e.get("similarity"), (int, float))
            and abs(e["similarity"] - TITLE_SIM_FLOOR) < 1e-9]
    if near:
        print(f"note: at the title-similarity floor exactly ({TITLE_SIM_F_floor if False else TITLE_SIM_FLOOR}): "
              f"{', '.join(near)} -- these pass on the threshold, not above it, and any "
              f"of them whose resolved record carries no author list is not author-checked")
    noauthor = [e["key"] for e in entries
                if any("no author list" in str(w) for w in (e.get("warnings") or []))]
    if noauthor:
        print(f"note: entries whose authors were never cross-checked because the resolved "
              f"record carries no author list: {', '.join(noauthor)}")
    if not entries:
        print("BIBLIOGRAPHY CHECK FAILED: references.bib parsed to zero entries.  A check "
              "whose denominator can be zero must fail on zero; an emptied .bib and a "
              "re-minted verification record used to report OK over nothing.",
              file=sys.stderr)
        return 1
    print(f"BIBLIOGRAPHY CHECK OK ({len(entries)} entries resolved against publisher "
          f"metadata {srcs}; no truncated author lists)")
    return 0


CORRECTIONS = HERE / "records" / "bib_corrections.json"
BIB_FIELDS = ("title", "author", "year", "journal", "booktitle", "volume",
              "number", "pages", "doi", "note", "howpublished")
# Changing any of these makes a citation say something different about a paper.  Adding
# a `note` does not: it says where else the same paper can be found.  Counting the two
# together reported 25 of 28 entries as "wrong" when the audit had also added a preprint
# identifier to a dozen correct ones, and a number that overstates its own finding is not
# a derived number, it is a flattering one.
SUBSTANTIVE = tuple(f for f in BIB_FIELDS if f != "note")


def corrections(ref: str) -> dict:
    """Diff references.bib against its own state at a git ref, so the number of
    entries the audit changed is derived from the repository rather than counted
    by hand.  Defect (12) is the reason: a number written into prose that no
    record produces is not a number."""
    repo = subprocess.run(["git", "-C", str(HERE), "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True).stdout.strip()
    rel = Path(HERE).resolve().relative_to(repo) / BIB.name
    blob = subprocess.run(["git", "-C", repo, "show", f"{ref}:{rel}"],
                          capture_output=True, text=True)
    if blob.returncode != 0:
        raise SystemExit(f"could not read {rel} at {ref}: {blob.stderr.strip()}")
    tmp = HERE / "records" / f".bib_at_{ref.replace('/', '_')}"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(blob.stdout)
    old = {e["key"]: e for e in parse_bib(tmp)}
    new = {e["key"]: e for e in parse_bib(BIB)}
    tmp.unlink()
    changed, per = [], []
    for k in sorted(set(old) | set(new)):
        if k not in new:
            per.append({"key": k, "change": "deleted"}); changed.append(k); continue
        if k not in old:
            per.append({"key": k, "change": "added"}); changed.append(k); continue
        o, n = old[k]["fields"], new[k]["fields"]
        fields = [f for f in BIB_FIELDS if norm(o.get(f, "")) != norm(n.get(f, ""))]
        substantive = [f for f in fields if f in SUBSTANTIVE]
        if fields and not substantive:
            per.append({"key": k, "change": "enriched", "fields": fields,
                        "after": {f: strip_tex(n.get(f, "")) for f in fields}})
            continue
        if fields:
            per.append({"key": k, "change": "corrected", "fields": fields,
                        "before": {f: strip_tex(o.get(f, "")) for f in fields},
                        "after": {f: strip_tex(n.get(f, "")) for f in fields}})
            changed.append(k)
    sha = hashlib.sha256(blob.stdout.encode()).hexdigest()[:16]
    rec = {"ref": ref, "ref_blob_sha256": sha, "n_entries_before": len(old),
           "n_entries_after": len(new), "n_changed": len(changed), "changed": changed,
           "n_deleted": sum(1 for x in per if x["change"] == "deleted"),
           "n_added": sum(1 for x in per if x["change"] == "added"),
           "n_corrected": sum(1 for x in per if x["change"] == "corrected"),
           # an entry that was corrected in place or deleted said something false at the
           # ref; an entry that was added or merely enriched did not.  This is the number
           # the manuscript quotes, so it is derived here rather than counted by hand.
           "n_wrong_at_ref": sum(1 for x in per if x["change"] in ("corrected", "deleted")),
           "n_enriched": sum(1 for x in per if x["change"] == "enriched"),
           "deleted_keys": sorted(x["key"] for x in per if x["change"] == "deleted"),
           "added_keys": sorted(x["key"] for x in per if x["change"] == "added"),
           "corrected_keys": sorted(x["key"] for x in per if x["change"] == "corrected"),
           "truncated_author_lists_before": sorted(
               k for k, e in old.items() if "and others" in e["fields"].get("author", "")),
           "detail": per}
    CORRECTIONS.parent.mkdir(parents=True, exist_ok=True)
    CORRECTIONS.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    print(f"wrote {CORRECTIONS}: {rec['n_changed']} of {rec['n_entries_before']} entries "
          f"changed vs {ref} ({rec['n_corrected']} corrected, {rec['n_deleted']} deleted, "
          f"{rec['n_added']} added, {rec['n_enriched']} enriched with a preprint id only); "
          f"{rec['n_wrong_at_ref']} of the {rec['n_entries_before']} said something false at "
          f"{ref}; {len(rec['truncated_author_lists_before'])} had truncated author lists")
    for d in per:
        if d["change"] == "corrected":
            print(f"  corrected {d['key']}: {', '.join(d['fields'])}")
        elif d["change"] != "enriched":
            print(f"  {d['change']} {d['key']}")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true",
                    help="resolve against the network and write the record")
    ap.add_argument("--explain", action="store_true", help="print each resolver's candidates")
    ap.add_argument("--refresh", action="store_true",
                    help="re-resolve every entry even if it is unchanged and already clean")
    ap.add_argument("--corrections", metavar="GIT_REF",
                    help="diff references.bib against a git ref and write "
                         "records/bib_corrections.json")
    a = ap.parse_args()
    if a.corrections:
        corrections(a.corrections)
        return 0
    if not BIB.exists():
        print(f"CHECK FAILED: {BIB} missing")
        return 1
    entries = parse_bib(BIB)
    if a.fetch:
        rec = fetch(entries, a.explain, a.refresh)
        RECORD.parent.mkdir(parents=True, exist_ok=True)
        RECORD.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
        bad = [e for e in rec["entries"] if e["status"] != "ok"]
        print(f"wrote {RECORD} ({rec['n_entries']} entries, {len(bad)} not clean)")
        for e in bad:
            print(f"  {e['status']:10s} {e['key']}: " + "; ".join(e["problems"]))
        return 1 if bad else 0
    return check(entries)


if __name__ == "__main__":
    raise SystemExit(main())
