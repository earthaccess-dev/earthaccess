# Our commitments to backwards compatibility

We care deeply about minimizing negative impacts of changes to earthaccess, but we also care deeply about making earthaccess the most valuable it can be to our users. These are sometimes in conflict, and this documentation helps us make decisions that balance these needs in a way that's best for our users.

## Our versioning scheme

We use [Semantic Versioning (SemVer)](https://semver.org/) to tell users what to expect when upgrading. We recommend following the link to learn more, but here are the important ways this affects you:

    1. There are 3 version parts: `MAJOR.MINOR.PATCH` (e.g. `1.2.3` is major version `1`, minor release `2`, patch release `3`).
    2. When the major version changes, anything can break! Always visit this documentation when doing a major upgrade.
    3. When the minor version changes, new features should be available, but nothing should break. Visit the documentation to learn about new features. If you discover any breaking changes, please [open a bug report](https://github.com/earthaccess-dev/earthaccess/issues/new)!
    4. When the patch version changes, only bugfixes should be included. Visit the CHANGELOG to learn more about the fixes.

## Our commitments

### Versioning

We will follow SemVer. All version changes will consider [this to be the public API documentation](../../api/index.md) for the purposes of deciding whether a change is breaking or non-breaking.

### Release Communication

1. We will announce releases on the following channel: [Github Releases](https://github.com/earthaccess-dev/earthaccess/releases)
2. Release announcements will include a prominent notification of breaking changes, including migration instructions.

### CHANGELOG

1. We will update the CHANGELOG for every release following [Common Changelog](https://common-changelog.org/) style.
2. The CHANGELOG will include prominent notification of breaking changes, including a link to migration guide.

### Fixing Backwards Incompatible Changes

1. We will plan to fix any backwards incompatible changes in non-major releases _or_ re-release with a major version bump.

2. We cannot guarantee a timeline under which maintainers will be able to complete this work alongside their other priorities.

3. Our maintenance team will always welcome outside contributions towards this goal.

4. Please use a [GitHub Issue](https://github.com/earthaccess-dev/earthaccess/issues) to communicate about this work.

## Our Python and dependency support policy

Our project follows the [SPEC0](https://scientific-python.org/specs/spec-0000/) dependency version deprecation policy, which outlines the guidelines for upgrading dependencies of _earthaccess_.

## Migration guides

### 0.19.0

* Many `DataCollection` and `DataGranule` methods are now read-only fields.
  For example, you'll get the size of a `DataGranule` named `granule` via
  `granule.size` instead of `granule.size()`. If you use the old syntax, you'll
  receive errors like `TypeError: 'NoneType' object is not callable`,
  `TypeError: 'str' object is not callable`, etc.

    The following methods were changed to read-only fields of the same name,
    except where noted:

    * `DataCollection.concept_id()` -> `DataCollection.concept_id`
    * `DataCollection.data_type()` -> `DataCollection.data_type`
    * `DataCollection.doi()` -> `DataCollection.doi`
    * `DataCollection.get_links()` -> `DataCollection.data_links` (note the name change, details below)
    * `DataCollection.landing_page()` -> `DataCollection.landing_page`
    * `DataCollection.s3_bucket()` -> `DataCollection.s3_bucket`
    * `DataCollection.services()` -> `DataCollection.services`
    * `DataCollection.summary()` -> `DataCollection.summary`
    * `DataCollection.version()` -> `DataCollection.version`
    * `DataGranule.dataviz_links()` -> `DataGranule.dataviz_links`
    * `DataGranule.size()` -> `DataGranule.size`

    **NOTE:** The method `DataCollection.get_links()` was replaced with the field
    `DataCollection.data_links`. The method name was previously `get_links` because in
    the CMR API, the links are described as links of type "GET DATA". However, while the
    old method name matches the name of the link type in the CMR API, it can be read as a
    verb ("get" the links, rather than links of _type_ "get"), when it's an attribute
    (noun). The name `data_links` was chosen to avoid confusion (noun instead of verb),
    and for consistency with `DataGranule.data_links()` (SUBNOTE:
    `DataGranule.data_links()` has arguments, so it was **not** changed to a property at
    this time).

### 0.14.0

* `earthaccess.login()` will now raise an exception if Earthdata Login rejects credentials.
  If you want to ignore errors, which we do not recommend, use a `try` block:

  ```python
  try:
      earthaccess.login()
  except Exception:
      pass
  ```
