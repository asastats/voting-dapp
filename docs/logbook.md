# Voting dApp logbook

Why the code in this repository is the way it is: the measurements, the
decisions taken and the ones reversed.

One section per module, under its path from the repository root. Inside it,
`## Module` holds notes about the file as a whole and a subsection per
function, method or constant carries the rest.

The code carries what a reader needs to *use* it correctly. This carries what
they would need to *change* it safely.

Entries are dated where the date is known, and append-only: a note that turned
out to be wrong gets a correction beneath it rather than an edit.

---

## dapp/ (the package)

2026-11-15. The modules import each other relatively (`from .helpers import`)
and everything runs from the repository's root (`python -m dapp.sessions`,
`python -m pytest`).

Correction, 2026-11-15, later: the tests run from `dapp/`, as the Permission
dApp's do. `pytest.ini` and `.coveragerc` sat in the root, and a run from
`dapp/` found the first but not the second (coverage looks only in the working
directory), so the test modules were counted as source. Both files are in
`dapp/` now and `pythonpath = ..` keeps `dapp` importable as a package. The
scripts and the compile still run from the root. The Permission dApp does it the other way, with bare names
and `dapp/` as the import root, and the website puts that folder on `sys.path`.
Two submodules both exposing bare `helpers`, `network` and `contract` would
shadow each other there; with relative imports the website can import
`daovoting.dapp.*` and nothing has to be added to `sys.path`.

Correction, 2026-11-15, later: the submodule folder is `website/votingdapp`,
not `website/daovoting`, so the import is `votingdapp.dapp.*`.

---

## dapp/contract.py

### Module

2026-11-15. Built from `IMPLEMENTATION-PLAN.md` §3 and §4 (snapshot variant
D1-d, a working assumption until the DAO confirms it). Compiled with puyapy
5.9.0; the approval program is 1,182 bytes, one page.

The contract reads no asset balance and takes no asset reference. That is the
whole reason a transfer after the snapshot cannot change a weight, and why the
plan's "transfer after the snapshot" test was not written: there is nothing in
the program for it to exercise.

### `Session`

The box is a fixed 187 bytes with eight `totals` slots whatever the number of
options. A size that depends on `options_count` would save at most 48 bytes
(19,200 µALGO of minimum balance) per session and cost the readers a variable
layout.

### `create_session`

- `threshold_num * 2 >= threshold_den`: with a threshold below a half, two
  options could both exceed it and `finalize` would need a tie rule. At a half
  or more the option that clears it is unique.
- `threshold_den <= 1000`: keeps `total × den` far inside 64 bits without wide
  arithmetic. Totals are micro-ASASTATS, so they stay below about 10¹⁵.
- No payment is taken for the box. The application account is pre-funded
  (plan D6) and an underfunded account fails the call on its own.

Open, for the DAO or the user: whether the options stay 2–8 (the plan) or become
fixed yes / no / abstain (the 2026-11-14 review), and whether abstain counts
toward the quorum but not the turnout (D4). Either changes `finalize` only.

### `set_snapshot`

An amount of 0 deletes the seat's box: the plan gives a seat with no eligible
ASASTATS no box, and a correction during the review period must be able to
bring a seat down to that.

### `seal_snapshot`

Refuses before the snapshot round, which the plan does not ask for: a snapshot
of a block that does not exist yet cannot be a measurement. The 7-day review
period is not enforced on-chain.

### `vote`

`votes_of` is read again on every vote, including a changed one, and the value
is stored in the ballot. So a governor-seats update during a session changes
nothing for ballots already cast (D5) but does apply to a seat that votes again.

### `_eligible`

The one place that knows where the eligible amount comes from. If the DAO
chooses another mechanism than the snapshot, this is the subroutine to replace.

### `_max_votes`

Excluded from coverage: `arc4.abi_call` raises `NotImplementedError` under
algorand-python-testing 1.1.0, so the unit tests mock this method. It is
exercised compiled, in `tests/test_integration.py`, against the real
Permission dApp program.

### Left out on purpose

- `delete_ballot` (plan §4.2). The website is to show each seat's ballot from
  the boxes; deleting them would move that to an indexer to reclaim about
  4 ALGO per session. It can be added by an update.
- `set_admin` (plan §7, phase 2). `admin` is in global state so that it can be
  added without a migration.

---

## dapp/tests/artifacts/

2026-11-15. A copy of the Permission dApp's compiled program and ARC-56 file,
taken at permission-dapp `b585e7f` (the commit that adds `votes_of`). The
integration tests deploy it so that the inner call runs against the real
program, and so that this repository's CI needs no other checkout. Refresh the
copy whenever the Permission dApp is recompiled.

---

## dapp/requirements.txt

2026-11-15. `puyapy` is pinned to an exact version because CI diffs a fresh
compile against the committed TEAL, and a different compiler version emits
different TEAL for the same source (seen the same day on the Permission dApp,
5.8.1 against 5.9.0).

2026-11-15, later. `algorand-python` is capped below 3.6.0 for the same reason:
it is the range puyapy 5.9.0 supports. Left open, a fresh install took 4.0.0
and the compiler warned that typing errors could follow. Raise the cap and the
puyapy pin together, then recompile and reread the TEAL diff.
