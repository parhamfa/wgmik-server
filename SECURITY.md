# Security Policy

## Supported versions

Security fixes are applied to the current `main` branch and included in the
next published container image. Older commits, image tags, and release
artifacts are not maintained as separate support lines.

| Version | Supported |
| --- | --- |
| `main` and the latest published container image | Yes |
| Older commits, images, and releases | No |

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Instead, use
[GitHub private vulnerability reporting](https://github.com/parhamfa/wgmik-server/security/advisories/new).

Include, where possible:

- The affected commit, release, or container image tag.
- The deployment environment and relevant configuration, with secrets removed.
- Reproduction steps or a minimal proof of concept.
- The expected impact and any suggested mitigation.

Reports will be reviewed as maintainer availability permits. Please allow time
to investigate and coordinate a fix before publicly disclosing the issue.

For vulnerabilities in MikroTik RouterOS itself rather than in wgmik-server,
report them directly to MikroTik through its official security contact.
