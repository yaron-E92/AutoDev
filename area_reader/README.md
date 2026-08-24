# Area reader

Area-reader functionality is an internal library used by the canonical `autodev` workflow. It is not a standalone command-line application.

The maintained modules separate repository discovery, bounded context collection, routing, prompt construction, verification, recommendations, and settings. Canonical OpenCode roles import those responsibility modules directly.

Run AutoDev through the installed `autodev` command rather than invoking modules in this package directly.
