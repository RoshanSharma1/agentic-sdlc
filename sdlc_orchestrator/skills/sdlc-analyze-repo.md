# sdlc-analyze-repo

You are an experienced software architect onboarding a repository for autonomous SDLC management.

## Project: {{PROJECT_NAME}}

{{MEMORY}}

## Your task

Deeply analyze this repository and produce `memory/project.md` with the following sections:

### Stack
- Language, runtime version, package manager
- Frameworks and key libraries (with versions from lock files)
- Database, cache, message broker (if any)

### Architecture
- High-level component map
- Folder structure and what each top-level directory owns
- Key design patterns in use (MVC, event-driven, microservices, etc.)

### Domain Concepts
- Core business entities and their relationships
- Key terminology used in the codebase

### Testing Setup
- Test framework(s) in use
- How to run tests (exact command)
- Current coverage (if measurable)
- Any known gaps

### Tech Debt & Known Issues
- TODO/FIXME/HACK comments in the codebase (summarise, don't list all)
- Open GitHub issues (fetch with `gh issue list --limit 20`)
- Obvious quality gaps

### Deployment
- How the app is deployed (Docker, serverless, VMs, etc.)
- Environment variables required (from .env.example or README)
- CI/CD pipeline (from .github/workflows/)

### Conventions
- Naming conventions (files, functions, variables)
- Commit message format (from git log)
- Any style guide or linter config

---

Write everything to `.sdlc/memory/project.md`.  
Create `.sdlc/memory/` directory if it doesn't exist.  
When done, output exactly: PHASE_COMPLETE: analyze-repo
