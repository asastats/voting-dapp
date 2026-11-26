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

---

## dapp/tests/test_integration_testnet.py

2026-11-15. Deselected by default through the `testnet` marker, unlike the
Permission dApp's Testnet tests, which run with the suite and in CI. Every run
creates a session box and a ballot box that nothing can delete (there is no
`delete_ballot`), so each run locks 109,800 µALGO of the application account's
balance for good; in CI, with two Python versions, that would be about
0.22 ALGO per push out of the 5 ALGO the app was funded with.

The test writes the seat's Permission dApp box itself and deletes it at the
end, so the admin of the Voting dApp on Testnet has to be the creator of the
Permission dApp there too. It was written on a computer without the Testnet
keys and had not been run when committed.

Correction, 2026-11-15, later: the user ran it on Testnet against apps
775947805 and 750094865 and it passed.

---

## pytest.ini and dapp/pytest.ini

2026-11-15. Two files with the same settings, one per place the suite gets
run from. With the configuration only in `dapp/`, a run from the root found
none: no coverage, the `testnet` marker unknown and, worse, the Testnet test
not deselected, so it ran and left a session on Testnet. A run from `dapp/`
finds `dapp/pytest.ini` first, so the root file never applies there. One file
cannot serve both, because coverage looks for `.coveragerc` in the working
directory only and `--cov-config` is relative to it.


---

## dapp/tally.py

2026-11-23. The independent tally of the plan's §5 item 2 (workspace
`dao-voting/IMPLEMENTATION-PLAN.md`): the audit a governor can run without the admin's
keys or the ASA Stats engine, hence here and not in the engine.

What it can and cannot check. Everything a session's result rests on is public:
the session box, the ballot boxes, the snapshot file whose sha256 is sealed, and
the `Voted` events. `votes_of` at the moment of a vote is not: algod reads only the
current box, and the Permission dApp's box history would have to be replayed from
its `write_box` calls. The ballot box keeps the value the contract read
(`max_votes`), so the tally checks `weight == min(max_votes × 10⁶, eligible)` and
reports a seat whose `votes_of` differs today as a note, not a problem: votes are
meant to change between sessions.

The totals are summed twice, from the boxes and from each seat's last `Voted` event,
and both are compared with the session's own totals; the result is recomputed with
the contract's `finalize` rule (`contract_result`, strict `>` so a tie goes to the
lower option). The events are searched only between the session's start and end
rounds: the contract rejects a vote outside them, so a vote found outside would be
reported, but none can be found by that search; the check stays for events fed in
by other means (tests, another indexer query).

`permission_votes` reads the box itself instead of simulating `votes_of`: no
account or fee is needed, and the decoding (12 base64 characters, first 8 bytes) is
the contract's own, tested against the deployed Permission dApp on localnet.

Localnet: the integration test needs the localnet indexer. On 2026-11-23 it stayed
at round 0 because Conduit rejected the newer algod's blocks ("unknown protocol …
upgrade"); `algokit localnet reset --update` fixed it.

2026-11-23, later. Run read-only against Testnet app 775947805. Session 1 (the
November integration run; its snapshot file was not kept, so one rebuilt from the
ballot boxes was used) agreed in events, weights, totals and result; the only
problem was the rebuilt file's hash, as expected. Session 2 is unsealed with its
start round passed; the tally said "Voting runs from …", so a "Lapsed" note was
added for that case (the website's "lapsed" phase).

---

## dapp/contract.py — weight from the lowest amount held while voting (2026-11-26)

The user chose, for testnet session 4 and as the proposal to the DAO, that a
seat's eligible ASASTATS is the **lowest total it held from the start round to
the end round** (`dao-voting/PLAN-MINIMUM-WEIGHT.md` in the workspace), and that
the period is the voting window only, with no snapshot before it ("involving a
snapshot adds to the confusion"). That replaces the sealed snapshot of
2026-11-15; everything about `set_snapshot`, `seal_snapshot` and `_eligible`
above describes the earlier contract.

- **No amounts before voting.** They cannot be known or sealed before the first
  vote, so `vote` records only the option and `votes_of`, and creates the
  ballot box itself (the app account pays its MBR, 0.0293 ALGO). Any account
  whose `votes_of` is above zero can vote; a seat that held nothing ends with
  weight 0. Totals stay zero until `set_final`.
- **`set_final`** (admin, after the end round, until the seal) writes each
  voter's lowest amount and moves the totals by the difference, so corrections
  are O(1) per seat and `finalize` keeps no loop over ballots. `weighed_count`
  must equal `voters_count` before the seal, so no voter can be left out.
- **`seal_final`** stores the final file's sha256, not before `seal_round`,
  which `create_session` takes so that the review period is on chain (the
  user chose 2 days).
- **Given up, accepted by the user:** the plan's on-chain bound "the admin can
  only lower a weight" (it needed an R1 amount sealed before voting). Trust in
  the amounts rests on the published file, the review and `dapp.tally`.
  Totals during voting are not on chain; the website shows "so far" totals
  from a published engine file instead (the lowest amount so far can only go
  down, so they are an upper bound).
- **Abstain** (user, 2026-11-26, same update): `create_session` takes its
  index, 255 for none. `finalize` leaves it out of the winner choice and out of
  the total the winner must beat, and keeps it in the turnout and the quorum.
  At least two options besides Abstain.
- **`Voted`** keeps its signature, so its selector is unchanged, but the last
  field is now `max_votes`, not the weight (there is none at vote time).
- **Box layouts changed** (session 187 → 196 bytes, ballot 25 → 26), so the
  update cannot reuse app 775947805, whose session boxes 1–3 have the old
  layout: a new testnet app is deployed and its sessions start at 1.

### `FINAL_SEATS_PER_CALL` (dapp/configuration.py)

2026-11-26, measured with `simulate` on localnet: `set_final` costs 111 opcodes
plus 112 per seat, so 5 seats use 671 of the 700 budget and 7 failed ("dynamic
cost budget exceeded" in the first localnet run). 4 seats (559) leave room; 139
voters take 35 calls. Raising it needs `ensure_budget` (inner OpUp calls paid
by the admin) or a cheaper loop.

## dapp/network.py — holdings rounds (2026-11-26)

ASASTATS live on mainnet, so a testnet session reads mainnet holdings
(`HOLDINGS_NETWORK`). `holdings_rounds` maps the session's start and end rounds
to the last mainnet rounds whose blocks are not after those rounds' times (a
binary search over `block_info`); on mainnet they are the session's own
rounds. `sessions period` prints the engine command with them, `final` and
`seal-final` refuse a file for other rounds, and the tally checks the same.
