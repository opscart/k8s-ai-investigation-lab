"""Human-readable report; provider output remains untrusted text."""


def markdown(result: dict) -> str:
    diagnosis = result["diagnosis"]
    sections = [
        f"# Investigation: {result['case_id']}",
        f"Mode: {result['mode']} | Cause: {diagnosis['cause_status']}",
    ]
    for key in ("assessment", "likely_cause"):
        claim = diagnosis[key]
        sections += [
            f"## {key.replace('_', ' ').title()}",
            claim["text"],
            "Evidence: " + ", ".join(claim["evidence_ids"]),
        ]
    sections += ["## Proposed correction", diagnosis["proposed_correction"] or "Not established"]
    for key in ("alternatives", "verification", "missing_evidence"):
        sections += [
            f"## {key.replace('_', ' ').title()}",
            "\n".join("- " + value for value in diagnosis[key]) or "None reported",
        ]
    sections += [
        "## Evidence sources",
        "\n".join(f"- {key}: {source}" for key, source in result["evidence_sources"].items()),
    ]
    sections += [
        "## Run statistics",
        str(result["stats"]),
        "AI-generated diagnosis. No application or cluster change was executed.",
    ]
    return "\n\n".join(sections) + "\n"
