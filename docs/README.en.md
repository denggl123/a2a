<!-- source: 9bd2e566e404 -->
# A2N documentation entry point

The current product version is whatever [VERSION](../VERSION) says. The project is in Beta: functional acceptance and calibration against real independent users are recorded separately, and a local test passing does not mean every network environment passes.

Read [current project status, the six layers and the next stage](PROJECT-STATUS.en.md) first. That is the assessment entry point for this version; the actual nodes are checked by the [read-only project inspection](../scripts/README.md). Older stage plans such as `DEVELOPMENT-ROADMAP.md` are no longer marked as the latest progress.

## Usage and business rules

- [Installation and overview](../README.en.md): the full SDK product and the single-node business system.
- [Server installation](SERVER-DEPLOYMENT.en.md), [Docker node](../docker/README.md).
- [Connecting an existing agent](INTEGRATIONS.en.md): what A2A, JSON HTTP and MCP HTTP actually support.
- [Automatic settlement rules](AUTOMATIC-SETTLEMENT.en.md): provider list, consumer order, quota and restoring the original order.
- [Payment coordination](PAYMENT-COORDINATION.en.md), [points rules](POINTS.en.md), [x402](X402.md).
- [Discovery rules](COORDINATION-RULES.md), [coordination API](COORDINATION-API.en.md), [filtering after discovery](DISCOVERY-FILTERS.en.md).
- [Two-sided feedback](FEEDBACK-RULES.en.md), [selection module](SELECTION-MODULE.en.md), [task quality and calibration](TASK-QUALITY-CALIBRATION.md).
- [Private eight-dimension preference learning](PRIVATE-PREFERENCE-LEARNING.md): suggestions come only from authorised human reviews, and the version and any retraction are checked before use.

## Architecture and development

- [Current five-package architecture](SDK-ARCHITECTURE.en.md), [single-node migration](NODE-UNIFICATION.md).
- [Releasing and acceptance](RELEASING.en.md), [contributing](../CONTRIBUTING.en.md), [security](../SECURITY.en.md), [changelog](../CHANGELOG.en.md).
- [Vision implementation](VISION-IMPLEMENTATION.md), [completion work](VISION-COMPLETION-WORK.md), [workflow and maintenance](WORKFLOW-MEDIA-MAINTENANCE.md). These contain earlier delivery records; what this version can do is decided by the code and by this version's acceptance results.

## Historical material

`docs/design/`, `REVIEW-ROUND*.md`, `ARCHITECTURE-REVIEW.md`, `OPTIMIZATION-PLAN.md`, `DEVELOPMENT-ROADMAP.md`, `PRODUCT*.md`, `SDK-RUNTIME.md` and others keep the original designs and stage assessments. Old platform interfaces, commercialisation discussion, historical completion rates and the open items of that time are not current operating rules. Ideas such as AP2, ANP and ERC-8004 in the design drafts do not mean those standards have been implemented.

When sources conflict, check in this order: current code and acceptance results → the current rules linked from this page → vision design → historical material. New behaviour must update the current rules and the corresponding meaningful acceptance.
