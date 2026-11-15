# Integration tests

## Testing most popular datasets

Some integration tests operate on the most popular collections for each provider in CMR.
Those collection IDs are cached as static data in `tests/integration/popular_collections/`
to give our test suite more stability. The list of most popular collections can be
updated by running a script in the same directory.

Sometimes, we find collections with unexpected conditions, like 0 granules, and
therefore "comment" those collections from the list by prefixing the line with a `#`.

!!! note

    Let's consider a CSV format for this data; we may want to, for example, allow
    skipping collections with a EULA by representing that as a column.

## Proxy worker tests

`tests/integration/test_proxy.py` verifies that `earthaccess.set_proxy()` works
end to end. It starts the Cloudflare Worker in
`tests/integration/proxy_worker/` using `wrangler` (via `npx`), so running these
tests requires Node.js. The worker fixture is skipped (not failed) when `npx` is
unavailable, but the credential-dependent tests fail if credentials are not
configured. Credentials are read from the environment, or from a gitignored
`.env` file at the repository root:

- `EARTHDATA_TOKEN`, or `EARTHDATA_USERNAME`/`EARTHDATA_PASSWORD`, for PROD
- `EARTHDATA_UAT_TOKEN`, or `EARTHDATA_UAT_USERNAME`/`EARTHDATA_UAT_PASSWORD`, for UAT
