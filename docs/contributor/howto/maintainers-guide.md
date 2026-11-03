# Maintainers Guide

This page offers guidance to project maintainers regarding our setup procedures, release processes, package creation, and other related tasks.

## Maintainer Onboarding and Best Practices

### Becoming a Maintainer or Triager

If you are interested in becoming a maintainer, you can join our community. Maintainers have several important responsibilities, so please read on to understand the role.

Also, if you're interested in helping manage issues with labels and interacting with incoming requests, you can have a "triager" role!

To get permissions, please start by participating on GitHub by answering questions, reviewing PRs, or contributing code or documentation. Once you're feeling comfortable, you can ask any of our maintainers for permissions by `@`ing them on GitHub.

### Maintainer Responsibilities and Expectations

1. As a maintainer, there is no strict time obligation, as we understand that everyone's ability to commit can fluctuate. However, we do expect maintainers to communicate openly and transparently with the team and the community.

2. As a maintainer, you are expected to uphold a positive team culture. This includes following the guidelines outlined in the [Openscapes team culture page](https://openscapes.github.io/series/core-lessons/team-culture.html) and the [recorded psychological safety talk](https://www.youtube.com/watch?v=rzi-qkl8u5M) . By doing so, you can help ensure that all team members and contributors feel safe, respected, and valued.


### Maintainer Processes Beyond Regular Contributing

1. As a maintainer, label issues clearly and consistently to help contributors identify issue types and priority. Use 'good first issue' for contributor-friendly issues.

2. As a maintainer, create welcoming environment when communicating with contributors (issue / PR / discussion posters).

3. As a maintainer reviewing and merging contributions is critical. Here are some best practices:

    3a. Review contributions thoroughly.

    3b. Provide constructive feedback.

    3c. Communicate clearly and respectfully.

    3d. Merge contributions promptly.

4. As a maintainer, you will be releasing different versions. More on this in [here](./releasing.md).

## Branches

main: This is the main branch, which is consistently tested and prepared for release as a new version. Avoid pushing changes directly to this branch. Instead, create a new branch and submit a pull request for any modifications.


## Continuous Integration & Delivery

The GitHub Actions CI services handle the project's building, testing, and management across Linux, macOS, and Windows platforms. The CI configuration files can be found in the `./.github/workflows/`.


## Continuous Documentation

[ReadTheDocs](https://readthedocs.org/projects/earthaccess/) is used to generate and host [our documentation website](https://earthaccess.readthedocs.io/) as well as the preview for documentation changes made in pull requests. This service uses a configuration file in the root of the project, `.readthedocs.yml`.

## Community Automations

We use a few GitHub bots and workflows to keep the community side of the project moving. Their configuration lives in the `.github/` directory.

### Stale bot

We use [Issue Manager](https://github.com/tiangolo/issue-manager) (`issue-manager.yml`) as our "stale" bot. When an issue is labeled `feedback requested` and goes 10 days without a response, the bot closes it and leaves a comment letting the author know they can re-open it, `@` a maintainer, or open a new issue.

The bot runs once a day (midnight UTC), and also responds to issues and pull requests being labeled or commented on.

### Dependency updates

[Dependabot](https://docs.github.com/en/code-security/dependabot) (`dependabot.yml`) opens pull requests to keep our Python and GitHub Actions dependencies up to date on a quarterly schedule.

### Pull request helpers

A few bots make life easier on pull requests:

- **Binder badge** (`binder-badge.yml`): adds a "Launch Binder" link to every pull request, so changes can be tried in a live notebook.
- **ReadTheDocs preview link** (`pr-rtd-link.yml`): adds a documentation preview link to each pull request description.
- **Integration test review comment** (`integration-test-review.yml`): when integration tests fail because the author doesn't have permission to run them, this leaves a comment asking a maintainer to do a security review and re-run the tests.

### Issue metrics

The issue metrics workflow (`issue-metrics.yml`) posts a monthly report summarizing issue activity.

### Collaboration Cafe discussions

A manually-triggered workflow (`discussions.yml`) creates a discussion thread for each Collaboration Cafe.
