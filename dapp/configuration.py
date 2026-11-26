"""Module with Voting dApp constants."""

# None until the application is deployed to the network
VOTING_APP_ID = None
VOTING_APP_ID_TESTNET = 775947805

PERMISSION_APP_ID = 3647489997
PERMISSION_APP_ID_TESTNET = 750094865

ASASTATS_ASSET_ID = 393537671

# `set_final` costs 111 + 112 per seat of the 700 opcode budget (docs/logbook.md)
FINAL_SEATS_PER_CALL = 4

NOT_VOTED = 255

# micro-units per vote; mirrors `contract.MICRO`
MICRO = 1_000_000

# ASASTATS live on mainnet, so a testnet session reads mainnet holdings
HOLDINGS_NETWORK = "mainnet"

STATUS_OPEN, STATUS_SEALED, STATUS_PASSED, STATUS_FAILED = 0, 1, 2, 3
STATUS_NAMES = {0: "open", 1: "sealed", 2: "passed", 3: "failed"}

# offset and size of every Session field; mirrors `contract.Session`
SESSION_LAYOUT = (
    ("start_round", 8),
    ("end_round", 8),
    ("seal_round", 8),
    ("options_count", 1),
    ("abstain", 1),
    ("quorum", 8),
    ("threshold_num", 8),
    ("threshold_den", 8),
    ("proposal_hash", 32),
    ("final_hash", 32),
    ("status", 1),
    ("winner", 1),
    ("voters_count", 8),
    ("weighed_count", 8),
    ("totals", 64),
)
# mirrors `contract.Ballot`; `weighed` is an ARC-4 bool (0x80 when true)
BALLOT_LAYOUT = (
    ("option", 1),
    ("max_votes", 8),
    ("eligible", 8),
    ("weight", 8),
    ("weighed", 1),
)
