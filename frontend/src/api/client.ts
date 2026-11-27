import {
  UserResponse,
  TokenResponse,
  LoginRequest,
  RegisterRequest,
  SupervisorRouteResponse,
  OrderSummaryResponse,
  OrderResponse,
  OrderItemResponse,
  GuestVerificationRequest,
  GenericVerificationResponse,
  GuestVerifyOTPRequest,
  GuestTokenResponse,
  GuestOrderResponse,
  PersonalGeminiKeyResponse,
  ValidatePersonalGeminiKeyResponse,
} from '../types/api';
import type {
  AdminOverviewResponse,
  AdminAnalyticsUsageResponse,
  AdminModelsResponse,
  AdminSourcesResponse,
  AdminFallbacksResponse,
  AdminUserListResponse,
  AdminUserDetailResponse,
  AdminUserStatusUpdateRequest,
  AdminUserRoleUpdateRequest,
  AdminProvidersResponse,
  AdminAuditLogResponse,
  AIProvider,
  AIProviderCreateRequest,
  AIProviderUpdateRequest,
  AIModel,
  AIModelCreateRequest,
  AIModelUpdateRequest,
  PlatformAIKey,
  PlatformAIKeyCreateRequest,
  PlatformAIKeyUpdateRequest,
  CatalogueSeedResponse,
} from '../types/admin';

export class ApiError extends Error {
  status: number;
  detail?: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

async function handleResponse<T>(res: Response): Promise<T> {
  if (res.status === 204) {
    return undefined as T;
  }
  const contentType = res.headers.get('content-type');
  const isJson = contentType && contentType.includes('application/json');
  const body = isJson ? await res.json() : await res.text();

  if (!res.ok) {
    let errorMsg = 'An unexpected error occurred.';
    if (typeof body === 'object' && body !== null && 'detail' in body) {
      if (typeof body.detail === 'string') {
        errorMsg = body.detail;
      } else if (Array.isArray(body.detail)) {
        // FastAPI 422 validation errors
        errorMsg = body.detail.map((d: { msg?: string; loc?: string[] }) => d.msg || JSON.stringify(d)).join(', ');
      } else {
        errorMsg = JSON.stringify(body.detail);
      }
    } else if (typeof body === 'string' && body.trim().length > 0) {
      errorMsg = body;
    }
    throw new ApiError(errorMsg, res.status, body);
  }

  return body as T;
}

export const api = {
  auth: {
    async getMe(token: string): Promise<UserResponse> {
      const res = await fetch(`${BASE_URL}/api/v1/auth/me`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return handleResponse<UserResponse>(res);
    },

    async register(data: RegisterRequest): Promise<UserResponse> {
      const res = await fetch(`${BASE_URL}/api/v1/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: data.email,
          password: data.password,
          display_name: data.display_name,
        }),
      });
      return handleResponse<UserResponse>(res);
    },

    async login(data: LoginRequest): Promise<TokenResponse> {
      const res = await fetch(`${BASE_URL}/api/v1/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: data.email,
          password: data.password,
        }),
      });
      return handleResponse<TokenResponse>(res);
    },
  },

  chat: {
    async sendQuestion(question: string, token?: string | null): Promise<SupervisorRouteResponse> {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }
      const res = await fetch(`${BASE_URL}/api/v1/shopping/chat`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ question }),
      });
      return handleResponse<SupervisorRouteResponse>(res);
    },
  },

  orders: {
    async list(token: string, limit = 20, offset = 0): Promise<OrderSummaryResponse[]> {
      const res = await fetch(`${BASE_URL}/api/v1/orders?limit=${limit}&offset=${offset}`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });
      return handleResponse<OrderSummaryResponse[]>(res);
    },

    async get(orderId: string, token: string): Promise<OrderResponse> {
      const res = await fetch(`${BASE_URL}/api/v1/orders/${orderId}`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });
      return handleResponse<OrderResponse>(res);
    },

    async getItems(orderId: string, token: string): Promise<OrderItemResponse[]> {
      const res = await fetch(`${BASE_URL}/api/v1/orders/${orderId}/items`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });
      return handleResponse<OrderItemResponse[]>(res);
    },
  },

  guest: {
    async requestOtp(data: GuestVerificationRequest): Promise<GenericVerificationResponse> {
      const res = await fetch(`${BASE_URL}/api/v1/orders/guest/request-verification`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      return handleResponse<GenericVerificationResponse>(res);
    },

    async verifyOtp(data: GuestVerifyOTPRequest): Promise<GuestTokenResponse> {
      const res = await fetch(`${BASE_URL}/api/v1/orders/guest/verify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          order_number: data.order_number,
          email: data.email,
          otp: data.otp,
        }),
      });
      return handleResponse<GuestTokenResponse>(res);
    },

    async getOrder(orderNumber: string, guestToken: string): Promise<GuestOrderResponse> {
      const res = await fetch(`${BASE_URL}/api/v1/orders/guest/${orderNumber}`, {
        headers: {
          Authorization: `Bearer ${guestToken}`,
        },
      });
      return handleResponse<GuestOrderResponse>(res);
    },
  },

  gemini: {
    async getStatus(token: string): Promise<PersonalGeminiKeyResponse> {
      const res = await fetch(`${BASE_URL}/api/v1/customers/me/gemini-key`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });
      return handleResponse<PersonalGeminiKeyResponse>(res);
    },

    async saveKey(apiKey: string, token: string): Promise<PersonalGeminiKeyResponse> {
      const res = await fetch(`${BASE_URL}/api/v1/customers/me/gemini-key`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ api_key: apiKey }),
      });
      return handleResponse<PersonalGeminiKeyResponse>(res);
    },

    async toggleKey(isEnabled: boolean, token: string): Promise<PersonalGeminiKeyResponse> {
      const res = await fetch(`${BASE_URL}/api/v1/customers/me/gemini-key`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ is_enabled: isEnabled }),
      });
      return handleResponse<PersonalGeminiKeyResponse>(res);
    },

    async deleteKey(token: string): Promise<void> {
      const res = await fetch(`${BASE_URL}/api/v1/customers/me/gemini-key`, {
        method: 'DELETE',
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });
      return handleResponse<void>(res);
    },

    async validateKey(apiKey: string, token: string): Promise<ValidatePersonalGeminiKeyResponse> {
      const res = await fetch(`${BASE_URL}/api/v1/customers/me/gemini-key/validate`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ api_key: apiKey }),
      });
      return handleResponse<ValidatePersonalGeminiKeyResponse>(res);
    },
  },

  admin: {
    async getOverview(token: string): Promise<AdminOverviewResponse> {
      const res = await fetch(`${BASE_URL}/api/v1/admin/overview`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return handleResponse<AdminOverviewResponse>(res);
    },

    async getAnalyticsUsage(
      token: string,
      params?: { start_date?: string; end_date?: string; group_by?: string }
    ): Promise<AdminAnalyticsUsageResponse> {
      const q = new URLSearchParams();
      if (params?.start_date) q.append('start_date', params.start_date);
      if (params?.end_date) q.append('end_date', params.end_date);
      if (params?.group_by) q.append('group_by', params.group_by);
      const queryStr = q.toString() ? `?${q.toString()}` : '';

      const res = await fetch(`${BASE_URL}/api/v1/admin/analytics/usage${queryStr}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return handleResponse<AdminAnalyticsUsageResponse>(res);
    },

    async getAnalyticsModels(
      token: string,
      params?: { start_date?: string; end_date?: string }
    ): Promise<AdminModelsResponse> {
      const q = new URLSearchParams();
      if (params?.start_date) q.append('start_date', params.start_date);
      if (params?.end_date) q.append('end_date', params.end_date);
      const queryStr = q.toString() ? `?${q.toString()}` : '';

      const res = await fetch(`${BASE_URL}/api/v1/admin/analytics/models${queryStr}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return handleResponse<AdminModelsResponse>(res);
    },

    async getAnalyticsSources(
      token: string,
      params?: { start_date?: string; end_date?: string }
    ): Promise<AdminSourcesResponse> {
      const q = new URLSearchParams();
      if (params?.start_date) q.append('start_date', params.start_date);
      if (params?.end_date) q.append('end_date', params.end_date);
      const queryStr = q.toString() ? `?${q.toString()}` : '';

      const res = await fetch(`${BASE_URL}/api/v1/admin/analytics/sources${queryStr}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return handleResponse<AdminSourcesResponse>(res);
    },

    async getAnalyticsFallbacks(
      token: string,
      params?: { start_date?: string; end_date?: string }
    ): Promise<AdminFallbacksResponse> {
      const q = new URLSearchParams();
      if (params?.start_date) q.append('start_date', params.start_date);
      if (params?.end_date) q.append('end_date', params.end_date);
      const queryStr = q.toString() ? `?${q.toString()}` : '';

      const res = await fetch(`${BASE_URL}/api/v1/admin/analytics/fallbacks${queryStr}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return handleResponse<AdminFallbacksResponse>(res);
    },

    async listUsers(
      token: string,
      params?: {
        page?: number;
        page_size?: number;
        search?: string;
        role?: string;
        is_active?: boolean;
      }
    ): Promise<AdminUserListResponse> {
      const q = new URLSearchParams();
      if (params?.page) q.append('page', String(params.page));
      if (params?.page_size) q.append('page_size', String(params.page_size));
      if (params?.search) q.append('search', params.search);
      if (params?.role) q.append('role', params.role);
      if (params?.is_active !== undefined) q.append('is_active', String(params.is_active));
      const queryStr = q.toString() ? `?${q.toString()}` : '';

      const res = await fetch(`${BASE_URL}/api/v1/admin/users${queryStr}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return handleResponse<AdminUserListResponse>(res);
    },

    async getUser(userId: string, token: string): Promise<AdminUserDetailResponse> {
      const res = await fetch(`${BASE_URL}/api/v1/admin/users/${userId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return handleResponse<AdminUserDetailResponse>(res);
    },

    async updateUserStatus(
      userId: string,
      data: AdminUserStatusUpdateRequest,
      token: string
    ): Promise<{ status: string; user_id: string; email: string; is_active: boolean }> {
      const res = await fetch(`${BASE_URL}/api/v1/admin/users/${userId}/status`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(data),
      });
      return handleResponse<{ status: string; user_id: string; email: string; is_active: boolean }>(res);
    },

    async updateUserRole(
      userId: string,
      data: AdminUserRoleUpdateRequest,
      token: string
    ): Promise<{ status: string; user_id: string; email: string; old_role: string; new_role: string }> {
      const res = await fetch(`${BASE_URL}/api/v1/admin/users/${userId}/role`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(data),
      });
      return handleResponse<{ status: string; user_id: string; email: string; old_role: string; new_role: string }>(res);
    },

    async getProviders(token: string): Promise<AdminProvidersResponse> {
      const res = await fetch(`${BASE_URL}/api/v1/admin/providers`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return handleResponse<AdminProvidersResponse>(res);
    },

    async getPlatformKeys(token: string): Promise<AdminProvidersResponse> {
      const res = await fetch(`${BASE_URL}/api/v1/admin/providers/platform-keys`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return handleResponse<AdminProvidersResponse>(res);
    },

    async listAuditLogs(
      token: string,
      params?: {
        page?: number;
        page_size?: number;
        actor_id?: string;
        action?: string;
        target_type?: string;
        start_date?: string;
        end_date?: string;
      }
    ): Promise<AdminAuditLogResponse> {
      const q = new URLSearchParams();
      if (params?.page) q.append('page', String(params.page));
      if (params?.page_size) q.append('page_size', String(params.page_size));
      if (params?.actor_id) q.append('actor_id', params.actor_id);
      if (params?.action) q.append('action', params.action);
      if (params?.target_type) q.append('target_type', params.target_type);
      if (params?.start_date) q.append('start_date', params.start_date);
      if (params?.end_date) q.append('end_date', params.end_date);
      const queryStr = q.toString() ? `?${q.toString()}` : '';

      const res = await fetch(`${BASE_URL}/api/v1/admin/audit-logs${queryStr}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return handleResponse<AdminAuditLogResponse>(res);
    },

    ai: {
      async listProviders(
        token: string,
        params?: { status?: string; is_enabled?: boolean; adapter_type?: string }
      ): Promise<AIProvider[]> {
        const q = new URLSearchParams();
        if (params?.status) q.append('status', params.status);
        if (params?.is_enabled !== undefined) q.append('is_enabled', String(params.is_enabled));
        if (params?.adapter_type) q.append('adapter_type', params.adapter_type);
        const queryStr = q.toString() ? `?${q.toString()}` : '';

        const res = await fetch(`${BASE_URL}/api/v1/admin/ai/providers${queryStr}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        return handleResponse<AIProvider[]>(res);
      },

      async getProvider(providerId: string, token: string): Promise<AIProvider> {
        const res = await fetch(`${BASE_URL}/api/v1/admin/ai/providers/${providerId}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        return handleResponse<AIProvider>(res);
      },

      async createProvider(data: AIProviderCreateRequest, token: string): Promise<AIProvider> {
        const res = await fetch(`${BASE_URL}/api/v1/admin/ai/providers`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(data),
        });
        return handleResponse<AIProvider>(res);
      },

      async updateProvider(providerId: string, data: AIProviderUpdateRequest, token: string): Promise<AIProvider> {
        const res = await fetch(`${BASE_URL}/api/v1/admin/ai/providers/${providerId}`, {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(data),
        });
        return handleResponse<AIProvider>(res);
      },

      async listModels(
        token: string,
        params?: { provider_id?: string; access_class?: string; is_enabled?: boolean }
      ): Promise<AIModel[]> {
        const q = new URLSearchParams();
        if (params?.provider_id) q.append('provider_id', params.provider_id);
        if (params?.access_class) q.append('access_class', params.access_class);
        if (params?.is_enabled !== undefined) q.append('is_enabled', String(params.is_enabled));
        const queryStr = q.toString() ? `?${q.toString()}` : '';

        const res = await fetch(`${BASE_URL}/api/v1/admin/ai/models${queryStr}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        return handleResponse<AIModel[]>(res);
      },

      async getModel(modelId: string, token: string): Promise<AIModel> {
        const res = await fetch(`${BASE_URL}/api/v1/admin/ai/models/${modelId}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        return handleResponse<AIModel>(res);
      },

      async createModel(data: AIModelCreateRequest, token: string): Promise<AIModel> {
        const res = await fetch(`${BASE_URL}/api/v1/admin/ai/models`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(data),
        });
        return handleResponse<AIModel>(res);
      },

      async updateModel(modelId: string, data: AIModelUpdateRequest, token: string): Promise<AIModel> {
        const res = await fetch(`${BASE_URL}/api/v1/admin/ai/models/${modelId}`, {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(data),
        });
        return handleResponse<AIModel>(res);
      },

      async listKeys(
        token: string,
        params?: { provider_id?: string; quota_group?: string; status?: string }
      ): Promise<PlatformAIKey[]> {
        const q = new URLSearchParams();
        if (params?.provider_id) q.append('provider_id', params.provider_id);
        if (params?.quota_group) q.append('quota_group', params.quota_group);
        if (params?.status) q.append('status', params.status);
        const queryStr = q.toString() ? `?${q.toString()}` : '';

        const res = await fetch(`${BASE_URL}/api/v1/admin/ai/keys${queryStr}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        return handleResponse<PlatformAIKey[]>(res);
      },

      async getKey(keyId: string, token: string): Promise<PlatformAIKey> {
        const res = await fetch(`${BASE_URL}/api/v1/admin/ai/keys/${keyId}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        return handleResponse<PlatformAIKey>(res);
      },

      async createKey(data: PlatformAIKeyCreateRequest, token: string): Promise<PlatformAIKey> {
        const res = await fetch(`${BASE_URL}/api/v1/admin/ai/keys`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(data),
        });
        return handleResponse<PlatformAIKey>(res);
      },

      async updateKey(keyId: string, data: PlatformAIKeyUpdateRequest, token: string): Promise<PlatformAIKey> {
        const res = await fetch(`${BASE_URL}/api/v1/admin/ai/keys/${keyId}`, {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(data),
        });
        return handleResponse<PlatformAIKey>(res);
      },

      async deleteKey(keyId: string, token: string): Promise<void> {
        const res = await fetch(`${BASE_URL}/api/v1/admin/ai/keys/${keyId}`, {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        });
        return handleResponse<void>(res);
      },

      async seedCatalogue(token: string): Promise<CatalogueSeedResponse> {
        const res = await fetch(`${BASE_URL}/api/v1/admin/ai/catalogue/seed`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
        });
        return handleResponse<CatalogueSeedResponse>(res);
      },
    },
  },

  neon: {
    async status(token?: string): Promise<any> {
      const headers: Record<string, string> = {};
      if (token) headers['Authorization'] = `Bearer ${token}`;
      const res = await fetch(`${BASE_URL}/api/v1/neon/status`, { headers });
      return handleResponse<any>(res);
    },
    async invokeHello(token?: string): Promise<{ status: string; response: string }> {
      const headers: Record<string, string> = {};
      if (token) headers['Authorization'] = `Bearer ${token}`;
      const res = await fetch(`${BASE_URL}/api/v1/neon/function/hello`, { headers });
      return handleResponse<{ status: string; response: string }>(res);
    },
    async uploadAsset(key: string, content: string, contentType = 'text/plain', token?: string): Promise<any> {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (token) headers['Authorization'] = `Bearer ${token}`;
      const res = await fetch(`${BASE_URL}/api/v1/neon/storage/upload`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ key, content, content_type: contentType }),
      });
      return handleResponse<any>(res);
    },
    async downloadAsset(key: string, token?: string): Promise<any> {
      const headers: Record<string, string> = {};
      if (token) headers['Authorization'] = `Bearer ${token}`;
      const res = await fetch(`${BASE_URL}/api/v1/neon/storage/download?key=${encodeURIComponent(key)}`, { headers });
      return handleResponse<any>(res);
    },
    async getModels(token?: string): Promise<any> {
      const headers: Record<string, string> = {};
      if (token) headers['Authorization'] = `Bearer ${token}`;
      const res = await fetch(`${BASE_URL}/api/v1/neon/ai-gateway/models`, { headers });
      return handleResponse<any>(res);
    },
  },
};
