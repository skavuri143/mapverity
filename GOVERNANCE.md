# Governance

## Roles

- **Users** use MapVerity and are encouraged to share feedback.
- **Contributors** submit issues, documentation, code, examples or reviews.
- **Maintainers** review and merge changes, cut releases and steward the roadmap. The current
  maintainer is listed in [MAINTAINERS.md](MAINTAINERS.md).

## Decisions

Day-to-day decisions are made in pull requests by lazy consensus: a change merges when a
maintainer approves it and no maintainer objects within a reasonable time. Changes to the spec
format, the public Python API, gate semantics or the evaluation methodology need an Architecture
Decision Record in `docs/adr/` and at least 7 days for comment.

## Becoming a maintainer

Contributors who have made sustained, high-quality contributions (code, reviews, docs or support)
may be invited by the existing maintainers. The goal is at least two maintainers from different
organizations before 1.0.

## Releases

Semantic versioning. Releases are tagged `vX.Y.Z`, built by CI and published to PyPI through
trusted publishing; every release has a changelog entry.

## Code of conduct

All participants follow the [Code of Conduct](CODE_OF_CONDUCT.md).
