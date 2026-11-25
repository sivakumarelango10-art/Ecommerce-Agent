import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AuthContext } from '../context/AuthContext';
import { AIProviderCatalogue } from '../components/admin/AIProviderCatalogue';
import { AIProviderDetail } from '../components/admin/AIProviderDetail';
import { PlatformKeyVault } from '../components/admin/PlatformKeyVault';
import { AIModelRegistry } from '../components/admin/AIModelRegistry';
import { AIRoutingPreferences } from '../components/admin/AIRoutingPreferences';
import { AIProviderActivity } from '../components/admin/AIProviderActivity';
import { AdminGuard } from '../components/admin/AdminGuard';
import { AdminLayout } from '../components/admin/AdminLayout';
import { api, ApiError } from '../api/client';
import type { AIProvider, AIModel, PlatformAIKey } from '../types/admin';

// Helper to render with AuthContext
function renderWithAuth(
  ui: React.ReactElement,
  authOverrides: Partial<React.ComponentProps<typeof AuthContext.Provider>['value']> = {}
) {
  const defaultAuth = {
    accessToken: 'test-admin-token',
    currentUser: {
      id: 'admin-uuid-1',
      email: 'admin@example.com',
      role: 'admin',
      is_active: true,
    },
    isAuthenticated: true,
    isLoading: false,
    error: null,
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    clearError: vi.fn(),
    setUserRole: vi.fn(),
  };

  const authValue = { ...defaultAuth, ...authOverrides };

  return {
    ...render(<AuthContext.Provider value={authValue}>{ui}</AuthContext.Provider>),
    authValue,
  };
}

// 20 Mock Providers
const mockProviders: AIProvider[] = [
  {
    id: 'p-1',
    code: 'google_gemini',
    display_name: 'Google Gemini',
    adapter_type: 'google_gemini',
    base_url: 'https://generativelanguage.googleapis.com',
    documentation_url: 'https://ai.google.dev/docs',
    is_enabled: true,
    priority: 10,
    connection_status: 'AVAILABLE',
    created_at: '2026-11-20T10:00:00Z',
    updated_at: '2026-11-20T10:00:00Z',
    models_count: 2,
    keys_count: 1,
  },
  {
    id: 'p-2',
    code: 'groq',
    display_name: 'Groq',
    adapter_type: 'groq',
    base_url: 'https://api.groq.com/openai/v1',
    documentation_url: 'https://console.groq.com/docs',
    is_enabled: true,
    priority: 20,
    connection_status: 'PLANNED',
    created_at: '2026-11-20T10:00:00Z',
    updated_at: '2026-11-20T10:00:00Z',
    models_count: 1,
    keys_count: 1,
  },
  ...[
    'openrouter',
    'sarvam',
    'cloudflare_workers_ai',
    'cerebras',
    'huggingface',
    'mistral',
    'deepseek',
    'nvidia_nim',
    'together',
    'fireworks',
    'sambanova',
    'cohere',
    'xai',
    'openai',
    'perplexity',
    'github_models',
    'aws_bedrock',
    'azure_ai_foundry',
  ].map((code, idx) => ({
    id: `p-${idx + 3}`,
    code,
    display_name: code.replace(/_/g, ' ').toUpperCase(),
    adapter_type: code,
    base_url: `https://api.${code}.com`,
    documentation_url: `https://docs.${code}.com`,
    is_enabled: true,
    priority: 30 + idx * 10,
    connection_status: 'PLANNED',
    created_at: '2026-11-20T10:00:00Z',
    updated_at: '2026-11-20T10:00:00Z',
    models_count: 0,
    keys_count: 0,
  })),
];

// Mock Models
const mockModels: AIModel[] = [
  {
    id: 'm-1',
    provider_id: 'p-1',
    model_identifier: 'gemini-3.8-flash',
    display_name: 'Gemini 3.8 Flash',
    description: 'Current stable multimodal workhorse model.',
    supports_text: true,
    supports_vision: true,
    supports_tools: true,
    supports_streaming: true,
    supports_json_mode: true,
    supports_embeddings: false,
    supports_speech_input: false,
    supports_speech_output: false,
    context_window: 1048576,
    max_output_tokens: 8192,
    pricing_input_per_million: null,
    pricing_output_per_million: null,
    pricing_currency: 'USD',
    access_class: 'FREE_TIER',
    free_tier_limit_metadata: {
      is_free_tier_available: true,
      has_unlimited_access: false,
      data_use_conditions: {
        free_tier_training_use: true,
        human_review_permitted: true,
        sensitive_workloads_allowed: false,
      },
    },
    terms_url: 'https://ai.google.dev/gemini-api/terms',
    pricing_url: 'https://ai.google.dev/pricing',
    last_pricing_verified_at: null,
    is_enabled: true,
    is_default: true,
    created_at: '2026-11-20T10:00:00Z',
    updated_at: '2026-11-20T10:00:00Z',
  },
  {
    id: 'm-2',
    provider_id: 'p-1',
    model_identifier: 'gemini-3.5-flash-lite',
    display_name: 'Gemini 3.5 Flash-Lite',
    description: 'High efficiency lightweight model.',
    supports_text: true,
    supports_vision: true,
    supports_tools: true,
    supports_streaming: true,
    supports_json_mode: true,
    supports_embeddings: false,
    supports_speech_input: false,
    supports_speech_output: false,
    context_window: 1048576,
    max_output_tokens: 8192,
    pricing_input_per_million: null,
    pricing_output_per_million: null,
    pricing_currency: 'USD',
    access_class: 'FREE_TIER',
    free_tier_limit_metadata: {
      is_free_tier_available: true,
      has_unlimited_access: false,
    },
    terms_url: 'https://ai.google.dev/gemini-api/terms',
    pricing_url: 'https://ai.google.dev/pricing',
    last_pricing_verified_at: null,
    is_enabled: true,
    is_default: false,
    created_at: '2026-11-20T10:00:00Z',
    updated_at: '2026-11-20T10:00:00Z',
  },
  {
    id: 'm-3',
    provider_id: 'p-2',
    model_identifier: 'llama-3.3-70b-versatile',
    display_name: 'Llama 3.3 70B Versatile',
    description: 'Ultra-fast open source LLM on Groq LPUs.',
    supports_text: true,
    supports_vision: false,
    supports_tools: true,
    supports_streaming: true,
    supports_json_mode: true,
    supports_embeddings: false,
    supports_speech_input: false,
    supports_speech_output: false,
    context_window: 128000,
    max_output_tokens: 8192,
    pricing_input_per_million: '0.5900',
    pricing_output_per_million: '0.7900',
    pricing_currency: 'USD',
    access_class: 'PAY_AS_YOU_GO',
    free_tier_limit_metadata: null,
    terms_url: null,
    pricing_url: null,
    last_pricing_verified_at: '2026-11-24T12:00:00Z',
    is_enabled: true,
    is_default: true,
    created_at: '2026-11-20T10:00:00Z',
    updated_at: '2026-11-20T10:00:00Z',
  },
  {
    id: 'm-4',
    provider_id: 'p-3',
    model_identifier: 'custom-preview-model',
    display_name: 'Custom Preview Model',
    description: 'Experimental unverified model.',
    supports_text: true,
    supports_vision: false,
    supports_tools: false,
    supports_streaming: true,
    supports_json_mode: false,
    supports_embeddings: false,
    supports_speech_input: false,
    supports_speech_output: false,
    context_window: 32000,
    max_output_tokens: 4096,
    pricing_input_per_million: null,
    pricing_output_per_million: null,
    pricing_currency: 'USD',
    access_class: 'UNKNOWN',
    free_tier_limit_metadata: null,
    terms_url: null,
    pricing_url: null,
    last_pricing_verified_at: null,
    is_enabled: false,
    is_default: false,
    created_at: '2026-11-20T10:00:00Z',
    updated_at: '2026-11-20T10:00:00Z',
  },
];

// Mock Platform Keys
const mockKeys: PlatformAIKey[] = [
  {
    id: 'k-1',
    provider_id: 'p-1',
    key_hint: '...7777',
    quota_group: 'production-default',
    key_label: 'Gemini Primary Production Key',
    is_enabled: true,
    status: 'UNVERIFIED',
    cooldown_until: null,
    last_validated_at: null,
    last_used_at: null,
    created_at: '2026-11-24T10:00:00Z',
    updated_at: '2026-11-24T10:00:00Z',
  },
  {
    id: 'k-2',
    provider_id: 'p-2',
    key_hint: '...8888',
    quota_group: 'groq-burst-pool',
    key_label: 'Groq High Throughput Key',
    is_enabled: false,
    status: 'UNVERIFIED',
    cooldown_until: null,
    last_validated_at: null,
    last_used_at: null,
    created_at: '2026-11-24T11:00:00Z',
    updated_at: '2026-11-24T11:00:00Z',
  },
];

describe('StoreFlow Phase 6E.2: Admin AI Control Centre Frontend Test Suite', () => {
  beforeEach(() => {
    vi.restoreAllMocks();

    vi.spyOn(api.admin.ai, 'listProviders').mockResolvedValue(mockProviders);
    vi.spyOn(api.admin.ai, 'getProvider').mockImplementation(async (id: string) => {
      const found = mockProviders.find((p) => p.id === id);
      if (!found) throw new ApiError('Provider not found', 404);
      return found;
    });
    vi.spyOn(api.admin.ai, 'createProvider').mockResolvedValue(mockProviders[0]);
    vi.spyOn(api.admin.ai, 'updateProvider').mockImplementation(async (id: string, data: any) => ({
      ...mockProviders[0],
      ...data,
    }));

    vi.spyOn(api.admin.ai, 'listModels').mockResolvedValue(mockModels);
    vi.spyOn(api.admin.ai, 'getModel').mockImplementation(async (id: string) => {
      const found = mockModels.find((m) => m.id === id);
      if (!found) throw new ApiError('Model not found', 404);
      return found;
    });
    vi.spyOn(api.admin.ai, 'createModel').mockImplementation(async (data: any) => ({
      id: 'm-new',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      pricing_currency: 'USD',
      pricing_input_per_million: null,
      pricing_output_per_million: null,
      free_tier_limit_metadata: null,
      terms_url: null,
      pricing_url: null,
      last_pricing_verified_at: null,
      ...data,
    }));
    vi.spyOn(api.admin.ai, 'updateModel').mockImplementation(async (id: string, data: any) => {
      const found = mockModels.find((m) => m.id === id) || mockModels[0];
      return { ...found, ...data };
    });

    vi.spyOn(api.admin.ai, 'listKeys').mockResolvedValue(mockKeys);
    vi.spyOn(api.admin.ai, 'getKey').mockImplementation(async (id: string) => {
      const found = mockKeys.find((k) => k.id === id);
      if (!found) throw new ApiError('Key not found', 404);
      return found;
    });
    vi.spyOn(api.admin.ai, 'createKey').mockImplementation(async (data: any) => ({
      id: 'k-new',
      provider_id: data.provider_id,
      key_hint: '...9999',
      quota_group: data.quota_group,
      key_label: data.key_label || 'Default Key',
      is_enabled: true,
      status: 'UNVERIFIED',
      cooldown_until: null,
      last_validated_at: null,
      last_used_at: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }));
    vi.spyOn(api.admin.ai, 'updateKey').mockImplementation(async (id: string, data: any) => {
      const found = mockKeys.find((k) => k.id === id) || mockKeys[0];
      return { ...found, ...data };
    });
    vi.spyOn(api.admin.ai, 'deleteKey').mockResolvedValue(undefined);
    vi.spyOn(api.admin.ai, 'seedCatalogue').mockResolvedValue({
      providers_created: 0,
      providers_updated: 20,
      models_created: 0,
    });

    vi.spyOn(api.admin, 'getOverview').mockResolvedValue({
      total_registered_users: 10,
      active_users: 8,
      inactive_users: 2,
      new_users_today: 1,
      new_users_this_week: 3,
      new_users_this_month: 5,
      total_orders: 15,
      total_logical_ai_requests: 40,
      total_provider_attempts: 50,
      company_provider_attempts: 45,
      personal_provider_attempts: 5,
      guest_ai_requests: 10,
      successful_attempts: 48,
      failed_attempts: 2,
      total_input_tokens: 10000,
      total_output_tokens: 5000,
      total_tokens: 15000,
      personal_to_company_fallbacks: 2,
    });

    vi.spyOn(api.admin, 'getProviders').mockResolvedValue({
      provider: 'google',
      configured_model: 'gemini-3.8-flash',
      total_slots: 2,
      active_slots: 2,
      healthy_slots: 2,
      cooldown_slots: 0,
      disabled_slots: 0,
      keys: [
        {
          slot_index: 0,
          slot_id: 'slot-0',
          key_id: 'k-1',
          masked_key: '...7777',
          quota_group: 'production-default',
          is_active: true,
          in_cooldown: false,
          cooldown_remaining_seconds: 0,
          health_status: 'HEALTHY',
          failure_count: 0,
          last_used_at: null,
        },
      ],
    });
  });

  // 1. All providers render
  it('1. All providers render in the catalogue', async () => {
    renderWithAuth(<AIProviderCatalogue />);

    await waitFor(() => {
      expect(screen.getByText('Google Gemini')).toBeInTheDocument();
      expect(screen.getByText('Groq')).toBeInTheDocument();
      expect(screen.getByText('OPENROUTER')).toBeInTheDocument();
      expect(screen.getByText(/20 Registered/i)).toBeInTheDocument();
    });
  });

  // 2. Planned providers are not presented as operational
  it('2. Planned providers are not presented as operational', async () => {
    renderWithAuth(<AIProviderCatalogue />);

    await waitFor(() => {
      // Google Gemini is AVAILABLE
      const geminiCard = screen.getByTestId('provider-card-google_gemini');
      expect(geminiCard).toHaveTextContent(/AVAILABLE/i);
      expect(geminiCard).toHaveTextContent(/Runtime adapter active/i);

      // Groq is PLANNED
      const groqCard = screen.getByTestId('provider-card-groq');
      expect(groqCard).toHaveTextContent(/PLANNED/i);
      expect(groqCard).toHaveTextContent(/Catalogue entry only/i);
      expect(groqCard).not.toHaveTextContent(/AVAILABLE/i);
    });
  });

  // 3. Normal admin can view registry
  it('3. Normal admin can view registry', async () => {
    renderWithAuth(<AIProviderCatalogue />, {
      currentUser: { id: 'a1', email: 'admin@store.com', role: 'admin', is_active: true },
    });

    await waitFor(() => {
      expect(screen.getByText('Google Gemini')).toBeInTheDocument();
      // Normal admin sees the list but does not see super_admin mutation button like Seed 20 Providers
      expect(screen.queryByRole('button', { name: /Seed 20 Providers/i })).not.toBeInTheDocument();
    });
  });

  // 4. Normal admin cannot mutate provider credentials
  it('4. Normal admin cannot mutate provider credentials', async () => {
    renderWithAuth(<PlatformKeyVault />, {
      currentUser: { id: 'a1', email: 'admin@store.com', role: 'admin', is_active: true },
    });

    await waitFor(() => {
      expect(screen.getByText('Company API Key Vault')).toBeInTheDocument();
      expect(screen.getByText(/Super Admin required to manage keys/i)).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Add Company Key/i })).not.toBeInTheDocument();
      // Actions column shows read-only
      expect(screen.getAllByText('Read-only').length).toBeGreaterThan(0);
    });
  });

  // 5. Super admin can add a company key
  it('5. Super admin can add a company key', async () => {
    renderWithAuth(<PlatformKeyVault />, {
      currentUser: { id: 'sa1', email: 'super@store.com', role: 'super_admin', is_active: true },
    });

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Add Company Key/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Add Company Key/i }));

    expect(screen.getByRole('heading', { name: 'Add Company API Key' })).toBeInTheDocument();

    // Fill form
    fireEvent.change(screen.getByPlaceholderText(/e.g. Primary Production Pool A/i), {
      target: { value: 'New Test Key' },
    });
    fireEvent.change(screen.getByPlaceholderText(/e.g. production-default/i), {
      target: { value: 'tier-1-quota' },
    });
    fireEvent.change(screen.getByPlaceholderText(/Enter provider API key/i), {
      target: { value: 'sk-live-super-secret-12345' },
    });

    fireEvent.click(screen.getByRole('button', { name: /Save & Encrypt Key/i }));

    await waitFor(() => {
      expect(api.admin.ai.createKey).toHaveBeenCalledWith(
        expect.objectContaining({
          api_key: 'sk-live-super-secret-12345',
          key_label: 'New Test Key',
          quota_group: 'tier-1-quota',
        }),
        'test-admin-token'
      );
    });
  });

  // 6. Super admin can update key label and quota group
  it('6. Super admin can update key label and quota group', async () => {
    renderWithAuth(<PlatformKeyVault />, {
      currentUser: { id: 'sa1', email: 'super@store.com', role: 'super_admin', is_active: true },
    });

    await waitFor(() => {
      expect(screen.getByText('Gemini Primary Production Key')).toBeInTheDocument();
    });

    const editBtn = screen.getByLabelText('Edit Gemini Primary Production Key');
    fireEvent.click(editBtn);

    expect(screen.getByText('Edit Key Configuration')).toBeInTheDocument();

    const labelInput = screen.getByDisplayValue('Gemini Primary Production Key');
    fireEvent.change(labelInput, { target: { value: 'Renamed Gemini Key' } });

    fireEvent.click(screen.getByRole('button', { name: /Save Changes/i }));

    await waitFor(() => {
      expect(api.admin.ai.updateKey).toHaveBeenCalledWith(
        'k-1',
        expect.objectContaining({
          key_label: 'Renamed Gemini Key',
        }),
        'test-admin-token'
      );
    });
  });

  // 7. Super admin can enable/disable a key
  it('7. Super admin can enable/disable a key', async () => {
    renderWithAuth(<PlatformKeyVault />, {
      currentUser: { id: 'sa1', email: 'super@store.com', role: 'super_admin', is_active: true },
    });

    await waitFor(() => {
      expect(screen.getByLabelText('Toggle Gemini Primary Production Key')).toBeInTheDocument();
    });

    const toggleBtn = screen.getByLabelText('Toggle Gemini Primary Production Key');
    fireEvent.click(toggleBtn);

    await waitFor(() => {
      expect(api.admin.ai.updateKey).toHaveBeenCalledWith(
        'k-1',
        { is_enabled: false },
        'test-admin-token'
      );
    });
  });

  // 8. Key input clears after successful save
  it('8. Key input clears immediately after successful save', async () => {
    renderWithAuth(<PlatformKeyVault />, {
      currentUser: { id: 'sa1', email: 'super@store.com', role: 'super_admin', is_active: true },
    });

    await waitFor(() => {
      fireEvent.click(screen.getByRole('button', { name: /Add Company Key/i }));
    });

    // Provide required label and secret key
    fireEvent.change(screen.getByPlaceholderText(/e.g. Primary Production Pool A/i), {
      target: { value: 'Temporary Secret Label' },
    });

    const keyInput = screen.getByPlaceholderText(/Enter provider API key/i) as HTMLInputElement;
    fireEvent.change(keyInput, { target: { value: 'sk-temporary-key-to-clear' } });
    expect(keyInput.value).toBe('sk-temporary-key-to-clear');

    fireEvent.click(screen.getByRole('button', { name: /Save & Encrypt Key/i }));

    await waitFor(() => {
      expect(api.admin.ai.createKey).toHaveBeenCalled();
      // Form modal closes
      expect(screen.queryByText('Add Company API Key')).not.toBeInTheDocument();
    });
  });

  // 9. Plaintext keys are never displayed after saving
  it('9. Plaintext keys are never displayed after saving', async () => {
    renderWithAuth(<PlatformKeyVault />);

    await waitFor(() => {
      // Only masked hints like ...7777 and ...8888 are rendered
      expect(screen.getByText('...7777')).toBeInTheDocument();
      expect(screen.getByText('...8888')).toBeInTheDocument();
      // Plaintext or secret prefix is never in document
      expect(screen.queryByText(/sk-live/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/super-secret/i)).not.toBeInTheDocument();
    });
  });

  // 10. Key deletion requires confirmation
  it('10. Key deletion requires confirmation', async () => {
    renderWithAuth(<PlatformKeyVault />, {
      currentUser: { id: 'sa1', email: 'super@store.com', role: 'super_admin', is_active: true },
    });

    await waitFor(() => {
      expect(screen.getByLabelText('Delete Gemini Primary Production Key')).toBeInTheDocument();
    });

    const deleteBtn = screen.getByLabelText('Delete Gemini Primary Production Key');
    fireEvent.click(deleteBtn);

    // Confirmation dialog appears
    expect(screen.getByText('Delete Company API Key')).toBeInTheDocument();
    expect(screen.getByText(/Are you sure you want to permanently delete/i)).toBeInTheDocument();

    const confirmBtn = screen.getByRole('button', { name: /Confirm Delete Key/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(api.admin.ai.deleteKey).toHaveBeenCalledWith('k-1', 'test-admin-token');
    });
  });

  // 11. Unverified keys are labelled correctly
  it('11. Unverified keys are labelled correctly with explanation', async () => {
    renderWithAuth(<PlatformKeyVault />);

    await waitFor(() => {
      const unverifiedBadges = screen.getAllByText('UNVERIFIED');
      expect(unverifiedBadges.length).toBeGreaterThan(0);
      expect(screen.getByText(/Newly registered company keys are initialized as/i)).toBeInTheDocument();
    });
  });

  // 12. Free/paid/unknown model filters work
  it('12. Free, paid, and unknown model filters work correctly in model registry', async () => {
    renderWithAuth(<AIModelRegistry />);

    await waitFor(() => {
      expect(screen.getByText('Gemini 3.8 Flash')).toBeInTheDocument();
      expect(screen.getByText('Llama 3.3 70B Versatile')).toBeInTheDocument();
      expect(screen.getByText('Custom Preview Model')).toBeInTheDocument();
    });

    // Click "Filter Free Models" button
    const freeFilterBtn = screen.getByTestId('free-models-filter-button');
    fireEvent.click(freeFilterBtn);

    // Only FREE_TIER models should be visible
    expect(screen.getByText('Gemini 3.8 Flash')).toBeInTheDocument();
    expect(screen.getByText('Gemini 3.5 Flash-Lite')).toBeInTheDocument();
    expect(screen.queryByText('Llama 3.3 70B Versatile')).not.toBeInTheDocument();
    expect(screen.queryByText('Custom Preview Model')).not.toBeInTheDocument();

    // Toggle free off
    fireEvent.click(freeFilterBtn);

    // Filter by Access Class: PAY_AS_YOU_GO
    const accessSelect = screen.getByLabelText(/Filter by Access Class/i);
    fireEvent.change(accessSelect, { target: { value: 'PAY_AS_YOU_GO' } });

    expect(screen.getByText('Llama 3.3 70B Versatile')).toBeInTheDocument();
    expect(screen.queryByText('Gemini 3.8 Flash')).not.toBeInTheDocument();

    // Filter by Access Class: UNKNOWN
    fireEvent.change(accessSelect, { target: { value: 'UNKNOWN' } });
    expect(screen.getByText('Custom Preview Model')).toBeInTheDocument();
    expect(screen.queryByText('Llama 3.3 70B Versatile')).not.toBeInTheDocument();
  });

  // 13. Missing pricing is displayed as unverified
  it('13. Missing pricing is displayed as unverified (NULL)', async () => {
    renderWithAuth(<AIModelRegistry />);

    await waitFor(() => {
      // Gemini models with NULL pricing show Unverified (NULL)
      const unverifiedElements = screen.getAllByText('Unverified (NULL)');
      expect(unverifiedElements.length).toBeGreaterThan(0);

      // Groq model with verified pricing shows prices
      expect(screen.getByText('$0.5900 / $0.7900')).toBeInTheDocument();
    });
  });

  // 14. Admin model enabled/default controls work
  it('14. Admin model enabled/default controls work for super_admin', async () => {
    renderWithAuth(<AIModelRegistry />, {
      currentUser: { id: 'sa1', email: 'super@store.com', role: 'super_admin', is_active: true },
    });

    await waitFor(() => {
      expect(screen.getByText('Gemini 3.8 Flash')).toBeInTheDocument();
    });

    // Disable Gemini 3.8 Flash
    const disableButtons = screen.getAllByRole('button', { name: 'Disable' });
    fireEvent.click(disableButtons[0]);

    await waitFor(() => {
      expect(api.admin.ai.updateModel).toHaveBeenCalledWith(
        'm-1',
        { is_enabled: false },
        'test-admin-token'
      );
    });

    // Make Gemini 3.5 Flash-Lite default
    const makeDefaultBtn = screen.getByRole('button', { name: 'Make Default' });
    fireEvent.click(makeDefaultBtn);

    await waitFor(() => {
      expect(api.admin.ai.updateModel).toHaveBeenCalledWith(
        'm-2',
        { is_default: true },
        'test-admin-token'
      );
    });
  });

  // 15. Data-use warnings are displayed
  it('15. Data-use warnings and privacy policies are displayed', async () => {
    renderWithAuth(<AIModelRegistry />);

    await waitFor(() => {
      expect(screen.getByText(/Free Tier Availability & Data Privacy Guidelines/i)).toBeInTheDocument();
      expect(screen.getByText(/Sensitive customer orders, payments, credentials, or PII must not be transmitted/i)).toBeInTheDocument();
      expect(screen.getByText(/Free availability does not imply unlimited access/i)).toBeInTheDocument();
    });
  });

  // 16. Provider API failures are handled
  it('16. Provider API failures are handled with friendly error messages', async () => {
    vi.spyOn(api.admin.ai, 'listProviders').mockRejectedValue(
      new ApiError('Failed to communicate with provider registry database.', 500)
    );

    renderWithAuth(<AIProviderCatalogue />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to communicate with provider registry database/i)).toBeInTheDocument();
    });
  });

  // 17. Mobile layout works
  it('17. Mobile layout works with drawer toggle and responsive controls', () => {
    const onTabChange = vi.fn();
    renderWithAuth(
      <AdminLayout activeTab="providers" onTabChange={onTabChange}>
        <div data-testid="mobile-child">AI Catalogue Content</div>
      </AdminLayout>
    );

    // Mobile drawer button exists
    const drawerBtn = screen.getByLabelText(/Toggle Navigation Drawer/i);
    expect(drawerBtn).toBeInTheDocument();

    // Open mobile menu
    fireEvent.click(drawerBtn);

    // Nav items are rendered
    expect(screen.getAllByRole('button', { name: /Provider Catalogue/i })[0]).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /Company API Key Vault/i })[0]).toBeInTheDocument();
  });

  // 18. Unauthenticated access blocked by AdminGuard
  it('18. Unauthenticated access is blocked by AdminGuard from accessing AI Control Centre', async () => {
    renderWithAuth(
      <AdminGuard>
        <div data-testid="secret-ai-content">Protected AI Content</div>
      </AdminGuard>,
      { isAuthenticated: false, accessToken: null }
    );

    expect(screen.queryByTestId('secret-ai-content')).not.toBeInTheDocument();
    expect(screen.getByText(/Administrator Login Required/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Sign In as Administrator/i })).toBeInTheDocument();
  });

  // 19. Customer users receive a 403 Forbidden screen
  it('19. Customer users receive a 403 Forbidden screen when attempting access', async () => {
    renderWithAuth(
      <AdminGuard>
        <div data-testid="secret-ai-content">Protected AI Content</div>
      </AdminGuard>,
      {
        currentUser: { id: 'c1', email: 'cust@store.com', role: 'customer', is_active: true },
      }
    );

    expect(screen.queryByTestId('secret-ai-content')).not.toBeInTheDocument();
    expect(screen.getByText(/Access Denied \(403\)/i)).toBeInTheDocument();
    expect(screen.getByText(/You do not possess administrator privileges/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Return to StoreFlow/i })).toBeInTheDocument();
  });

  // 20. Routing Preferences component remains explicitly inactive
  it('20. Routing Preferences component remains explicitly inactive and makes zero API calls', () => {
    const listSpy = vi.spyOn(api.admin.ai, 'listProviders');
    renderWithAuth(<AIRoutingPreferences />);

    expect(screen.getByText(/AI Traffic Routing Preferences/i)).toBeInTheDocument();
    expect(screen.getByText(/Preview \/ Inactive \(Phase 6F\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Runtime Routing Status: Direct Single-Driver Mode/i)).toBeInTheDocument();
    expect(screen.getByText(/gemini-3.8-flash/i)).toBeInTheDocument();
    expect(listSpy).not.toHaveBeenCalled();
  });

  // 21. Provider Activity displays live Gemini worker snapshot and multi-provider unavailable notice
  it('21. Provider Activity displays live Gemini worker snapshot and multi-provider unavailable notice', async () => {
    renderWithAuth(<AIProviderActivity />);

    await waitFor(() => {
      expect(screen.getByText(/Live Runtime Telemetry: Google Gemini Driver/i)).toBeInTheDocument();
      expect(screen.getByText(/Multi-Provider Activity Telemetry: Unavailable \(Phase 6F\)/i)).toBeInTheDocument();
      expect(screen.getByText(/Worker-Specific Runtime Snapshot:/i)).toBeInTheDocument();
      expect(screen.getByText('AI Providers & Platform Keys')).toBeInTheDocument();
    });
  });

  // 22. Provider Detail view displays provider metadata and UNVERIFIED key notices
  it('22. Provider Detail view displays provider metadata and UNVERIFIED key notices', async () => {
    const onBack = vi.fn();
    renderWithAuth(<AIProviderDetail providerId="p-1" onBack={onBack} />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Google Gemini' })).toBeInTheDocument();
      expect(screen.getByText(/This provider is operational and connected to StoreFlow's live runtime agent pipeline/i)).toBeInTheDocument();
      expect(screen.getAllByText(/Key is unverified & not yet active for live traffic/i).length).toBeGreaterThan(0);
    });
  });

  // 23. Empty states are rendered appropriately
  it('23. Empty states are rendered appropriately when lists have zero elements', async () => {
    vi.spyOn(api.admin.ai, 'listKeys').mockResolvedValue([]);

    renderWithAuth(<PlatformKeyVault />);

    await waitFor(() => {
      expect(screen.getByText('No Company Keys Found')).toBeInTheDocument();
      expect(screen.getByText(/No company platform API keys have been registered in the vault yet/i)).toBeInTheDocument();
    });
  });

  // 24. Direct HTTP contract tests verify exact endpoints and HTTP methods
  it('24. Direct API client methods match backend routes and HTTP methods', async () => {
    vi.restoreAllMocks();
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (init?.method === 'DELETE' || url.includes('/keys/')) {
        return {
          status: 204,
          ok: true,
          headers: new Headers(),
          text: async () => '',
        };
      }
      return {
        status: 200,
        ok: true,
        headers: new Headers({ 'content-type': 'application/json' }),
        json: async () => ({ success: true, providers_created: 0, providers_updated: 20, models_created: 0 }),
      };
    });
    vi.stubGlobal('fetch', fetchMock);

    // GET /api/v1/admin/ai/providers
    await api.admin.ai.listProviders('test-token');
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/admin/ai/providers'),
      expect.objectContaining({
        headers: { Authorization: 'Bearer test-token' },
      })
    );

    // POST /api/v1/admin/ai/catalogue/seed
    await api.admin.ai.seedCatalogue('test-token');
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/admin/ai/catalogue/seed'),
      expect.objectContaining({
        method: 'POST',
        headers: { Authorization: 'Bearer test-token' },
      })
    );

    // DELETE /api/v1/admin/ai/keys/k-1
    await api.admin.ai.deleteKey('k-1', 'test-token');
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/admin/ai/keys/k-1'),
      expect.objectContaining({
        method: 'DELETE',
        headers: { Authorization: 'Bearer test-token' },
      })
    );

    vi.unstubAllGlobals();
  });
});
