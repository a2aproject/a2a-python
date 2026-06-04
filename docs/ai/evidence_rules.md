# Evidence Rules

Rules for what counts as adequate evidence when making claims about
this codebase. These are graduated learnings — promoted from
`docs/ai/ai_learnings.md` (local journal) once a rule has earned a
permanent home.

When in doubt, the bar is: **a future agent reading your response
should be able to verify the claim from the citations alone, without
re-doing your investigation.**

## Claims about runtime behavior

Back any claim about how code behaves at runtime with a `file:line`
reference from a tool call in the same response, or with a runnable
demonstration.

The citation must support the specific claim. The *existence* of code
is not evidence of its *behavior*: a function being defined doesn't
mean it's called; an exception being raised doesn't mean it
propagates; a parameter being declared doesn't mean it's honored; a
config option existing doesn't mean it takes effect. Behavior claims
require control-flow evidence (call chain, test output, log) — not
just a definition site.
