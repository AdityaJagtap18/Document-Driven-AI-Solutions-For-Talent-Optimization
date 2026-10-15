"""Azure AI Document Intelligence client — extracts fields from an
uploaded certificate/marksheet. Deliberately thin: the actual cross-check
and confidence-flagging logic lives in document_verification.py (pure,
fully unit-tested, zero Azure dependency) so that real logic is testable
even though this file's SDK call is not.

*** UNTESTED. *** No Azure AI Document Intelligence resource is
provisioned, and unlike Cosmos DB (which has LocalVectorStore as a local
stand-in in semantic-library/), there's no meaningful offline equivalent
to substitute here — OCR against a real scanned/photographed document is
not something that can be faked locally. Validated only as far as this
environment allows: the real azure-ai-documentintelligence SDK is
installed and its method/model signatures were checked directly
(DocumentIntelligenceClient.begin_analyze_document(model_id, body, ...),
AnalyzeDocumentRequest(bytes_source=...), AnalyzeResult.documents[].fields
with .content/.confidence) — not against a live call or a real document.
"""

from secrets_loader import get_secret


def _require_config():
    endpoint = get_secret("DOCUMENT_INTELLIGENCE_ENDPOINT")
    key = get_secret("DOCUMENT_INTELLIGENCE_KEY")
    missing = [n for n, v in [("DOCUMENT_INTELLIGENCE_ENDPOINT", endpoint), ("DOCUMENT_INTELLIGENCE_KEY", key)] if not v]
    if missing:
        raise RuntimeError(
            f"Missing required secret(s): {', '.join(missing)}. "
            "Add them to your .env file (see .env.example) or Key Vault, once Azure AI Document "
            "Intelligence is provisioned — see "
            "docs/azure-provisioning/phase5-document-intelligence-whatsapp.md."
        )
    return endpoint, key


def analyze_document(file_bytes: bytes, model_id: str = "prebuilt-document") -> dict:
    """Runs Document Intelligence on the given file bytes, returns a
    simplified {field_name: {"value": str, "confidence": float}} dict —
    not the raw SDK AnalyzeResult object, so document_verification.py's
    pure functions stay decoupled from the SDK's object model.

    `model_id` defaults to the generic "prebuilt-document" model (general
    key-value extraction). A CUSTOM model trained on this project's actual
    certificate/marksheet formats would extract far more reliably — exam
    marksheets and degree certificates have fairly standardized per-
    institution layouts, worth training a custom model on once sample
    documents are available — but training one needs the user to supply
    sample documents via Document Intelligence Studio. Noted as a real
    follow-up, not attempted here (no sample documents exist in this
    repo to train against, and it can't be done blind)."""
    endpoint, key = _require_config()
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
    from azure.core.credentials import AzureKeyCredential

    client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))
    poller = client.begin_analyze_document(model_id, AnalyzeDocumentRequest(bytes_source=file_bytes))
    result = poller.result()

    fields = {}
    for document in result.documents or []:
        for name, field in (document.fields or {}).items():
            fields[name] = {"value": field.content, "confidence": field.confidence}
    return fields
