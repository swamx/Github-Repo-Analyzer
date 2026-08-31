"""Specialized review agents (doc section 7)."""
import json
import logging
from typing import Optional

from app.services.llm_service import LLMService
from app.services.code_review.schemas import Finding, FindingCategory, Severity

logger = logging.getLogger(__name__)

_llm_service = LLMService()


def _parse_findings_json(raw_json: str) -> list[dict]:
    """Parse JSON response from agent, handling fence wrapping."""
    if not raw_json:
        return []
    try:
        # Strip markdown fences if present
        cleaned = raw_json.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```", 1)[1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        data = json.loads(cleaned)
        return data if isinstance(data, list) else [data]
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse agent JSON: %s", e)
        return []


async def run_correctness_agent(
    file_path: str,
    patch: str,
    related_content: dict[str, str],
) -> list[Finding]:
    """Run correctness agent (doc section 7).

    Looks for: null handling, incorrect branching, race conditions,
    transaction bugs, API contract violations.
    """
    context_str = "\n".join(
        f"=== {f} ===\n{content[:500]}"
        for f, content in related_content.items()
    )

    system_prompt = """You are an expert code reviewer focused on correctness.
Analyze the PR diff for potential logic errors, including:
- Null pointer dereferences or improper null handling
- Incorrect branching logic or missing edge cases
- Race conditions or concurrency issues
- Incorrect variable scope or lifecycle
- API contract violations
- Type mismatches or casting errors

Output ONLY a JSON array of findings, with no other text.
Each finding must have: file, line (int), severity (low/medium/high/critical),
confidence (0.0-1.0), evidence (list of strings), suggested_fix (string).
Return [] if no issues found."""

    user_prompt = f"""Review this code change for correctness issues.

File: {file_path}

Diff:
```
{patch[:2000]}
```

Related code context:
{context_str}

Return findings as JSON array only, no explanation."""

    try:
        response = await _llm_service.chat_message_async(
            system_message=system_prompt,
            user_message=user_prompt,
            temperature=0.2,
        )
        raw_findings = _parse_findings_json(response)

        findings = []
        for raw in raw_findings:
            try:
                finding = Finding(
                    file=raw.get("file", file_path),
                    line=raw.get("line", 1),
                    category=FindingCategory.CORRECTNESS,
                    severity=Severity(raw.get("severity", "medium")),
                    confidence=float(raw.get("confidence", 0.5)),
                    evidence=raw.get("evidence", []),
                    suggested_fix=raw.get("suggested_fix", ""),
                )
                findings.append(finding)
            except (ValueError, KeyError) as e:
                logger.warning("Skipped malformed finding: %s", e)

        logger.info("Correctness agent found %d findings in %s", len(findings), file_path)
        return findings

    except Exception as e:
        logger.error("Correctness agent failed: %s", e)
        return []


async def run_security_agent(
    file_path: str,
    patch: str,
    related_content: dict[str, str],
) -> list[Finding]:
    """Run security agent (doc section 7).

    Looks for: authentication, authorization, SQL injection,
    secret exposure, unsafe deserialization, dependency vulnerabilities.
    """
    context_str = "\n".join(
        f"=== {f} ===\n{content[:500]}"
        for f, content in related_content.items()
    )

    system_prompt = """You are a security expert code reviewer.
Analyze the PR diff for security issues, including:
- Authentication or authorization bypass
- SQL injection or database vulnerabilities
- Secret/credential exposure (API keys, tokens, passwords)
- Unsafe deserialization or code execution
- Cross-site scripting (XSS) or injection attacks
- Insecure cryptography or weak hash functions
- Broken access control

Output ONLY a JSON array of findings, with no other text.
Each finding must have: file, line (int), severity (low/medium/high/critical),
confidence (0.0-1.0), evidence (list of strings), suggested_fix (string).
Return [] if no issues found."""

    user_prompt = f"""Review this code change for security vulnerabilities.

File: {file_path}

Diff:
```
{patch[:2000]}
```

Related code context:
{context_str}

Return findings as JSON array only, no explanation."""

    try:
        response = await _llm_service.chat_message_async(
            system_message=system_prompt,
            user_message=user_prompt,
            temperature=0.2,
        )
        raw_findings = _parse_findings_json(response)

        findings = []
        for raw in raw_findings:
            try:
                finding = Finding(
                    file=raw.get("file", file_path),
                    line=raw.get("line", 1),
                    category=FindingCategory.SECURITY,
                    severity=Severity(raw.get("severity", "medium")),
                    confidence=float(raw.get("confidence", 0.5)),
                    evidence=raw.get("evidence", []),
                    suggested_fix=raw.get("suggested_fix", ""),
                )
                findings.append(finding)
            except (ValueError, KeyError) as e:
                logger.warning("Skipped malformed finding: %s", e)

        logger.info("Security agent found %d findings in %s", len(findings), file_path)
        return findings

    except Exception as e:
        logger.error("Security agent failed: %s", e)
        return []


async def run_test_agent(
    file_path: str,
    patch: str,
    related_content: dict[str, str],
) -> list[Finding]:
    """Run testing agent (doc section 7).

    Looks for: untested code, missing edge cases, inadequate coverage.
    """
    context_str = "\n".join(
        f"=== {f} ===\n{content[:500]}"
        for f, content in related_content.items()
    )

    system_prompt = """You are a testing expert code reviewer.
Analyze the PR diff for test/coverage issues, including:
- Production code changes without corresponding test updates
- Missing tests for new functions or edge cases
- Inadequate test coverage for risky code paths
- Tests that should be added or improved
- Untested error handling or exceptional cases

Output ONLY a JSON array of findings, with no other text.
Each finding must have: file, line (int), severity (low/medium/high/critical),
confidence (0.0-1.0), evidence (list of strings), suggested_fix (string).
Return [] if no issues found."""

    user_prompt = f"""Review this code change for testing gaps.

File: {file_path}

Diff:
```
{patch[:2000]}
```

Related test files and code context:
{context_str}

Return findings as JSON array only, no explanation."""

    try:
        response = await _llm_service.chat_message_async(
            system_message=system_prompt,
            user_message=user_prompt,
            temperature=0.2,
        )
        raw_findings = _parse_findings_json(response)

        findings = []
        for raw in raw_findings:
            try:
                finding = Finding(
                    file=raw.get("file", file_path),
                    line=raw.get("line", 1),
                    category=FindingCategory.TESTING,
                    severity=Severity(raw.get("severity", "medium")),
                    confidence=float(raw.get("confidence", 0.5)),
                    evidence=raw.get("evidence", []),
                    suggested_fix=raw.get("suggested_fix", ""),
                )
                findings.append(finding)
            except (ValueError, KeyError) as e:
                logger.warning("Skipped malformed finding: %s", e)

        logger.info("Test agent found %d findings in %s", len(findings), file_path)
        return findings

    except Exception as e:
        logger.error("Test agent failed: %s", e)
        return []
