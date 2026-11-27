export interface AdminOverviewResponse {
  total_registered_users: number;
  active_users: number;
  inactive_users: number;
  new_users_today: number;
  new_users_this_week: number;
  new_users_this_month: number;
  total_orders: number;
  total_logical_ai_requests: number;
  total_provider_attempts: number;
  company_provider_attempts: number;
  personal_provider_attempts: number;
  guest_ai_requests: number;
  successful_attempts: number;
  failed_attempts: number;
  total_input_tokens: number | null;
  total_output_tokens: number | null;
  total_tokens: number | null;
  personal_to_company_fallbacks: number;
  fallback_recovery_rate?: number | null;
}

export interface AdminAnalyticsUsagePoint {
  period: string;
  logical_requests: number;
  provider_attempts: number;
  company_attempts: number;
  personal_attempts: number;
  guest_requests: number;
  successful_attempts: number;
  failed_attempts: number;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  fallbacks: number;
}

export interface AdminAnalyticsUsageResponse {
  group_by: 'daily' | 'hourly' | string;
  start_date: string | null;
  end_date: string | null;
  data: AdminAnalyticsUsagePoint[];
}

export interface AdminModelMetric {
  model_name: string;
  provider_attempts: number;
  logical_requests: number;
  successful_attempts: number;
  failed_attempts: number;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  avg_latency_ms: number | null;
}

export interface AdminModelsResponse {
  start_date: string | null;
  end_date: string | null;
  models: AdminModelMetric[];
}

export interface AdminSourceMetric {
  source: string;
  provider_attempts: number;
  logical_requests: number;
  successful_attempts: number;
  failed_attempts: number;
  total_tokens: number | null;
}

export interface AdminSourcesResponse {
  start_date: string | null;
  end_date: string | null;
  sources: AdminSourceMetric[];
  trends: Array<{
    period: string;
    source: string;
    attempts: number;
    logical_requests: number;
  }>;
}

export interface AdminFallbacksResponse {
  start_date: string | null;
  end_date: string | null;
  total_fallbacks: number;
  personal_quota_failures: number;
  invalid_personal_credentials: number;
  temporary_provider_failures: number;
  successful_company_fallbacks: number;
  failed_fallbacks: number;
  breakdown_by_reason: Array<{
    reason: string;
    count: number;
    last_occurred: string | null;
  }>;
}

export interface AdminUserListItem {
  id: string;
  email: string;
  role: 'customer' | 'admin' | 'super_admin' | string;
  is_active: boolean;
  created_at: string;
  display_name: string | null;
  total_requests: number;
  total_attempts: number;
  company_attempts: number;
  personal_attempts: number;
  total_tokens: number | null;
  last_ai_activity: string | null;
  gemini_key_status: string | null;
  gemini_key_hint: string | null;
  gemini_key_enabled: boolean | null;
  gemini_cooldown_until: string | null;
}

export interface AdminUserListResponse {
  items: AdminUserListItem[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface AdminUserDetailResponse {
  id: string;
  email: string;
  role: 'customer' | 'admin' | 'super_admin' | string;
  is_active: boolean;
  created_at: string;
  display_name: string | null;
  phone: string | null;
  ai_summary: {
    total_requests: number;
    total_attempts: number;
    company_attempts: number;
    personal_attempts: number;
    input_tokens: number | null;
    output_tokens: number | null;
    total_tokens: number | null;
    last_ai_activity: string | null;
  };
  model_breakdown: Array<{
    model_name: string;
    attempts: number;
    tokens: number | null;
  }>;
  source_breakdown: {
    company_attempts: number;
    personal_attempts: number;
  };
  recent_activity: Array<{
    correlation_id: string;
    model_name: string;
    key_source: string;
    status: string;
    error_category: string | null;
    total_tokens: number | null;
    latency_ms: number | null;
    created_at: string;
  }>;
  gemini_settings: {
    status: string;
    key_hint: string;
    is_enabled: boolean;
    cooldown_until: string | null;
    last_validated_at: string | null;
    last_used_at: string | null;
  } | null;
}

export interface AdminUserStatusUpdateRequest {
  is_active: boolean;
  reason?: string | null;
}

export interface AdminUserRoleUpdateRequest {
  role: 'customer' | 'admin' | 'super_admin';
  reason?: string | null;
}

export interface AdminProviderKeySlotItem {
  slot_index: number;
  slot_id: string;
  key_id: string;
  masked_key: string;
  quota_group: string;
  is_active: boolean;
  in_cooldown: boolean;
  cooldown_remaining_seconds: number;
  health_status: 'HEALTHY' | 'COOLDOWN' | 'DISABLED' | string;
  failure_count: number;
  last_used_at: string | null;
}

export interface AdminProvidersResponse {
  provider: string;
  configured_model: string;
  total_slots: number;
  active_slots: number;
  healthy_slots: number;
  cooldown_slots: number;
  disabled_slots: number;
  keys: AdminProviderKeySlotItem[];
}

export interface AdminAuditLogItem {
  id: string;
  actor_user_id: string | null;
  actor_email: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  metadata_json: Record<string, unknown> | null;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
}

export interface AdminAuditLogResponse {
  items: AdminAuditLogItem[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

// ---------------------------------------------------------------------------
// AI Provider Registry & Key Vault Types (Phase 6E.2)
// ---------------------------------------------------------------------------

export type ProviderConnectionStatus =
  | 'AVAILABLE'
  | 'PLANNED'
  | 'CONFIGURED'
  | 'DEGRADED'
  | 'DISABLED'
  | string;

export type ModelAccessClass =
  | 'FREE_TIER'
  | 'PROMOTIONAL_CREDITS'
  | 'PAY_AS_YOU_GO'
  | 'SELF_HOSTED'
  | 'UNKNOWN'
  | string;

export type PlatformKeyStatus =
  | 'UNVERIFIED'
  | 'ACTIVE'
  | 'COOLDOWN'
  | 'QUOTA_EXHAUSTED'
  | 'INVALID'
  | 'REVOKED'
  | string;

export interface AIProvider {
  id: string;
  code: string;
  display_name: string;
  adapter_type: string;
  base_url: string | null;
  documentation_url: string | null;
  is_enabled: boolean;
  priority: number;
  connection_status: ProviderConnectionStatus;
  created_at: string;
  updated_at: string;
  models_count?: number | null;
  keys_count?: number | null;
}

export interface AIProviderCreateRequest {
  code: string;
  display_name: string;
  adapter_type?: string;
  base_url?: string | null;
  documentation_url?: string | null;
  is_enabled?: boolean;
  priority?: number;
  connection_status?: string;
}

export interface AIProviderUpdateRequest {
  display_name?: string | null;
  adapter_type?: string | null;
  base_url?: string | null;
  documentation_url?: string | null;
  is_enabled?: boolean | null;
  priority?: number | null;
  connection_status?: string | null;
}

export interface FreeTierRateLimits {
  enforcement?: string;
  limit_types?: string[];
  rate_limits_url?: string;
  notes?: string;
  [key: string]: unknown;
}

export interface FreeTierDataUseConditions {
  free_tier_training_use?: boolean;
  human_review_permitted?: boolean;
  paid_tier_training_use?: boolean;
  promotional_credits_permanent_free?: boolean;
  sensitive_workloads_allowed?: boolean;
  requires_policy_approval?: boolean;
  summary?: string;
  [key: string]: unknown;
}

export interface FreeTierLimitMetadata {
  is_free_tier_available?: boolean;
  has_unlimited_access?: boolean;
  rate_limits?: FreeTierRateLimits;
  data_use_conditions?: FreeTierDataUseConditions;
  terms_url?: string;
  pricing_url?: string;
  rate_limits_url?: string;
  [key: string]: unknown;
}

export interface AIModel {
  id: string;
  provider_id: string;
  model_identifier: string;
  display_name: string;
  description: string | null;
  supports_text: boolean;
  supports_vision: boolean;
  supports_tools: boolean;
  supports_streaming: boolean;
  supports_json_mode: boolean;
  supports_embeddings: boolean;
  supports_speech_input: boolean;
  supports_speech_output: boolean;
  context_window: number | null;
  max_output_tokens: number | null;
  pricing_input_per_million: number | string | null;
  pricing_output_per_million: number | string | null;
  pricing_currency: string;
  access_class: ModelAccessClass;
  free_tier_limit_metadata: FreeTierLimitMetadata | null;
  terms_url: string | null;
  pricing_url: string | null;
  last_pricing_verified_at: string | null;
  is_enabled: boolean;
  is_default: boolean;
  created_at: string;
  updated_at: string;
  provider?: AIProvider;
}

export interface AIModelCreateRequest {
  provider_id: string;
  model_identifier: string;
  display_name: string;
  description?: string | null;
  supports_text?: boolean;
  supports_vision?: boolean;
  supports_tools?: boolean;
  supports_streaming?: boolean;
  supports_json_mode?: boolean;
  supports_embeddings?: boolean;
  supports_speech_input?: boolean;
  supports_speech_output?: boolean;
  context_window?: number | null;
  max_output_tokens?: number | null;
  pricing_input_per_million?: number | string | null;
  pricing_output_per_million?: number | string | null;
  pricing_currency?: string;
  access_class?: string;
  free_tier_limit_metadata?: Record<string, unknown> | null;
  terms_url?: string | null;
  pricing_url?: string | null;
  is_enabled?: boolean;
  is_default?: boolean;
}

export interface AIModelUpdateRequest {
  display_name?: string | null;
  description?: string | null;
  supports_text?: boolean | null;
  supports_vision?: boolean | null;
  supports_tools?: boolean | null;
  supports_streaming?: boolean | null;
  supports_json_mode?: boolean | null;
  supports_embeddings?: boolean | null;
  supports_speech_input?: boolean | null;
  supports_speech_output?: boolean | null;
  context_window?: number | null;
  max_output_tokens?: number | null;
  pricing_input_per_million?: number | string | null;
  pricing_output_per_million?: number | string | null;
  pricing_currency?: string | null;
  access_class?: string | null;
  free_tier_limit_metadata?: Record<string, unknown> | null;
  terms_url?: string | null;
  pricing_url?: string | null;
  last_pricing_verified_at?: string | null;
  is_enabled?: boolean | null;
  is_default?: boolean | null;
}

export interface PlatformAIKey {
  id: string;
  provider_id: string;
  key_hint: string;
  quota_group: string;
  key_label: string;
  is_enabled: boolean;
  status: PlatformKeyStatus;
  cooldown_until: string | null;
  last_validated_at: string | null;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PlatformAIKeyCreateRequest {
  provider_id: string;
  api_key: string;
  quota_group: string;
  key_label?: string;
  is_enabled?: boolean;
}

export interface PlatformAIKeyUpdateRequest {
  quota_group?: string | null;
  key_label?: string | null;
  is_enabled?: boolean | null;
  status?: string | null;
}

export interface CatalogueSeedResponse {
  providers_created: number;
  providers_updated: number;
  models_created: number;
}
