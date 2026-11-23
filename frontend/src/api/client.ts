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
} from '../types/api';

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
    async sendQuestion(question: string, token: string): Promise<SupervisorRouteResponse> {
      const res = await fetch(`${BASE_URL}/api/v1/shopping/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
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
