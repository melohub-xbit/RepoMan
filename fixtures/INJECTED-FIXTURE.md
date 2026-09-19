# Do not delete this fixture

`fixtures/injected/` is the security regression test for `repoman/probes/injection.py`
and the boundary discipline in `docs/05-security-model.md`. It is also a demo asset.

It carries three planted payloads:

| Where | Shape |
|---|---|
| `README.md` | an HTML comment addressed to an "AI evaluator", and a line telling a grader to ignore previous instructions |
| `src/main/java/app/service/OrderService.java` | an instruction-shaped code comment |
| a zero-width character | hidden inside `src/main/resources/application.yml` |

Two things must stay true, and `tests/test_injection.py` asserts both:

1. The scanner produces a `PROMPT_INJECTION` flag with a locator for each payload.
2. No payload string appears anywhere in the messages sent to the model.

The second is the one that matters. The scanner is defence in depth; the control is that
submitted content only ever reaches the model as `<untrusted>` tool results, and quarantined
artifacts are withheld from the tools entirely.
