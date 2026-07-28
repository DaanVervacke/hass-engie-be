# ENGIE Belgium Bruno collection

Every HTTP endpoint the integration talks to, as runnable requests. It is the
fastest way to see what ENGIE actually returns, whether you are reproducing a
user's bug or working out the shape of a payload before writing a parser.

Format is [OpenCollection](https://spec.opencollection.com/) YAML, the multi-file
working layout: `opencollection.yml` at the root, one `.yml` per request, one
`folder.yml` per folder. Open the `.bruno` directory in the Bruno GUI, or run it
with `bru` from inside this directory.

## Layout

| Folder | What it covers |
| --- | --- |
| `01-auth` | Auth0 PKCE + MFA login, steps 1 to 13, with an `email-mfa` subfolder |
| `02-token` | Refresh token rotation |
| `03-accounts` | Customer account relations, service points |
| `04-contracts` | Energy contracts, supplier energy prices |
| `05-insights` | Peaks, Happy Hours, TOU schedules, solar surplus, usage history |
| `06-billing` | Account balance |
| `07-feature-flags` | The three per-BAN flags the integration reads |
| `08-epex` | Day-ahead wholesale prices, hourly and quarter-hourly |

Each request's `docs` block names the `api.py` method it mirrors. That link is
enforced: `scripts/check-bruno-drift` fails CI when an endpoint method exists in
`api.py` with no matching request here.

## Setup

Select the **Local** environment and fill in two secrets. Bruno keeps secret
values in its own store, so nothing lands in Git.

There are two environment files, and they are not deployment targets. ENGIE has
one API. `Local` and `CI` differ only in where credentials come from: Bruno's
secret store when you are driving it, the process environment when a workflow
is.

| Variable | What it is |
| --- | --- |
| `ENGIE_USERNAME` | ENGIE account email |
| `ENGIE_PASSWORD` | ENGIE account password |

That is all the login needs. The other two identifiers seed themselves:

| Variable | Where it comes from |
| --- | --- |
| `ENGIE_BAN` | `03-accounts/Customer account relations`, first active agreement |
| `ENGIE_EAN` | `04-contracts/Energy contracts`, first active electricity contract |

Run those two once after logging in and the rest of the collection works. Both
scripts leave an existing value alone, so setting either by hand always wins.

Order matters between them. `Customer account relations` is the only data
request that needs no BAN, which is why it goes first. Running anything else
against an empty `ENGIE_BAN` drops the BAN out of the path and ENGIE answers 400
with `size must be between 12 and 12`.

If you do set `ENGIE_BAN` yourself, it must be the **businessAgreementNumber**,
not the shorter customerAccountNumber. Most endpoints return HTTP 400 for the
wrong one, and the peaks endpoint returns 500.

Everything else, including all base URLs, is already filled in. The base URLs
mirror `const.py` and CI checks that they still match.

## Running the login

Run `01-auth` requests 01 to 07 in order. Step 07 is what sends the SMS. Put the
code in `MFA_CODE`, then run step 08.

**Step 09 branches**, and which branch you get is decided by Auth0 per session:

- `Outcome A` means the auth code came back in the `Location` header. Steps 10 to
  12 must not run, because the Auth0 session is already finalised and a second
  resume returns `error=access_denied`. In a folder run the script jumps to step
  13 for you. Running requests one at a time, go to 13 yourself.
- `Outcome B` means Auth0 wants a passkey first. Run 10, 11, 12, then 13.

For email MFA instead of SMS, run 01 to 06, then the whole `email-mfa` subfolder,
then rejoin at step 09. Step 07 is never run on that path, so no SMS is sent.

Step 13 sets `accessToken`, which every request in folders 03 to 08 picks up
through the collection-level bearer auth. The access token stays valid for about
two minutes. When it expires, run `02-token/Refresh token` rather than logging in
again.

## Things that will bite you

**Refresh tokens rotate.** Every call to `02-token/Refresh token` invalidates the
token you sent. Replaying a spent one returns HTTP 400.

**Bruno sends the URL, not the params array.** A query parameter that appears
only under `params` is dropped without warning. Both places have to agree, which
is why every URL here carries its full query string. `scripts/check-bruno-drift`
fails the build when they diverge.

**A coordinator key is not a wire field.** `_peaks.py` talks about `peaks`
throughout, but that is the key `coordinator.py` files the whole response under.
ENGIE sends `dailyPeaks`. Check what the API returns before assuming a name in
the parsers is on the wire.

**Requests are ordered by dependency, not by resource.** `Service point by EAN`
sits in `04-contracts` rather than `03-accounts` because it needs the EAN that
`Energy contracts` seeds.

## How the assertions are written

Assertions never compare a raw response value. Chai prints both sides of a failed
comparison, and `expect(body).to.have.property('x')` prints the whole object, so
the usual patterns would put a BAN, an EAN, an address or a balance into a
world-readable Actions log.

They compare a derived fact instead: a status code, a JavaScript type name, or a
count of malformed rows. None of those carry customer data, and the failure
messages stay useful:

```
expected 404 to equal 200
expected 'undefined' to equal 'string'
expected 'missing' to equal 'present'
expected 3 to equal 0            <- three rows lost a required field
```

Keep to that rule when adding a request. Asserting on a value is how a BAN ends
up in a public log.

## CI

`.github/workflows/bruno-validate.yml` runs on pushes to `main` and on pull
requests targeting it. It needs no credentials and makes no network calls,
because all it does is check the collection against `api.py`.

`.github/workflows/bruno-live.yml` runs weekly against the real API. It cannot
log in, because MFA needs a human, so it refreshes a stored token instead.

Seed these repository secrets once:

| Secret | Value |
| --- | --- |
| `ENGIE_REFRESH_TOKEN` | a fresh `refreshToken` from a local step 13 |
| `ENGIE_BAN` | 12-digit business agreement number |
| `ENGIE_EAN` | a bare EAN from that agreement, no `_ID1` suffix |
| `TOKEN_WRITEBACK_PAT` | PAT with Secrets:write |

CI sets `ENGIE_BAN` and `ENGIE_EAN` explicitly rather than letting them seed
themselves, so a scheduled run always targets the same account.

The PAT is unavoidable: the built-in `GITHUB_TOKEN` cannot write repository
secrets, and the rotated token has to be stored for the next run.

### When the token gets stranded

The workflow writes the rotated token back in the step straight after the
refresh, and that step runs even when the refresh step failed. A run killed
between the two, by a cancel or a runner dying, still leaves the stored token
spent. The next run then fails with `Refresh returned no tokens`.

Recovery: run the login locally through step 13, copy `refreshToken`, and update
the `ENGIE_REFRESH_TOKEN` secret by hand.

### Why nothing is uploaded as an artifact

This repository is public, so artifacts and logs are readable by anyone. Request
URLs carry the BAN in the path and the balance endpoint returns amounts, so no
report is ever uploaded.

Run reports are also gitignored. A `--reporter-json` file from the token folder
contains a live access token and refresh token in clear text, and committing one
would publish working credentials.
