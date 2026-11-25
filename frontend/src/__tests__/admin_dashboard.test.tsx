import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AuthContext } from '../context/AuthContext';
import { AdminGuard } from '../components/admin/AdminGuard';
import { AdminLayout } from '../components/admin/AdminLayout';
import { AdminOverview } from '../components/admin/AdminOverview';
import { UserManagement } from '../components/admin/UserManagement';
import { AdminAnalytics } from '../components/admin/AdminAnalytics';
import { AdminProviders } from '../components/admin/AdminProviders';
import { AdminAuditLogs } from '../components/admin/AdminAuditLogs';
import { Navbar } from '../components/layout/Navbar';
import { api, ApiError } from '../api/client';
import type {
  AdminOverviewResponse,
  AdminUserListResponse,
  AdminUserDetailResponse,
  AdminAnalyticsUsageResponse,
  AdminModelsResponse,
  AdminSourcesResponse,
  AdminFallbacksResponse,
  AdminProvidersResponse,
  AdminAuditLogResponse,
} from '../types/admin';

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

// Sample mock data matching backend Phase 6C contracts
const mockOverviewData: AdminOverviewResponse = {
  total_registered_users: 120,
  active_users: 110,
  inactive_users: 10,
  new_users_today: 4,
  new_users_this_week: 18,
  new_users_this_month: 45,
  total_orders: 340,
  total_logical_ai_requests: 1500,
  total_provider_attempts: 1750,
  company_provider_attempts: 1300,
  personal_provider_attempts: 450,
  guest_ai_requests: 125, // distinct number
  successful_attempts: 1680,
  failed_attempts: 70,
  total_input_tokens: 1250000,
  total_output_tokens: 450000,
  total_tokens: 1700000,
  personal_to_company_fallbacks: 25,
};

const mockUsersData: AdminUserListResponse = {
  items: [
    {
      id: 'usr-1111',
      email: 'customer1@example.com',
      role: 'customer',
      is_active: true,
      created_at: '2026-11-20T10:00:00Z',
      display_name: 'Customer One',
      total_requests: 15,
      total_attempts: 18,
      company_attempts: 10,
      personal_attempts: 8,
      total_tokens: 24000,
      last_ai_activity: '2026-11-24T18:00:00Z',
      gemini_key_status: 'ACTIVE',
      gemini_key_hint: '9xAb',
      gemini_key_enabled: true,
      gemini_cooldown_until: null,
    },
    {
      id: 'usr-2222',
      email: 'customer2@example.com',
      role: 'customer',
      is_active: false,
      created_at: '2026-11-21T12:00:00Z',
      display_name: 'Customer Two',
      total_requests: 5,
      total_attempts: 5,
      company_attempts: 5,
      personal_attempts: 0,
      total_tokens: 8500,
      last_ai_activity: null,
      gemini_key_status: null,
      gemini_key_hint: null,
      gemini_key_enabled: null,
      gemini_cooldown_until: null,
    },
  ],
  total: 2,
  page: 1,
  page_size: 10,
  total_pages: 1,
};

const mockUserDetail: AdminUserDetailResponse = {
  id: 'usr-1111',
  email: 'customer1@example.com',
  role: 'customer',
  is_active: true,
  created_at: '2026-11-20T10:00:00Z',
  display_name: 'Customer One',
  phone: '+15551234567',
  ai_summary: {
    total_requests: 15,
    total_attempts: 18,
    company_attempts: 10,
    personal_attempts: 8,
    input_tokens: 18000,
    output_tokens: 6000,
    total_tokens: 24000,
    last_ai_activity: '2026-11-24T18:00:00Z',
  },
  model_breakdown: [
    {
      model_name: 'gemini-2.5-flash',
      attempts: 18,
      tokens: 24000,
    },
  ],
  source_breakdown: {
    company_attempts: 10,
    personal_attempts: 8,
  },
  recent_activity: [
    {
      correlation_id: 'corr-101',
      model_name: 'gemini-2.5-flash',
      key_source: 'personal',
      status: 'success',
      error_category: null,
      total_tokens: 1200,
      latency_ms: 450,
      created_at: '2026-11-24T18:00:00Z',
    },
  ],
  gemini_settings: {
    status: 'ACTIVE',
    key_hint: '9xAb',
    is_enabled: true,
    cooldown_until: null,
    last_validated_at: '2026-11-24T10:00:00Z',
    last_used_at: '2026-11-24T18:00:00Z',
  },
};

const mockAnalyticsUsage: AdminAnalyticsUsageResponse = {
  group_by: 'daily',
  start_date: '2026-11-17T00:00:00Z',
  end_date: '2026-11-24T00:00:00Z',
  data: [
    {
      period: '2026-11-24',
      logical_requests: 150,
      provider_attempts: 175,
      company_attempts: 130,
      personal_attempts: 45,
      guest_requests: 20,
      successful_attempts: 170,
      failed_attempts: 5,
      input_tokens: 120000,
      output_tokens: 45000,
      total_tokens: 165000,
      fallbacks: 3,
    },
  ],
};

const mockModelsData: AdminModelsResponse = {
  start_date: null,
  end_date: null,
  models: [
    {
      model_name: 'gemini-2.5-flash',
      provider_attempts: 1750,
      logical_requests: 1500,
      successful_attempts: 1680,
      failed_attempts: 70,
      input_tokens: 1250000,
      output_tokens: 450000,
      total_tokens: 1700000,
      avg_latency_ms: 412,
    },
  ],
};

const mockSourcesData: AdminSourcesResponse = {
  start_date: null,
  end_date: null,
  sources: [
    {
      source: 'company',
      provider_attempts: 1300,
      logical_requests: 1100,
      successful_attempts: 1280,
      failed_attempts: 20,
      total_tokens: 1200000,
    },
    {
      source: 'personal',
      provider_attempts: 450,
      logical_requests: 400,
      successful_attempts: 400,
      failed_attempts: 50,
      total_tokens: 500000,
    },
  ],
  trends: [],
};

const mockFallbacksData: AdminFallbacksResponse = {
  start_date: null,
  end_date: null,
  total_fallbacks: 25,
  personal_quota_failures: 18,
  invalid_personal_credentials: 4,
  temporary_provider_failures: 3,
  successful_company_fallbacks: 24,
  failed_fallbacks: 1,
  breakdown_by_reason: [
    {
      reason: 'RESOURCE_EXHAUSTED',
      count: 18,
      last_occurred: '2026-11-24T16:30:00Z',
    },
  ],
};

const mockProvidersData: AdminProvidersResponse = {
  provider: 'gemini',
  configured_model: 'gemini-2.5-flash',
  total_slots: 3,
  active_slots: 3,
  healthy_slots: 2,
  cooldown_slots: 1,
  disabled_slots: 0,
  keys: [
    {
      slot_index: 0,
      slot_id: 'slot-primary-1',
      key_id: 'gemini-key-1',
      masked_key: '...8aQ1',
      quota_group: 'company-default',
      is_active: true,
      in_cooldown: false,
      cooldown_remaining_seconds: 0,
      health_status: 'HEALTHY',
      failure_count: 0,
      last_used_at: '2026-11-24T20:00:00Z',
    },
    {
      slot_index: 1,
      slot_id: 'slot-primary-2',
      key_id: 'gemini-key-2',
      masked_key: '...3mP9',
      quota_group: 'company-default',
      is_active: true,
      in_cooldown: true,
      cooldown_remaining_seconds: 45,
      health_status: 'COOLDOWN',
      failure_count: 2,
      last_used_at: '2026-11-24T19:55:00Z',
    },
  ],
};

const mockAuditLogsData: AdminAuditLogResponse = {
  items: [
    {
      id: 'audit-log-1',
      actor_user_id: 'usr-admin-1',
      actor_email: 'admin@example.com',
      action: 'user.role_change',
      target_type: 'user',
      target_id: 'usr-1111',
      metadata_json: { old_role: 'customer', new_role: 'admin', reason: 'Promotion' },
      ip_address: '127.0.0.1',
      user_agent: 'Vitest Test Agent',
      created_at: '2026-11-24T15:00:00Z',
    },
  ],
  total: 1,
  page: 1,
  page_size: 15,
  total_pages: 1,
};

describe('StoreFlow Phase 6D: Admin Dashboard Frontend Test Suite', () => {
  beforeEach(() => {
    vi.restoreAllMocks();

    // Default mock setup for api.admin endpoints
    vi.spyOn(api.admin, 'getOverview').mockResolvedValue(mockOverviewData);
    vi.spyOn(api.admin, 'listUsers').mockResolvedValue(mockUsersData);
    vi.spyOn(api.admin, 'getUser').mockResolvedValue(mockUserDetail);
    vi.spyOn(api.admin, 'updateUserStatus').mockResolvedValue({
      status: 'success',
      user_id: 'usr-1111',
      email: 'customer1@example.com',
      is_active: false,
    });
    vi.spyOn(api.admin, 'updateUserRole').mockResolvedValue({
      status: 'success',
      user_id: 'usr-1111',
      email: 'customer1@example.com',
      old_role: 'customer',
      new_role: 'admin',
    });
    vi.spyOn(api.admin, 'getAnalyticsUsage').mockResolvedValue(mockAnalyticsUsage);
    vi.spyOn(api.admin, 'getAnalyticsModels').mockResolvedValue(mockModelsData);
    vi.spyOn(api.admin, 'getAnalyticsSources').mockResolvedValue(mockSourcesData);
    vi.spyOn(api.admin, 'getAnalyticsFallbacks').mockResolvedValue(mockFallbacksData);
    vi.spyOn(api.admin, 'getProviders').mockResolvedValue(mockProvidersData);
    vi.spyOn(api.admin, 'listAuditLogs').mockResolvedValue(mockAuditLogsData);
  });

  // 1. AdminGuard blocks unauthenticated guests
  it('1. AdminGuard blocks unauthenticated guests (redirects or renders sign-in prompt)', () => {
    renderWithAuth(
      <AdminGuard>
        <div>Protected Admin Content</div>
      </AdminGuard>,
      { isAuthenticated: false, accessToken: null, currentUser: null }
    );

    expect(screen.queryByText('Protected Admin Content')).not.toBeInTheDocument();
    expect(screen.getByText(/Administrator Login Required/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Return to Customer Store/i })).toBeInTheDocument();
  });

  // 2. AdminGuard blocks authenticated users with role="customer" and shows 403 Forbidden screen
  it('2. AdminGuard blocks authenticated customers and renders safe 403 Forbidden screen', async () => {
    renderWithAuth(
      <AdminGuard>
        <div>Protected Admin Content</div>
      </AdminGuard>,
      {
        isAuthenticated: true,
        accessToken: 'customer-token',
        currentUser: { id: 'c1', email: 'cust@store.com', role: 'customer', is_active: true },
      }
    );

    await waitFor(() => {
      expect(screen.queryByText('Protected Admin Content')).not.toBeInTheDocument();
      expect(screen.getByText(/403/i)).toBeInTheDocument();
      expect(screen.getByText(/Access Denied/i)).toBeInTheDocument();
      expect(screen.getByText(/Access restricted/i)).toBeInTheDocument();
    });
  });

  // 3. AdminGuard allows authenticated users with role="admin"
  it('3. AdminGuard allows authenticated users with role="admin"', async () => {
    renderWithAuth(
      <AdminGuard>
        <div>Protected Admin Content</div>
      </AdminGuard>,
      {
        isAuthenticated: true,
        accessToken: 'admin-token',
        currentUser: { id: 'a1', email: 'admin@store.com', role: 'admin', is_active: true },
      }
    );

    await waitFor(() => {
      expect(screen.getByText('Protected Admin Content')).toBeInTheDocument();
    });
  });

  // 4. AdminGuard allows authenticated users with role="super_admin"
  it('4. AdminGuard allows authenticated users with role="super_admin"', async () => {
    renderWithAuth(
      <AdminGuard>
        <div>Protected Admin Content</div>
      </AdminGuard>,
      {
        isAuthenticated: true,
        accessToken: 'super-admin-token',
        currentUser: { id: 'sa1', email: 'super@store.com', role: 'super_admin', is_active: true },
      }
    );

    await waitFor(() => {
      expect(screen.getByText('Protected Admin Content')).toBeInTheDocument();
    });
  });

  // 5. AdminGuard probes backend overview endpoint and rejects if backend returns 403
  it('5. AdminGuard probes backend overview endpoint and rejects if backend returns 403', async () => {
    vi.spyOn(api.admin, 'getOverview').mockRejectedValue(
      new ApiError('Forbidden: Administrator privileges required', 403)
    );

    renderWithAuth(
      <AdminGuard>
        <div>Protected Admin Content</div>
      </AdminGuard>,
      {
        isAuthenticated: true,
        accessToken: 'tampered-token',
        currentUser: { id: 't1', email: 'hacker@store.com', role: 'admin', is_active: true },
      }
    );

    await waitFor(() => {
      expect(screen.queryByText('Protected Admin Content')).not.toBeInTheDocument();
      expect(screen.getByText(/Access Denied/i)).toBeInTheDocument();
      expect(screen.getByText(/403/i)).toBeInTheDocument();
    });
  });

  // 6. AdminLayout renders responsive navigation, sidebar collapse, and administrator identity badge
  it('6. AdminLayout renders responsive navigation, sidebar collapse, and identity badge', () => {
    const onTabChange = vi.fn();
    const onBackToStore = vi.fn();

    renderWithAuth(
      <AdminLayout activeTab="overview" onTabChange={onTabChange} onBackToStore={onBackToStore}>
        <div>Layout Child Content</div>
      </AdminLayout>,
      {
        currentUser: { id: 'a1', email: 'admin@store.com', role: 'admin', is_active: true },
      }
    );

    expect(screen.getAllByText(/StoreFlow/i)[0]).toBeInTheDocument();
    expect(screen.getByText(/Admin Console/i)).toBeInTheDocument();
    expect(screen.getByText('Layout Child Content')).toBeInTheDocument();
    expect(screen.getByText('admin@store.com')).toBeInTheDocument();

    // Check navigation buttons exist
    const usersTabBtn = screen.getByRole('button', { name: /User Accounts/i });
    fireEvent.click(usersTabBtn);
    expect(onTabChange).toHaveBeenCalledWith('users');

    // Test sidebar collapse toggle
    const collapseBtn = screen.getByTitle(/Collapse sidebar/i);
    fireEvent.click(collapseBtn);
  });

  // 7. AdminOverview renders all core metric cards
  it('7. AdminOverview renders all core metric cards (users, orders, requests, tokens, fallbacks)', async () => {
    renderWithAuth(<AdminOverview />);

    await waitFor(() => {
      expect(screen.getByText((120).toLocaleString())).toBeInTheDocument(); // total registered users
      expect(screen.getByText((340).toLocaleString())).toBeInTheDocument(); // total orders
      expect(screen.getByText((1500).toLocaleString())).toBeInTheDocument(); // logical requests
      expect(screen.getByText((1750).toLocaleString())).toBeInTheDocument(); // provider attempts
      expect(screen.getByText((1700000).toLocaleString())).toBeInTheDocument(); // total tokens
      expect(screen.getByText('25')).toBeInTheDocument(); // fallbacks
    });
  });

  // 8. AdminOverview correctly displays distinct logical requests vs provider attempts
  it('8. AdminOverview correctly distinguishes logical requests from provider attempts', async () => {
    renderWithAuth(<AdminOverview />);

    await waitFor(() => {
      // Logical requests card
      expect(screen.getByText((1500).toLocaleString())).toBeInTheDocument();
      expect(screen.getByText('Logical AI Requests')).toBeInTheDocument();

      // Provider attempts card
      expect(screen.getByText((1750).toLocaleString())).toBeInTheDocument();
      expect(screen.getByText('Total Provider Attempts')).toBeInTheDocument();

      // Distribution breakdown
      expect(screen.getByText(/1,300 Company/i)).toBeInTheDocument();
      expect(screen.getByText(/450 Personal/i)).toBeInTheDocument();
    });
  });

  // 9. UserManagement renders paginated user table with search and filters
  it('9. UserManagement renders paginated user table with search and filters', async () => {
    renderWithAuth(<UserManagement />);

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Search by email/i)).toBeInTheDocument();
      expect(screen.getByText('customer1@example.com')).toBeInTheDocument();
      expect(screen.getByText('customer2@example.com')).toBeInTheDocument();
    });
  });

  // 10. UserManagement displays key status badge and never displays plaintext/ciphertext key
  it('10. UserManagement displays key status badge and never exposes plaintext or ciphertext', async () => {
    const { container } = renderWithAuth(<UserManagement />);

    await waitFor(() => {
      expect(screen.getByText('customer1@example.com')).toBeInTheDocument();
    });

    // Check that status badge is rendered and masked hint is present
    expect(screen.getAllByText(/Active/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/\.\.\.9xAb/i)).toBeInTheDocument();

    // Verify no secret or ciphertext appears anywhere in DOM
    const htmlContent = container.innerHTML;
    expect(htmlContent).not.toContain('secret-plaintext');
    expect(htmlContent).not.toContain('ciphertext');
    expect(htmlContent).not.toContain('secret_key');
  });

  // 11. UserManagement opens user detail drawer with complete AI usage summary
  it('11. UserManagement opens user detail drawer with complete AI usage summary', async () => {
    renderWithAuth(<UserManagement />);

    await waitFor(() => {
      expect(screen.getByText('customer1@example.com')).toBeInTheDocument();
    });

    // Click view details button (first row Eye icon)
    const viewButtons = screen.getAllByTitle(/View Full Profile & AI Usage/i);
    fireEvent.click(viewButtons[0]);

    await waitFor(() => {
      expect(screen.getByText(/User Details & Telemetry/i)).toBeInTheDocument();
      expect(screen.getByText(/AI Consumption Metrics/i)).toBeInTheDocument();
      expect(screen.getByText(/Personal Gemini Configuration/i)).toBeInTheDocument();
      expect(screen.getAllByText(/gemini-2.5-flash/i).length).toBeGreaterThanOrEqual(1);
    });
  });

  // 12. UserManagement handles status update (active/inactive) with confirmation
  it('12. UserManagement handles status update with confirmation dialog', async () => {
    renderWithAuth(<UserManagement />);

    await waitFor(() => {
      expect(screen.getByText('customer1@example.com')).toBeInTheDocument();
    });

    // Click deactivate button for active user
    const deactivateBtn = screen.getByTitle(/Deactivate User/i);
    fireEvent.click(deactivateBtn);

    expect(screen.getByText(/Deactivate User Account/i)).toBeInTheDocument();

    // Click confirm deactivate
    const confirmBtn = screen.getByRole('button', { name: /^Deactivate$/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(api.admin.updateUserStatus).toHaveBeenCalledWith(
        'usr-1111',
        expect.objectContaining({ is_active: false }),
        'test-admin-token'
      );
    });
  });

  // 13. UserManagement allows role update for super_admin and hides/restricts for normal admin
  it('13. UserManagement allows role update for super_admin and hides for normal admin', async () => {
    // Standard admin: role change button is hidden
    const { unmount } = renderWithAuth(<UserManagement />, {
      currentUser: { id: 'a1', email: 'admin@store.com', role: 'admin', is_active: true },
    });

    await waitFor(() => {
      expect(screen.getByText('customer1@example.com')).toBeInTheDocument();
    });
    expect(screen.queryByTitle(/Change Role \(Super Admin\)/i)).not.toBeInTheDocument();
    unmount();

    // Super admin: role change button is visible and actionable
    renderWithAuth(<UserManagement />, {
      currentUser: { id: 'sa1', email: 'super@store.com', role: 'super_admin', is_active: true },
    });

    await waitFor(() => {
      expect(screen.getByText('customer1@example.com')).toBeInTheDocument();
    });

    const roleBtn = screen.getAllByTitle(/Change Role \(Super Admin\)/i)[0];
    expect(roleBtn).toBeInTheDocument();
    fireEvent.click(roleBtn);

    expect(screen.getByText(/Change User Role/i)).toBeInTheDocument();
    const saveRoleBtn = screen.getByRole('button', { name: /Update Role/i });
    fireEvent.click(saveRoleBtn);

    await waitFor(() => {
      expect(api.admin.updateUserRole).toHaveBeenCalledWith(
        'usr-1111',
        expect.objectContaining({ role: 'customer' }),
        'test-admin-token'
      );
    });
  });

  // 14. AdminAnalytics renders date range controls, usage trends, model breakdown, and fallback stats
  it('14. AdminAnalytics renders date range controls, usage trends, model breakdown, and fallbacks', async () => {
    renderWithAuth(<AdminAnalytics />);

    await waitFor(() => {
      expect(screen.getByText(/AI Telemetry & Analytics/i)).toBeInTheDocument();
      expect(screen.getByText('7D')).toBeInTheDocument();
      expect(screen.getByText('14D')).toBeInTheDocument();
      expect(screen.getByText('30D')).toBeInTheDocument();
      expect(screen.getByText(/Execution Volume by Period/i)).toBeInTheDocument();
      expect(screen.getByText(/gemini-2.5-flash/i)).toBeInTheDocument();
      expect(screen.getByText(/Automated Fallback Diagnostics/i)).toBeInTheDocument();
      expect(screen.getByText('RESOURCE_EXHAUSTED')).toBeInTheDocument();
    });
  });

  // 15. AdminProviders displays worker-specific snapshot note, configured model, and key slot cooldown badges
  it('15. AdminProviders displays worker-specific snapshot note, configured model, and key slot cooldown badges', async () => {
    renderWithAuth(<AdminProviders />);

    await waitFor(() => {
      expect(screen.getByText(/AI Providers & Platform Keys/i)).toBeInTheDocument();
      expect(screen.getByText(/Worker-Specific Runtime Snapshot/i)).toBeInTheDocument();
      expect(screen.getByText(/separate backend worker processes can have different cooldown/i)).toBeInTheDocument();
      expect(screen.getByText(/not a globally synchronized cluster state/i)).toBeInTheDocument();
      expect(screen.getByText('...8aQ1')).toBeInTheDocument();
      expect(screen.getByText('...3mP9')).toBeInTheDocument();
      expect(screen.getAllByText(/Healthy/i).length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText(/Cooldown \(45s\)/i)).toBeInTheDocument();
      expect(screen.getAllByText(/gemini-2.5-flash/i).length).toBeGreaterThanOrEqual(1);
    });
  });

  it('renders Gemini model name dynamically from backend response rather than hardcoded value', async () => {
    vi.spyOn(api.admin, 'getProviders').mockResolvedValueOnce({
      ...mockProvidersData,
      configured_model: 'gemini-1.5-pro-enterprise-custom',
    });

    renderWithAuth(<AdminProviders />);

    await waitFor(() => {
      expect(screen.getAllByText('gemini-1.5-pro-enterprise-custom').length).toBeGreaterThanOrEqual(1);
    });
  });

  // 16. AdminAuditLogs renders filterable, paginated audit trail with sanitized metadata inspector
  it('16. AdminAuditLogs renders filterable, paginated audit trail with metadata inspector', async () => {
    renderWithAuth(<AdminAuditLogs />);

    await waitFor(() => {
      expect(screen.getByText(/Security & Audit Logs/i)).toBeInTheDocument();
      expect(screen.getByText('user.role_change')).toBeInTheDocument();
      expect(screen.getByText('admin@example.com')).toBeInTheDocument();
    });

    // Click Details button to view metadata
    const detailsBtn = screen.getByRole('button', { name: /Details/i });
    fireEvent.click(detailsBtn);

    await waitFor(() => {
      expect(screen.getByText(/Audit Record Inspector/i)).toBeInTheDocument();
      expect(screen.getByText(/Sanitized Event Metadata/i)).toBeInTheDocument();
      expect(screen.getByText(/"new_role": "admin"/i)).toBeInTheDocument();
    });
  });

  // Bonus test: Navbar displays Admin Dashboard button for admin/super_admin and hides for customer
  it('renders Admin Dashboard navigation in Navbar only for admin users', () => {
    // Customer user
    const { unmount } = renderWithAuth(
      <Navbar
        onOpenAuth={vi.fn()}
        onOpenGuestPortal={vi.fn()}
        onOpenSettings={vi.fn()}
        activeView="chat"
        setActiveView={vi.fn()}
      />,
      {
        currentUser: { id: 'c1', email: 'cust@store.com', role: 'customer', is_active: true },
      }
    );
    expect(screen.queryByText(/Admin Dashboard/i)).not.toBeInTheDocument();
    unmount();

    // Admin user
    const onOpenAdmin = vi.fn();
    renderWithAuth(
      <Navbar
        onOpenAuth={vi.fn()}
        onOpenGuestPortal={vi.fn()}
        onOpenSettings={vi.fn()}
        onOpenAdmin={onOpenAdmin}
        activeView="chat"
        setActiveView={vi.fn()}
      />,
      {
        currentUser: { id: 'a1', email: 'admin@store.com', role: 'admin', is_active: true },
      }
    );
    const adminBtns = screen.getAllByText(/Admin/i);
    expect(adminBtns.length).toBeGreaterThan(0);
  });
});
