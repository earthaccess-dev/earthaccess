# Issue Triaging and Prioritization Guide

This document outlines our approach to triaging issues in GitHub, including guidelines for labeling and resolving issues, and best practices for maintaining a well-organized, prioritized, and up-to-date issue tracker via the [`earthaccess` GitHub Project](https://github.com/orgs/earthaccess-dev/projects/1). 

**We hope that this guide will empower anyone to contribute to issue triaging, and address a common question for contributors: "I'm interested in working on the highest priority issues that will solve important problems facing the `earthaccess` community. Where do I begin?"**

## Issue Lifecycle

1. A [new issue](https://github.com/earthaccess-dev/earthaccess/issues/new/choose) is created, either using a pre-existing template, or as a blank issue.
2. The issue is triaged to initially assess the reported issue and determine its priority.  
3. The issue is reviewed and groomed by the `earthaccess` community manager using the `earthaccess` [GitHub Project](https://github.com/orgs/earthaccess-dev/projects/1). 
4. The issue is worked following the [Pull Request (PR) Guide](./pr-guide.md).

Details on each of these workflow steps are provided below. 

### A new issue is created

Issues are created using one of several templates or as a blank issue. See the issue template choices [here](https://github.com/earthaccess-dev/earthaccess/issues/new/choose). When an issue is first created, provide initial acknowledgement and gratitude for the submission as a text or emoji response. 

By default, all new issues are created without a project status. Issues without a status are listed in the [Needs Triage](https://github.com/orgs/earthaccess-dev/projects/1/views/3) project view. 

### The issue is triaged

Triaging is led by the `earthaccess` community manager, though any community member is welcome and encouraged to contribute. Triaging involves:

- Determining whether the issue should be worked or not. If yes, move to "Backlog" status, otherwise close as not planned.
- Adding or adjusting issue labels.
- Adding an issue prioritization.
- Responding and follow up as needed (i.e. tagging relevant earthaccess maintainers for further support).

#### Moving an issue to backlog status

When triaging a new issue, review the information and provide a response or follow up with question(s) if needed. Move the issue to the Backlog unless it ought to be closed as "not planned", as outlined below. If you are unsure, add the **needs: triage** label. On the righthand side of the issue page, the "Projects" section contains an `earthaccess` project box. Click "no status" to select the status options. Statuses include:

- Backlog
- In Progress
- In Review
- Done

Select "Backlog". This will move the project out of the [Needs Triage](https://github.com/orgs/earthaccess-dev/projects/1/views/3) project view into its relevant backlog view depending on issue type. See below for more details on these other project views. 

#### When to "Close as not planned"?

Close issues as "not planned" when:

- An issue is not aligned with the project's goals or priorities.
- An issue is not feasible to be addressed due to technical or resource constraints.
- An issue is a duplicate of an existing issue that has already been addressed.

When closing an issue as not planned:

- Provide a clear explanation as to why the issue is not planned or feasible.
- Offer alternative solutions or workarounds, if possible.
- Link to relevant documentation or resources, if applicable.

#### Labeling issues

When labeling an issue, choose the [label(s)](https://github.com/earthaccess-dev/earthaccess/labels) that best describes the issue. Using labels consistently and accurately ensures that issues are trackable and searchable. 

Labels are mainly categorized by the prefix `type:`, `impact:`, or `needs:`. 
Impact labels describe what portion of the project they affect. Impact labels are also used to help group related issues based on a particular feature or topic. For example, **impact: virtual-datasets** is used to categorize Issues or Discussions related to virtualizarr integration and support. These labels may evolve over time as new features are worked. 

Refer to the [Labels](https://github.com/earthaccess-dev/earthaccess/labels) page for details on label types and descriptions. 

##### Linking Labels in GitHub Markdown

When referencing a label in a GitHub issue or discussion, it is useful to link to the label page to provide additional context and help other members to quickly understand the issue's category.

To link to a label in GitHub Markdown, copy-and-paste the URL to the label by right-clicking [any label](https://github.com/earthaccess-dev/earthaccess/labels) and selecting "Copy Link". Then, paste that label in a GitHub issue, PR, discussion, or Markdown document. For example, to link to the "good first issue" label in the earthaccess-dev/earthaccess repository, you would use the URL:

```
https://github.com/earthaccess-dev/earthaccess/labels/good%20first%20issue
```

### The issue is groomed

Issues are groomed periodically to organize and prioritize the backlog. Issue priorities are surfaced within the [Bug Priority](https://github.com/orgs/earthaccess-dev/projects/1/views/4) and [Docs](https://github.com/orgs/earthaccess-dev/projects/1/views/6) project views. When triaging a new issue, select a priority based on the user impact and urgency. The following guidelines apply broadly across issue types, with additional notes for bugs and documentation issues.

#### Priority: `1 - Critical`

The issue has significant, immediate impact on users and/or major components of the `earthaccess` library. 

- Core functionality is broken or inaccessible for a meaningful number of users
- Key workflows or use cases are blocked
- Users are likely to stop using the library or lose trust in it

*Bug example:* Users cannot search or access data for a significant number of collections or key `earthaccess` workflows.

*Documentation example:* Content is incorrect or missing in a way that would immediately block or mislead a user.

#### Priority: `2 - Important`

The issue has real impact but is not immediately blocking a majority of users.

- Affects primary user workflows but a workaround exists, or only a subset of users is affected
- Incorrect or confusing content that degrades the experience without fully blocking users
- Less common use cases or data collections are impacted

*Bug example:* Functionality is degraded but users can still accomplish their goals.

*Documentation example:* A Tutorial or secondary documentation is broken or unclear; contributing docs with significant usability issues.

#### Priority: `3 - Nice to have` 

The issue is a real improvement but not urgent.

- Affects a small percentage of users, data collections, or use cases
- Polish, enhancements, or "nice to haves" with no meaningful workflow impact
- Minor inconsistencies or style issues

*Bug example:* Affects a small number of users, data collections, or edge-case workflows; a workaround is readily available or the impact is cosmetic.

*Documentation example:* Minor inconsistencies, typos, or style issues; improvements to contributing or developer-facing docs with no impact on end-user workflows.

#### Notes for Triagers

- **When in doubt, start at Medium** and adjust based on community feedback or additional context.
- The [User Guide](https://earthaccess.readthedocs.io/en/stable/user/) and [API Reference](https://earthaccess.readthedocs.io/en/stable/api/) generally warrant higher priority than contributing or developer docs when impact is otherwise similar.
- Priority reflects *impact and urgency*, not effort — a quick fix can still be High priority.


### The issue is worked as a new pull request

- [Assign](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/assigning-issues-and-pull-requests-to-other-github-users) the issue to the implementer.
- Update status to `In Progress`.
- Link the issue to the related PR with a comment "Resolves #N" (`N` is the issue number). The issue will auto-close when the PR is merged.
- If the previous step is missed, [link any related PRs](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue) before manually closing an issue.


## Issue Triaging Workflow

``` mermaid

flowchart TD
  %%{init: {"flowchart": {"htmlLabels": false}} }%%
  classDef default font-size:32pt;
  start{"`Followed
  issue
  template?`"}
  start ==NO==> close1[Request needed information from reporter and update issue on behalf of reporter]
  start == YES ==> dupe{Is duplicate?}
  dupe == YES ==> close2[Close and point to duplicate]
  dupe == NO ==> repro{Has proper reproduction?}
  repro == NO ==> close3[Label: 'needs reproduction' bot will auto close if no update has been made in 3 days]
  repro == YES ==> real{Is actually a bug?}
  real == NO ==> intended{Is the intended behaviour?}
  intended == YES ==> explain[Explain and close point to docs if needed]
  intended == NO ==> open[Keep open for discussion Remove 'pending triage' label]
  real == YES ==> real2["Confirm that 'Bug' label was automatically added as part of the Bug Issue template, otherwise add 'Bug' label."]


  %% Link Color %%
    linkStyle default stroke:black,stroke-width:2px,font-size:24pt;

```


## Migrating between Discussions vs Issues

Use your best judgement when migrating between issues and discussions. Sometimes it makes more sense to open a new issue or discussion instead of migrating, for example when there are many things being discussed, but we want to create an issue or task out of just one of those things.

Migrate a discussion to an issue when:

- A specific task is identified.
- A bug or error is reported.
- A change or improvement is requested.

Migrate an issue to a discussion when:

- The issue is a nebulous idea that needs to be workshopped before it can be implemented.
- The issue is a general question or topic.
- The issue is not specific or actionable.


