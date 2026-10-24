<!-- source: ed3c3d3b7590 -->
# Private eight-dimension preference learning

The default recommendation is still the balanced setting, prioritising quality and integrity. The learning module offers suggestions about one's own preferences; by default they are not adopted automatically, and they change nothing about discovery, public credit, dispute handling or payment rules.

## Data and module boundaries

`SelectionFactAdapter.preference_observations` reads the candidate snapshots this machine explicitly selected, verified delivery facts and task reviews, and projects them into eight 0–1 features plus a personal utility label. The learning algorithm depends only on that small port and local storage, and does not know the review ledger, the network or the payment module directly. Unknown, stale and conflicting features use the same neutral value 0.5 as ranking does.

Raw inputs, outputs, credentials and wallet addresses do not enter the learning data. Selection records, candidate snapshots, task reviews, learning suggestions and adoption records are stored separately in the existing encrypted local database. A suggestion stores only weights, support counts, validation error and a data digest; it is never published.

Only prior selection records that the person has explicitly authorised, that are PRODUCTION, HUMAN, not self-use and that have a verified delivery may be trained on. CONTROLLED engineering records, ASSISTANT reviews, withdrawn or unauthorised reviews are all excluded; only the latest review of a transaction is kept, and an older review cannot be re-enabled by withdrawing the latest record. Personal utility scores of 1–5 are mapped to 0–1, and the result is not called an objective accuracy rate or independent user certification.

## Learning and adoption rules

1. Read the selection snapshot of the current preference version, checking at most the most recent 5,000 selections; training uses only the last 90 days, at most 1,000 transactions and at most 100 per supplier.
2. At least 40 human reviews, 3 suppliers and 3 dates, and the utility must show variation; otherwise the original weights are kept.
3. Split by review time into the first 80% for training and the last 20% for validation, with at least 8 validation items. Both parts must contain at least two suppliers; influence is balanced per supplier.
4. Use a gradient fit constrained by the original weights, with the eight items summing to 1 and each item changing by at most 5 percentage points. The floors for quality and credit respect a lower value the person has explicitly set.
5. A READY suggestion is produced only if the validation error falls by at least 5% relative **and** at least 0.0001 absolute. When there is no improvement, no adoptable new weights are generated.
6. When the person clicks adopt, the data digest, withdrawal state and preference version are re-checked. If the data or the person's settings have changed, the old suggestion is refused. Only the eight weights are updated; the original budget, hard requirements and payment authorisation stay completely independent.

The console path "Discovery → Adjust preferences and requirements → Learn preferences from my reviews" can generate a suggestion; the adopt button appears only after validation. The current controlled calls do not meet the human-history requirement, so these records cannot be used to declare that preference calibration is complete.

## Protected local interfaces

- `GET /v1/selection/learning`: module state and the five most recent local suggestions; does not train.
- `POST /v1/selection/learning/fit {profile_id}`: reads the person's authorised history and produces a suggestion or an explicit insufficient-data state.
- `POST /v1/selection/learning/apply {candidate_id, expected_revision}`: re-checks the original data and version, then adopts the suggestion.

Utility calibration for task quality is still handled by the separate CalibrationService. It maps the objective checks of a specific task to the person's utility; this module learns the relative weights of the eight recommendation dimensions, and the two cannot substitute for each other.
