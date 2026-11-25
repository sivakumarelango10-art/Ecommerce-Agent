import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { SettingsModal } from '../components/settings/SettingsModal';
import { Navbar } from '../components/layout/Navbar';
import { AuthContext } from '../context/AuthContext';
import { api, ApiError } from '../api/client';
import { PersonalGeminiKeyResponse } from '../types/api';

// Helper to wrap component in AuthContext with custom values
function renderWithAuth(
  ui: React.ReactElement,
  authOverrides: Partial<React.ComponentProps<typeof AuthContext.Provider>['value']> = {}
) {
  const defaultAuth = {
    accessToken: 'test-valid-access-token',
    currentUser: { id: 'u1', email: 'customer@example.com', is_active: true },
    isAuthenticated: true,
    isLoading: false,
    error: null,
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    clearError: vi.fn(),
  };

  const authValue = { ...defaultAuth, ...authOverrides };

  return {
    ...render(<AuthContext.Provider value={authValue}>{ui}</AuthContext.Provider>),
    authValue,
  };
}

describe('Settings Navigation & Visibility', () => {
  it('renders AI Settings button in Navbar for authenticated users', () => {
    const onOpenSettings = vi.fn();
    renderWithAuth(
      <Navbar
        onOpenAuth={vi.fn()}
        onOpenGuestPortal={vi.fn()}
        onOpenSettings={onOpenSettings}
        activeView="chat"
        setActiveView={vi.fn()}
      />,
      { isAuthenticated: true }
    );

    const settingsButtons = screen.getAllByRole('button', { name: /AI Settings/i });
    expect(settingsButtons.length).toBeGreaterThan(0);

    fireEvent.click(settingsButtons[0]);
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });

  it('restricts AI Settings button from guests in Navbar', () => {
    renderWithAuth(
      <Navbar
        onOpenAuth={vi.fn()}
        onOpenGuestPortal={vi.fn()}
        onOpenSettings={vi.fn()}
        activeView="chat"
        setActiveView={vi.fn()}
      />,
      { isAuthenticated: false, accessToken: null, currentUser: null }
    );

    expect(screen.queryByRole('button', { name: /AI Settings/i })).not.toBeInTheDocument();
  });
});

describe('SettingsModal Component', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('does not render when isOpen is false', () => {
    renderWithAuth(<SettingsModal isOpen={false} onClose={vi.fn()} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('fetches existing key status on open and displays masked key and active status', async () => {
    const mockStatus: PersonalGeminiKeyResponse = {
      has_key: true,
      is_enabled: true,
      key_hint: '8888',
      status: 'ACTIVE',
      last_validated_at: '2026-11-24T10:00:00Z',
      last_used_at: '2026-11-24T11:00:00Z',
      cooldown_until: null,
    };

    vi.spyOn(api.gemini, 'getStatus').mockResolvedValueOnce(mockStatus);

    renderWithAuth(<SettingsModal isOpen={true} onClose={vi.fn()} />);

    expect(screen.getByRole('heading', { name: 'AI Provider Settings' })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText(/•••• •••• •••• 8888/)).toBeInTheDocument();
    });

    expect(screen.getByText('Active & Ready')).toBeInTheDocument();
    expect(screen.getByText('Enabled')).toBeInTheDocument();
    expect(screen.getByText('No Cooldown')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Replace Key' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove Key' })).toBeInTheDocument();
  });

  it('displays empty state with Add Personal Key button when user has no key', async () => {
    const mockStatus: PersonalGeminiKeyResponse = {
      has_key: false,
      is_enabled: false,
      key_hint: null,
      status: null,
      last_validated_at: null,
      last_used_at: null,
      cooldown_until: null,
    };

    vi.spyOn(api.gemini, 'getStatus').mockResolvedValueOnce(mockStatus);

    renderWithAuth(<SettingsModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Platform Pool Only')).toBeInTheDocument();
    });

    expect(screen.getByRole('button', { name: /Add Personal Key/i })).toBeInTheDocument();
  });

  it('allows validating a key successfully without saving', async () => {
    vi.spyOn(api.gemini, 'getStatus').mockResolvedValueOnce({
      has_key: false,
      is_enabled: false,
      key_hint: null,
      status: null,
      last_validated_at: null,
      last_used_at: null,
      cooldown_until: null,
    });

    const validateSpy = vi.spyOn(api.gemini, 'validateKey').mockResolvedValueOnce({
      is_valid: true,
      status: 'VALID',
      message: 'Key is valid and verified by Google Gemini.',
      masked_key: '...4321',
    });

    renderWithAuth(<SettingsModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Add Personal Key/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Add Personal Key/i }));

    const input = screen.getByLabelText(/API Key/i);
    expect(input).toHaveAttribute('type', 'password');

    fireEvent.change(input, { target: { value: 'test-only-fake-gemini-key-material-5678' } });

    const validateButton = screen.getByRole('button', { name: /Validate Key/i });
    fireEvent.click(validateButton);

    await waitFor(() => {
      expect(validateSpy).toHaveBeenCalledWith('test-only-fake-gemini-key-material-5678', 'test-valid-access-token');
    });

    expect(screen.getByText('Validation Passed')).toBeInTheDocument();
    expect(screen.getByText('Key is valid and verified by Google Gemini.')).toBeInTheDocument();
  });

  it('displays clear error on validation failure', async () => {
    vi.spyOn(api.gemini, 'getStatus').mockResolvedValueOnce({
      has_key: false,
      is_enabled: false,
      key_hint: null,
      status: null,
      last_validated_at: null,
      last_used_at: null,
      cooldown_until: null,
    });

    vi.spyOn(api.gemini, 'validateKey').mockResolvedValueOnce({
      is_valid: false,
      status: 'INVALID_KEY',
      message: 'The provided API key is invalid or has been revoked.',
      masked_key: null,
    });

    renderWithAuth(<SettingsModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Add Personal Key/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Add Personal Key/i }));

    const input = screen.getByLabelText(/API Key/i);
    fireEvent.change(input, { target: { value: 'test-only-fake-gemini-key-material-invalid' } });

    fireEvent.click(screen.getByRole('button', { name: /Validate Key/i }));

    await waitFor(() => {
      expect(screen.getByText('Validation Failed')).toBeInTheDocument();
    });

    expect(screen.getByText('The provided API key is invalid or has been revoked.')).toBeInTheDocument();
  });

  it('adds a key, clears sensitive input from React memory, and refreshes status', async () => {
    vi.spyOn(api.gemini, 'getStatus').mockResolvedValueOnce({
      has_key: false,
      is_enabled: false,
      key_hint: null,
      status: null,
      last_validated_at: null,
      last_used_at: null,
      cooldown_until: null,
    });

    const saveSpy = vi.spyOn(api.gemini, 'saveKey').mockResolvedValueOnce({
      has_key: true,
      is_enabled: true,
      key_hint: '7777',
      status: 'ACTIVE',
      last_validated_at: '2026-11-24T12:00:00Z',
      last_used_at: null,
      cooldown_until: null,
    });

    renderWithAuth(<SettingsModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Add Personal Key/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Add Personal Key/i }));

    const input = screen.getByLabelText(/API Key/i);
    fireEvent.change(input, { target: { value: 'test-only-fake-gemini-key-material-7777' } });

    fireEvent.click(screen.getByRole('button', { name: /Save Key/i }));

    await waitFor(() => {
      expect(saveSpy).toHaveBeenCalledWith('test-only-fake-gemini-key-material-7777', 'test-valid-access-token');
    });

    // Success banner is displayed
    await waitFor(() => {
      expect(screen.getByText('Personal Gemini API key saved and activated successfully.')).toBeInTheDocument();
    });

    // Form is closed and new masked key is displayed
    expect(screen.getByText(/•••• •••• •••• 7777/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/API Key/i)).not.toBeInTheDocument();
  });

  it('replaces an existing key and preserves existing key if replacement validation fails', async () => {
    const existingStatus: PersonalGeminiKeyResponse = {
      has_key: true,
      is_enabled: true,
      key_hint: '1111',
      status: 'ACTIVE',
      last_validated_at: '2026-11-24T10:00:00Z',
      last_used_at: null,
      cooldown_until: null,
    };

    vi.spyOn(api.gemini, 'getStatus').mockResolvedValueOnce(existingStatus);

    vi.spyOn(api.gemini, 'saveKey').mockRejectedValueOnce(
      new ApiError('Gemini API quota exhausted for this key.', 429)
    );

    renderWithAuth(<SettingsModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText(/•••• •••• •••• 1111/)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Replace Key' }));

    const input = screen.getByLabelText(/API Key/i);
    fireEvent.change(input, { target: { value: 'test-only-fake-gemini-key-material-noquota' } });

    fireEvent.click(screen.getByRole('button', { name: /Save Key/i }));

    await waitFor(() => {
      expect(screen.getByText('Gemini API quota exhausted for this key.')).toBeInTheDocument();
    });

    // Existing key remains intact
    expect(screen.getByText(/•••• •••• •••• 1111/)).toBeInTheDocument();
  });

  it('toggles key enabled and disabled state', async () => {
    const existingStatus: PersonalGeminiKeyResponse = {
      has_key: true,
      is_enabled: true,
      key_hint: '2222',
      status: 'ACTIVE',
      last_validated_at: '2026-11-24T10:00:00Z',
      last_used_at: null,
      cooldown_until: null,
    };

    vi.spyOn(api.gemini, 'getStatus').mockResolvedValueOnce(existingStatus);

    const toggleSpy = vi.spyOn(api.gemini, 'toggleKey').mockResolvedValueOnce({
      ...existingStatus,
      is_enabled: false,
    });

    renderWithAuth(<SettingsModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Enabled')).toBeInTheDocument();
    });

    const toggleButton = screen.getByRole('button', { name: 'Disable Personal Key' });
    fireEvent.click(toggleButton);

    await waitFor(() => {
      expect(toggleSpy).toHaveBeenCalledWith(false, 'test-valid-access-token');
    });

    expect(screen.getByText('Personal Gemini key disabled. Requests will use the StoreFlow platform pool.')).toBeInTheDocument();
  });

  it('requires confirmation before deleting key and completes deletion', async () => {
    const existingStatus: PersonalGeminiKeyResponse = {
      has_key: true,
      is_enabled: true,
      key_hint: '3333',
      status: 'ACTIVE',
      last_validated_at: '2026-11-24T10:00:00Z',
      last_used_at: null,
      cooldown_until: null,
    };

    vi.spyOn(api.gemini, 'getStatus').mockResolvedValueOnce(existingStatus);
    const deleteSpy = vi.spyOn(api.gemini, 'deleteKey').mockResolvedValueOnce(undefined);

    renderWithAuth(<SettingsModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Remove Key' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Remove Key' }));

    // Confirmation dialog appears
    expect(screen.getByText('Remove your personal Gemini key?')).toBeInTheDocument();

    // Clicking cancel hides confirmation
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByText('Remove your personal Gemini key?')).not.toBeInTheDocument();
    expect(deleteSpy).not.toHaveBeenCalled();

    // Clicking remove again and confirming
    fireEvent.click(screen.getByRole('button', { name: 'Remove Key' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Remove' }));

    await waitFor(() => {
      expect(deleteSpy).toHaveBeenCalledWith('test-valid-access-token');
    });

    expect(screen.getByText('Personal Gemini API key removed. StoreFlow will use platform keys.')).toBeInTheDocument();
    expect(screen.getByText('Platform Pool Only')).toBeInTheDocument();
  });

  it('handles authentication expiration gracefully by displaying notice and calling logout', async () => {
    vi.spyOn(api.gemini, 'getStatus').mockRejectedValueOnce(
      new ApiError('Could not validate credentials', 401)
    );

    const { authValue } = renderWithAuth(<SettingsModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Your session has expired. Please sign in again.')).toBeInTheDocument();
    });

    expect(authValue.logout).toHaveBeenCalled();
  });

  it('displays link to official Google AI Studio Gemini API keys page', async () => {
    vi.spyOn(api.gemini, 'getStatus').mockResolvedValueOnce({
      has_key: false,
      is_enabled: false,
      key_hint: null,
      status: null,
      last_validated_at: null,
      last_used_at: null,
      cooldown_until: null,
    });

    renderWithAuth(<SettingsModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Platform Pool Only')).toBeInTheDocument();
    });

    const link = screen.getByRole('link', { name: /Google AI Studio API Keys/i });
    expect(link).toHaveAttribute('href', 'https://aistudio.google.com/app/apikey');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });
});
