<!-- source: 19f49685d217 -->
# Filtering and display after discovery

## 1. Layers and data flow

The coordination layer discovers nodes level by level by capability, fetches signed offering cards and merges channels. The console keeps the candidates already obtained in a `pool`; the display layer reads the cards to build facets and computes `found` from the current conditions for the card, list and comparison views.

Filtering never modifies the original cards, the candidate set, the discovery session, the neighbour table or the channels. It does not ask for quotes, read a wallet, query the points directory, prepare an order, execute an agent or settle anything. Clearing a condition restores the original candidates immediately, with no rediscovery.

Filtering covers only the candidates already obtained and makes no claim to cover the whole network. A small count, or zero, after filtering does not mean the search is exhausted. You can still use the coordination layer's "keep discovering", and new candidates are filtered by the same display conditions.

## 2. Conditions

| Condition | Facts read | How it is shown |
|---|---|---|
| Keyword | name, description, capability name and id, skill tags, origin | case-insensitive substring match |
| Industry category | declared skills plus the local category table | an unlisted capability falls under "other" |
| Capability | `skills[].id` | the original id is kept |
| Payment method | `accepts`, `x-a2n.accepts`, `x-a2n.payments.methods`, the agent's points policy | native coin, x402, points and other explicitly declared methods |
| Points service mode | `x-a2n.points.enabled` and `modes` | EARN, DEBT and PAY are filtered separately |
| Sample stage | `x-a2n.trial.cap=10` and `ended` | in the first-ten-free-samples stage / first ten samples finished / not declared |
| Price declaration | the existing price parsing of `x-a2n.price_book` | explicitly zero / priced / unpriced |
| Capability tags | `skills[].tags` | filtered by the original tags |
| Declared currency | price-book currency and payment-declaration currency | each currency separately; points are not converted into money |
| Payment network | the payment method's `network`, or the node's points network for points | this is a payment declaration, not the discovery network or a route type |
| Provider node | the candidate's `key.provider_did`, or the node DID of the card projection/source | supplies from one node are grouped; the interface still shows the full identity hint |
| Agent version | `version` | exact version match, with no inferred compatibility range |
| Input format | `defaultInputModes` and the skills' `inputModes` | only declared formats are listed |
| Output format | `defaultOutputModes` and the skills' `outputModes` | only declared formats are listed |
| Origin | the card's existing origin declaration | a missing declaration is listed separately |
| Discovery verification marker | the candidate record's `verification=CARD_VERIFIED` | kept apart from a card's self-attestation of identity |
| Discovery source | the source recorded by the console | level-by-level coordination discovery, public node, peer to peer, and so on |
| Identity declaration | the card's `projection.attested` or `origin.self_attested` | self-attestation is neither discovery verification nor proof of an independent person |

Except for keyword and identity declaration, a group allows multiple choices and any one of them suffices; different groups must all be satisfied. For example, choosing "points or native coin" together with "supports debt" still requires a match on the debt mode.

Six commonly used groups are expanded by default and the rest sit under "more conditions". They stay expanded while extra conditions are chosen. Each chosen condition has its own remove button; "all" clears the group, and "clear filters" clears every condition while keeping the sort order and the view.

## 3. Points and free samples

`points_method=a2n-points/1` only says the node has the points submodule installed; it does not mean a particular agent accepts points.

A current points supply must declare `x-a2n.points.enabled=true` and carry at least one valid service mode:

- **EARN**: free service is allowed, and after a real service completes the provider receives points it issued itself.
- **DEBT**: the consumer is allowed to owe.
- **PAY**: existing points may be spent on the service.

An old card that explicitly accepts points in `accepts` is still recognised; a new policy that explicitly disables points wins and cannot be overridden by the old declaration. A supply declaring only EARN or DEBT is not matched by the "allows payment with existing points" condition.

The first ten samples are a separate business stage that skips all settlement. An agent that supports points is still free in that initial stage and produces no points settlement. Supporting points in the filter does not mean this particular call must settle in points.

Balance, quota, the issuer's acceptance table, exchange ratios and eligibility for this trade are decided by quoting and the payment coordination layer. The display layer does not stand in for a quote and cannot say "this will definitely go through now". Points from different issuers are neither priced together nor ranked across issuers by amount.

## 4. Counts, unknowns and evidence

Totals are split into "candidates in hand this time" and "current matches". A facet's number applies the other chosen groups and ignores its own group for the moment, so you can see what else switching that group might reach. An unknown field stays "not declared" and is never filled in as free, verified, samples finished, or supporting some payment method.

With no results, all conditions, the zero-hit options and the original candidates are kept, and a way to clear them is offered. The three common payment and points modes remain viewable even at zero hits, so "I cannot see the condition" is not mistaken for the product not supporting it.

Cards, the list, the comparison and the detail view all show payment and points declarations. The comparison view keeps the historical evidence display from the cards; when a card has no reputation data it says so plainly rather than inventing a reputation threshold filter or a network-wide leaderboard. When public experience and the local reputation view are wired in later, conditions based on records already obtained can be added while keeping the source, version and calculation visible.

The later eight-dimension scoring, personal preferences, quality normalisation, dispute credit, data ownership and refresh mechanism are in [the personalised agent selection design](PERSONALIZED-AGENT-SELECTION.en.md). That design is not implemented yet; this page's boundary still holds — discover first, filter second, and filtering makes no remote operation.

## 5. Implementation and verification

- `web/discovery.js`: declaration reading, normalisation and matching, with no I/O.
- `web/runtime.html`: facet counts, multi-select, clearing, sorting and the three views.
- The local management interface serves `/console/discovery.js`; no public discovery request or payment interface is extended.
- `scripts/runtime_logic_check.js`: declaration recognition, combined filtering, unknown fields, zero hits and isolation from any request.
- `scripts/check_discovery_filters.js`: browser interaction checks against real listed offering cards, with support for the real coordination-discovery mode of an installed node; it never executes or pays an agent.
