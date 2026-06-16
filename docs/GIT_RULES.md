## Git Workflow & Repository Hygiene

The Git history is part of the evaluation. The commit timeline should demonstrate incremental development, clear reasoning, and consistent progress.

### Commit Frequency

* Commit after every completed feature or meaningful milestone.
* Prefer small, reviewable commits over large "dump" commits.
* If a task takes more than 60–90 minutes, create intermediate commits.
* Maintain a visible progression from setup → backend → frontend → realtime → AI → tests → documentation.

Examples:

```text
[setup] initialize backend structure
[backend] add user and channel models
[backend] implement JWT authentication
[backend] add websocket connection manager
[frontend] create login page
[frontend] implement channel list
[ai] add thread summarization endpoint
[test] add auth integration tests
[docs] update README deployment instructions
```

### Before Every Commit

Verify:

* Project builds successfully.
* New tests pass.
* No secrets are staged.
* No temporary debug code remains.
* No commented-out dead code is committed.

Review:

```bash
git status
git diff --staged
```

before committing.

### Commit Quality Rules

Each commit should:

* Represent one logical unit of work.
* Leave the repository in a runnable state whenever possible.
* Include only related changes.
* Avoid mixing refactors with feature implementation unless required.

Bad:

```text
[backend] auth, websocket, ai, frontend fixes
```

Good:

```text
[backend] implement auth endpoints
[backend] add websocket authentication
[ai] create summarization service
```

### Pull Request Simulation

Even though this is a solo project, work as if another engineer will review every commit.

For significant architectural decisions:

* Explain the rationale in the commit message body.
* Prefer documenting tradeoffs over documenting implementation details.



### Branching & Merging Strategy

The goal is to maintain a clean, understandable history while still working safely.

#### Branch Rules

* `main` must always remain in a runnable state.
* Create a feature branch for every meaningful task or milestone.
* Keep branches focused on a single concern.
* Prefer short-lived branches (hours, not days).
* **Do NOT delete merged branches** — keep them so the evaluator can see the full development history.

Examples:

```text
feature/auth
feature/channels
feature/websocket-messaging
feature/presence
feature/ai-summary
feature/frontend-chat
test/auth-integration
docs/readme
```

#### Branch Workflow

For each feature:

```bash
git checkout main
git pull
git checkout -b feature/auth
```

Work, commit incrementally, then merge when:

* Feature is complete.
* Code builds successfully.
* Relevant tests pass.
* No known regressions exist.

#### Merging Rules

* Prefer merge commits for major milestones to preserve development history.
* Avoid force-pushing shared branches.
* Avoid rebasing branches that have already been pushed and reviewed.
* Resolve conflicts immediately rather than accumulating them.

Example:

```bash
git checkout main
git merge --no-ff feature/auth
```

The resulting history should clearly show when major capabilities were completed.

#### Submission Repository Strategy

The evaluator should primarily view a clean `main` branch.

Before submission:

* Ensure all completed feature branches are merged into `main`.
* Keep all feature branches visible on remote — do not delete them.
* Ensure `main` contains the final working version.
* Ensure commit history reflects the order in which the system was built.
* Tag the final submission commit if desired:

```bash
git tag submission-v1
```

The repository should demonstrate professional development practices, not just a final working codebase.


```
```
