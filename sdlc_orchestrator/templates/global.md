# Global Engineering Rules

These rules apply to every project managed by this SDLC orchestrator.

## Code Quality
- Follow modular architecture — each module has one responsibility
- Keep functions small and focused (under 50 lines as a guide)
- Prefer composition over inheritance
- No magic numbers — use named constants

## Testing
- Write unit tests for all features before marking a task done
- Do not skip, comment out, or weaken assertions
- Integration tests for all critical paths
- Test edge cases: empty input, max input, invalid input

## Security
- No secrets or credentials in code — always use environment variables
- Validate all inputs at system boundaries (user input, external APIs)
- Never expose internal stack traces to end users

## Commits
- Format: `type(scope): description`
- Types: feat, fix, refactor, test, docs, chore
- One logical change per commit

## Documentation
- Update docs after every feature implementation
- Every public function/module needs a docstring or JSDoc comment

## Definition of Done
A task is done when:
1. Feature is implemented as specified
2. Unit tests pass
3. Linting/type-check passes
4. Docs are updated
5. Committed with a descriptive message
