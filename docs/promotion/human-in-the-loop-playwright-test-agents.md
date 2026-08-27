# Beyond the Chat Transcript: Human-in-the-Loop Playwright Test Generation

*What we learned while turning requirements, plans, browser validation, repair,
and execution evidence into one stateful workflow.*

[Playwright Test Agents](https://playwright.dev/docs/test-agents) provide a
planner, generator, and healer for producing Playwright tests. Generating a test
file is useful, but it is only one step in a testing lifecycle. A team still
needs to review intent, validate the test in a real browser, understand failures,
repair the test, and keep enough evidence to trust the result later.

While building [Waterfall AI Test](https://github.com/jiongfeng/waterfall-ai-test-platform),
an independent open-source project, I explored what happens when that lifecycle
becomes a stateful, human-in-the-loop workflow instead of a long agent chat.

**Watch the five-minute English walkthrough:**
[Playwright Test Agents vs. Waterfall AI Test](https://youtu.be/0xX1qA6q12c)

> Disclosure: I maintain Waterfall AI Test. It is an independent Apache-2.0
> project built with Playwright; it is not affiliated with, sponsored by, or
> endorsed by Microsoft or the Playwright project.

## The missing layer is workflow state

The planner-generator-healer loop already has meaningful artifacts: a Markdown
plan, executable Playwright tests, and repaired test code. The operational
problem begins when a team needs to answer questions that are wider than any
single agent call:

- Which requirement and test module produced this plan?
- Which version of the test was actually verified?
- Did a retry pass unchanged, or did an agent or person edit the code?
- Can a tester take over one stage without restarting the entire workflow?
- Which report, trace, screenshot, or video belongs to this exact run?

A chat transcript can contain those answers, but it does not make them easy to
query or govern. The alternative is to model the testing lifecycle as explicit
state with durable transitions.

The workflow we use is intentionally artifact-first:

```text
Requirement
  -> test modules
  -> reviewable Markdown plans
  -> Playwright tests
  -> real-browser verification
  -> test suites
  -> evidence-rich runs
```

Each transition leaves something a person can inspect:

| Stage | Output | Review | Gate |
| --- | --- | --- | --- |
| Requirement | Source and context | Scope and target | Accepted |
| Modules | Grouped coverage | Gaps and risk | Approved |
| Plan | Markdown scenarios | Steps and assertions | Accepted |
| Generation | Playwright files | Code and setup | Saved |
| Verification | Status, errors, artifacts | Failure cause | Passes |
| Suite | Verified versions | Coverage | Ready |
| Run | ID, report, evidence | Release decision | Reviewed |

This model is not a replacement for Playwright's agents. It is an operational
surface around their artifacts and transitions.

## One-click automation should still expose checkpoints

Full automation and human control are sometimes presented as opposing choices.
They do not need to be.

An automated path can move from a requirement to an executed suite, while each
stage remains visible and interruptible. A tester should be able to pause,
change scope or strategy, edit a plan or test, regenerate one artifact, and
resume from the current stage. Taking over a failed test should not require
discarding the accepted requirement, reviewed plan, and previous evidence.

That leads to a useful design rule:

> One-click automation is a route through the state machine, not permission to
> hide the state machine.

The trade-off is more orchestration work. The system must preserve stage state,
task ownership, artifact versions, and resumability. In return, automation does
not become an opaque all-or-nothing operation.

## Verification is a gate, not a badge

Generated code should not enter a maintained test suite merely because the
generation step completed. It must be executed against the intended target with
the real Playwright runner and browser.

In Waterfall AI Test, verification is a separate transition. A generated test
can be pending, running, failed, or verified. Only a verified version is
eligible for suite selection. If someone edits the test after verification, the
new version has to pass again.

This distinction matters because generation-time confidence and runtime truth
are different things. A syntactically plausible locator can be wrong. An
assertion can check the wrong business outcome. Test data or authentication can
be incomplete. A page can have changed since exploration. The browser run—not
the agent's explanation—is the acceptance signal.

Verification also needs context. A useful failure record includes the stage,
test version, target configuration, error, logs, and any Playwright artifacts
captured for that attempt. Without that association, a red result becomes
another fragment someone must reconstruct from chat and directories.

## Repair is a visible state transition

The goal is not to pretend agent-generated tests never fail. The goal is to make
failure and recovery inspectable:

```text
generated -> verifying -> failed -> repairing -> re-verifying -> verified
                              \-> manual edit -----------/
                              \-> retry unchanged -------/
```

When a test fails, there are at least three legitimate next actions:

1. **Retry unchanged** when the failure may be environmental or transient.
2. **Agent-assisted repair** when the test no longer matches the application or
   contains a generation defect.
3. **Manual takeover** when a person needs to change code, data, fixtures, or
   the underlying plan.

Every path returns to execution. A repair is a proposal until the changed test
passes in the browser.

There is an important guardrail here: healing must not silently convert a real
product regression into a passing test. The failed evidence and code diff need
to remain reviewable, and the tester must be able to reject the repair. The
right outcome may be to keep the test failing and fix the product.

## Version the assets, then link versions to runs

Plans and tests are source assets, so ordinary Git concepts are useful: commit,
diff, history, and restore. Local Git history makes it possible to compare what
the agent or a person changed without inventing a proprietary diff format.

Git alone is not the complete audit model, though. The application still needs
to connect domain state to source revisions:

```text
requirement -> plan revision -> test revision -> verification attempt
                                            \-> suite membership -> run ID
```

That relationship answers a practical question: *Which exact plan and test
version produced this result?* It also lets a reviewer distinguish a passing
retry from a passing repair.

Version history is traceability, not safety. A dangerous generated script is
still dangerous when it is committed. Code review and constrained execution
remain necessary.

## Treat evidence as part of the result

A single green or red status is rarely enough for a team decision. For each run,
we associate a Run ID and summary with per-test status, logs, and a Playwright
report. Depending on the Playwright configuration, that result can also link to
video, screenshots, and traces.

The evidence model has two jobs:

- **Debugging:** give a maintainer enough context to reproduce and repair a
  failure.
- **Trust:** show what executed, which version executed, and why the workflow
  marked it verified or failed.

Artifacts need lifecycle rules of their own. Reports, videos, screenshots, and
traces can contain cookies, page data, credentials, or personal information.
Retention, access control, redaction, and deletion should be design inputs—not
an afterthought added when storage becomes expensive.

## Generated tests are a code-execution boundary

An agent-generated Playwright test is code. So are setup scripts and fixtures.
Running them can read credentials available to the process, make network
requests, and modify the target system.

Waterfall AI Test is therefore not presented as a hostile-code sandbox. The
current public beta is intended for trusted, single-tenant Linux/amd64
deployments against authorized, isolated, recoverable non-production targets.
Generated code should be reviewed, credentials should be least-privilege and
revocable, and model providers should be approved by the deploying
organization.

This boundary is documented in the project's
[security model](https://github.com/jiongfeng/waterfall-ai-test-platform/blob/main/docs/security-model.md)
and [support matrix](https://github.com/jiongfeng/waterfall-ai-test-platform/blob/main/docs/support-matrix.md).
The beta scope is deliberately narrower than the UI workflow: no public
anonymous use, hostile multi-tenancy, production targets, or high-availability
claim.

## Four lessons from building the workflow

1. **The browser run is the source of truth.** Agent reasoning can guide the
   process, but only execution can verify an executable test.
2. **Human control works best at stage boundaries.** Review and takeover are
   much easier when plans, tests, failures, and repairs are explicit objects.
3. **Repair needs provenance.** Keep the original failure, changed revision,
   actor, and re-verification result together.
4. **Auditability starts in the data model.** A polished report cannot recover
   relationships that were never stored between requirements, revisions,
   attempts, and runs.

The cost is additional state, storage, and UI complexity. For small personal
projects, a terminal and a few files may already be the right interface. The
workbench pattern becomes more useful when multiple requirements, tests, runs,
and reviewers need a shared operational picture.

## Try the implementation and challenge the model

Waterfall AI Test is an Apache-2.0 public beta. The repository contains the
[source and quickstart](https://github.com/jiongfeng/waterfall-ai-test-platform),
and the [English walkthrough](https://youtu.be/0xX1qA6q12c) compares the
conversation-oriented flow with the visual workflow described here.

I would especially value feedback from Playwright users on two questions:

1. Are these the right human checkpoints for an agent-driven testing workflow?
2. What evidence do you need before you trust an AI-generated test as a
   maintainable team asset?
