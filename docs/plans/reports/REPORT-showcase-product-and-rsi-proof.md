# REPORT: first showcase product and the recursive-self-improvement proof design

Deliverable of docs/plans/PLAN-showcase-product-and-rsi-proof.yaml and the single evidence base of
that one plan: Decision 220 (the product boundary) and Decision 221 (the proof design) both cite it
by line. It holds (a) the rationale and evidence those entries rest on and (b) the reconciliation of
the proof design with the wave 1-3 feedback-loop reports. Section 10 records product detail as
provisional, non-authoritative input that the product repository adopts or supersedes at otl init
(Decision 220 clause 6); nothing in it binds this repository. This is not a standing architecture
document (Decision 86): commitments live in the two Decisions, forward work in the recommendations
they name.

Sources were read on 2026-10-16 and 2026-10-17. Market and legal statements are as reported by the
cited source on that date; none is legal advice or a finding of this repository. Nothing here is a
cost of this platform or a financial figure of a hosted product (Decision 101 point (d), read on
2026-10-17 as financial performance of any kind).

## 1. Provenance and authority

- Operator session 2026-10-16/17 (https://claude.ai/code/session_013fhQibkVcJdx5FuNXpNZN2). The
  operator ruled the product, its placement, the purpose ordering, the commercial model, the
  product repository as the first otl init proving ground, and taste as human-owned. The
  orchestrator proposed the proof design, the learned-component terms, the staged ladder with its
  stop rule and the Tier A list; the operator agreed to those conclusions in aggregate.
- Purpose ordering, in the operator's words: proving governed recursive self-improvement publicly
  is "more valuable than anything else"; revenue is a hedge ("if I can make money on the side,
  excellent; if I can outcompete them, even better"). Proof of market for On The Loop comes from
  other people adopting it, never from product revenue.
- The operator's first instinct on bot gates was "CAPTCHAs, which agents can fill out anyway".
  Decision 220 clause 3 reverses it on the grounds in section 2; a later change re-decides it
  through /plan, never by quietly reverting it.
- The operator holds that every class of difficulty, adversarial included, is surmountable by the
  loop short of prohibitive compute cost. That is a belief, recorded here and not as a clause; the
  ladder scales difficulty instead of avoiding it.

## 2. Why not an adversarial scraper (Decision 220 clauses 2-3)

The stop rule stands on four grounds:
- Oracle poisoning. Defenders serve decoys to a detected scraper without telling it (Cloudflare AI
  Labyrinth, https://blog.cloudflare.com/ai-labyrinth/). A self-improving loop optimising a signal
  its adversary controls converges on garbage, and its receipts stop being trustworthy.
- Provider terms. Model-provider acceptable-use policies bar bypassing security controls and
  circumventing other platforms' guardrails (for example https://www.anthropic.com/legal/aup); API
  keys via LiteLLM are the sole inference lane On The Loop holds (Decision 205 clause 4, the
  operative text of Decision 184 clause 4).
- The buyer. Governed AI operations are bought by a compliance function; a governance platform
  whose showcase defeats public bodies' access controls cannot be sold to it.
- Root cause. Every evasion patch is a workaround with no root cause the operator owns (Decision
  55).

Legal context, as reported and not relied on as a finding (a legal review is the tier-three
precondition):
- UK Computer Misuse Act 1990 s.1 makes knowing unauthorised access an offence
  (https://www.cps.gov.uk/legal-guidance/computer-misuse); the question of authorisation is
  sharpest once a control whose sole purpose is to refuse bots has been defeated.
- US first-instance rulings reported in 2026 treat CAPTCHAs and bot detection as access controls
  under DMCA s.1201: Reddit v SerpApi (S.D.N.Y., July 2026,
  https://www.loeb.com/en/insights/publications/2026/08/reddit-v-serpapi-llc ) and Google v SerpApi
  (N.D. Cal., July 2026, dismissed only because search results carry no copyright,
  https://thenextweb.com/news/google-serpapi-dmca-scraping-lawsuit-dismissed ). Planning documents
  are third-party copyright.

The arms race is scaled by defenders with in-path visibility (as reported): Cloudflare has blocked
AI crawlers by default since 2025-07-01
(https://blog.cloudflare.com/content-independence-day-no-ai-crawl-without-compensation/), charges
per crawl over HTTP 402 (https://blog.cloudflare.com/introducing-pay-per-crawl/), co-authors the
IETF Web Bot Auth draft (https://datatracker.ietf.org/doc/html/draft-ietf-webbotauth-httpsig-protocol)
and delisted Perplexity for stealth crawling
(https://blog.cloudflare.com/perplexity-is-using-stealth-undeclared-crawlers-to-evade-website-no-crawl-directives/).
Cost per round is asymmetric: a heuristic change is free to the defender, while a countermeasure
costs the loop an inference cycle plus proxy spend, and Decision 217 clause 4 caps spend per rec.
Autonomy raises exposure: an unattended system circumventing controls at scale is a worse fact
pattern than a person doing it once.

Routes when a gate appears: request whitelisting (Cambridge City Council's published minutes
record scraper load on its planning portal and a move to whitelisting identified scrapers,
https://democracy.cambridge.gov.uk/documents/s55947/Minutes%20from%20Previous%20Meeting.pdf ); a
licensed bulk extract under the dataset provisions added to the Freedom of Information Act in 2012;
a partner; a bought feed. Unprotected portals are crawled politely: robots.txt, rate limits, an
identifiable agent string and a contact header.

## 3. Why not a contracts or tenders pipeline (Decision 220, options considered)

The operator's first, half-remembered idea, never persisted (the tree, git history and warehouse
were swept on 2026-10-16 and nothing was found). UK procurement notices are open data by law: Find
a Tender publishes every notice as OCDS JSON under OGL v3
(https://www.find-tender.service.gov.uk/apidocumentation/1.0/GET-ocdsReleasePackages ,
https://www.find-tender.service.gov.uk/Developer/Documentation); TED's API is anonymous and allows
commercial reuse (https://docs.ted.europa.eu/api/latest/search.html ,
https://ted.europa.eu/en/legal-notice); Kingfisher Collect already open-sources an OCDS loader
(https://kingfisher-collect.readthedocs.io/en/latest/spiders.html); funded incumbents exist
(Stotles, Tussell, BidStats). The data is not hard, the showcase is thin and the bid side is weak
for a solo founder. Procurement data survives as the linkage layer of the organisation graph.

Also considered and rejected in the session: job-posting history, and self-healing connectors sold
as a service.

## 4. Why a UK organisation graph over public money (Decision 220 clause 1)

- Compounding history nobody can backfill, and an entity graph that improves with each source;
  buyers pay for multi-year history with stable collection and for accurate entity mapping (as
  reported: https://beyondelevation.com/insights/license-company-data-buyers ,
  https://marketintelligencetools.com/rankings/data-providers/).
- Mandated and licensed sources: the Local Government Transparency Code 2015 (quarterly spend over
  GBP 500 and procurement over GBP 5,000, open and machine-readable, OGL recommended;
  https://www.gov.uk/government/publications/local-government-transparency-code-2015/local-government-transparency-code-2015);
  HM Treasury guidance for NHS bodies' spend over GBP 25,000, monthly, on a fixed CSV template;
  Companies House bulk files under OGL (https://www.gov.uk/guidance/companies-house-data-products);
  Procurement Act 2023 s.70 payments over GBP 30,000, quarterly, for contracts procured from
  2026-04-01.
- The gap: an ONS review found about three quarters of English authorities had published a full
  quarter, scattered, inconsistent and with no common supplier identifiers
  (https://datasciencecampus.ons.gov.uk/exploring-trends-in-local-government-spending-through-transparency-declarations/).
  Entity resolution across those files is the moat. Incumbents are small and human-maintained (as
  reported): Tussell, Spend Network, Stotles, UKSpend.
- A clean oracle and statistical power: published totals (NHS accounts, Find a Tender award totals)
  reconcile the graph from outside the loop, so self-healing is judged against facts the loop cannot
  shape (Decision 221 clauses 1-2), and hundreds of near-identical connectors give the loop the
  population its own sessions lack (section 8).
- Personal-data load is low once sole-trader payees are suppressed at the write boundary (Decision
  210).
- Law that shapes the split, as reported and not a finding: Regulation 13 of the Copyright and
  Rights in Databases Regulations 1997 protects substantial investment in obtaining, verifying or
  presenting contents (https://www.legislation.gov.uk/uksi/1997/3032/regulation/13), and British
  Horseracing Board v William Hill reads "obtaining" as collecting existing material; Regulation 19
  lets a lawful user take insubstantial parts for any purpose and voids contrary terms
  (https://www.legislation.gov.uk/uksi/1997/3032/regulation/19). Free aggregates with metered bulk
  rows is the split that law supports. OGL v3 permits commercial exploitation, including in your
  own product, with attribution, and excludes personal data
  (https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).

## 5. Why the product repository before three repositories the operator did not write

The operator wants flexibility and forgiveness for growing pains: a repository the operator
controls can be built and burned until its first paying customer. Decision 211 clause 6 is
unchanged; the product is a fourth adoption case, never a substitute for the three (Decision 220
clause 4). It is the intended first real fixture for T4.24 c2, recorded as intent only: T4.24's
criteria are a Decision 194 clause 2 licence-flip condition and must not wait on a product. An
option not decided: start the product repository OTL-free, built by ordinary sessions, and run init
later -- a target with history the platform did not shape, at the cost of building before MVP.

## 6. Planning data stays tier two

PlanIt (https://www.planit.org.uk/) aggregates English planning applications and offers a free API
without a stated licence or service level (as reported on its site). Its news page reports coverage
eroding to bot gates: 417 authorities with over thirty stale after a vendor change in June 2026 put
24 Tascomi council sites behind AWS CAPTCHA (https://www.planit.org.uk/news). Councils' stated
reason is load, not principle (the Cambridge minutes in section 2). Planning rivals, as reported:
Glenigan (early access via the Planning Portal since October 2025), Searchland, LandTech, Plota.
Mandated open planning data is arriving slowly: the Levelling-up and Regeneration Act 2023 ss.84-86
powers are in force; the Planning Data (England) Regulations 2026 cover plan timetables only;
application specifications are at vendor-pilot stage with no date; the national application dataset
is empty. Planning enters only as a non-personal projection (site, proposal, decision, appeal;
applicant and objector fields dropped). Training on third-party planning documents is excluded: the
UK text-and-data-mining exception is non-commercial only (section 10).

## 7. Comparables, positioning and market context

- Searchland is the benchmark for breadth and GUI standards (from GBP 195 a month); Plota is the API
  price comparison (GBP 49 to 399 a month, resale banned); both are planning-data businesses (tier
  two). LandTech lists GBP 2,500 to 6,000 per licence. Tier-one price comparables: Tussell (about
  GBP 11,400 per licence per year,
  https://www.applytosupply.digitalmarketplace.service.gov.uk/g-cloud-14/services/430518247842905)
  and Stotles (from about GBP 450 a month). Prices are from public pricing pages and listings, read
  2026-10-16. The operator intends to price well below the tier-one comparables and does not expect
  Searchland's breadth without more investment than planned at this stage.
- Positioning: On The Loop competes with code-generation tools through a methodology, not an IDE --
  AI software architecture and maintenance where development is led by human intent. Human review
  of AI code is already the bottleneck at most organisations, so code nobody comprehended already
  reaches production; On The Loop makes promotion mechanically earned through levels of trust. The
  gap is governed AI operations with bring-your-own models: the labs govern long-horizon agents for
  their own models only.
- Market context, as reported: capital concentrates in coding agents, where throughput is the
  product and compute decides (Cognition about USD 48B,
  https://techcrunch.com/2026/09/08/cognition-hits-48b-valuation-signaling-investors-believe-ai-coding-is-far-from-a-winner-take-all-market/);
  agent web-access infrastructure (Exa, https://exa.ai/blog/announcing-series-c ; Parallel, about
  USD 2B,
  https://techcrunch.com/2026/04/29/parallel-web-systems-hits-2b-valuation-five-months-after-its-last-big-raise/);
  agent governance raised about USD 435M across twelve rounds in five months, sold as runtime
  security to compliance buyers
  (https://cvj.ai/briefing/finance-news/ai-agent-security-startups-raise-435m-as-governance-becomes-the-bottleneck/);
  self-healing scrapers and agents are seed-stage
  (https://www.kadoa.com/blog/autogenerate-self-healing-web-scrapers ,
  https://www.startuphub.ai/ai-news/funding-round/2026/autoheal-raised-7-9m-to-heal-its-own-agents).
  Nobody well-funded sells living datasets or self-healing pipelines.
- Anthropic's Scion is an internal source-control system (the 2026-10-13 post Decision 217 cites),
  not an autonomous-direction system and not for general use; it shows that at agent throughput
  governance becomes the scarce layer. Anthropic's Institute post "When AI builds itself" reports
  Claude writing over 80 percent of merged code and says full recursive self-improvement "is not
  here yet", with humans setting direction
  (https://www.anthropic.com/institute/recursive-self-improvement). An unrelated Scion is Google's
  open-source multi-agent orchestration platform (https://github.com/GoogleCloudPlatform/scion), the
  nearest open-source comparator. The Hacker News discussion of Anthropic's post
  (https://news.ycombinator.com/item?id=50073633) voices doubt about intent-only review and agent
  technical debt; the operator regards proving that doubt wrong as the job.

## 8. The proof design against the designed feedback loop (Decision 221)

Reconciliation, verified 2026-10-16 against the wave 1-3 reports:
- P1 OUTCOME is absent by design: the ten loop components measure this repository's own sessions
  (docs/plans/reports/REPORT-w2-feedback-loop-synthesis.md:352-361) and product outcomes enter only
  at maturity (docs/PROJECT_CONTEXT.md, Recursive self-improvement model, the at-maturity sentence).
  It attaches as a user-authored Tier A north star with a
  failure_signal in the adopting repository (Decision 196 clause 7; Decision 211 clause 5), its
  detector taking a Goodhart register row.
- P2 (a judge the loop cannot edit) is partly covered: the register gives each detector an
  independent counter at a 0.99 floor, a seeded drill and a daily deterministic verdict, and
  back-validation fixes sample size before reading post-fix data
  (docs/plans/reports/REPORT-w1-back-validation.md:149-154). Missing: a human-sealed gold set
  (drills are build-authored and rotation is unowned), a separately credentialed probe, and judge
  paths in the Decision 117 boundary and Decision 185 clause 3's base-ref check (T4.21).
- P3 (a frozen control arm) is absent. Nearest attachment: back-validation's difference-in-differences
  option, deferred to the sampled rung (REPORT-w3-feedback-loop-staging.md:619). The population is one
  project under the operator's tenant (Decision 200's ids key it, never an arm); connectors are hashed
  into two arms per pre-registered epoch, the frozen arm running the epoch-start ref of its ontheloop/
  process artefacts and learned-component stamps on the same installed kernel (T4.24 c3), self-healing
  only. Arm and stamps are recorded at work-item admission in the loop record journal
  (REPORT-w3-feedback-loop-staging.md:318-343); each arm is a ladder lane (:290), so a non-cosmetic
  stamp change returns only the improving lane to read_all (:611). The arm label is a closed enum,
  allow-listable under k1 (a)/k4 (a) once rec-4141 admits it (:613-616); until then arm curves are
  reported through Decision 221 clause 4's pre-registered protocol, never the live receipts. Residual
  confounds: the shared CI verdict plane runs HEAD verifiers on both arms unless verifier edits are
  arm-scoped, and the trainer must filter traces by arm, so arms are compared on write-stamped OUTCOME
  and PROCESS series, never re-derived friction (REPORT-w1-back-validation.md:139-141, 532-541).
- P4 (pre-registration) exists outside the loop: ROADMAP-SEMANTO S1.7 and S1.8, deferred_post_mvp.
  S1.8 waits on a Decision 209 clause 3 placement for a fork repository, which Decision 220 does not
  supply, so S1.8 is unchanged.
- P5 (public receipts) is partly designed: the egress allow-list
  (docs/plans/reports/REPORT-w1-allow-list-transport.md:317-336), with CD.51 unruled
  (REPORT-w3-feedback-loop-staging.md:423) and rec-4141 the open owner. Per-change receipts cannot
  cross it (open-vocabulary keys, derived ids and commit hashes are excluded), so the public surface
  shows day-aggregate curves, never commits: a curated projection (Decision 111) derived at read
  (Decision 199 clause 1), with Decision 88's egress invariants binding every reader. Decision 209
  clause 5(i) is not amended; the consumer has its own recommendation, dependent on rec-4141.

Rulings that bind the design: dollars are never stored or published
(REPORT-w3-feedback-loop-staging.md:380-382); tokens and GB-seconds are the stored and published
facts, meaningful under subscription usage too, and dollars are only derived at read through the
price table (Decision 217 clause 4), so the receipts carry no financial figure; separate
ladders share one controller and one transition log (REPORT-w3-feedback-loop-staging.md:627), so the
product gets a ladder instance, not a third mechanism, and likewise one allow-list, one journal and
one filer; back-validation is the only proposer of a PROOF close
(REPORT-w3-feedback-loop-staging.md:359), so recursive aggregates are read-only views over the loop
record journal staged as CD.47 (REPORT-w3-feedback-loop-staging.md:318-343). The operator renumbers
placeholder CD ids at filing, so staged rows are cited by line (Decision 105). The staging declined
new T3.3 or T3.4 criteria while Decision 67 holds (REPORT-w3-feedback-loop-staging.md:243-246), and
T4.24's criteria stay untouched (section 5), so the design lands as Decision 221 and
recommendations.

Statistical power: about twelve sessions a day here; a friction under roughly three percent of
exposed sessions is unprovable in 28 days (REPORT-w1-back-validation.md:610-612). The product's
population (hundreds of near-identical connectors) is what gives the loop power to prove anything.
Review envelope: every component starts at read_all and promotions are human-approved, about sixty
reviews a day (REPORT-w3-feedback-loop-staging.md:299-302); the product enters as a small shard.

Learned components (Decision 221 clause 6): "the model never improves, the harness does" holds only
for frontier models reached through API keys. The loop can own learned components inside the
product (entity-resolution matcher, extractor, source-format classifier) retrained on its own
verified output, open-weight models fine-tuned on verified traces (a fine-tuned open-weight model
reached through LiteLLM is inside the inference lane of Decision 205 clause 4, and training is not
inference; rec-3386 recorded the weak-model thesis), and the routing policy. The control arm becomes
two-by-two: harness frozen or improving, learned components frozen or improving.

Sequence: MVP (Decision 93: one unattended iteration); T3.4 c1 (an anomaly is detected, a rec filed,
queued and fixed, and a telemetry delta proves the fix); the product as the first population; the
control arm once a sampled rung exists.

## 9. The init proving ground (Decision 220 clause 4; Decision 221 clause 7)

- The product repository is the first otl init target and the fourth adoption case (the dogfood
  case). Decision 211 clause 6 still requires three repositories the operator did not write; a
  greenfield repository the operator controls never substitutes for them.
- Burn protocol: keep the code and its git history (Decision 211 clause 1); delete the tracked
  ontheloop/ root and the gitignored .ontheloop/ runtime root (clause 5); re-run init; diff the
  generated boundary map and verification registry against the previous run. Init determinism on
  an unchanged tree is the first probe metric.
- Burn cases: an empty repository; the same repository after weeks of OTL-free history; a polyglot
  tree with a JavaScript front end beside the Python pipeline; boundaries that are network fetches,
  which Decision 211 clause 3's no-network sandbox characterises only through recorded fixtures (the
  needs-seams path).
- The init-attempt record (owner rec-4388; grain one row per otl init attempt, Decision 207): platform
  version, target fingerprint, structural assumptions resolved, accepted or failed
  (docs/contracts/ports.yaml structural_assumptions, seeded by rec-4191), characterisation coverage,
  seams needed, duration, tokens. A failed attempt or a non-empty determinism diff is a finding the
  filer can turn into a platform work item. Its fields land in the owning journal contract the day
  the table exists (REPORT-w3-feedback-loop-staging.md:337). rec-4388 also owns the engine capability
  the frozen arm needs: loading the ontheloop/ process artefacts at a pinned epoch-start ref rather
  than the working tree (Decision 208 clause 1 reads a governed artefact at a pinned ref).
- The burn window closes at the first paying customer; the product declines the house-style
  profile (Decision 211 clause 2) so adoption is tested honestly.

## 10. Provisional product inputs (non-authoritative; Decision 220 clause 6)

Provisional init-handoff input, recorded here only because the product repository does not yet
exist and superseded by it at otl init: operator rulings and accepted proposals from the session for
that repository to adopt or supersede. Taste and the outcome it protects enter that
repository's Tier A only as north-star invariants carrying a failure_signal (Decision 196 clauses 2,
3 and 7; Decision 211 clause 5), for example an accessibility-conformance invariant; everything else
below is Tier C (product decisions), including the design-system selection, legal terms, the pricing
rule and schema choices. None of it binds this repository.
- Commercial model (operator ruling): sell scale. Free aggregated, navigable data in a GUI is the
  showpiece; closed row-level data is sold through a self-serve metered API with minimal human
  involvement. Rows, history depth and freshness are paid and the insight is free -- volume is
  metered, row access is never gated as a capability (Decision 209 clause 1).
- Hosting: nothing is decided now. The operator's lean (2026-10-17) is one shared domain,
  onthelooplabs.com, with a subdirectory per product surface. Decision 101 point (e) makes that
  domain's public site a static Cloudflare Pages build with no hand-rolled hosting tooling, so a
  product surface under it needs a reading or amendment of point (e) when the host is chosen; rec-4389,
  the full supersession of Decision 101, carries that question. Point (d) binds any public surface
  regardless.
- First shard: NHS bodies' spend over GBP 25,000 (one Treasury CSV template, monthly, about two
  hundred trusts, totals reconcilable to published accounts), then Find a Tender (one API), then
  English councils (the fragmentation).
- Publisher provenance per row from day one (source URL, fetched-at, parser version) as the product's
  own field semantics: a buyer requirement, database-right evidence and a back-validation input.
  (docs/contracts/source-lineage.yaml is this platform's agent-type lineage key, not publisher
  provenance.)
- Static aggregates computed daily into a static site; the metered API is the only dynamic surface
  (Decision 100). Point-in-time reads come from DuckLake snapshots through named verbs (Decision
  84's closed boundary), never an exposed catalog.
- Data rules and scale: ops tables hold hundreds of rows, a spend graph tens of millions. Suppress
  sole-trader payees at the write boundary (Decision 210); partition every table (Decision 137);
  expect Decision 214's measured catalog-move triggers to fire; give the product its own catalog
  login and schema (Decision 216), never the ops catalog. Training uses verified, openly licensed
  rows only, never third-party documents (section 6).
- Recipes (Decision 211 clause 5): front-end build and test, per-source scheduling (T4.24 c4), a
  training loop for the learned matcher, release notes, support triage; each is a recipe init
  provides or declares as an adoption requirement.
- Taste: choose a design system before the first screen. For a UK public-money product the GOV.UK
  Design System's patterns are open and accessible by construction; its crown, header and typeface
  are not for non-government use. Enforce with axe, Playwright visual regression and Lighthouse
  budgets (T2.53 c3 already names zero console errors and zero axe violations and leaves aesthetic
  judgement to a human); keep UI components on a human-review rung permanently.
- Legal hygiene: terms of service; a privacy notice; a data-processing agreement for API customers;
  VAT on digital services; a correction and takedown route; OGL attribution and the no-official-
  status clause on every page; terms banning resale or bulk below an enterprise tier; a small free
  API quota so buyers can verify quality.
- Channels: Stripe metered billing; an AWS Data Exchange listing (as reported: a 3 percent
  public-offer fee, UK sellers eligible, a support process and a data-update process required).
- Day-one work items for the product repository's own store (Decision 211 clause 2): a source census
  of English councils and NHS bodies, the sealed gold set, the design-system choice.
- Trademark: Decision 198's trademark-conflict-found condition watches classes 9 and 42; a
  business-data information service may need class 35. Check before the product is named (rec-4385).
