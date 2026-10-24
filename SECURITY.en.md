<!-- source: bfd30ef42b48 -->
# Security reporting and release boundaries

This is an early Beta. Test payments are verified against a local private EVM; there are no mainnet funds and no independent external security audit yet.

## Reporting a problem

Once the public repository has GitHub private vulnerability reporting enabled, use Security → Report a vulnerability in the repository. Until then, report through a private contact the repository maintainers have published; this project has no preset email address or third-party reporting service.

Please include the affected version, a minimal reproduction, the scope of impact and redacted logs. For anything involving private keys, wallets, user data or an exploitable vulnerability, do not put the full details in a public issue first. Ordinary non-sensitive defects can go in an issue.

## Support scope

The current pre-release is fixed first. There is no promise of long-term maintenance for older versions. Response and resolution times depend on what the maintainers can actually do; no security service is promised that does not exist.

## Publisher and installer

The project's Ed25519 publisher signature and the operating-system distribution certificate are verified separately. A candidate build may be unsigned; a public trusted upgrade must use the persistent publisher identity the owner has explicitly endorsed.

The source digest, the program's SHA-256, the version, the signature and the isolated runtime self-check must all agree. A temporary CI signing identity must never be presented as an existing trusted publisher, and publisher keys and node data are never uploaded.
