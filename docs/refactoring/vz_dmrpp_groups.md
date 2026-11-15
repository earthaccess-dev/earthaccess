# Upstream PR Plan: Nested Group Support in VirtualiZarr's DMR++ Parser

## Problem statement

`virtualizarr.open_virtual_datatree()` returns a single-node `xr.DataTree` when
using `DMRPPParser`, even for files with multiple nested HDF5/NetCDF4 groups
(e.g. NASA TEMPO_NO2_L3 granules with `/product`, `/geolocation`,
`/support_data`, ...). The same file opened locally with `xr.open_datatree()`
(or virtualized with `HDFParser`) correctly exposes the full group hierarchy.

This means `earthaccess.virtualize([granule], access="https", group="/",
tree=True)` silently drops all subgroups when the default `parser="DMRPPParser"`
is used (the common case, since most NASA collections ship DMR++ sidecars).

## Root cause

Repo: `betolink/VirtualiZarr` (fork), file: `virtualizarr/parsers/dmrpp.py`

- `DMRParser._split_groups()` / `_split_groups_recursive()` (lines ~222-258)
  **already recursively walks** the DMR++ XML and builds a flat
  `dict[Path, ET.Element]` mapping every group path to its XML element
  (confirmed exercised by `test_split_groups` /
  `virtualizarr/tests/test_parsers/test_dmrpp.py`).
- `DMRParser.parse_dataset()` (lines ~140-185) takes that flat dict and does
  `.get(group_path)` for **exactly one** requested group, discarding every
  other entry.
- `DMRParser._parse_dataset()` (lines ~260-305) builds
  `ManifestGroup(arrays=manifest_dict, attributes=attrs)` — note **no
  `groups=` kwarg is ever passed**, so the resulting `ManifestGroup` is always
  a leaf, regardless of how many `<Group>` children existed in the XML.

Contrast with `HDFParser._construct_manifest_group()`
(`virtualizarr/parsers/hdf/hdf.py`, lines ~196-238), which recursively calls
itself for every `h5py.Group` child and passes results into
`ManifestGroup(groups={...})`. That's the structural piece missing from the
DMR++ path.

Existing test coverage already proves the group-splitting logic works
correctly for nested groups:
- `test_parse_dataset_nested` (test_dmrpp.py ~line 526) opens
  `/test`, `/test/group`, and root individually via `group=` and checks each
  is parsed correctly — but never checks that opening root also yields the
  children as a *tree*.
- `hdf5_groups_file` fixture (`/test/group/...`) is a ready-made 2-level
  nested-group DMR++ test fixture we can reuse for the new tree test.

## Proposed fix

Make `_parse_dataset` recurse into child `<Group>` elements and attach them
as subgroups, rather than requiring the caller to preselect a single group
via `group_path` lookup:

```python
def _parse_dataset(self, root: ET.Element) -> ManifestGroup:
    # ... existing manifest_dict variable-parsing logic (unchanged) ...
    # ... existing attrs / HDF5_GLOBAL unwrapping logic (unchanged) ...

    groups: dict[str, ManifestGroup] = {
        g.attrib["name"]: self._parse_dataset(g)
        for g in root.iterfind("dap:Group", self._NS)
    }

    return ManifestGroup(
        arrays=manifest_dict,
        groups=groups,
        attributes=attrs,
    )
```

`parse_dataset()` changes from "look up the one group the caller asked for
and parse only that" to "always parse the full tree from `self.root`, then
(optionally) drill down to the requested `group` as a subtree root" — mirrors
how `HDFParser` handles `group=` via path-based subgroup selection
(`ManifestGroup.__getitem__`/subtree navigation), so `group=` still works for
`open_virtual_dataset` (single-group case) but `open_virtual_datatree` gets
the full tree for free.

### Wrinkles to resolve during implementation

1. **`HDF5_GLOBAL` attribute unwrapping mutates `root` in place**
   (`root.remove(hdf5_global_attrs)` / `root.extend(...)`, lines ~294-298).
   Must confirm this happens per-recursion-level correctly and doesn't leak
   global attrs into subgroup `<Attribute>` iteration, or hoist it out so it
   only ever applies to the true root element.
2. **`_split_groups` / `_split_groups_recursive` may become redundant** once
   `_parse_dataset` recurses natively via `root.iterfind("dap:Group", ...)`.
   Decide whether to keep them (e.g. still used by `find_node_fqn` subtree
   selection, or by `test_split_groups`) or remove/refactor once the new path
   lands. Don't break existing tests that assert on `_split_groups` directly.
3. **`group=` argument semantics for `parse_dataset()`** — need to decide:
   does passing `group="/test"` return a `ManifestStore` rooted at `/test`
   (i.e. `/test` becomes the new root, current single-group behavior,
   preserving backward compatibility for `open_virtual_dataset`), or does it
   always return the full tree and only `open_virtual_datatree` cares about
   `group=` as a subtree-selection convenience? Recommend: preserve current
   `parse_dataset(group=...)` behavior exactly (returns subtree rooted at
   `group`, single-group `ManifestGroup` semantics as today), but make
   `_parse_dataset` build a *nested* `ManifestGroup` at whatever root it's
   given, so `to_virtual_datatree()` on that root's `ManifestStore` yields all
   descendants of `group`, not just `group` itself.
4. **`skip_variables` / `drop_variables` propagation** — must be threaded
   through recursive calls so it applies at every group level (already the
   case for `self.skip_variables`, verify no regression when recursing).
5. **Performance** — recursing eagerly parses every group's variables even
   when a caller only wants a single group via `open_virtual_dataset(group=X)`.
   Confirm this isn't a meaningful regression for very deeply-nested files
   (TEMPO-like products have ~2-3 levels, should be fine) — may want to keep
   single-group `parse_dataset(group=X)` non-recursive/lazy where the caller
   doesn't need `groups=` populated (i.e. only eagerly recurse when called via
   the datatree code path).

## Test plan

- New test: `test_parse_dataset_datatree_nested` (or similar) using the
  existing `hdf5_groups_file` fixture — parse root, assert the returned
  `ManifestGroup.groups` contains `"test"`, and `groups["test"].groups`
  contains `"group"`, with the correct arrays/attrs at each level.
- New/extended test on `open_virtual_datatree` end-to-end using
  `DMRPPParser` (not just `HDFParser`) against `hdf5_groups_file` — assert
  the resulting `xr.DataTree` has nodes `/`, `/test`, `/test/group` (mirrors
  whatever `HDFParser` produces for the same underlying file, if such a
  parallel test/fixture pair already exists — check
  `test_parsers/test_hdf/` for the HDFParser equivalent to reuse as a
  template).
- Ensure existing tests still pass: `test_split_groups`,
  `test_parse_dataset` (group warns / no-such-group cases),
  `test_parse_dataset_nested`, `test_find_node_fqn_grouped`,
  `test_NASA_dmrpp`, `test_NASA_dmrpp_load`.
- Add a regression test using a real NASA DMR++ fixture with nested groups if
  one isn't already present (TEMPO_NO2_L3 structure: `/product`,
  `/geolocation`, `/support_data`, `/qa_statistics`) — may require adding a
  new small fixture/cassette if `test_NASA_dmrpp` doesn't already cover a
  multi-group DMR++.

## Rollout steps

1. Open an issue on `zarr-developers/VirtualiZarr` (upstream) describing the
   bug with a minimal repro:
   ```python
   import earthaccess
   g = earthaccess.search_data(short_name="TEMPO_NO2_L3", version="V03",
                                temporal=("2025-01-11", "2025-01-18"), count=1)
   vdt = earthaccess.virtualize([g[0]], access="https", tree=True)
   print(vdt)  # only root group present, expected /product, /geolocation, etc.
   ```
   Reference this plan / the root-cause analysis above.
2. Implement the fix on `betolink/VirtualiZarr` fork (already cloned locally
   at `/home/betolink/hackweek/VirtualiZarr`), branched off `main`
   (currently at `ceee48a`).
3. Add/adjust tests per the Test Plan above; run full `test_dmrpp.py` and
   `test_xarray.py` (datatree-related) suites.
4. Open PR upstream referencing the issue. Call out the `group=` semantics
   decision (wrinkle #3) explicitly for maintainer review, since it's the
   main API-shape question.
5. Once merged and released (>= next VirtualiZarr version), bump the
   earthaccess pin in `pyproject.toml`
   (`virtualizarr >=2.3.0  # open_virtual_datatree() added in 2.3.0`) to the
   new minimum version, and drop/update any earthaccess-side docs/warnings
   that steer users toward `parser="HDFParser"` as a workaround for
   `tree=True` (see `earthaccess/virtual/core.py` docstring for `tree`).
6. Add an earthaccess-side test exercising
   `virtualize(..., tree=True)` with the default `DMRPPParser` against a
   multi-group product (once the fix is available), to prevent regressions
   from silently reintroducing the flat-tree behavior.

## Files involved (fork: betolink/VirtualiZarr)

- `virtualizarr/parsers/dmrpp.py` — primary fix (`DMRParser._parse_dataset`,
  `parse_dataset`)
- `virtualizarr/tests/test_parsers/test_dmrpp.py` — existing tests to
  preserve + new tests to add (fixture `hdf5_groups_file` already available)
- `virtualizarr/parsers/hdf/hdf.py` — reference implementation pattern
  (`_construct_manifest_group`) to mirror
- `virtualizarr/manifests/group.py` — `ManifestGroup(groups=...)` API already
  supports this; no changes expected here
- `virtualizarr/manifests/store.py` — `ManifestStore.to_virtual_datatree` /
  `construct_virtual_datatree` (`virtualizarr/xarray.py`) already consume
  nested `ManifestGroup.groups` correctly for `HDFParser`; no changes
  expected here, this confirms the fix is isolated to the DMR++ parser only

## Related earthaccess-side follow-up (separate, smaller PR)

Once the VirtualiZarr fix lands, consider whether
`earthaccess/virtual/core.py`'s `virtualize(..., tree=True)` docstring/warning
about falling back to `HDFParser` needs updating, and whether a test should be
added in earthaccess's own test suite
(`tests/` — locate existing `virtualize`/`tree=True` tests) asserting nested
groups are present when `tree=True` is used with the default `DMRPPParser`
against a multi-group fixture.
