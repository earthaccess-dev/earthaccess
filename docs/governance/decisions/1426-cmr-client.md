```yaml
needs-decision-url: https://github.com/earthaccess-dev/earthaccess/issues/1426
approvers-required: []
review-start: 2026-10-02
```

# Direction for Upstream Contributions to a CMR Client

The `earthaccess` community is piloting a formal, open decision process as part of a
governance framework for community-owned open source software projects
([1030](https://github.com/earthaccess-dev/earthaccess/pull/1030)). This document is
part of the decision process that was invoked on the issue "[decide the path for a CMR
client dependency](https://github.com/earthaccess-dev/earthaccess/issues/1426)".

## Context and Problem Statement

The `earthaccess` package depends on the `python-cmr` package for interactions with
NASA's Common Metadata Repository (CMR). The `python-cmr` package wraps CMR's HTTP API
calls in an object-oriented Python library, which provides the low-level implementation
of `earthaccess.search_data`, `earthaccess.search_datasets`, and other top-level
functions. The `python-cmr` package was originally developed by Justin (@jdeal),
subsequently contributed to the [`nasa`](https://github.com/nasa/) GitHub organization,
and has since garnered use by 153 repositories and 8 packages on GitHub.

Several improvements to the experience of `earthaccess` users have come about as changes to
the `python-cmr` package contributed upstream by  the `earthaccess` community.
However, these and other contributions have not been merged or released in a timely
manner, because `python-cmr` lacks active developers, or clear project governance, to
guide review and approval of upstream contributions from `earthaccess` developers. There
are NASA affiliated `earthaccess` contributors who hold GitHub's "maintainer" role on
`python-cmr`, but they remain hesitant to adopt the position of maintainer without
certainty about the project's governance. While `python-cmr` has been foundational,
`earthaccess` maintainers have raised the question of whether continued contributions to
the `python-cmr` package under the `nasa` org is the best approach. An initial proposal,
GitHub discussion
[1282](https://github.com/earthaccess-dev/earthaccess/discussions/1282), was to move the
`python-cmr` package into `earthaccess-dev`, adopting it as a community-owned project
like `earthaccess`. In-person conversations since then have led to needing a decision
among the options detailed below.

There are two areas of concern with contributing to the `python-cmr` under the `nasa`
GitHub organization. The first stems from the inability of any `nasa` owned project to
include non-NASA employees as maintainers on an equal-footing as NASA employees;
`earthaccess` relies on its status as a community-owned project to preserve momentem,
for itself and many of its dependencies. The second area is the legacy nature of the
`python-cmr` package, which pre-dated widespread adoption of certain standards,
specifications, and practices; some `earthaccess` maintainers have brought up developing
a new, modernized package.

Initial discussion on moving the `python-cmr` package outside the `nasa` org on GitHub
and into the `earthaccess-dev` organization touched on the following administrative and
community-engagement challenges incurred by the `nasa` org's ownership:
  - no "owner" role allowed on the repository; this role is restricted to `nasa` org
    owners
  - the "maintainer" role, including access to repository settings for GitHub Actions,
    is restricted to holders of a NASA identity
  - the lack of NASA employees with expertise in, or time dedicated to, administration
    and maintenance of `python-cmr`
  - potential requirement for uses of Contribotor License Agreements (CLAs)

These factors have been perceived as discouraging contributions from non-NASA employees,
even though
[documentation](https://github.com/nasa/instructions/blob/master/docs/INSTRUCTIONS.md)
for the NASA GitHub organization does strive to [welcome such
contributions](https://github.com/nasa/instructions/blob/master/docs/INSTRUCTIONS.md#instructions-for-who-can-be-a-member).

Any renewed development of legacy Python package faces the question of "shoehorning"
certain features versus starting over, and this may be especially true for client
packages maintained separately from the API they support.
  - the `python-cmr` client could be re-built following an Open API specification
  - the `python-cmr` client is unaware of CMR's GraphQL endpoint development

## Considered Options

### Option 1: continue depending on `python-cmr` in-place, i.e. within the `nasa` org

Pros:
- no new repository or package to create
- existing user-base of `python-cmr` will benefit from bug fixes and enhancements
- the NASA employees developing the CMR API may be more likely to contribute
- potential driver for change within the `nasa` org to welcome maintainership by
  non-NASA employees

Cons:
- continues an apparent barrier to contributions by non-NASA employees
- continues existing sources of administrative friction with `nasa` ownership noted in
  the problem statement
- inherits any existing technical debt or architectural limitations
- new features could be harder to develop within the package's legacy framework.
- does not prescribe a resolution for issues in `earthaccess` that are blocked by `python-cmr` issues
- perverse incentive to implement fixes within `earthaccess` that belong in a standalone
  CMR client

### Option 2: continue depending on `python-cmr` and move it to the `earthaccess-dev` org

Pros:
- retains the commit history and existing codebase of `python-cmr`
- brings a ready group of knowledgeable maintainers with equal standing, regardless of
  employment status
- promotes new contributions from current `earthaccess` contributors
- facilitates coupling CMR client releases with `earthaccess` releases when needed

Cons:
- inherits any existing technical debt or architectural limitations
- new features could be harder to develop within the package's legacy framework
- the NASA employees developing the CMR API may be less likely to contribute
- may engender perceptions of disfunction with the NASA Open Source processes

### Option 3: develop a new Python client based on an Open-API Specification (OAS)

Pros:
- adopts a widely used API specification
- the client is trivial to build from the (OAS) description (a JSON object)
- existing user base of `python-cmr` will encounter fewer (if any) breaking changes
- community-ownership under `earthaccess-dev`

Cons:
- the CMR API is not bound by the Open-API Specification, so all features may not work
- the `earthaccess` maintainers cannot assume CMR developers will contribute
- the OAS description would probably not be served by the CMR API itself

### Option 4: develop a CMR client package targeting only `earthaccess` requirements

Pros:
- existing user base of `python-cmr` will encounter fewer (if any) breaking changes
- community-ownership under `earthaccess-dev`
- clean slate to implement modern Python standards (e.g., strong typing, async http
  clients)
- enables cross-repository workflows for robust testing against `earthaccess`
  requirements
- surfaces only the CMR endpoings used by `earthaccess`
- responses from CMR can be matched to requirements for `earthaccess`
- creates opportunity to adopt CMR's GraphQL where it improves `earthaccess` performance

Cons:
- existing user base of `python-cmr` will encounter fewer (if any) improvements
- development effort to build a new package up to feature parity with the critical
  portions of `python-cmr`
- fragmentation of the ecosystem (having two CMR wrappers available may confuse new
  users initially)

## Decision Drivers

This decision rests not only on which option will most improve the `earthaccess` user
experience. The options above have real and perceived side-effects on the community of
contributors, both NASA employees and others. Therefore the decision drivers include the
`earthaccess` user experience, the sustainability of contributions to the CMR client,
and the continuation of NASA employee contributions to free and open source (FOSS)
software. The priorities of the decision committee while choosing among the options
above are, in order:
1. to ensure `earthaccess-dev` contributors are always welcomed and empowered
2. reduce barriers to improving `earthaccess` interactions with the CMR HTTP API
3. maintain pathways for NASA employees to contribute to community-owned, open source
   projects

### Selected Option

### Rejected Options
