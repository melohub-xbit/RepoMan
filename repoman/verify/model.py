"""The provider port. The entire local-vs-cloud difference, resolved once from the environment.

Track 1 (Ollama on the evaluator's laptop, no AWS account, "no byte of a submission left this
machine") and Track 2 (Bedrock, S3, App Runner, a whole cohort) are the same codebase. They
differ here and in `store/`, and nowhere else. Never write `if is_cloud` in business logic.
"""

from __future__ import annotations

import os

DEFAULT_OLLAMA_MODEL = "qwen3:8b"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"  # free tier, tool calling; a test bench, not a track
DEFAULT_REGION = "us-east-1"


def make_model():
    """Whichever provider the environment asks for. Nothing downstream may ask which one it got."""
    host = os.environ.get("REPOMAN_OLLAMA_HOST")
    if host:
        from strands.models.ollama import OllamaModel

        return OllamaModel(host=host, model_id=os.environ.get("REPOMAN_MODEL_ID", DEFAULT_OLLAMA_MODEL),
                           temperature=0)

    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        # Test bench only: Groq's OpenAI-compatible endpoint, free and fast, for exercising the prompts
        # when neither a local model nor Bedrock is at hand. Submissions leave the machine on this path.
        return _GroqModel(client_args={"api_key": groq_key, "base_url": "https://api.groq.com/openai/v1"},
                          model_id=os.environ.get("REPOMAN_MODEL_ID", DEFAULT_GROQ_MODEL),
                          params={"temperature": 0})

    from strands.models import BedrockModel

    model_id = os.environ.get("REPOMAN_MODEL_ID")
    if not model_id:
        raise RuntimeError(
            "Set REPOMAN_MODEL_ID to the Bedrock inference profile ID copied from the console "
            "(Model catalog → Claude Sonnet → cross-region inference profile), or set "
            "REPOMAN_OLLAMA_HOST to run locally. Do not construct the profile ID from memory."
        )
    # Prompt caching stays off until per-submission cost is measured (docs/04). When it goes on,
    # it goes on here: the system prompt and tool list are identical across requirements, so the
    # cached prefix is exactly the stable part.
    kwargs = {"model_id": model_id, "region_name": os.environ.get("AWS_REGION", DEFAULT_REGION),
              "temperature": 0}
    if os.environ.get("REPOMAN_CACHE") == "1":
        # strands 1.56: cache_prompt= is deprecated (warns); cache_config carries the system-prompt cache point
        from strands.models.bedrock import CacheConfig

        kwargs |= {"cache_config": CacheConfig(strategy="auto"), "cache_tools": "default"}
    return BedrockModel(**kwargs)


def _inline_refs(schema: dict) -> dict:
    """Replace every {"$ref": "#/$defs/X"} with the definition itself. Groq's tool validator has no $ref support;
    Bedrock's does, so this is bench-only."""
    defs: dict = {}

    def collect(node):  # $defs can sit at any level, not only the root
        if isinstance(node, dict):
            defs.update(node.get("$defs", {}))
            for v in node.values():
                collect(v)
        elif isinstance(node, list):
            for x in node:
                collect(x)

    collect(schema)

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node and node["$ref"].startswith("#/$defs/"):
                return walk(defs[node["$ref"].rsplit("/", 1)[1]])
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


try:
    from strands.models.openai import OpenAIModel as _OpenAIModel
except ImportError:  # the [openai] extra is only needed for the bench
    _OpenAIModel = object


class _GroqModel(_OpenAIModel):
    def format_request(self, messages, tool_specs=None, system_prompt=None, tool_choice=None, **kw):
        flat = [{**t, "inputSchema": {"json": _inline_refs(t["inputSchema"]["json"])}} for t in tool_specs or []]
        return super().format_request(messages, flat, system_prompt, tool_choice, **kw)


def model_id() -> str:
    """What to record in the RunManifest, so a disputed finding can be reproduced."""
    if os.environ.get("REPOMAN_OLLAMA_HOST"):
        return f"ollama:{os.environ.get('REPOMAN_MODEL_ID', DEFAULT_OLLAMA_MODEL)}"
    if os.environ.get("GROQ_API_KEY"):
        return f"groq:{os.environ.get('REPOMAN_MODEL_ID', DEFAULT_GROQ_MODEL)}"
    return f"bedrock:{os.environ.get('REPOMAN_MODEL_ID', 'unset')}"
