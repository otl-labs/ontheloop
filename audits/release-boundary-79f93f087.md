# Release boundary review (S1-S5)

Audited at `origin/main` `79f93f087`, 2026-11-23. Companion to `audits/release-boundary-79f93f087.yaml`: 18 findings
(6 novel, 11 planned-insufficient, 1 planned-unbuilt; 5 high, 13 medium, none critical), 13 rejected-candidate rows,
all 27 candidates placed. Read-only audit; the operator disposes.

## Synopsis

A release should ship four things: core code under one import namespace; schemas, default values, base persona
contracts and base skills as read-only package data; a generated Tier B snapshot of the decisions its mechanisms
enforce; and the house-style profile as defaults an adopter may decline. This repository keeps its CI, Terraform,
roadmap, plans, audits, operator deployment configuration, its own north stars and decisions, and its instance files,
which move under a tracked `ontheloop/` root like any adopter's. Today that boundary is neither recorded nor enforced:
no artifact carries a release class, and core code finds the project from its own file location (at least 85 root
constants, at least 305 files quoting repository paths), so an installed build could not find the project it governs.
Three designs would leave gaps if built as written: the Tier B trimming rule keeps 215 of 228 live decisions when
read literally; declining the house-style profile scaffolds records that no check reads; and persona contracts pin
this repository's review rules into hashed prompts with no slot for adopter guidance. The pull-forward is viable now,
but T4.24 cannot simply be reactivated, because the roadmap schema refuses a live item that depends on deferred ones.
Do first: record the pull-forward as its own roadmap item, then add the release classification and the
path-resolution seam in parallel, then settle two names (the settings file and the import namespace). Two built
precedents are worth generalising: the verdict shell that runs the default branch's code over a snapshot of the
change, and the digest-pinned persona image.

## Verdicts

- Q1 Release classification: **partial** -- validated homes exist (contract headers, check entries, import-linter);
  no classification is recorded (RB-04, RB-05).
- Q2 Init-specification source: **insufficient** -- 16 hand-kept rows and 22 settings cannot cover a structural
  coupling (RB-02).
- Q3 Target layout and enforcement: **insufficient** -- no check can fail when core code reads a repository-only
  file (RB-01, RB-16).
- Q4 Pull-forward: **viable_with_preconditions** -- its own roadmap item, two naming decisions, and the
  classification before bulk migration (RB-18).
- Q5 Repository topology: **instinct_holds_with_conditions** -- the three-repository split holds; the adopter
  products monorepo first needs an installable release, the seam, a correct Tier B, a working profile decline and
  persona guidance.
- Q6 Released-version rule: **instinct_holds_with_conditions** -- wait for SIT and PROD only for the cloud-hosted
  kernel; checks, hooks and the verdict lane can run a pinned build sooner (RB-15).
- Q7 Persona and skill customisation: **insufficient** -- no design exists; guidance must enter as input values in
  slots the shipped contract already declares (RB-10, RB-12, RB-13).
- Q8 Decisions carry their why: **partial** -- the reason exists upstream for most entries but does not reach the
  snapshot, the failure text or the override (RB-06, RB-07, RB-08, RB-09).
- Q9 Industry practice: **partially_at_practice** -- 2 met, 7 partial, 3 missed of 12.

## First five slices

1. NEW-1 -- Record the pull-forward as its own live roadmap item; T4.24 keeps the deferred remainder.
2. NEW-2 -- Add a `release` field to contract headers, check entries and skill frontmatter, with a check that fails
   an unclassified artifact.
3. NEW-3 -- Add the path resolver (release, project and runtime roots), move `scripts/checks/_common.py` onto it,
   and ratchet new file-location roots.
4. rec-4673 -- Rename the settings file before it exists.
5. T4.24:c5 -- Record the artifact form, the import namespace and the single build root in a Decision.

Slices 2 and 3 can run in parallel. The first slice of T4.24:c7 (Tier B and Tier C id prefixes) shares order 3 with
slices 4 and 5.

## Highest-leverage change

RB-01, the path-resolution seam. Every later move depends on core code asking one resolver where shipped schemas,
project content and runtime state live, instead of deriving paths from its own location. It changes no behaviour on
day one (its defaults equal today's layout); it unblocks creating `ontheloop/`, the settings and north-star moves, an
installable build and the released-version rule; and its role list becomes the generated source for the `otl init`
specification (RB-02). RB-04's classification is its companion: it is cheap and decides which packages need the seam
at all.

## Open questions for the operator

1. Which import namespace and which build root does the distribution use, given that `src/ontheloop/` is named but
   absent and a placeholder package already exists (RB-16, T4.24:c5)?
2. Do you accept a typed `enforces` edge as the Tier B membership test in place of free-text citation, with an
   explicit include list for direction decisions (RB-05)?
3. Is a Tier B entry's effective text consolidated when the snapshot is built, or must each amending Decision carry
   replacement clause text (RB-06)?
4. Should generic guidance slots and a provider seam be added to the persona contracts before T4.28 to T4.30 build
   producers for this repository's specific inputs (RB-10)?
5. Will you amend Decision 185 clause 3 so the released-version rule binds per plane, this repository first, with the
   cloud-hosted kernel after T2.58 (RB-15)?
6. How should the north stars that bind every release (NS.1, NS.6, NS.9) reach an adopter: as read-only Tier A text,
   only through their carrying Decisions in Tier B, or not at all (release_classification K19, Q10 j)?
