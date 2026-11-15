"""Module with Voting dApp constants."""

# None until the application is deployed to the network
VOTING_APP_ID = None
VOTING_APP_ID_TESTNET = None

PERMISSION_APP_ID = 3647489997
PERMISSION_APP_ID_TESTNET = 750094865

ASASTATS_ASSET_ID = 393537671

# a transaction holds eight references and one of them is the session box
SNAPSHOT_SEATS_PER_CALL = 7

NOT_VOTED = 255

STATUS_NAMES = {0: "snapshot open", 1: "sealed", 2: "passed", 3: "failed"}

# offset and size of every Session field; mirrors `contract.Session`
SESSION_LAYOUT = (
    ("snapshot_round", 8),
    ("snapshot_hash", 32),
    ("start_round", 8),
    ("end_round", 8),
    ("options_count", 1),
    ("quorum", 8),
    ("threshold_num", 8),
    ("threshold_den", 8),
    ("proposal_hash", 32),
    ("status", 1),
    ("winner", 1),
    ("voters_count", 8),
    ("totals", 64),
)
# mirrors `contract.Ballot`
BALLOT_LAYOUT = (("eligible", 8), ("option", 1), ("max_votes", 8), ("weight", 8))
