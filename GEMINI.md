# Gemini Project Mandates: Chorus AIDLC

This file provides foundational context and engineering standards for the Chorus AIDLC orchestrator.

## 🚀 System Architecture
- **Chorus AIDLC:** A state-machine-driven orchestrator for autonomous software development.
- **Core Components:**
  - `sdlc_orchestrator/`: Python backend and CLI logic.
  - `sdlc_orchestrator/ui/react-app/`: Vite/React dashboard (Chorus).
  - `sdlc_orchestrator/state_machine.py`: Central workflow logic.

## 🤖 Agent Standards
- **Supported Agents:** Claude Code, Kiro, Gemini, Codex.
- **Gemini Integration:** 
  - **Usage Tracking:** Strictly use `AgentRegistry` and standard CLI output. 
  - **Cleanliness:** Avoid re-introducing manual JSONL session log parsing or interactive terminal capture scripts (`debug_gemini_capture.py` etc.) as these were intentionally removed to keep the integration clean.
  - **Executor:** Configured via `spec.yaml` as `executor: gemini`.

## 🛠 Operational Details
- **Chorus Dashboard:** Runs on port `7842` by default (`sdlc ui`).
- **Webhook Service:** Runs on port `8765` (`sdlc webhook`) to handle real-time GitHub PR events and auto-approvals.
- **Local Development:** When modifying the core orchestrator, use `reinstall-cap.sh` to refresh the installation in the `ContentAutomationPlatform` environment.

## 🧪 Testing & Validation
- **Backend:** Use `pytest tests/` for verifying state machine and registry logic.
- **UI:** React app uses Vite; development server typically runs on port `3000` with backend proxy to `8765` or `7842`.
