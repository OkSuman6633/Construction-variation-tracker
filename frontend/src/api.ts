export type Organisation = { tenant_id: string; name: string; role: string };
export type LocalUser = {
  user_id: string;
  name: string;
  email: string;
  must_change_password: boolean;
};
export type LocalSession = {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: LocalUser;
};
export type TeamMember = {
  user_id: string;
  name?: string | null;
  email?: string | null;
  role: string;
};
export type Project = {
  project_id: string;
  tenant_id: string;
  name: string;
  reference?: string | null;
  client?: string | null;
  site_address?: string | null;
  contract: { form: string; description?: string | null; jurisdiction?: string | null };
  created_at: string;
};
export type Evidence = {
  document_id: string;
  filename: string;
  mime_type: string;
  size_bytes?: number;
  parser_status: string;
  page_count?: number | null;
  category?: string | null;
  recorded_event_date?: string | null;
  duplicate_of?: string | null;
};
export type CaseSummary = {
  case_id: string;
  project_id: string;
  title: string;
  alleged_change: string;
  stage: string;
  revision: number;
  created_at: string;
  evidence: Evidence[];
  active_job_id?: string | null;
  events?: TimelineEvent[];
  candidates?: Candidate[];
  contradictions?: Contradiction[];
  missing_evidence?: MissingEvidence[];
  reviewer_questions?: ReviewerQuestion[];
  report_refs?: string[];
};
export type TimelineEvent = {
  event_id: string;
  event_type: string;
  event_date?: string | null;
  description: string;
  parties: string[];
  references: string[];
  amount?: number | null;
  currency?: string | null;
  evidence: EvidenceReference[];
  visual_region?: string | null;
  basis: string;
  confidence: number;
  uncertainties: string[];
  review_status: string;
};
export type EvidenceReference = {
  document_id?: string | null;
  page_ref?: string | null;
  quoted_span?: string | null;
  visual_region?: string | null;
  user_authored?: boolean;
};
export type Candidate = {
  candidate_id: string;
  title: string;
  alleged_original_scope?: string | null;
  proposed_scope_delta?: string | null;
  related_instruction_refs: string[];
  quantity_text?: string | null;
  amount?: number | null;
  currency?: string | null;
  evidence: EvidenceReference[];
  contradictions: string[];
  missing_evidence: string[];
  confidence: number;
  review_status: string;
  entitlement_asserted: false;
};
export type Contradiction = {
  contradiction_id: string;
  kind: string;
  description: string;
  evidence: EvidenceReference[];
  detected_by: string;
};
export type MissingEvidence = {
  missing_id: string;
  description: string;
  expected_document_kind?: string | null;
  related_event_ids: string[];
};
export type ReviewerQuestion = {
  question_id: string;
  question: string;
  related_event_ids: string[];
  related_candidate_ids: string[];
};
export type Job = {
  job_id: string;
  case_id: string;
  status: string;
  stage?: string | null;
  attempt: number;
  failure_code?: string | null;
  failure_message?: string | null;
  created_at: string;
  updated_at: string;
};
export type DocumentSummary = {
  document_id: string;
  filename: string;
  category: string;
  date: string;
  summary: string;
};
export type DocumentProgressionStep = {
  step: number;
  document_id: string;
  filename: string;
  category: string;
  date: string;
  change_summary: string;
};
export type VariationPosition = {
  variation_id: string;
  variation_title: string;
  project_name: string;
  alleged_change: string;
  parties: string[];
  instruction_refs: string[];
  evidence_status: string;
  original_position: {
    work_description?: string | null;
    quantity?: number | null;
    quantity_unit?: string | null;
    specification?: string | null;
    quotation_ref?: string | null;
    quoted_amount?: number | null;
    currency?: string | null;
  };
  changed_position: {
    work_description?: string | null;
    added_quantity?: number | null;
    revised_total_quantity?: number | null;
    quantity_unit?: string | null;
    specification_change?: string | null;
    instruction_ref?: string | null;
    instruction_date?: string | null;
    revised_quotation_ref?: string | null;
    revised_quoted_amount?: number | null;
    currency?: string | null;
  };
  quantity_progress?: {
    item_description: string;
    original_quantity?: number | null;
    additional_requested?: number | null;
    total_proposed?: number | null;
    recorded_installed?: number | null;
    remaining_not_evidenced?: number | null;
    completion_evidence_status: string;
    calculation_note?: string | null;
  } | null;
  commercial_position?: {
    original_quoted_amount?: number | null;
    revised_quoted_amount?: number | null;
    calculated_difference?: number | null;
    difference_label: string;
    currency?: string | null;
    commercial_acceptance_status: string;
    payment_status: string;
  } | null;
  unresolved_items: Array<{
    category: string;
    description: string;
    reviewer_action: string;
  }>;
};

export type CompleteChronologyEvent = {
  event_id: string;
  event_date?: string | null;
  event_time?: string | null;
  event_type: string;
  description: string;
  parties: string[];
  document_id: string;
  document_name: string;
  page_ref: string;
  quoted_span?: string | null;
  evidence_classification: string;
  verification_status: string;
};

export type ReviewItem = {
  review_id: string;
  related_variation_id?: string | null;
  category: string;
  issue_title: string;
  description: string;
  why_requires_verification: string;
  suggested_question: string;
  suggested_action: string;
  priority: string;
  status: string;
};

export type DocumentRegisterItem = {
  document_id: string;
  filename: string;
  category: string;
  document_date?: string | null;
  upload_date?: string | null;
  parsing_status: string;
  page_count?: number | null;
  events_extracted_count: number;
  source_availability: string;
};

export type Timeline = {
  case_id: string;
  revision: number;
  stage: string;
  active_job_id?: string | null;
  events: TimelineEvent[];
  candidates: Candidate[];
  relationships: Array<{
    relationship_id: string;
    document_ids: string[];
    relation: string;
    rationale: string;
    confidence: number;
  }>;
  contradictions: Contradiction[];
  missing_evidence: MissingEvidence[];
  reviewer_questions: ReviewerQuestion[];
  plain_english_summary?: {
    text: string;
    event_ids: string[];
    candidate_ids: string[];
  } | null;
  document_register?: DocumentRegisterItem[];
  variation_positions?: VariationPosition[];
  complete_chronology?: CompleteChronologyEvent[];
  review_checklist?: ReviewItem[];
  commercial_analysis?: Array<{
    original_quoted_amount?: number | null;
    revised_quoted_amount?: number | null;
    calculated_difference?: number | null;
    difference_label: string;
    currency?: string | null;
  }>;
  work_progress_analysis?: Array<{
    item_description: string;
    original_quantity?: number | null;
    additional_requested?: number | null;
    total_proposed?: number | null;
    recorded_installed?: number | null;
    remaining_not_evidenced?: number | null;
    completion_evidence_status: string;
  }>;
  document_summaries?: DocumentSummary[];
  document_progression?: DocumentProgressionStep[];
  evidence: Evidence[];
  notice: string;
};

export type ApiContext = { token: string; tenantId: string };
type ApiError = { code?: string; message?: string; correlation_id?: string; retryable?: boolean };

const apiBase = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

function requestHeaders(context?: ApiContext, idempotency = false, token?: string): Headers {
  const headers = new Headers();
  const bearer = context?.token ?? token;
  if (bearer) {
    headers.set("Authorization", `Bearer ${bearer}`);
  }
  if (context) {
    headers.set("X-Tenant-ID", context.tenantId);
  }
  if (idempotency) headers.set("Idempotency-Key", crypto.randomUUID());
  return headers;
}

async function request<T>(
  path: string,
  options: {
    context?: ApiContext;
    token?: string;
    method?: string;
    body?: unknown;
    idempotency?: boolean;
  } = {},
): Promise<T> {
  const headers = requestHeaders(options.context, options.idempotency, options.token);
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  const response = await fetch(`${apiBase}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  if (!response.ok) {
    let detail: ApiError = {};
    try {
      detail = (await response.json()) as ApiError;
    } catch {
      // Some proxies return a plain-text error response.
    }
    const message = detail.message ?? `Request failed (${response.status})`;
    const info = [detail.code, detail.correlation_id && `ID ${detail.correlation_id}`]
      .filter(Boolean)
      .join(" · ");
    throw new Error(info ? `${message} (${info})` : message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function requestPdf(
  path: string,
  context: ApiContext,
  method: "GET" | "POST",
): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(`${apiBase}${path}`, {
    method,
    headers: requestHeaders(context),
  });
  if (!response.ok) {
    let detail: ApiError = {};
    try {
      detail = (await response.json()) as ApiError;
    } catch {
      // Some proxies return a plain-text error response.
    }
    const message = detail.message ?? `Request failed (${response.status})`;
    const info = [detail.code, detail.correlation_id && `ID ${detail.correlation_id}`]
      .filter(Boolean)
      .join(" · ");
    throw new Error(info ? `${message} (${info})` : message);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const filename = /filename="([^"]+)"/i.exec(disposition)?.[1] ?? "fieldnote-report.pdf";
  return { blob: await response.blob(), filename };
}

export const api = {
  health: () => request<{ status: string }>("/healthz"),
  authMode: () => request<{ local_password_enabled: boolean }>("/v1/auth/mode"),
  localSignup: (body: { name: string; email: string; password: string }) =>
    request<LocalSession>("/v1/auth/signup", { method: "POST", body }),
  localLogin: (body: { email: string; password: string }) =>
    request<LocalSession>("/v1/auth/login", { method: "POST", body }),
  changePassword: (token: string, body: { current_password: string; new_password: string }) =>
    request<{ status: string }>("/v1/auth/change-password", { token, method: "POST", body }),
  me: (token: string) =>
    request<{ user_id: string; email: string | null }>("/v1/me", { token }),
  organisations: (token: string) =>
    request<{ organisations: Organisation[] }>("/v1/organisations", { token }),
  createOrganisation: (token: string, name: string) =>
    request<Organisation>("/v1/organisations", {
      token,
      method: "POST",
      body: { name },
      idempotency: true,
    }),
  listMembers: (context: ApiContext) =>
    request<{ members: TeamMember[] }>(
      `/v1/organisations/${encodeURIComponent(context.tenantId)}/members`,
      { context },
    ),
  createMember: (context: ApiContext, body: { email: string; role: string }) =>
    request<{ member: TeamMember; temporary_password: string | null }>(
      `/v1/organisations/${encodeURIComponent(context.tenantId)}/members`,
      { context, method: "POST", body },
    ),
  listProjects: (context: ApiContext) =>
    request<{ items: Project[] }>("/v1/projects", { context }),
  getProject: (context: ApiContext, id: string) =>
    request<Project>(`/v1/projects/${encodeURIComponent(id)}`, { context }),
  createProject: (context: ApiContext, body: Record<string, unknown>) =>
    request<Project>("/v1/projects", { context, method: "POST", body, idempotency: true }),
  listCases: (context: ApiContext, projectId?: string) => {
    const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
    return request<{ items: CaseSummary[] }>(`/v1/cases${query}`, { context });
  },
  getCase: (context: ApiContext, id: string) =>
    request<CaseSummary>(`/v1/cases/${encodeURIComponent(id)}`, { context }),
  createCase: (context: ApiContext, body: Record<string, unknown>) =>
    request<{ case_id: string; revision: number }>("/v1/cases", {
      context,
      method: "POST",
      body,
      idempotency: true,
    }),
  uploadEvidence: async (
    context: ApiContext,
    caseId: string,
    file: File,
    options: { recordedDate?: string; dateUnknown: boolean; origin?: string },
  ) => {
    const mimeTypes: Record<string, string> = {
      pdf: "application/pdf",
      docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      eml: "message/rfc822",
      jpg: "image/jpeg",
      jpeg: "image/jpeg",
      png: "image/png",
    };
    const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
    const mimeType = file.type || mimeTypes[extension];
    if (!mimeType) throw new Error("Unsupported file type. Choose PDF, DOCX, XLSX, EML, JPG or PNG.");
    const upload = await request<{
      document_id: string;
      upload_url: string;
      headers: Record<string, string>;
    }>(`/v1/cases/${encodeURIComponent(caseId)}/upload-url`, {
      context,
      method: "POST",
      body: { filename: file.name, mime_type: mimeType, size_bytes: file.size },
      idempotency: true,
    });

    const hasSignedUrl = /^https?:\/\//i.test(upload.upload_url);
    const url = hasSignedUrl ? upload.upload_url : `${apiBase}${upload.upload_url}`;
    const uploadHeaders = new Headers(upload.headers);
    if (!hasSignedUrl) {
      uploadHeaders.set("Authorization", `Bearer ${context.token}`);
      uploadHeaders.set("X-Tenant-ID", context.tenantId);
    }
    const uploaded = await fetch(url, { method: "PUT", headers: uploadHeaders, body: file });
    if (!uploaded.ok) throw new Error(`File upload failed (${uploaded.status})`);

    const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    const sha256 = Array.from(new Uint8Array(digest), (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("");
    const body: Record<string, unknown> = {
      document_id: upload.document_id,
      filename: file.name,
      mime_type: mimeType,
      sha256,
      event_date_unknown: options.dateUnknown,
    };
    if (options.recordedDate) body.recorded_event_date = options.recordedDate;
    if (options.origin) body.origin = options.origin;
    return request<Evidence>(`/v1/cases/${encodeURIComponent(caseId)}/evidence`, {
      context,
      method: "POST",
      body,
      idempotency: true,
    });
  },
  analyse: (context: ApiContext, caseId: string) =>
    request<{ job_id: string; status: string; duplicate_active_job: boolean }>(
      `/v1/cases/${encodeURIComponent(caseId)}/analyse`,
      { context, method: "POST", idempotency: true },
    ),
  getJob: (context: ApiContext, jobId: string) =>
    request<Job>(`/v1/jobs/${encodeURIComponent(jobId)}`, { context }),
  getTimeline: (context: ApiContext, caseId: string) =>
    request<Timeline>(`/v1/cases/${encodeURIComponent(caseId)}/timeline`, { context }),
  createReport: (context: ApiContext, caseId: string, format: "pdf" | "docx" = "pdf") =>
    requestPdf(`/v1/cases/${encodeURIComponent(caseId)}/report?format=${format}`, context, "POST"),
  getReport: (context: ApiContext, caseId: string, format: "pdf" | "docx" = "pdf") =>
    requestPdf(`/v1/cases/${encodeURIComponent(caseId)}/report?format=${format}`, context, "GET"),
};
