# Feature: TUI Interface with Animated Viki Face

**Created**: 2026-05-09
**Priority**: HIGH
**Status**: Planning

## Vision
A terminal UI for kiro-claw featuring the Viki dot-cloud particle face from `web_graphics` as the conversational entity. The face animates and responds while the user interacts with FRIDAY.

## Reference
- **Web prototype**: `/Users/yusuf/Development/web_graphics/` (Three.js + MediaPipe)
- **Live demo**: https://yusuf.kaka.co.za/ai_mirror/
- **Concept**: 2,500 particles forming a holographic face with expression tracking

## TUI Adaptation
The web version uses Three.js/WebGL. TUI needs:
- ASCII/Unicode particle rendering (braille dots, block chars, or similar)
- Simplified face mesh (fewer particles for terminal resolution)
- Animation states: idle breathing, talking (mouth movement), thinking, listening
- Responsive to terminal size

## Dual Interface
1. **TUI** — Terminal-based, runs locally or over SSH
2. **Web** — Adapt existing web_graphics as a standalone web UI for kiro-claw

## User Stories
- As a user, I see an animated face in my terminal that appears to be "alive"
- As a user, the face animates (talks) while FRIDAY is responding
- As a user, the face shows a thinking state while processing
- As a user, I can resize my terminal and the face adapts
- As a user, I can toggle between face-only and face+chat-log layout

## Technical Options (TUI)
- **Python**: `textual` or `rich` for TUI framework, custom particle renderer
- **Rust**: `ratatui` for high-performance terminal rendering
- **Node**: `blessed` or `ink` (React for terminals)

## Open Questions
- Which TUI framework? (Python keeps consistency with kiro-claw)
- How many particles can a terminal render at 15+ fps?
- Should the face track the user's webcam in TUI mode too, or just animate states?
- Split pane (face left, chat right) or face above chat?
