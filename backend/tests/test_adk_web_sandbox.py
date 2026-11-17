import asyncio
import base64
import io

import docx
import pytest
from google.adk.agents import SequentialAgent
from google.genai import types
from pypdf import PdfWriter

from construction_variation_agent import adk_web_sandbox as sandbox
from construction_variation_agent.shared_libraries.runtime import (
    _REGISTRY,
    register_runtime,
    runtime_from_state,
    unregister_runtime,
)
from construction_variation_agent.tracing import strip_user_attachments


def _docx_bytes() -> bytes:
    document = docx.Document()
    document.add_paragraph("Site instruction SI-042 directs additional drainage work.")
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _content(data: bytes, *, filename: str = "C:\\uploads\\instruction.docx") -> types.Content:
    return types.Content.model_validate(
        {
            "role": "user",
            "parts": [
                {"text": "Review this instruction."},
                {
                    "inlineData": {
                        "mimeType": (
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        ),
                        "data": base64.b64encode(data).decode("ascii"),
                        "displayName": filename,
                    }
                },
            ],
        }
    )


def test_adk_web_runtime_only_exposes_current_direct_uploads():
    runtime = sandbox.create_adk_web_runtime(_content(_docx_bytes()))
    state = sandbox._session_state(runtime)
    document_id = runtime.request.document_ids[0]

    register_runtime(runtime)
    try:
        assert runtime_from_state(state) is runtime
    finally:
        unregister_runtime(runtime.request.job_id)
    assert runtime.provider.get_document(document_id).pages[0].text
    metadata = runtime.provider.list_documents()[0]
    assert metadata["filename"] == "instruction.docx"
    assert metadata["origin"] == "ADK Web direct upload"
    assert runtime.provider.case_context()["alleged_change"] == "Review this instruction."

def test_adk_web_requires_supported_direct_uploads():
    with pytest.raises(sandbox.AdkWebUploadError, match="Attach one or more"):
        sandbox.create_adk_web_runtime(types.Content(role="user", parts=[types.Part(text="hello")]))

    content = types.Content(
        role="user",
        parts=[
            types.Part(
                inline_data=types.Blob(
                    mime_type="text/plain", data=b"not a supported document", display_name="notes.txt"
                )
            )
        ],
    )
    with pytest.raises(sandbox.AdkWebUploadError, match="not a supported"):
        sandbox.create_adk_web_runtime(content)


def test_adk_web_rejects_files_over_configured_limit(monkeypatch):
    monkeypatch.setattr(sandbox, "DEFAULT_MAX_FILE_BYTES", 1)
    with pytest.raises(sandbox.AdkWebUploadError, match="25 MB or smaller"):
        sandbox.create_adk_web_runtime(_content(_docx_bytes()))


def test_adk_web_rejects_scanned_pdf_instead_of_silently_skipping_pages():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = io.BytesIO()
    writer.write(output)
    content = types.Content(
        role="user",
        parts=[
            types.Part(
                inline_data=types.Blob(
                    mime_type="application/pdf",
                    data=output.getvalue(),
                    display_name="scanned.pdf",
                )
            )
        ],
    )

    with pytest.raises(sandbox.AdkWebUploadError, match="OCR the PDF first"):
        sandbox.create_adk_web_runtime(content)


def test_model_requests_keep_upload_text_but_not_raw_attachment_bytes():
    upload = types.Part(
        inline_data=types.Blob(mime_type="image/png", data=b"\x89PNG", display_name="plan.png")
    )
    remote_file = types.Part(file_data=types.FileData(file_uri="gs://not-used/plan.png"))
    prompt = types.Part(text="Review these documents.")
    tool_response = types.Part(function_response=types.FunctionResponse(name="lookup", response={}))

    class Request:
        contents = [
            types.Content(role="user", parts=[prompt, upload, remote_file]),
            types.Content(role="user", parts=[tool_response]),
        ]

    request = Request()
    strip_user_attachments(request)

    assert request.contents[0].parts == [prompt]
    assert request.contents[1].parts == [tool_response]


def test_adk_web_unregisters_runtime_even_when_pipeline_fails(monkeypatch):
    content = _content(_docx_bytes())
    context = type(
        "Context",
        (),
        {
            "user_content": content,
            "session": type("Session", (), {"state": {}})(),
            "agent_states": {},
            "invocation_id": "invocation-1",
        },
    )()
    seen_jobs = []

    async def fail_after_scope_is_authorized(self, ctx):
        assert self.name == "sandbox"
        runtime = runtime_from_state(ctx.session.state)
        seen_jobs.append(runtime.request.job_id)
        yield None
        raise RuntimeError("simulated pipeline failure")

    monkeypatch.setattr(SequentialAgent, "_run_async_impl", fail_after_scope_is_authorized)
    agent = sandbox.AdkWebSandboxAgent(name="sandbox", sub_agents=[])

    async def consume():
        async for _ in agent._run_async_impl(context):
            pass

    with pytest.raises(RuntimeError, match="simulated pipeline failure"):
        asyncio.run(consume())

    assert len(seen_jobs) == 1
    assert seen_jobs[0] not in _REGISTRY

