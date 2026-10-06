# Third-Party Notices

This repository contains modified versions of the following open-source projects. Each subdirectory keeps its original license file and copyright notices.

| Directory | Upstream | License | Base revision |
|---|---|---|---|
| `AionUi/` | https://github.com/iOfficeAI/AionUi | Apache License 2.0 (`AionUi/LICENSE`) | `6744099` |
| `AionCore/` | https://github.com/iOfficeAI/AionCore | Apache License 2.0 (`AionCore/LICENSE`) | `47e66d0` |
| `Tingji/` | https://github.com/baigong-ai/Tingji | MIT, as declared in the upstream README (`Tingji/README.md`) | `4002c75` |

## Modifications

**AionUi**
- Sidebar entry and embedded workspace for the sales meeting assistant; meeting-reference cards in chat input and history titles.
- Product branding (“销售智能工作台”, S mark, app icons), sales-oriented example prompts, login page text.
- CSRF and session-refresh adjustments for the self-hosted web host.
- Large marketing GIF/MP4 files under `AionUi/resources/` were removed from this copy to keep the repository small; they are only referenced by upstream README files.

**AionCore**
- New `aionui-meeting` crate: authenticated, allow-listed gateway to the local meeting service (no browser credentials forwarded).
- Route and service wiring, security adjustments for the web host.

**Tingji**
- Text-only entry point (`app/text_main.py`, `app/text_storage.py`) without audio/ASR dependencies.
- New sales meeting pipeline in `app/text_processing/`: serial clean-then-extract workflow, source anchoring, per-segment and per-item validation, budgeted model adapter, versions/drafts, archive, reference snapshots.
- Text-mode pages (`static/text-*.js`, `static/text-mode.css`): sales board card, priority to-dos, meeting tiles, source highlighting, exports.

All modifications are © 2026 Moro-w and are provided under the same license as the file they modify.
