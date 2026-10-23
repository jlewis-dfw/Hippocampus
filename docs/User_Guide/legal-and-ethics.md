# Responsible crawling: legal and ethical guide

Hippocampus can fetch web pages and store their text. That is a power with obligations. This page explains what the crawler does and does not
do to behave well, what you should check **before** you crawl a site, and the legal and ethical considerations that apply to copying and
storing other people's content.

> **This is not legal advice.** Laws about copying, scraping, contracts, computer access and personal data differ by country and change over
> time, and the right answer often depends on the facts: who you are, what the site says, what you do with the content. The notes below are a
> general orientation to help you ask the right questions. If a crawl matters (a business use, a large or repeated crawl, personal data, a site
> that objects), ask a qualified lawyer. **You are responsible for what you crawl and how you use it.** The authors of this software accept no
> responsibility for your use of it.

## The short version

1. **Crawl your own sites, or sites that clearly allow it**, before you crawl anyone else's. Prefer an official API, a data export or a
   downloadable copy of documentation to scraping pages.
2. **Read the site's terms of use and its `robots.txt`.** If they forbid automated access, do not crawl.
3. **Never get around a login, a paywall, a rate limit or a block.** The crawler does not try to, and you must not make it.
4. **Be gentle.** Keep the page cap, depth and delay low. Do not re-crawl often. Crawl off-peak where you can.
5. **Collect only what you need**, keep it only as long as you need it, and keep it **private**. Do not republish other people's content.
6. **Be careful with personal data.** Pages about people are subject to privacy law almost everywhere.
7. **Credit and link the source** when you quote results (the index keeps every source address for exactly this).
8. **Be ready to delete.** If a site owner asks you to stop or remove their content, do so promptly.
9. **Keep a record** of what you crawled, when and why.

## What the crawler does to behave well

These behaviours are in the code ([../Architecture/crawler.md](../Architecture/crawler.md)); you do not have to configure them.

| Behaviour | Detail |
|---|---|
| Reads `robots.txt` | Fetched once from the start host before the crawl; a disallowed path is skipped and logged (`skip (robots.txt) <url>`) |
| Stays on one host | Only links on the start page's host are followed; it never wanders to other sites |
| Waits between requests | 0.5 seconds by default (`HIPPO_CRAWL_DELAY`), never parallel requests to the site |
| Limits its size | 200 pages per crawl by default (`HIPPO_CRAWL_MAX_PAGES`), depth 1 to 5 (you choose), at most 2 MB read per page, at most 5 redirects |
| Fetches only text pages | HTML only; images, media, archives, PDFs, fonts, scripts and data files are not downloaded |
| Does not log in | No credentials are sent and no forms are filled; pages behind a login are simply not reachable |
| Does not hide | The crawler identifies itself with its own user agent (also in the browser engine), not a pretend browser |
| Refuses private addresses | It never fetches local, private or VPN addresses, so it cannot be turned on a network it should not touch |
| Stores text, not the site | It keeps the page's converted text, title and address, not the raw HTML, images or scripts |
| Skips unchanged pages on re-index | A page whose text has not changed is not re-embedded (it is still downloaded again; see below) |

## What it does NOT do (known gaps)

Be honest with yourself about these: they are things a careful crawler would do and this one does not yet.

| Not done | Consequence and what to do about it |
|---|---|
| **`Crawl-delay` in `robots.txt` is ignored.** The delay is fixed by `HIPPO_CRAWL_DELAY` | If a site asks for a longer delay, set `HIPPO_CRAWL_DELAY` at least that high |
| **`<meta name="robots">` (`noindex`, `nofollow`) and the `X-Robots-Tag` header are not honoured** | A page that asks not to be indexed will still be stored. Do not crawl sites that use these to opt out |
| **`rel="nofollow"` links are followed** | See above |
| **Opt-out signals for text and data mining or AI use** (for example the TDM Reservation Protocol, `noai` hints) **are not read** | Check the site yourself before collecting content for any AI use |
| **A `robots.txt` that cannot be fetched counts as "allowed"** | The robots standard (RFC 9309) says a server error should mean "disallowed". Check `robots.txt` yourself if the site is flaky |
| **No conditional requests** (`If-Modified-Since`, `ETag`) | Every crawl downloads every page again even when nothing changed. Re-crawl rarely |
| **The user agent has no contact address** | A site owner who sees your traffic cannot reach you. Another reason to crawl only sites you have permission for |
| **The terms of use are not checked** (they cannot be, by software) | This is your responsibility |
| **No per-source delete** in the API | Removing one crawled page means SQL ([../Database/README.md](../Database/README.md#a-sql-cookbook)); removing a whole site means deleting the collection |
| **No record is kept** of why you crawled | Job logs live in memory and vanish on restart: keep your own notes |

## Before you crawl: a checklist

- [ ] **Do I own the site or have permission?** If yes, you are mostly done; keep to the technical limits anyway.
- [ ] **Is there an API, a dump or a repository?** Use that instead; it is kinder to the site and usually allowed.
- [ ] **Did I read the terms of use?** Look for clauses on automated access, scraping, "data mining" and reuse of content.
- [ ] **Did I read `https://<site>/robots.txt`?** Make sure the paths I want are allowed and note any `Crawl-delay`.
- [ ] **Does the site use `noindex`, `X-Robots-Tag` or an opt-out for AI or data mining?** The crawler will not notice; you must.
- [ ] **Does it need a login or a subscription?** Then do not crawl it.
- [ ] **Is it a small site?** A hobby site on a small server can be hurt by 200 requests. Use depth 1 or 2 and a long delay, or ask first.
- [ ] **Will pages contain personal data** (names, contact details, photos, forums, social media, health, children)? If so, see
      [personal data](#personal-data-and-privacy) before you start.
- [ ] **What will I do with the content?** Personal reference is the easiest case; sharing, selling or training models are harder.
- [ ] **Can I limit the crawl?** Use `match_pattern` to confine it to the section you need, and a low depth.
- [ ] **Have I decided where it will be stored and who can read it?** Use a **restricted** collection for anything not meant for everyone.
- [ ] **Do I know how I would delete it?** (See [handling a removal request](#handling-a-removal-request).)

## Legal considerations

These are the main areas to think about. They are general; details depend on where you and the site are.

### Terms of use and contracts

Many sites forbid automated access or copying in their terms of use. Whether a clause binds you, and whether breaking it is a legal wrong, depends on
the law and on whether you agreed to the terms (for instance by creating an account or clicking through). Breaching terms can lead to your access being
blocked or, in some places, to a claim. Treat an explicit prohibition as a reason not to crawl.

### Copyright

Web pages are normally protected by copyright. Fetching a page and storing its text **makes a copy**; the vectors derived from it are also derived from
the work. Whether that is allowed depends on the jurisdiction and the purpose:

- **Private, non-commercial reference** (your own searchable notes) is generally the lowest-risk use, but is not automatically lawful everywhere.
- **Some countries allow "text and data mining"** for research or, with an opt-out for rights holders, more generally (for example under the EU's copyright
  directive). Those regimes often require that you respect a machine-readable opt-out, which this crawler does not read (see the gaps above).
- **Fair use or fair dealing** exceptions exist in some systems and depend on the purpose, amount and effect on the market; they are decided case by case.
- **Republishing or redistributing** crawled content, serving it to others, or using it commercially raises the stakes. Do not do it without permission.
- **Content with an open licence** (Creative Commons, open-source documentation) is usually fine to copy under its terms: read them, and honour attribution
  and share-alike requirements.

Hippocampus keeps the source address (`uri`) of every chunk so that you can attribute and link back.

### Unauthorised access and computer-misuse laws

Many countries have laws against accessing computer systems without authorisation. Accessing a **publicly available page** the way a browser would is
generally treated differently from getting around a technical barrier, but the law is not uniform and keeps developing. Do not: use credentials you were not
given, defeat a login or paywall, evade a block or rate limit (for example by rotating addresses), or keep hammering a site that has told you to stop. The
crawler's design avoids all of this, and that should stay true of how you use it.

### Personal data and privacy

If a crawled page contains information about identifiable people, **data-protection law probably applies to you** as soon as you store it, even if the
page is public. In the EU and UK (GDPR) and in many other places this means, in outline: you need a lawful reason; you should collect and keep only what you
need and for no longer than you need it; you must keep it secure; people may have rights to see, correct and delete their data; and some categories (health,
political opinions, children's data and similar) have stricter rules. Practical steps:

- Avoid crawling pages whose main content is about individuals: forums, social networks, directories, review sites, comment sections.
- Do not crawl to build profiles of people.
- Keep such data in a restricted collection, and delete it when its purpose ends.
- If you share an index with others, you may be disclosing personal data to them.

### Database rights and other regimes

Some jurisdictions protect the investment in a database of facts separately from copyright, and some countries restrict exporting certain data. If you
extract structured data in bulk from one source, check.

### Liability for what you store

Once you hold copies, you hold whatever is in them: defamatory text, illegal material, secrets published by mistake. The ingestion filters reject common secret
shapes in repository packs, but a crawl is stored as fetched. Know what you are keeping, and delete what you should not hold.

## Ethical considerations

Law sets a minimum; these are the questions a considerate crawler asks.

- **Respect the owner's wishes.** A `robots.txt` that disallows you, a polite request to stop, or an obvious sign that a site does not want to be copied are
  answers, even where the law would allow you to continue.
- **Do not impose costs.** A small site pays for your traffic in server load and bandwidth. Crawl less, slower, and rarely.
- **Take only what you need** for the purpose you have, and use it only for that purpose.
- **Do not take credit.** Quote, attribute and link. Do not present other people's work as your own or as your assistant's knowledge.
- **Protect third parties.** If pages mention people, treat their information with the care you would want for your own.
- **Be transparent where you can.** Where a site owner would reasonably want to know who is crawling, tell them (and ideally make yourself reachable).
- **Think about downstream use.** An index feeds search, assistants and possibly model training. Content collected for private search should not quietly
  become training data or a public product.
- **Prefer cooperation.** Asking a site owner, or using what they offer for reuse, is often faster than working around them.

## Practical settings

| Goal | How |
|---|---|
| Crawl slower | `HIPPO_CRAWL_DELAY=2` (seconds) in `hippocampus.env`, then `./hippocampus up` |
| Crawl fewer pages | `HIPPO_CRAWL_MAX_PAGES=50`, and a low depth in the crawl form or request |
| Confine a crawl to one section | The **URL match pattern**, for example `/docs/` |
| Avoid repeat load | Crawl once, then search the index; re-crawl only when you must |
| Keep a crawl private | Create the collection as **restricted** and give accounts only the access they need ([collections-and-access.md](collections-and-access.md)) |
| Limit who can start crawls | Keep `HIPPO_REQUIRE_AUTH=true` so a crawl needs a token with write access |

## Handling a removal request

If a site owner or a person asks you to remove their content:

1. **Stop** crawling that site.
2. **Delete** the content. For a whole site in its own collection: delete the collection (dashboard **Delete**, or `DELETE /v1/collections/{slug}`). For single pages:
   use SQL ([../Database/README.md](../Database/README.md#a-sql-cookbook)). If the collection mixes sites, the page addresses (`uri`) tell you which sources belong
   to which site.
3. **Remember backups.** A dump of the database still contains the content ([operations.md](operations.md#backups)); handle it according to your retention policy.
4. **Do not re-crawl** unless the owner agrees.
5. **Reply politely** and keep a short record of what you did.

## Keeping records

Keep a simple log outside the service: the date, the start URL, the depth and pattern, the purpose, who ran it, and any permission obtained (a link or an email).
Job logs list what was fetched, but they live in memory and are lost when the service restarts.

## Quick decision guide

| Situation | Suggestion |
|---|---|
| Your own site or documentation | Fine; keep to the technical limits |
| Open-source project documentation | Usually fine for private use; check the licence for sharing; crawl gently |
| A commercial site's public pages, for personal notes | Check the terms and `robots.txt`; if they forbid it, do not; keep it small and private |
| A site that requires login or payment | Do not |
| A social network, forum or people directory | Do not; personal data and the terms almost always forbid it |
| A site that disallows you in `robots.txt` | Do not |
| Bulk collecting a site's content to share or sell | Get permission and legal advice first |
| Content for training a model | Get legal advice; this is the most contested area |
| Someone asks you to stop or delete | Do so, promptly |

## Where to read more

- The Robots Exclusion Protocol is specified in **RFC 9309**.
- Privacy law: for the EU and UK, the **GDPR**; for other places, your national data-protection authority's guidance on scraping and on personal data.
- Text and data mining and opt-outs: the **EU Directive on Copyright in the Digital Single Market** (articles on text and data mining) and the W3C community work on the
  **TDM Reservation Protocol**.
- Your own country's rules on unauthorised computer access and on copyright exceptions.
- For anything that matters, a lawyer who knows the field.

## Planned improvements

These would close gaps listed above and are candidates for future work: honouring `Crawl-delay`, `X-Robots-Tag` and `<meta name="robots">`;
treating an unreachable `robots.txt` as disallowing; conditional requests to avoid re-downloading unchanged pages; a configurable user agent with a contact
address; a per-source delete in the API; and a short responsible-use reminder in the crawl form.
