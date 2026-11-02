# Android scanner lifecycle override

The two scanner/overlay Kotlin files originate from `tauri-plugin-barcode-scanner`
2.5.1, under its [MIT license](LICENSE-MIT.md). Their original copyright/SPDX notices
are retained. The Rust/JS plugin, permissions, manifest and dependencies still
come from the locked upstream crate; only its Android main source set is replaced
by this directory in the app's preserved Gradle configuration.

Local changes resolve the saved invocation before clearing it on cancellation,
invalidate camera initialization and ML Kit callbacks across scan sessions, and
remove the preview even when cancellation occurs before camera initialization.
`GraphicOverlay.kt` is unchanged upstream source. `ScannerSession.kt` supplies
the tested session boundary. Unit tests run in the scanner Gradle module.

When upgrading the upstream crate, compare these files and remove the override
when the upstream lifecycle provides the same guarantees. Do not edit the Cargo
registry or turn off optimization to work around application bugs.
