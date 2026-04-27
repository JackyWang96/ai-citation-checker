from app.rules.apa7 import ALL_RULES
from app.services.docx_parser import ReferenceParagraph
from app.models.schemas import CitationIssue


def validate_reference_paragraph(para: ReferenceParagraph) -> list[CitationIssue]:
    issues = []
    for rule in ALL_RULES:
        issue = rule(para)
        if issue:
            issues.append(issue)
    return issues
