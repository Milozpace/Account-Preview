# Cloud namespace isolation implementation plan

> **For agentic workers:** Execute this authorized repair in the current session; the independent bootstrap redirection verification is delegated separately. Preserve installed identities, owner data, pairing and publication gates. No push, release upload or tag creation.

**Goal:** Allow preview and personal installations to use the same WebDAV account without addressing one another's cloud files.

**Architecture:** Resolve the exact build identifier once at compile time: `com.account.preview` uses `Account-Preview`, `com.account.probe` keeps `Account`. All ledger, probe and inbox clients share this mapping. Preview publication confirmations and recovery intents use new local filenames so old-directory evidence cannot authorize writes or falsely report the new directory as published; encrypted caches, pending content, identities, credentials and gates remain.

**Tech Stack:** Rust/Tauri, local HTTP WebDAV test servers, Python build/profile tooling, Windows PowerShell 5.1.

## Task 1: Establish failing routing regressions

- [x] Add `cloud_namespace` tests for the compiled ledger/probe directories and inbox prefix, based on the selected installation identifier.
- [x] Run `cargo test --lib --offline cloud_namespace`; the old preview build must fail because it points at `Account`.
- [x] Add a local WebDAV coexistence regression that seeds production paths with sentinel bytes, publishes/fetches preview snapshots, and checks that no request or byte change reaches those production paths.

## Task 2: Bind all channels and local publication evidence to the build identity

- [x] Add a shared identifier mapping with explicit rejection of unsupported identifiers; `build.rs` reads `tauri.conf.json` plus the Tauri build overlay and emits the resolved build constants.
- [x] Route ledger, probe, diagnostics and inbox through those constants; make the standalone publisher use the same compiled identifier as the GUI.
- [x] Accept old preview probe configuration read-only while always resolving its network path to the new preview scope; do not rewrite credentials or fall back to old cloud paths.
- [x] Scope preview confirmation/intent filenames and reject reuse of a pending revision older than the retained cache. Test old preview cache/pending migration and untouched legacy evidence.
- [x] Run the complete native test suite and both explicit profile build checks.

## Task 3: Document and test on this computer

- [x] Update `docs/手机与同步.md` and `docs/同步与隐私.md` with exact directory ownership, old-preview behavior and authentication failure handling; preserve ownership guards.
- [x] Incorporate the independently verified bootstrap result without changing an already working launcher.
- [x] Build the preview candidate and validate its components before a data-preserving local desktop upgrade/test. Record unsupported or unexecuted device/cloud checks honestly; do not write real cloud files for a local mock test.
- [x] Review the final diff, run relevant checks, proactively commit only task files, and report local results and remaining release authority.
