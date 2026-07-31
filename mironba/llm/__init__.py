"""The model-agnostic LLM layer.

Everything outside this package sees one interface: ``LLMClient.complete``.
Provider differences — how a server is told to constrain decoding to a schema,
what it calls its sampling knobs, where it publishes its model list — live in
``llm/providers/`` and nowhere else. The charter makes that a hard boundary,
and ``test_no_provider_specific_code_outside_providers`` enforces it.
"""
