export type OrderStatus = 'pending' | 'confirmed' | 'shipped' | 'delivered' | 'cancelled' | string;

export interface UserResponse {
  id: string; // Backend UUID
  email: string;
  is_active: boolean;
  role?: string;
  created_at?: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in?: number;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface RegisterRequest {
  email: string;
  password: string;
  display_name: string;
}

export interface ShippingAddressSnapshotResponse {
  recipient_name: string;
  line1: string;
  line2?: string | null;
  city: string;
  region: string;
  postal_code: string;
  country_code: string;
}

export interface OrderItemResponse {
  id?: string;
  product_id?: string | null;
  product_sku: string;
  product_name: string;
  unit_price: number;
  quantity: number;
  line_total: number;
  created_at?: string;
}

export interface OrderSummaryResponse {
  id: string;
  order_number: string;
  user_id?: string;
  status: OrderStatus;
  currency: string;
  total_amount: number;
  item_count?: number;
  created_at: string;
}

export interface OrderResponse {
  id: string;
  order_number: string;
  user_id?: string;
  status: OrderStatus;
  currency: string;
  subtotal: number;
  shipping_fee: number;
  tax: number;
  total_amount: number;
  shipping_address_id?: string | null;
  shipping_snapshot: ShippingAddressSnapshotResponse;
  items: OrderItemResponse[];
  created_at: string;
  updated_at?: string;
}

export interface GuestOrderItemResponse {
  product_sku: string;
  product_name: string;
  quantity: number;
  unit_price: number;
  line_total: number;
}

export interface GuestOrderResponse {
  order_number: string;
  created_at: string;
  status: OrderStatus;
  currency: string;
  subtotal: number;
  shipping_fee: number;
  tax: number;
  total_amount: number;
  shipping_snapshot: ShippingAddressSnapshotResponse;
  items: GuestOrderItemResponse[];
}

export interface ProductCategoryResponse {
  id?: string;
  name: string;
  slug?: string;
}

export interface ShoppingProductItem {
  id: string;
  sku: string;
  name: string;
  description?: string | null;
  price: number;
  currency: string;
  categories?: ProductCategoryResponse[];
  in_stock: boolean;
  available_quantity?: number;
  recommendation_reason?: string | null;
}

export interface ShoppingSearchResponse {
  query?: string | null;
  message: string;
  products: ShoppingProductItem[];
  currency?: string;
  status?: string;
  total_count?: number;
  limit?: number;
  offset?: number;
}

export interface SupervisorRouteResponse {
  destination: 'order_support' | 'shopping_agent' | 'general_assistant';
  confidence?: number;
  reason?: string;
  reasoning?: string;
  order_answer?: string | null;
  shopping_response?: ShoppingSearchResponse | null;
  general_answer?: string | null;
}

export interface QuestionRequest {
  question: string;
}

export interface GuestVerificationRequest {
  order_number: string;
  email: string;
}

export interface GenericVerificationResponse {
  status?: string;
  message: string;
}

export interface GuestVerifyOTPRequest {
  order_number: string;
  email: string;
  otp: string;
}

export interface GuestTokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number; // In seconds
  order_number: string;
}

export interface ChatMessage {
  id: string;
  sender: 'user' | 'assistant';
  timestamp: string;
  text?: string;
  destination?: 'order_support' | 'shopping_agent' | 'general_assistant';
  orderAnswer?: string | null;
  shoppingResponse?: ShoppingSearchResponse | null;
  generalAnswer?: string | null;
  referencedOrderNumbers?: string[];
  isError?: boolean;
}

export interface PersonalGeminiKeyResponse {
  has_key: boolean;
  is_enabled: boolean;
  key_hint: string | null;
  status: string | null;
  last_validated_at: string | null;
  last_used_at: string | null;
  cooldown_until: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SavePersonalGeminiKeyRequest {
  api_key: string;
}

export interface ValidatePersonalGeminiKeyRequest {
  api_key: string;
}

export interface TogglePersonalGeminiKeyRequest {
  is_enabled: boolean;
}

export interface ValidatePersonalGeminiKeyResponse {
  is_valid: boolean;
  status: 'VALID' | 'INVALID_KEY' | 'QUOTA_EXHAUSTED' | 'MODEL_UNAVAILABLE' | 'TEMPORARY_FAILURE' | string;
  message: string;
  masked_key?: string | null;
}
