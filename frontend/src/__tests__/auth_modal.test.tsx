import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AuthModal } from '../components/auth/AuthModal';
import { AuthProvider } from '../context/AuthContext';

describe('AuthModal Component', () => {
  it('renders sign in tab by default', () => {
    render(
      <AuthProvider>
        <AuthModal isOpen={true} onClose={vi.fn()} />
      </AuthProvider>
    );

    expect(screen.getByRole('heading', { name: 'Welcome Back' })).toBeInTheDocument();
    expect(screen.getByLabelText(/Email Address/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Password/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Sign In to Account/i })).toBeInTheDocument();
  });

  it('switches to register tab and validates display_name requirement', async () => {
    render(
      <AuthProvider>
        <AuthModal isOpen={true} onClose={vi.fn()} />
      </AuthProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: 'Register' }));

    expect(screen.getByRole('heading', { name: 'Create Customer Account' })).toBeInTheDocument();
    expect(screen.getByLabelText(/Display Name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Confirm Password/i)).toBeInTheDocument();

    // Fill email and valid 12-char password but no display name
    fireEvent.change(screen.getByLabelText(/Email Address/i), {
      target: { value: 'test@example.com' },
    });
    fireEvent.change(screen.getByLabelText(/^Password/i), {
      target: { value: 'Password123456!' },
    });
    fireEvent.change(screen.getByLabelText(/Confirm Password/i), {
      target: { value: 'Password123456!' },
    });

    fireEvent.submit(screen.getByRole('button', { name: /Create Account/i }).closest('form')!);

    expect(screen.getByText('Display name is required.')).toBeInTheDocument();
  });

  it('validates password length between 12 and 128 characters matching backend', async () => {
    render(
      <AuthProvider>
        <AuthModal isOpen={true} onClose={vi.fn()} />
      </AuthProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: 'Register' }));

    fireEvent.change(screen.getByLabelText(/Display Name/i), {
      target: { value: 'Jane Doe' },
    });
    fireEvent.change(screen.getByLabelText(/Email Address/i), {
      target: { value: 'test@example.com' },
    });
    // Password with fewer than 12 characters (e.g. 11 characters)
    fireEvent.change(screen.getByLabelText(/^Password/i), {
      target: { value: 'ShortPass1!' },
    });
    fireEvent.change(screen.getByLabelText(/Confirm Password/i), {
      target: { value: 'ShortPass1!' },
    });

    fireEvent.click(screen.getByRole('button', { name: /Create Account/i }));

    expect(screen.getByText('Password must be between 12 and 128 characters long.')).toBeInTheDocument();

    // Password with more than 128 characters (e.g. 129 characters)
    const longPassword = 'A'.repeat(129);
    fireEvent.change(screen.getByLabelText(/^Password/i), {
      target: { value: longPassword },
    });
    fireEvent.change(screen.getByLabelText(/Confirm Password/i), {
      target: { value: longPassword },
    });

    fireEvent.click(screen.getByRole('button', { name: /Create Account/i }));

    expect(screen.getByText('Password must be between 12 and 128 characters long.')).toBeInTheDocument();
  });

  it('validates password mismatch on registration', async () => {
    render(
      <AuthProvider>
        <AuthModal isOpen={true} onClose={vi.fn()} />
      </AuthProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: 'Register' }));

    fireEvent.change(screen.getByLabelText(/Display Name/i), {
      target: { value: 'Jane Doe' },
    });
    fireEvent.change(screen.getByLabelText(/Email Address/i), {
      target: { value: 'test@example.com' },
    });
    fireEvent.change(screen.getByLabelText(/^Password/i), {
      target: { value: 'ValidPassword123456!' },
    });
    fireEvent.change(screen.getByLabelText(/Confirm Password/i), {
      target: { value: 'MismatchPassword999!' },
    });

    fireEvent.click(screen.getByRole('button', { name: /Create Account/i }));

    expect(screen.getByText('Passwords do not match.')).toBeInTheDocument();
  });
});
